import logging
from pathlib import Path

import polars as pl

logger = logging.getLogger(__name__)

YOUTUBE_DOMAINS = ("youtube.com", "youtu.be")


def recording_youtube_url(bronze_dir: Path, output_dir: Path) -> Path:
    url = pl.read_parquet(bronze_dir / "url.parquet")
    l_recording_url = pl.read_parquet(bronze_dir / "l_recording_url.parquet")

    youtube_url = url.filter(
        pl.any_horizontal(pl.col("url").str.contains(domain, literal=True) for domain in YOUTUBE_DOMAINS)
    )

    result = (
        l_recording_url.join(youtube_url, left_on="entity1", right_on="id", how="inner")
        .select(
            pl.col("entity0").alias("recording_id"),
            pl.col("url").alias("youtube_url"),
        )
        .unique()
    )

    output_dir.mkdir(parents=True, exist_ok=True)
    output_path = output_dir / "1_recording_youtube_url.parquet"
    result.write_parquet(output_path)
    logger.info("wrote %d rows to %s", result.height, output_path)
    return output_path
