import gzip
import logging
import shutil
from pathlib import Path

import polars as pl
from common.quality_checks import check_non_empty, check_null_rate, check_unique_key

logger = logging.getLogger(__name__)

ROWS_PER_PART = 250_000
OUTPUT_DIRNAME = "2_songs"

_PATTERNS = {
    "musicbrainz_recording_id": r"^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$",
    "youtube_video_id": r"^[A-Za-z0-9_-]{11}$",
}
_UNPLAYABLE_REASONS = ["not_found", "not_embeddable", "private", "not_processed", "region_whitelisted"]


def _check_songs(songs: pl.DataFrame) -> None:
    check_non_empty(songs, OUTPUT_DIRNAME)
    for column in ("musicbrainz_recording_id", "title", "artist"):
        check_null_rate(songs, column, OUTPUT_DIRNAME)
    check_unique_key(songs, "musicbrainz_recording_id", OUTPUT_DIRNAME)
    for column, pattern in _PATTERNS.items():
        malformed = songs.filter(~pl.col(column).str.contains(pattern)).height
        if malformed:
            raise ValueError(f"{OUTPUT_DIRNAME}: {malformed} row(s) with a malformed {column}")
    bad_reason = songs.filter(~pl.col("youtube_unplayable_reason").is_in(_UNPLAYABLE_REASONS)).height
    if bad_reason:
        raise ValueError(f"{OUTPUT_DIRNAME}: {bad_reason} row(s) with an unknown youtube_unplayable_reason")


def export_songs(genre_match_path: Path, output_dir: Path) -> Path:
    logger.info("exporting songs from %s", genre_match_path)
    songs = pl.read_parquet(genre_match_path).select(
        "musicbrainz_recording_id",
        "title",
        "artist",
        "youtube_video_id",
        "youtube_unplayable_reason",
        genre_name=pl.when(pl.col("match_method").is_in(["exact", "music_suffix", "manual_alias"])).then(
            pl.col("wikidata_genre_name")
        ),
    )
    _check_songs(songs)
    for row in songs.group_by("youtube_unplayable_reason").len().sort("youtube_unplayable_reason").iter_rows():
        logger.info("youtube_unplayable_reason=%s: %d songs", row[0], row[1])

    # Wiped first so a smaller run never leaves a previous run's trailing parts behind.
    parts_dir = output_dir / OUTPUT_DIRNAME
    shutil.rmtree(parts_dir, ignore_errors=True)
    parts_dir.mkdir(parents=True)
    for index, part in enumerate(songs.iter_slices(ROWS_PER_PART)):
        with gzip.open(parts_dir / f"part-{index:05d}.ndjson.gz", "wb") as f:
            part.write_ndjson(f)
    logger.info(
        "wrote %d songs to %s in %d part(s), %d without a video, %d without a genre",
        songs.height,
        parts_dir,
        index + 1,
        songs["youtube_video_id"].null_count(),
        songs["genre_name"].null_count(),
    )
    return parts_dir
