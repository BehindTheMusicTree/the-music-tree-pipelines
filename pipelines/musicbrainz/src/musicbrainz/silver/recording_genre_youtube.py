import logging
from pathlib import Path

import polars as pl

logger = logging.getLogger(__name__)

YOUTUBE_DOMAINS = ("youtube.com", "youtu.be")


def recording_genre_youtube(silver_dir: Path, output_dir: Path) -> Path:
    recording_genre = pl.read_parquet(silver_dir / "2_recording_genre.parquet")
    recording_link = pl.read_parquet(silver_dir / "1_recording_link.parquet")

    recording_youtube_url = recording_link.filter(
        pl.any_horizontal(pl.col("url").str.contains(domain, literal=True) for domain in YOUTUBE_DOMAINS)
    )

    result = recording_genre.join(recording_youtube_url, on="recording_id", how="inner").select(
        pl.col("recording_id"),
        pl.col("genre_id"),
        pl.col("weight"),
        pl.col("url").alias("youtube_url"),
    )

    output_dir.mkdir(parents=True, exist_ok=True)
    output_path = output_dir / "3_recording_genre_youtube.parquet"
    result.write_parquet(output_path)
    logger.info("wrote %d rows to %s", result.height, output_path)
    return output_path
