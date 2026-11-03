import logging
from pathlib import Path

import polars as pl
from common.musicbrainz_artists import SPECIAL_PURPOSE_ARTIST_IDS

from musicbrainz.silver.recording_genre import genre_tags

logger = logging.getLogger(__name__)


def artist_genre(bronze_dir: Path, output_dir: Path) -> Path:
    """One row per (artist, genre name) from the artist's own genre tags, special-purpose artists excluded. Kept per
    artist, not fanned out to songs: Gold picks each artist's genre once."""
    artist = pl.scan_parquet(bronze_dir / "artist.parquet").select(entity_id="id", artist_musicbrainz_id="gid")
    genre = pl.scan_parquet(bronze_dir / "genre.parquet").select(genre_id="id", genre_name="name")
    result = (
        genre_tags(pl.scan_parquet(bronze_dir / "artist_tag.parquet"), "artist", bronze_dir)
        .join(artist, on="entity_id")
        .filter(~pl.col("artist_musicbrainz_id").is_in(SPECIAL_PURPOSE_ARTIST_IDS))
        .join(genre, on="genre_id")
        .select("artist_musicbrainz_id", "genre_name", "weight")
    )

    output_dir.mkdir(parents=True, exist_ok=True)
    output_path = output_dir / "13_artist_genre.parquet"
    result.sink_parquet(output_path)
    logger.info("wrote %d rows to %s", pl.scan_parquet(output_path).select(pl.len()).collect().item(), output_path)
    return output_path
