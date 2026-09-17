import logging
from pathlib import Path

import polars as pl

from common.quality_checks import check_non_empty, check_null_rate, check_row_count_delta

logger = logging.getLogger(__name__)

# Committed alongside the code (not a gitignored gold output): a data expert's triage decisions for
# musicbrainz tag names that don't auto-match a canonical wikidata genre label — either a real genre
# just named differently (this CSV) or permanent non-genre folksonomy noise
# (manual_accepted_non_genre_tags.csv). See 1_genre_match_unresolved.csv for the names still awaiting
# triage.
MANUAL_GENRE_ALIAS_PATH = Path(__file__).parent / "manual_genre_alias.csv"
MANUAL_ACCEPTED_NON_GENRE_TAGS_PATH = Path(__file__).parent / "manual_accepted_non_genre_tags.csv"

_MUSIC_SUFFIX_PATTERN = r" music$"


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


def genre_match(
    songs_path: Path,
    canonical_hierarchy_path: Path,
    manual_genre_alias_path: Path,
    manual_accepted_non_genre_tags_path: Path,
    output_dir: Path,
) -> Path:
    logger.info("matching %s genres against %s", songs_path, canonical_hierarchy_path)
    songs = pl.read_parquet(songs_path)
    hierarchy = pl.read_parquet(canonical_hierarchy_path)

    check_non_empty(songs, songs_path.name)
    check_null_rate(songs, "genre_name", songs_path.name)
    check_non_empty(hierarchy, canonical_hierarchy_path.name)
    check_null_rate(hierarchy, "item_label", canonical_hierarchy_path.name)

    canonical_labels = set(hierarchy.select("item_label").unique().to_series())
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

    matched = (
        songs.with_columns(
            genre_lower=pl.col("genre_name").str.to_lowercase(),
        )
        .with_columns(
            genre_suffix_stripped=pl.col("genre_lower").str.replace(_MUSIC_SUFFIX_PATTERN, ""),
        )
        .with_columns(
            exact_match=pl.col("genre_lower").replace_strict(canonical_lookup, default=None, return_dtype=pl.Utf8),
            suffix_match=pl.col("genre_suffix_stripped").replace_strict(
                canonical_lookup, default=None, return_dtype=pl.Utf8
            ),
            alias_match=pl.col("genre_lower").replace_strict(alias_lookup, default=None, return_dtype=pl.Utf8),
            is_non_genre=pl.col("genre_lower").is_in(list(non_genre_set)),
        )
        .with_columns(
            wikidata_genre_name=pl.coalesce("exact_match", "suffix_match", "alias_match"),
            match_method=pl.when(pl.col("exact_match").is_not_null())
            .then(pl.lit("exact"))
            .when(pl.col("suffix_match").is_not_null())
            .then(pl.lit("music_suffix"))
            .when(pl.col("alias_match").is_not_null())
            .then(pl.lit("manual_alias"))
            .when(pl.col("is_non_genre"))
            .then(pl.lit("accepted_non_genre"))
            .otherwise(pl.lit("unmatched")),
        )
        .select("title", "artist", "youtube_video_id", "genre_name", "wikidata_genre_name", "match_method")
    )

    # genre_match is a per-row lookup, not a join — height must stay exactly equal to `songs`; any
    # drift would mean a future change accidentally turned this into a fan-out/fan-in join.
    check_row_count_delta(songs.height, matched.height, "genre_match")

    unresolved = matched.filter(pl.col("match_method") == "unmatched").select(
        "genre_name", "title", "artist", "youtube_video_id"
    )
    if not unresolved.is_empty():
        distinct_names = sorted(set(unresolved.select("genre_name").to_series()))
        logger.warning(
            "%d unmatched genre name(s), see 1_genre_match_unresolved.csv: %s", len(distinct_names), distinct_names
        )

    output_dir.mkdir(parents=True, exist_ok=True)
    unresolved_path = output_dir / "1_genre_match_unresolved.csv"
    unresolved.write_csv(unresolved_path)

    output_path = output_dir / "1_genre_match.parquet"
    matched.write_parquet(output_path)
    logger.info(
        "wrote %d rows to %s (%d unresolved, see %s)", matched.height, output_path, unresolved.height, unresolved_path
    )
    return output_path
