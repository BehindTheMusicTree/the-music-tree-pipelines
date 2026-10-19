import logging
from pathlib import Path

import polars as pl
from common.quality_checks import (
    ARTIST_NAME_LEN_MAX,
    FIRST_RELEASE_YEAR_RANGE,
    SONG_TITLE_LEN_MAX,
    check_max_length,
    check_non_empty,
    check_null_rate,
    check_unique_key,
)

from gold.curation_csv import ITEM_ID_PATTERN, read_curation_csv
from gold.delta_export import write_delta
from gold.ndjson_parts import write_ndjson_parts

logger = logging.getLogger(__name__)

ROWS_PER_PART = 250_000
OUTPUT_DIRNAME = "2_songs"

MBID_PATTERN = r"^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$"
_PATTERNS = {
    "musicbrainz_recording_id": MBID_PATTERN,
    "artist_musicbrainz_id": MBID_PATTERN,
    "youtube_video_id": r"^[A-Za-z0-9_-]{11}$",
}
_UNPLAYABLE_REASONS = ["not_found", "not_embeddable", "private", "not_processed", "region_whitelisted"]


def _check_songs(songs: pl.DataFrame) -> None:
    check_non_empty(songs, OUTPUT_DIRNAME)
    for column in ("musicbrainz_recording_id", "title", "artist", "artist_musicbrainz_id"):
        check_null_rate(songs, column, OUTPUT_DIRNAME)
    check_unique_key(songs, "musicbrainz_recording_id", OUTPUT_DIRNAME)
    check_max_length(songs, "title", SONG_TITLE_LEN_MAX, OUTPUT_DIRNAME)
    check_max_length(songs, "artist", ARTIST_NAME_LEN_MAX, OUTPUT_DIRNAME)
    for column, pattern in _PATTERNS.items():
        malformed = songs.filter(~pl.col(column).str.contains(pattern)).height
        if malformed:
            raise ValueError(f"{OUTPUT_DIRNAME}: {malformed} row(s) with a malformed {column}")
    bad_reason = songs.filter(~pl.col("youtube_unplayable_reason").is_in(_UNPLAYABLE_REASONS)).height
    if bad_reason:
        raise ValueError(f"{OUTPUT_DIRNAME}: {bad_reason} row(s) with an unknown youtube_unplayable_reason")


def _genre_names_by_item_id(wikidata_silver_dir: Path) -> dict[str, str]:
    """Each tree item's emitted node name, which grow resolves a song's `genre_name` against."""
    names: dict[str, str] = {}
    for name in ("7_canonical_hierarchy.parquet", "8_regional_hierarchy.parquet"):
        hierarchy = pl.read_parquet(wikidata_silver_dir / name)
        label = pl.col("item_label")
        if "item_display_label" in hierarchy.columns:
            label = pl.coalesce("item_display_label", "item_label")
        names.update(hierarchy.select("item_id", label).unique(subset="item_id").iter_rows())
    return names


def _check_known_recordings(overrides: pl.DataFrame, songs: pl.DataFrame, csv_name: str) -> None:
    unknown = overrides.join(songs, on="musicbrainz_recording_id", how="anti").get_column("musicbrainz_recording_id")
    if not unknown.is_empty():
        raise ValueError(f"{csv_name} references unknown musicbrainz_recording_id(s): {unknown.to_list()[:3]}")


def _apply_recording_overrides(songs: pl.DataFrame, wikidata_silver_dir: Path, curation_dir: Path) -> pl.DataFrame:
    genre_csv = "manual_recording_genre.csv"
    genres = read_curation_csv(
        curation_dir / genre_csv,
        required=("musicbrainz_recording_id", "reason"),
        key=("musicbrainz_recording_id",),
        patterns={"musicbrainz_recording_id": MBID_PATTERN, "genre_item_id": ITEM_ID_PATTERN},
    )
    _check_known_recordings(genres, songs, genre_csv)
    names_by_id = _genre_names_by_item_id(wikidata_silver_dir)
    unknown_genres = sorted(set(genres.get_column("genre_item_id").drop_nulls()) - names_by_id.keys())
    if unknown_genres:
        raise ValueError(f"{genre_csv} references genre_item_id(s) not found in either genre tree: {unknown_genres}")

    video_csv = "manual_recording_youtube_video.csv"
    videos = read_curation_csv(
        curation_dir / video_csv,
        required=("musicbrainz_recording_id", "youtube_video_id", "reason"),
        key=("musicbrainz_recording_id",),
        patterns={
            "musicbrainz_recording_id": MBID_PATTERN,
            "youtube_video_id": _PATTERNS["youtube_video_id"],
            "unplayable_reason": f"^({'|'.join(_UNPLAYABLE_REASONS)})$",
        },
    )
    _check_known_recordings(videos, songs, video_csv)

    genre_overrides = genres.select(
        "musicbrainz_recording_id",
        has_genre_override=pl.lit(True),
        override_genre_name=pl.col("genre_item_id").replace_strict(names_by_id, default=None),
    )
    video_overrides = videos.select(
        "musicbrainz_recording_id",
        has_video_override=pl.lit(True),
        override_video_id="youtube_video_id",
        override_reason="unplayable_reason",
    )
    return (
        songs.join(genre_overrides, on="musicbrainz_recording_id", how="left")
        .join(video_overrides, on="musicbrainz_recording_id", how="left")
        .with_columns(
            genre_name=pl.when(pl.col("has_genre_override"))
            .then(pl.col("override_genre_name"))
            .otherwise(pl.col("genre_name")),
            youtube_video_id=pl.when(pl.col("has_video_override"))
            .then(pl.col("override_video_id"))
            .otherwise(pl.col("youtube_video_id")),
            youtube_unplayable_reason=pl.when(pl.col("has_video_override"))
            .then(pl.col("override_reason"))
            .otherwise(pl.col("youtube_unplayable_reason")),
        )
        .drop("has_genre_override", "override_genre_name", "has_video_override", "override_video_id", "override_reason")
    )


def export_songs(genre_match_path: Path, output_dir: Path, wikidata_silver_dir: Path, curation_dir: Path) -> Path:
    logger.info("exporting songs from %s", genre_match_path)
    songs = pl.read_parquet(genre_match_path).select(
        "musicbrainz_recording_id",
        "title",
        "artist",
        "artist_musicbrainz_id",
        "youtube_video_id",
        "youtube_unplayable_reason",
        "first_release_year",
        genre_name=pl.when(pl.col("match_method").is_in(["exact", "music_suffix", "manual_alias"])).then(
            pl.col("wikidata_genre_name")
        ),
    )
    songs = _apply_recording_overrides(songs, wikidata_silver_dir, curation_dir)
    out_of_range = songs.filter(~pl.col("first_release_year").is_between(*FIRST_RELEASE_YEAR_RANGE)).height
    if out_of_range:
        logger.warning(
            "nulling %d out-of-range first_release_year value(s) outside %s", out_of_range, FIRST_RELEASE_YEAR_RANGE
        )
        songs = songs.with_columns(
            pl.when(pl.col("first_release_year").is_between(*FIRST_RELEASE_YEAR_RANGE)).then(
                pl.col("first_release_year")
            )
        )
    _check_songs(songs)
    for row in songs.group_by("youtube_unplayable_reason").len().sort("youtube_unplayable_reason").iter_rows():
        logger.info("youtube_unplayable_reason=%s: %d songs", row[0], row[1])

    parts_dir = output_dir / OUTPUT_DIRNAME
    part_count = write_ndjson_parts(songs, parts_dir, ROWS_PER_PART)
    write_delta(songs, "musicbrainz_recording_id", OUTPUT_DIRNAME, output_dir, ROWS_PER_PART)
    logger.info(
        "wrote %d songs to %s in %d part(s), %d without a video, %d without a genre",
        songs.height,
        parts_dir,
        part_count,
        songs["youtube_video_id"].null_count(),
        songs["genre_name"].null_count(),
    )
    return parts_dir
