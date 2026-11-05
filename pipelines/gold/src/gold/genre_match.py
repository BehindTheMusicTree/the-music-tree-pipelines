import json
import logging
from dataclasses import dataclass
from pathlib import Path

import polars as pl
from jsonschema import ValidationError, validate

from common.quality_checks import check_non_empty, check_row_count_delta
from gold.genre_disambiguation import Disambiguation, disambiguate, load_curated_disambiguation, most_precise
from gold.genre_precedence import apply_precedence, precedence_closure
from gold.genre_targets import TREE_CANONICAL, load_genre_targets
from gold.match_method import METHOD_RANK

GENRE_MATCH_REPORT_SCHEMA_PATH = Path(__file__).parent / "schemas" / "genre_match_report.schema.json"

logger = logging.getLogger(__name__)

# manual_genre_alias.csv / manual_accepted_non_genre_tags.csv (curated in grow-the-music-tree-api, pulled into Bronze by the curation pipeline): a data expert's triage decisions for
# musicbrainz tag names that don't auto-match a wikidata genre label (canonical or regional) — either a real genre
# just named differently (this CSV) or permanent non-genre folksonomy noise
# (manual_accepted_non_genre_tags.csv). See 1_genre_match_unresolved.csv for the names still awaiting
# triage.

# A real YouTube video id is always exactly 11 characters. Malformed ids (seen in real MusicBrainz
# data — a truncated match or a stray trailing character) are cleared here, before matching, rather
# than reaching `song_export`'s checks: those raise hard, which would abort the *entire* export over
# a single bad row. The song itself is kept, videoless, like any other song without a video.
_YOUTUBE_VIDEO_ID_PATTERN = r"^[A-Za-z0-9_-]{11}$"


def _genre_key(name: pl.Expr) -> pl.Expr:
    """The one spelling-insensitive form every genre-name comparison goes through, applied to both sides
    alike: wikidata labels `rock music`/`hip-hop` and musicbrainz tags `rock`/`hip hop` share a key."""
    return name.str.to_lowercase().str.replace_all(r"[-_\s]+", " ").str.strip_chars().str.replace(r" music$", "")


@dataclass(frozen=True)
class _LabelIndex:
    """Target genre labels reachable by lowercased exact name, and by genre key when the key is unambiguous."""

    exact: dict[str, str]
    by_key: dict[str, str]

    @classmethod
    def build(cls, labels: set[str]) -> "_LabelIndex":
        frame = pl.DataFrame({"label": sorted(labels)}).with_columns(
            lower=pl.col("label").str.to_lowercase(), key=_genre_key(pl.col("label"))
        )
        # e.g. `dance` and `dance music` are distinct wikidata genres; a key shared by both can't pick one,
        # so each stays reachable only by its exact name.
        ambiguous = frame.group_by("key").agg(pl.col("label").sort()).filter(pl.col("label").list.len() > 1)
        if not ambiguous.is_empty():
            logger.warning(
                "genre labels sharing a genre key, matched by exact name only: %s",
                sorted(ambiguous["label"].to_list()),
            )
        unambiguous = frame.filter(~pl.col("key").is_in(ambiguous["key"].implode()))
        return cls(
            exact=dict(frame.select("lower", "label").iter_rows()),
            by_key=dict(unambiguous.select("key", "label").iter_rows()),
        )

    def exact_match(self, name: pl.Expr) -> pl.Expr:
        return name.str.to_lowercase().replace_strict(self.exact, default=None, return_dtype=pl.Utf8)

    def normalized_match(self, name: pl.Expr) -> pl.Expr:
        return _genre_key(name).replace_strict(self.by_key, default=None, return_dtype=pl.Utf8)

    def auto_matches(self, name: pl.Expr) -> pl.Expr:
        return self.exact_match(name).is_not_null() | self.normalized_match(name).is_not_null()


def _blank_mask(column: str) -> pl.Expr:
    return pl.col(column).is_null() | (pl.col(column).str.strip_chars() == "")


def _validate_curation_csv(df: pl.DataFrame, required: tuple[str, ...], csv_name: str) -> None:
    for column in required:
        if not df.filter(_blank_mask(column)).is_empty():
            raise ValueError(f"{csv_name} has row(s) with a null/blank '{column}'")
    if df.select(_genre_key(pl.col("musicbrainz_genre_name")).is_duplicated().any()).item():
        raise ValueError(f"{csv_name} contains duplicate musicbrainz_genre_name rows (same genre key)")


def _validate_manual_genre_alias(df: pl.DataFrame, index: _LabelIndex, target_labels: set[str], csv_name: str) -> None:
    _validate_curation_csv(df, ("musicbrainz_genre_name", "wikidata_genre_name", "reason"), csv_name)

    unknown = sorted(set(df.select("wikidata_genre_name").to_series()) - target_labels)
    if unknown:
        raise ValueError(
            f"{csv_name} references wikidata_genre_name(s) not found in the canonical or regional hierarchy "
            f"(or naming a regional overview without sub-genres): {unknown}"
        )

    dead_weight = df.filter(index.auto_matches(pl.col("musicbrainz_genre_name")))["musicbrainz_genre_name"].to_list()
    if dead_weight:
        raise ValueError(f"{csv_name} has dead-weight alias(es) that already auto-match: {dead_weight}")


def _validate_manual_accepted_non_genre_tags(
    df: pl.DataFrame, index: _LabelIndex, alias_keys: pl.Series, csv_name: str
) -> None:
    _validate_curation_csv(df, ("musicbrainz_genre_name", "reason"), csv_name)

    name = pl.col("musicbrainz_genre_name")
    conflicts = df.filter(index.auto_matches(name) | _genre_key(name).is_in(alias_keys.implode()))[
        "musicbrainz_genre_name"
    ].to_list()
    if conflicts:
        raise ValueError(
            f"{csv_name} has entr(y/ies) that conflict with an auto-match or manual_genre_alias.csv: {conflicts}"
        )


def _wikidata_alias_by_key(
    aliases: pl.DataFrame, targets: pl.DataFrame, closure: pl.DataFrame
) -> tuple[dict[str, str], pl.DataFrame]:
    """Wikidata's English aliases of target items, by genre key. An alias whose key is any target label's key
    is dropped (the label wins, even one reachable by exact name only). A key shared by aliases of several items
    keeps only the most precise of them: one left is a plain key -> item_id match (by id, so an alias of a regional
    item sharing a canonical item's label keeps its own item), several left (on different lineages) become automatic
    disambiguation candidates (`key, item_id, is_default`), with no default."""
    labels = targets.select("item_id", "item_label")
    label_keys = labels.select(_genre_key(pl.col("item_label"))).to_series().implode()
    keyed = (
        aliases.join(labels, on="item_id")
        .select("item_id", "item_label", key=_genre_key(pl.col("alias")))
        .filter(pl.col("key") != _genre_key(pl.col("item_label")))
        .unique()
    )
    colliding = sorted(set(keyed.filter(pl.col("key").is_in(label_keys))["key"]))
    if colliding:
        logger.warning("wikidata aliases colliding with a genre label, not matched: %s", colliding)
    precise = most_precise(
        keyed.filter(~pl.col("key").is_in(label_keys)).select("key", "item_id").unique(), ["key"], closure
    )
    is_single = pl.len().over("key") == 1
    single = precise.filter(is_single)
    ambiguous = precise.filter(~is_single).with_columns(is_default=pl.lit(False))
    return dict(single.select("key", "item_id").iter_rows()), ambiguous


def _resolve_tags(
    tag_names: pl.DataFrame,
    item_id_by_label: dict[str, str],
    index: _LabelIndex,
    alias_by_key: dict[str, str],
    non_genre_keys: pl.Series,
    curated_keys: pl.Series,
    ambiguous_keys: pl.Series,
    wikidata_alias_by_key: dict[str, str],
) -> pl.DataFrame:
    """One row per distinct tag name: `(genre_name, item_id, method)`, `method` being the first cascade step
    that resolves it, else `accepted_non_genre` / `ambiguous` (picked per song later) / `unmatched` (null
    `item_id`). A curated disambiguation key outranks the `normalized` step, an automatic one comes after
    `accepted_non_genre`."""
    name = pl.col("genre_name")
    return (
        tag_names.with_columns(
            exact_match=index.exact_match(name),
            normalized_match=index.normalized_match(name),
            alias_match=_genre_key(name).replace_strict(alias_by_key, default=None, return_dtype=pl.Utf8),
            is_non_genre=_genre_key(name).is_in(non_genre_keys.implode()),
            is_curated=_genre_key(name).is_in(curated_keys.implode()),
            is_ambiguous=_genre_key(name).is_in(ambiguous_keys.implode()),
            wikidata_alias_match=_genre_key(name).replace_strict(
                wikidata_alias_by_key, default=None, return_dtype=pl.Utf8
            ),
        )
        .with_columns(
            label=pl.coalesce(
                "exact_match",
                pl.when(~pl.col("is_curated")).then(pl.col("normalized_match")),
                "alias_match",
            ),
            method=pl.when(pl.col("exact_match").is_not_null())
            .then(pl.lit("exact"))
            .when(pl.col("is_curated"))
            .then(pl.lit("ambiguous"))
            .when(pl.col("normalized_match").is_not_null())
            .then(pl.lit("normalized"))
            .when(pl.col("alias_match").is_not_null())
            .then(pl.lit("manual_alias"))
            .when(pl.col("is_non_genre"))
            .then(pl.lit("accepted_non_genre"))
            .when(pl.col("is_ambiguous"))
            .then(pl.lit("ambiguous"))
            .when(pl.col("wikidata_alias_match").is_not_null())
            .then(pl.lit("wikidata_alias"))
            .otherwise(pl.lit("unmatched")),
        )
        .select(
            "genre_name",
            item_id=pl.coalesce(
                pl.col("label").replace_strict(item_id_by_label, default=None, return_dtype=pl.Utf8),
                pl.when(~pl.col("is_non_genre") & ~pl.col("is_ambiguous")).then(pl.col("wikidata_alias_match")),
            ),
            method="method",
        )
    )


_SONG = "musicbrainz_recording_id"
_ARTIST = "artist_musicbrainz_id"
LEVEL_RECORDING = "recording"
LEVEL_RELEASE_GROUP = "release_group"
LEVEL_ARTIST = "artist"
# Most precise first: a song takes its genre from the first level whose tags resolve to a wikidata genre.
LEVELS = (LEVEL_RECORDING, LEVEL_RELEASE_GROUP, LEVEL_ARTIST)


def _of_level(level_column: str, field: str) -> pl.Expr:
    return pl.coalesce(pl.when(pl.col(level_column) == level).then(pl.col(f"{level}_{field}")) for level in LEVELS)


def _pick(
    tags: pl.LazyFrame,
    subject: str,
    resolution: pl.LazyFrame,
    disambiguation: Disambiguation,
    closure: pl.DataFrame,
) -> tuple[pl.LazyFrame, pl.LazyFrame]:
    """One genre per subject from its tags (`<subject>, genre_name, weight`). Returns one row per tagged subject
    (`<subject>, item_id, secondary_genre_item_ids, resolved_method, leftover_method, musicbrainz_genres`, `item_id`
    null when no tag resolved) and the subject's unresolved tags (`<subject>, genre_name, weight`)."""
    resolved_tags = tags.join(resolution, on="genre_name")
    tag = [subject, "genre_name"]
    ambiguous_tags = resolved_tags.filter(pl.col("method") == "ambiguous").with_columns(
        key=_genre_key(pl.col("genre_name"))
    )
    context = resolved_tags.filter(pl.col("item_id").is_not_null()).select(subject, context_item_id="item_id").unique()
    picks, ignored = disambiguate(ambiguous_tags, context, disambiguation, closure, subject)
    settled = pl.concat([picks.select(tag), ignored])
    unpicked = ambiguous_tags.join(settled, on=tag, how="anti")

    resolved = (
        pl.concat(
            [
                resolved_tags.filter(pl.col("item_id").is_not_null()).select(*tag, "item_id", "weight", "method"),
                picks.select(*tag, "item_id", "weight", method=pl.lit("disambiguated")),
            ]
        )
        .with_columns(method_rank=pl.col("method").replace_strict(METHOD_RANK, return_dtype=pl.UInt8))
        .group_by(subject, "item_id")
        .agg(pl.col("weight").sum(), pl.col("method_rank").min())
    )
    # Precedence already drops every genre beaten by another of the subject's genres, so the genres left after the
    # first are neither ancestors of the primary nor of each other.
    best = (
        apply_precedence(resolved, closure, subject)
        .sort(["weight", "item_id"], descending=[True, False])
        .group_by(subject)
        .agg(
            pl.col("item_id").first(),
            pl.col("method_rank").first(),
            secondary_genre_item_ids=pl.col("item_id").slice(1),
        )
        .select(
            subject,
            "item_id",
            "secondary_genre_item_ids",
            resolved_method=pl.col("method_rank").replace_strict(dict(enumerate(METHOD_RANK)), return_dtype=pl.Utf8),
        )
    )
    raw_genres = (
        tags.sort(["weight", "genre_name"], descending=[True, False])
        .group_by(subject)
        .agg(musicbrainz_genres=pl.struct(name="genre_name", weight="weight"))
    )
    leftover = (
        resolved_tags.filter(pl.col("item_id").is_null())
        .join(settled, on=tag, how="anti")
        .sort(["weight", "genre_name"], descending=[True, False])
        .group_by(subject)
        .first()
        .select(subject, leftover_method="method")
    )
    picked = raw_genres.join(best, on=subject, how="left").join(leftover, on=subject, how="left")
    unresolved = pl.concat(
        [resolved_tags.filter(pl.col("method") == "unmatched").select(*tag, "weight"), unpicked.select(*tag, "weight")]
    )
    return picked, unresolved


_TAG_SET = "tag_set_id"


def _pick_by_tag_set(
    tags: pl.LazyFrame,
    subject: str,
    resolution: pl.LazyFrame,
    disambiguation: Disambiguation,
    closure: pl.DataFrame,
) -> tuple[pl.LazyFrame, pl.LazyFrame, pl.LazyFrame]:
    """`_pick` depends only on a subject's own tags, and subjects share them massively (every song of a release
    group carries the group's tags), so it runs once per distinct tag set and the result is joined back. Same
    output as `_pick`, returned as the `(<subject>, tag_set_id)` map, the picks per set and the unresolved tags,
    which carry `n`, the number of subjects sharing the set. Joining the picks per set, not per subject, keeps the
    wide list columns out of the 30M-row song join."""
    per_subject = tags.sort(["genre_name", "weight"]).group_by(subject).agg(tag_set=pl.struct("genre_name", "weight"))
    keyed = per_subject.with_columns(pl.col("tag_set").hash().alias(_TAG_SET)).collect()
    # A 64-bit hash collision would silently merge two different tag sets.
    if keyed["tag_set"].n_unique() != keyed[_TAG_SET].n_unique():
        raise ValueError(f"tag set hash collision while grouping {subject} tags")
    sets = keyed.unique(subset=_TAG_SET).select(_TAG_SET, "tag_set")
    sizes = keyed.group_by(_TAG_SET).agg(n=pl.len())
    set_tags = sets.explode("tag_set", empty_as_null=False).unnest("tag_set").lazy()
    picked, unresolved = _pick(set_tags, _TAG_SET, resolution, disambiguation, closure)
    unresolved = unresolved.join(sizes.lazy(), on=_TAG_SET).drop(_TAG_SET)
    return keyed.select(subject, _TAG_SET).lazy(), picked, unresolved


def genre_match(
    songs_path: Path,
    song_genre_path: Path,
    artist_genre_path: Path,
    canonical_hierarchy_path: Path,
    regional_hierarchy_path: Path,
    regional_classification_path: Path,
    parent_selection_path: Path,
    secondary_parents_path: Path,
    manual_genre_alias_path: Path,
    manual_accepted_non_genre_tags_path: Path,
    manual_genre_precedence_path: Path,
    manual_genre_disambiguation_path: Path,
    manual_genre_disambiguation_context_path: Path,
    wikidata_aliases_path: Path,
    output_dir: Path,
) -> Path:
    logger.info(
        "matching %s and %s genres against %s and %s",
        song_genre_path,
        artist_genre_path,
        canonical_hierarchy_path,
        regional_hierarchy_path,
    )
    songs = pl.scan_parquet(songs_path)
    targets = load_genre_targets(canonical_hierarchy_path, regional_hierarchy_path, regional_classification_path)
    matchable = targets.filter(~pl.col("is_leaf_overview"))

    check_non_empty(songs.head(1).collect(), songs_path.name)

    is_malformed = ~pl.col("youtube_video_id").str.contains(_YOUTUBE_VIDEO_ID_PATTERN)
    malformed_ids = sorted(set(songs.filter(is_malformed).select("youtube_video_id").collect().to_series()))
    if malformed_ids:
        logger.warning("malformed youtube_video_id(s), video cleared before genre matching: %s", malformed_ids)
        songs = songs.with_columns(
            pl.when(is_malformed).then(None).otherwise(pl.col(column)).alias(column)
            for column in ("youtube_video_id", "youtube_unplayable_reason", "youtube_view_count")
        )

    # A label both trees carry resolves to the canonical item.
    item_id_by_label = dict(
        matchable.sort(pl.col("genre_tree") != TREE_CANONICAL, "item_id")
        .unique(subset="item_label", keep="first", maintain_order=True)
        .select("item_label", "item_id")
        .iter_rows()
    )
    target_labels = set(item_id_by_label)
    index = _LabelIndex.build(target_labels)

    alias_df = pl.read_csv(manual_genre_alias_path)
    non_genre_df = pl.read_csv(manual_accepted_non_genre_tags_path)
    _validate_manual_genre_alias(alias_df, index, target_labels, manual_genre_alias_path.name)
    alias_keyed = alias_df.select(key=_genre_key(pl.col("musicbrainz_genre_name")), label="wikidata_genre_name")
    _validate_manual_accepted_non_genre_tags(
        non_genre_df, index, alias_keyed["key"], manual_accepted_non_genre_tags_path.name
    )
    non_genre_keys = non_genre_df.select(_genre_key(pl.col("musicbrainz_genre_name"))).to_series()

    # Childless overviews stay in the closure, so lineage through them and disambiguation context hints on them still apply.
    closure = precedence_closure(targets, parent_selection_path, secondary_parents_path, manual_genre_precedence_path)
    exact_labels = pl.Series([label.lower() for label in target_labels])
    curated = load_curated_disambiguation(
        manual_genre_disambiguation_path,
        manual_genre_disambiguation_context_path,
        set(matchable["item_id"]),
        set(targets["item_id"]),
        pl.concat([exact_labels, alias_keyed["key"], non_genre_keys]),
        closure,
        _genre_key,
    )
    wikidata_alias_by_key, automatic = _wikidata_alias_by_key(
        pl.read_parquet(wikidata_aliases_path), matchable, closure
    )
    disambiguation = curated.with_automatic(automatic)

    song_genre = pl.scan_parquet(song_genre_path)
    artist_genre = pl.scan_parquet(artist_genre_path)
    resolution = _resolve_tags(
        pl.concat([song_genre.select("genre_name"), artist_genre.select("genre_name")]).unique().collect(),
        item_id_by_label,
        index,
        dict(alias_keyed.iter_rows()),
        non_genre_keys,
        curated.keys,
        disambiguation.keys,
        wikidata_alias_by_key,
    ).lazy()

    level_tags = {
        LEVEL_RECORDING: (song_genre.filter(pl.col("level") == LEVEL_RECORDING), _SONG),
        LEVEL_RELEASE_GROUP: (song_genre.filter(pl.col("level") == LEVEL_RELEASE_GROUP), _SONG),
        LEVEL_ARTIST: (artist_genre, _ARTIST),
    }
    matched = songs
    unresolved_by_level = []
    for level, (tags, subject) in level_tags.items():
        tag_set_of, picked, unresolved_tags = _pick_by_tag_set(
            tags.select(subject, "genre_name", "weight"), subject, resolution, disambiguation, closure
        )
        fields = [field for field in picked.collect_schema().names() if field != _TAG_SET]
        matched = (
            matched.join(tag_set_of, on=subject, how="left")
            .join(picked.rename({field: f"{level}_{field}" for field in fields}), on=_TAG_SET, how="left")
            .drop(_TAG_SET)
        )
        unresolved_by_level.append(unresolved_tags.select("genre_name", "weight", "n", level=pl.lit(level)))

    resolved_level = pl.coalesce(
        pl.when(pl.col(f"{level}_item_id").is_not_null()).then(pl.lit(level)) for level in LEVELS
    )
    tagged_level = pl.coalesce(
        pl.when(pl.col(f"{level}_musicbrainz_genres").is_not_null()).then(pl.lit(level)) for level in LEVELS
    )
    labels = matchable.select("item_id", "is_umbrella", "genre_tree", wikidata_genre_name="item_label")
    matched = (
        matched.with_columns(genre_source=resolved_level, tagged_level=tagged_level)
        .with_columns(
            item_id=_of_level("genre_source", "item_id"),
            secondary_genre_item_ids=_of_level("genre_source", "secondary_genre_item_ids"),
            resolved_method=_of_level("genre_source", "resolved_method"),
            leftover_method=_of_level("tagged_level", "leftover_method"),
            musicbrainz_genres=pl.coalesce(
                _of_level("genre_source", "musicbrainz_genres"), _of_level("tagged_level", "musicbrainz_genres")
            ),
        )
        .join(labels.lazy(), on="item_id", how="left")
        .select(
            "musicbrainz_recording_id",
            "title",
            "artist",
            "artist_musicbrainz_id",
            "youtube_video_id",
            "youtube_unplayable_reason",
            "youtube_view_count",
            "first_release_year",
            "recording_count",
            "wikidata_genre_name",
            wikidata_genre_item_id="item_id",
            is_umbrella=pl.col("is_umbrella").fill_null(False),
            genre_tree="genre_tree",
            match_method=pl.coalesce("resolved_method", "leftover_method", pl.lit("no_genre")),
            genre_source="genre_source",
            secondary_genre_item_ids=pl.col("secondary_genre_item_ids").fill_null([]),
            musicbrainz_genres=pl.col("musicbrainz_genres").fill_null([]),
        )
    )

    output_dir.mkdir(parents=True, exist_ok=True)
    output_path = output_dir / "1_genre_match.parquet"
    matched.sink_parquet(output_path, engine="streaming")

    # genre_match is a per-song lookup, not a join — the row count must stay exactly equal to `songs`; any
    # drift would mean a future change accidentally turned this into a fan-out/fan-in join.
    song_count = songs.select(pl.len()).collect().item()
    check_row_count_delta(song_count, pl.scan_parquet(output_path).select(pl.len()).collect().item(), "genre_match")

    unresolved = (
        pl.concat(unresolved_by_level)
        .group_by("genre_name", "level")
        .agg(subject_count=pl.col("n").sum(), weight_sum=(pl.col("weight") * pl.col("n")).sum())
        .sort(["subject_count", "genre_name", "level"], descending=[True, False, False])
        .collect()
    )
    unresolved_names = unresolved["genre_name"].unique().sort().to_list()
    if unresolved_names:
        logger.warning(
            "%d unmatched or ambiguous genre name(s), see 1_genre_match_unresolved.csv: %s",
            len(unresolved_names),
            unresolved_names,
        )
    unresolved_path = output_dir / "1_genre_match_unresolved.csv"
    unresolved.write_csv(unresolved_path)

    report = {"unresolvedGenreTagCount": len(unresolved_names)}
    schema = json.loads(GENRE_MATCH_REPORT_SCHEMA_PATH.read_text())
    try:
        validate(report, schema)
    except ValidationError as e:
        raise ValueError(f"genre match report failed schema validation ({GENRE_MATCH_REPORT_SCHEMA_PATH}): {e.message}")
    (output_dir / "1_genre_match_report.json").write_text(json.dumps(report, indent=2))

    logger.info(
        "wrote %d rows to %s (%d unresolved tag(s), see %s)",
        song_count,
        output_path,
        len(unresolved_names),
        unresolved_path,
    )
    return output_path
