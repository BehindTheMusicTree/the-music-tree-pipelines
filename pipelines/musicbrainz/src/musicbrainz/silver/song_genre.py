import logging
from pathlib import Path

import polars as pl

from musicbrainz.silver.songs import _song_recordings

logger = logging.getLogger(__name__)

LEVEL_RECORDING = "recording"
LEVEL_RELEASE_GROUP = "release_group"


def _official_status_id(bronze_dir: Path) -> int:
    ids = pl.read_parquet(bronze_dir / "release_status.parquet").filter(pl.col("name") == "Official")["id"]
    if ids.len() != 1:
        raise ValueError(f"expected one 'Official' release status, found {ids.len()}")
    return ids.item()


def _song_release_groups(bronze_dir: Path, song_id: pl.LazyFrame) -> pl.LazyFrame:
    """Distinct `(song_id, release_group_id)` of the release groups an official release of which has one of the
    song's recordings as a track — distinct so a song on several editions of one album counts that album once."""
    release = (
        pl.scan_parquet(bronze_dir / "release.parquet")
        .filter(pl.col("status") == _official_status_id(bronze_dir))
        .select(release="id", release_group_id="release_group")
    )
    return (
        pl.scan_parquet(bronze_dir / "track.parquet")
        .select(recording_id="recording", medium="medium")
        .join(song_id, on="recording_id")
        .join(pl.scan_parquet(bronze_dir / "medium.parquet").select(medium="id", release="release"), on="medium")
        .join(release, on="release")
        .select("song_id", "release_group_id")
        .unique()
    )


def song_genre(bronze_dir: Path, silver_dir: Path, output_dir: Path) -> Path:
    """One row per (song, level, genre name), the weight summed across the song's recordings (`recording` level) or
    its eligible release groups (`release_group` level); picking a song's genre is Gold's job (it needs the Wikidata
    tree)."""
    song_recordings = _song_recordings(bronze_dir).cache()
    song_id = song_recordings.select("recording_id", "song_id")
    song_musicbrainz_id = song_recordings.filter(pl.col("recording_id") == pl.col("song_id")).select(
        "song_id", "musicbrainz_recording_id"
    )
    genre = pl.scan_parquet(bronze_dir / "genre.parquet").select(genre_id="id", genre_name="name")
    recording_level = (
        pl.scan_parquet(silver_dir / "2_recording_genre.parquet")
        .join(song_id, on="recording_id")
        .select("song_id", "genre_id", "weight", level=pl.lit(LEVEL_RECORDING))
    )
    release_group_level = (
        _song_release_groups(bronze_dir, song_id)
        .join(pl.scan_parquet(silver_dir / "12_release_group_genre.parquet"), on="release_group_id")
        .select("song_id", "genre_id", "weight", level=pl.lit(LEVEL_RELEASE_GROUP))
    )
    result = (
        pl.concat([recording_level, release_group_level])
        .group_by("song_id", "level", "genre_id")
        .agg(pl.col("weight").sum())
        .join(genre, on="genre_id")
        .join(song_musicbrainz_id, on="song_id")
        .select("musicbrainz_recording_id", "level", "genre_name", "weight")
    )

    output_dir.mkdir(parents=True, exist_ok=True)
    output_path = output_dir / "10_song_genre.parquet"
    result.sink_parquet(output_path, engine="streaming")
    logger.info("wrote %d rows to %s", pl.scan_parquet(output_path).select(pl.len()).collect().item(), output_path)
    return output_path
