import json
import logging
from dataclasses import dataclass
from pathlib import Path

import polars as pl
from jsonschema import ValidationError, validate

from common.quality_checks import check_non_empty, check_null_rate, check_row_count_delta
from gold.genre_precedence import apply_precedence, precedence_closure
from gold.match_method import METHOD_RANK

GENRE_MATCH_REPORT_SCHEMA_PATH = Path(__file__).parent / "schemas" / "genre_match_report.schema.json"

logger = logging.getLogger(__name__)

# manual_genre_alias.csv / manual_accepted_non_genre_tags.csv (curated in grow-the-music-tree-api, pulled into Bronze by the curation pipeline): a data expert's triage decisions for
# musicbrainz tag names that don't auto-match a canonical wikidata genre label — either a real genre
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
    """Canonical labels reachable by lowercased exact name, and by genre key when the key is unambiguous."""

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
                "canonical labels sharing a genre key, matched by exact name only: %s",
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


def _validate_manual_genre_alias(
    df: pl.DataFrame, index: _LabelIndex, canonical_labels: set[str], csv_name: str
) -> None:
    _validate_curation_csv(df, ("musicbrainz_genre_name", "wikidata_genre_name", "reason"), csv_name)

    unknown = sorted(set(df.select("wikidata_genre_name").to_series()) - canonical_labels)
    if unknown:
        raise ValueError(
            f"{csv_name} references wikidata_genre_name(s) not found in the canonical hierarchy: {unknown}"
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


def _resolve_tags(
    tag_names: pl.DataFrame,
    item_id_by_label: dict[str, str],
    index: _LabelIndex,
    alias_by_key: dict[str, str],
    non_genre_keys: pl.Series,
) -> pl.DataFrame:
    """One row per distinct tag name: `(genre_name, item_id, method)`, `method` being the first cascade step
    that resolves it, else `accepted_non_genre` / `unmatched` (null `item_id`)."""
    name = pl.col("genre_name")
    return (
        tag_names.with_columns(
            exact_match=index.exact_match(name),
            normalized_match=index.normalized_match(name),
            alias_match=_genre_key(name).replace_strict(alias_by_key, default=None, return_dtype=pl.Utf8),
            is_non_genre=_genre_key(name).is_in(non_genre_keys.implode()),
        )
        .with_columns(
            label=pl.coalesce("exact_match", "normalized_match", "alias_match"),
            method=pl.when(pl.col("exact_match").is_not_null())
            .then(pl.lit("exact"))
            .when(pl.col("normalized_match").is_not_null())
            .then(pl.lit("normalized"))
            .when(pl.col("alias_match").is_not_null())
            .then(pl.lit("manual_alias"))
            .when(pl.col("is_non_genre"))
            .then(pl.lit("accepted_non_genre"))
            .otherwise(pl.lit("unmatched")),
        )
        .select(
            "genre_name",
            item_id=pl.col("label").replace_strict(item_id_by_label, default=None, return_dtype=pl.Utf8),
            method="method",
        )
    )


def genre_match(
    songs_path: Path,
    song_genre_path: Path,
    canonical_hierarchy_path: Path,
    secondary_parents_path: Path,
    manual_genre_alias_path: Path,
    manual_accepted_non_genre_tags_path: Path,
    manual_genre_precedence_path: Path,
    output_dir: Path,
) -> Path:
    logger.info("matching %s genres against %s", song_genre_path, canonical_hierarchy_path)
    songs = pl.scan_parquet(songs_path)
    hierarchy = pl.read_parquet(canonical_hierarchy_path)

    check_non_empty(songs.head(1).collect(), songs_path.name)
    check_non_empty(hierarchy, canonical_hierarchy_path.name)
    check_null_rate(hierarchy, "item_label", canonical_hierarchy_path.name)

    is_malformed = ~pl.col("youtube_video_id").str.contains(_YOUTUBE_VIDEO_ID_PATTERN)
    malformed_ids = sorted(set(songs.filter(is_malformed).select("youtube_video_id").collect().to_series()))
    if malformed_ids:
        logger.warning("malformed youtube_video_id(s), video cleared before genre matching: %s", malformed_ids)
        songs = songs.with_columns(
            pl.when(is_malformed).then(None).otherwise(pl.col(column)).alias(column)
            for column in ("youtube_video_id", "youtube_unplayable_reason")
        )

    item_id_by_label = dict(
        hierarchy.sort("item_id")
        .unique(subset="item_label", keep="first", maintain_order=True)
        .select("item_label", "item_id")
        .iter_rows()
    )
    canonical_labels = set(item_id_by_label)
    index = _LabelIndex.build(canonical_labels)

    alias_df = pl.read_csv(manual_genre_alias_path)
    non_genre_df = pl.read_csv(manual_accepted_non_genre_tags_path)
    _validate_manual_genre_alias(alias_df, index, canonical_labels, manual_genre_alias_path.name)
    alias_keyed = alias_df.select(key=_genre_key(pl.col("musicbrainz_genre_name")), label="wikidata_genre_name")
    _validate_manual_accepted_non_genre_tags(
        non_genre_df, index, alias_keyed["key"], manual_accepted_non_genre_tags_path.name
    )
    non_genre_keys = non_genre_df.select(_genre_key(pl.col("musicbrainz_genre_name"))).to_series()

    song_genre = pl.scan_parquet(song_genre_path)
    resolution = _resolve_tags(
        song_genre.select("genre_name").unique().collect(),
        item_id_by_label,
        index,
        dict(alias_keyed.iter_rows()),
        non_genre_keys,
    )
    tags = song_genre.join(resolution.lazy(), on="genre_name")

    resolved = (
        tags.filter(pl.col("item_id").is_not_null())
        .with_columns(method_rank=pl.col("method").replace_strict(METHOD_RANK, return_dtype=pl.UInt8))
        .group_by("musicbrainz_recording_id", "item_id")
        .agg(pl.col("weight").sum(), pl.col("method_rank").min())
    )
    closure = precedence_closure(hierarchy, secondary_parents_path, manual_genre_precedence_path)
    best = (
        apply_precedence(resolved, closure)
        .sort(["weight", "item_id"], descending=[True, False])
        .group_by("musicbrainz_recording_id")
        .first()
        .select(
            "musicbrainz_recording_id",
            "item_id",
            resolved_method=pl.col("method_rank").replace_strict(dict(enumerate(METHOD_RANK)), return_dtype=pl.Utf8),
        )
    )
    leftover = (
        tags.filter(pl.col("item_id").is_null())
        .sort(["weight", "genre_name"], descending=[True, False])
        .group_by("musicbrainz_recording_id")
        .first()
        .select("musicbrainz_recording_id", leftover_method="method")
    )
    labels = hierarchy.select("item_id", wikidata_genre_name="item_label", is_umbrella=pl.col("is_umbrella"))
    matched = (
        songs.join(best, on="musicbrainz_recording_id", how="left")
        .join(leftover, on="musicbrainz_recording_id", how="left")
        .join(labels.lazy(), on="item_id", how="left")
        .select(
            "musicbrainz_recording_id",
            "title",
            "artist",
            "artist_musicbrainz_id",
            "youtube_video_id",
            "youtube_unplayable_reason",
            "first_release_year",
            "wikidata_genre_name",
            wikidata_genre_item_id="item_id",
            is_umbrella=pl.col("is_umbrella").fill_null(False),
            match_method=pl.coalesce("resolved_method", "leftover_method", pl.lit("no_genre")),
        )
    )

    output_dir.mkdir(parents=True, exist_ok=True)
    output_path = output_dir / "1_genre_match.parquet"
    matched.sink_parquet(output_path)

    # genre_match is a per-song lookup, not a join — the row count must stay exactly equal to `songs`; any
    # drift would mean a future change accidentally turned this into a fan-out/fan-in join.
    song_count = songs.select(pl.len()).collect().item()
    check_row_count_delta(song_count, pl.scan_parquet(output_path).select(pl.len()).collect().item(), "genre_match")

    unresolved = (
        tags.filter(pl.col("method") == "unmatched")
        .group_by("genre_name")
        .agg(song_count=pl.len(), weight_sum=pl.col("weight").sum())
        .sort(["song_count", "genre_name"], descending=[True, False])
        .collect()
    )
    if not unresolved.is_empty():
        logger.warning(
            "%d unmatched genre name(s), see 1_genre_match_unresolved.csv: %s",
            unresolved.height,
            unresolved["genre_name"].to_list(),
        )
    unresolved_path = output_dir / "1_genre_match_unresolved.csv"
    unresolved.write_csv(unresolved_path)

    report = {"unresolvedGenreTagCount": unresolved.height}
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
        unresolved.height,
        unresolved_path,
    )
    return output_path
