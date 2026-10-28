import logging
from pathlib import Path

import polars as pl
from common.step_timer import step
from common.quality_checks import (
    ARTIST_NAME_LEN_MAX,
    FIRST_RELEASE_YEAR_RANGE,
    SONG_TITLE_LEN_MAX,
    check_max_length,
    check_non_empty,
    check_null_rate,
    check_unique_key,
)

from gold.curation_csv import ITEM_ID_PATTERN, MBID_PATTERN, read_curation_csv
from gold.delta_export import write_delta
from gold.genre_precedence import PRECEDENCE_CSV, precedence_closure
from gold.match_method import RESOLVED_METHODS
from gold.ndjson_parts import write_ndjson_parts

logger = logging.getLogger(__name__)

ROWS_PER_PART = 250_000
OUTPUT_DIRNAME = "2_songs"

_PATTERNS = {
    "musicbrainz_recording_id": MBID_PATTERN,
    "artist_musicbrainz_id": MBID_PATTERN,
    "youtube_video_id": r"^[A-Za-z0-9_-]{11}$",
}
_SYNCED_COLUMNS = [
    "title",
    "artist",
    "artist_musicbrainz_id",
    "youtube_video_id",
    "youtube_unplayable_reason",
    "youtube_view_count",
    "genre_name",
    "first_release_year",
]
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
    for column in ("secondary_genre_item_ids", "musicbrainz_genres"):
        if songs.filter(pl.col(column).is_null() | pl.col(column).list.eval(pl.element().is_null()).list.any()).height:
            raise ValueError(f"{OUTPUT_DIRNAME}: null {column} or null element in it")
    self_secondary = songs.filter(pl.col("secondary_genre_item_ids").list.contains(pl.col("genre_item_id"))).height
    if self_secondary:
        raise ValueError(f"{OUTPUT_DIRNAME}: {self_secondary} song(s) listing their genre among their secondary genres")
    bad_view_count = songs.filter(
        (pl.col("youtube_view_count") < 0)
        | (pl.col("youtube_view_count").is_not_null() & pl.col("youtube_video_id").is_null())
    ).height
    if bad_view_count:
        raise ValueError(f"{OUTPUT_DIRNAME}: {bad_view_count} row(s) with a negative or video-less youtube_view_count")


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


def check_known_recordings(overrides: pl.DataFrame, songs: pl.DataFrame, csv_name: str) -> None:
    unknown = overrides.join(songs, on="musicbrainz_recording_id", how="anti").get_column("musicbrainz_recording_id")
    if not unknown.is_empty():
        raise ValueError(f"{csv_name} references unknown musicbrainz_recording_id(s): {unknown.to_list()[:3]}")


def _drop_beaten_secondaries(songs: pl.LazyFrame, closure: pl.DataFrame) -> pl.LazyFrame:
    """For songs whose genre was overridden, the secondary genres the new genre is or beats."""
    kept = (
        songs.filter(pl.col("override_item_id").is_not_null())
        .select("musicbrainz_recording_id", "override_item_id", item_id="secondary_genre_item_ids")
        .explode("item_id", empty_as_null=False)
        .filter(pl.col("item_id") != pl.col("override_item_id"))
        .join(closure.lazy(), left_on=["override_item_id", "item_id"], right_on=["winner", "loser"], how="anti")
        .group_by("musicbrainz_recording_id", maintain_order=True)
        .agg(kept_item_ids="item_id")
    )
    return (
        songs.join(kept, on="musicbrainz_recording_id", how="left", maintain_order="left")
        .with_columns(
            secondary_genre_item_ids=pl.when(pl.col("override_item_id").is_not_null())
            .then(pl.col("kept_item_ids").fill_null([]))
            .otherwise(pl.col("secondary_genre_item_ids"))
        )
        .drop("kept_item_ids")
    )


def _apply_recording_overrides(
    songs: pl.LazyFrame, names_by_id: dict[str, str], wikidata_silver_dir: Path, curation_dir: Path
) -> pl.LazyFrame:
    genre_csv = "manual_song_genre.csv"
    genres = read_curation_csv(
        curation_dir / genre_csv,
        required=("musicbrainz_recording_id", "reason"),
        key=("musicbrainz_recording_id",),
        patterns={"musicbrainz_recording_id": MBID_PATTERN, "genre_item_id": ITEM_ID_PATTERN},
    )
    known = (
        songs.select("musicbrainz_recording_id")
        .filter(pl.col("musicbrainz_recording_id").is_in(genres.get_column("musicbrainz_recording_id").implode()))
        .collect()
    )
    check_known_recordings(genres, known, genre_csv)
    hierarchy = pl.read_parquet(wikidata_silver_dir / "7_canonical_hierarchy.parquet")
    umbrella_ids = set(hierarchy.filter(pl.col("is_umbrella")).get_column("item_id"))
    umbrella_overrides = sorted(set(genres.get_column("genre_item_id").drop_nulls()) & umbrella_ids)
    if umbrella_overrides:
        raise ValueError(f"{genre_csv} assigns songs to umbrella genre(s), pick a subgenre: {umbrella_overrides}")
    unknown_genres = sorted(set(genres.get_column("genre_item_id").drop_nulls()) - names_by_id.keys())
    if unknown_genres:
        raise ValueError(f"{genre_csv} references genre_item_id(s) not found in either genre tree: {unknown_genres}")

    genre_overrides = genres.select(
        "musicbrainz_recording_id",
        has_genre_override=pl.lit(True),
        override_item_id="genre_item_id",
    )
    closure = precedence_closure(
        hierarchy, wikidata_silver_dir / "5_secondary_parents.parquet", curation_dir / PRECEDENCE_CSV
    )
    return (
        _drop_beaten_secondaries(
            songs.join(genre_overrides.lazy(), on="musicbrainz_recording_id", how="left", maintain_order="left"),
            closure,
        )
        .with_columns(
            genre_item_id=pl.when(pl.col("has_genre_override"))
            .then(pl.col("override_item_id"))
            .otherwise(pl.col("genre_item_id")),
        )
        .with_columns(genre_name=pl.col("genre_item_id").replace_strict(names_by_id, default=None))
        .drop("has_genre_override", "override_item_id")
    )


def export_songs(genre_match_path: Path, output_dir: Path, wikidata_silver_dir: Path, curation_dir: Path) -> Path:
    logger.info("exporting songs from %s", genre_match_path)
    lazy_songs = pl.scan_parquet(genre_match_path).select(
        "musicbrainz_recording_id",
        "title",
        "artist",
        "artist_musicbrainz_id",
        "youtube_video_id",
        "youtube_unplayable_reason",
        "youtube_view_count",
        "first_release_year",
        "secondary_genre_item_ids",
        "musicbrainz_genres",
        genre_item_id=pl.when(pl.col("match_method").is_in(RESOLVED_METHODS)).then(pl.col("wikidata_genre_item_id")),
    )
    names_by_id = _genre_names_by_item_id(wikidata_silver_dir)
    with step("songs.collect"):
        songs = _apply_recording_overrides(lazy_songs, names_by_id, wikidata_silver_dir, curation_dir).collect(
            engine="streaming"
        )
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
    with step("songs.checks"):
        _check_songs(songs)
    songs = songs.select("musicbrainz_recording_id", *_SYNCED_COLUMNS)
    for row in songs.group_by("youtube_unplayable_reason").len().sort("youtube_unplayable_reason").iter_rows():
        logger.info("youtube_unplayable_reason=%s: %d songs", row[0], row[1])

    parts_dir = output_dir / OUTPUT_DIRNAME
    with step("songs.parts"):
        part_count = write_ndjson_parts(songs, parts_dir, ROWS_PER_PART)
    with step("songs.delta"):
        write_delta(songs, "musicbrainz_recording_id", OUTPUT_DIRNAME, output_dir, ROWS_PER_PART, _SYNCED_COLUMNS)
    logger.info(
        "wrote %d songs to %s in %d part(s), %d without a video, %d without a genre",
        songs.height,
        parts_dir,
        part_count,
        songs["youtube_video_id"].null_count(),
        songs["genre_name"].null_count(),
    )
    return parts_dir
