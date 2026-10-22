import logging
from pathlib import Path

import polars as pl

from musicbrainz.silver.songs import _song_recordings

logger = logging.getLogger(__name__)


def song_genre(bronze_dir: Path, silver_dir: Path, output_dir: Path) -> Path:
    """One row per (song, genre name) with the weight summed across the song's recordings; picking a song's genre
    is Gold's job (it needs the Wikidata tree)."""
    song_recordings = _song_recordings(bronze_dir)
    song_id = song_recordings.select("recording_id", "song_id")
    song_musicbrainz_id = song_recordings.filter(pl.col("recording_id") == pl.col("song_id")).select(
        "song_id", "musicbrainz_recording_id"
    )
    genre = pl.scan_parquet(bronze_dir / "genre.parquet").select(genre_id="id", genre_name="name")
    result = (
        pl.scan_parquet(silver_dir / "2_recording_genre.parquet")
        .join(song_id, on="recording_id")
        .group_by("song_id", "genre_id")
        .agg(pl.col("weight").sum())
        .join(genre, on="genre_id")
        .join(song_musicbrainz_id, on="song_id")
        .select("musicbrainz_recording_id", "genre_name", "weight")
    )

    output_dir.mkdir(parents=True, exist_ok=True)
    output_path = output_dir / "10_song_genre.parquet"
    result.sink_parquet(output_path, engine="streaming")
    logger.info("wrote %d rows to %s", pl.scan_parquet(output_path).select(pl.len()).collect().item(), output_path)
    return output_path
