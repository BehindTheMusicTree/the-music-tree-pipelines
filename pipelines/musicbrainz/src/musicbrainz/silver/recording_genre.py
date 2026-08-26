import logging
from pathlib import Path

import polars as pl

logger = logging.getLogger(__name__)


def recording_genre(bronze_dir: Path, output_dir: Path) -> Path:
    recording_tag = pl.read_parquet(bronze_dir / "recording_tag.parquet")
    tag = pl.read_parquet(bronze_dir / "tag.parquet")
    genre = pl.read_parquet(bronze_dir / "genre.parquet")

    result = (
        recording_tag.filter(pl.col("count") > 0)
        .join(tag, left_on="tag", right_on="id", how="inner")
        .with_columns(pl.col("name").str.to_lowercase().alias("name_lower"))
        .join(
            genre.with_columns(pl.col("name").str.to_lowercase().alias("name_lower")),
            on="name_lower",
            how="inner",
            suffix="_genre",
        )
        .select(
            pl.col("recording").alias("recording_id"),
            pl.col("id").alias("genre_id"),
            pl.col("count").alias("weight"),
        )
    )

    output_dir.mkdir(parents=True, exist_ok=True)
    output_path = output_dir / "2_recording_genre.parquet"
    result.write_parquet(output_path)
    logger.info("wrote %d rows to %s", result.height, output_path)
    return output_path
