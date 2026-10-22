import json
import logging
from pathlib import Path

import polars as pl
from jsonschema import ValidationError, validate

from common.quality_checks import check_non_empty, check_null_rate, check_row_count_delta
from gold.genre_precedence import apply_precedence, precedence_closure

GENRE_MATCH_REPORT_SCHEMA_PATH = Path(__file__).parent / "schemas" / "genre_match_report.schema.json"

logger = logging.getLogger(__name__)

# manual_genre_alias.csv / manual_accepted_non_genre_tags.csv (curated in grow-the-music-tree-api, pulled into Bronze by the curation pipeline): a data expert's triage decisions for
# musicbrainz tag names that don't auto-match a canonical wikidata genre label — either a real genre
# just named differently (this CSV) or permanent non-genre folksonomy noise
# (manual_accepted_non_genre_tags.csv). See 1_genre_match_unresolved.csv for the names still awaiting
# triage.

_MUSIC_SUFFIX_PATTERN = r" music$"

# A real YouTube video id is always exactly 11 characters. Malformed ids (seen in real MusicBrainz
# data — a truncated match or a stray trailing character) are cleared here, before matching, rather
# than reaching `song_export`'s checks: those raise hard, which would abort the *entire* export over
# a single bad row. The song itself is kept, videoless, like any other song without a video.
_YOUTUBE_VIDEO_ID_PATTERN = r"^[A-Za-z0-9_-]{11}$"


def _blank_mask(df: pl.DataFrame, column: str) -> pl.Expr:
    return pl.col(column).is_null() | (pl.col(column).str.strip_chars() == "")


def _would_auto_match(mb_lower: str, canonical_lookup: dict[str, str]) -> bool:
    if mb_lower in canonical_lookup:
        return True
    return mb_lower.endswith(" music") and mb_lower[:-6] in canonical_lookup


def _validate_manual_genre_alias(
    df: pl.DataFrame, canonical_lookup: dict[str, str], canonical_labels: set[str], csv_name: str
) -> None:
    for column in ("musicbrainz_genre_name", "wikidata_genre_name", "reason"):
        if not df.filter(_blank_mask(df, column)).is_empty():
            raise ValueError(f"{csv_name} has row(s) with a null/blank '{column}'")

    names = (
        df.with_columns(pl.col("musicbrainz_genre_name").str.to_lowercase())
        .select("musicbrainz_genre_name")
        .to_series()
        .to_list()
    )
    if len(names) != len(set(names)):
        raise ValueError(f"{csv_name} contains duplicate musicbrainz_genre_name rows (case-insensitive)")

    unknown = sorted(set(df.select("wikidata_genre_name").to_series()) - canonical_labels)
    if unknown:
        raise ValueError(
            f"{csv_name} references wikidata_genre_name(s) not found in the canonical hierarchy: {unknown}"
        )

    dead_weight = [name for name in names if _would_auto_match(name, canonical_lookup)]
    if dead_weight:
        raise ValueError(f"{csv_name} has dead-weight alias(es) that already auto-match: {dead_weight}")


def _validate_manual_accepted_non_genre_tags(
    df: pl.DataFrame, canonical_lookup: dict[str, str], alias_lower: set[str], csv_name: str
) -> None:
    for column in ("musicbrainz_genre_name", "reason"):
        if not df.filter(_blank_mask(df, column)).is_empty():
            raise ValueError(f"{csv_name} has row(s) with a null/blank '{column}'")

    names = (
        df.with_columns(pl.col("musicbrainz_genre_name").str.to_lowercase())
        .select("musicbrainz_genre_name")
        .to_series()
        .to_list()
    )
    if len(names) != len(set(names)):
        raise ValueError(f"{csv_name} contains duplicate musicbrainz_genre_name rows (case-insensitive)")

    conflicts = [name for name in names if _would_auto_match(name, canonical_lookup) or name in alias_lower]
    if conflicts:
        raise ValueError(
            f"{csv_name} has entr(y/ies) that conflict with an auto-match or manual_genre_alias.csv: {conflicts}"
        )


METHOD_RANK = {"exact": 0, "music_suffix": 1, "manual_alias": 2}


def _resolve_tags(
    tag_names: pl.DataFrame,
    item_id_by_label: dict[str, str],
    canonical_lookup: dict[str, str],
    alias_lookup: dict[str, str],
    non_genre_set: set[str],
) -> pl.DataFrame:
    """One row per distinct tag name: `(genre_name, item_id, method)`, `method` being the first cascade step
    that resolves it, else `accepted_non_genre` / `unmatched` (null `item_id`)."""
    return (
        tag_names.with_columns(genre_lower=pl.col("genre_name").str.to_lowercase())
        .with_columns(genre_suffix_stripped=pl.col("genre_lower").str.replace(_MUSIC_SUFFIX_PATTERN, ""))
        .with_columns(
            exact_match=pl.col("genre_lower").replace_strict(canonical_lookup, default=None, return_dtype=pl.Utf8),
            suffix_match=pl.col("genre_suffix_stripped").replace_strict(
                canonical_lookup, default=None, return_dtype=pl.Utf8
            ),
            alias_match=pl.col("genre_lower").replace_strict(alias_lookup, default=None, return_dtype=pl.Utf8),
            is_non_genre=pl.col("genre_lower").is_in(list(non_genre_set)),
        )
        .with_columns(
            label=pl.coalesce("exact_match", "suffix_match", "alias_match"),
            method=pl.when(pl.col("exact_match").is_not_null())
            .then(pl.lit("exact"))
            .when(pl.col("suffix_match").is_not_null())
            .then(pl.lit("music_suffix"))
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
    canonical_lookup = {label.lower(): label for label in canonical_labels}

    alias_df = pl.read_csv(manual_genre_alias_path)
    non_genre_df = pl.read_csv(manual_accepted_non_genre_tags_path)
    _validate_manual_genre_alias(alias_df, canonical_lookup, canonical_labels, manual_genre_alias_path.name)
    alias_lower = {row["musicbrainz_genre_name"].lower() for row in alias_df.iter_rows(named=True)}
    _validate_manual_accepted_non_genre_tags(
        non_genre_df, canonical_lookup, alias_lower, manual_accepted_non_genre_tags_path.name
    )
    alias_lookup = {
        row["musicbrainz_genre_name"].lower(): row["wikidata_genre_name"] for row in alias_df.iter_rows(named=True)
    }
    non_genre_set = set(non_genre_df.select("musicbrainz_genre_name").to_series().str.to_lowercase())

    song_genre = pl.scan_parquet(song_genre_path)
    resolution = _resolve_tags(
        song_genre.select("genre_name").unique().collect(),
        item_id_by_label,
        canonical_lookup,
        alias_lookup,
        non_genre_set,
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
