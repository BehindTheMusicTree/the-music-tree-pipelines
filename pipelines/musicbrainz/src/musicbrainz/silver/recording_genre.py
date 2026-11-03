import logging
from pathlib import Path

import polars as pl

logger = logging.getLogger(__name__)


def genre_tags(entity_tag: pl.LazyFrame, entity_column: str, bronze_dir: Path) -> pl.LazyFrame:
    """`(entity_id, genre_id, weight)` for a MusicBrainz `<entity>_tag` table: tags whose name is a genre's
    (case-insensitive), upvoted at least once."""
    tag = pl.scan_parquet(bronze_dir / "tag.parquet").select(tag="id", name_lower=pl.col("name").str.to_lowercase())
    genre = pl.scan_parquet(bronze_dir / "genre.parquet").select(
        genre_id="id", name_lower=pl.col("name").str.to_lowercase()
    )
    return (
        entity_tag.filter(pl.col("count") > 0)
        .join(tag, on="tag")
        .join(genre, on="name_lower")
        .select(entity_id=entity_column, genre_id="genre_id", weight="count")
    )


def recording_genre(bronze_dir: Path, output_dir: Path) -> Path:
    result = (
        genre_tags(pl.scan_parquet(bronze_dir / "recording_tag.parquet"), "recording", bronze_dir)
        .rename({"entity_id": "recording_id"})
        .collect()
    )

    output_dir.mkdir(parents=True, exist_ok=True)
    output_path = output_dir / "2_recording_genre.parquet"
    result.write_parquet(output_path)
    logger.info("wrote %d rows to %s", result.height, output_path)
    return output_path
