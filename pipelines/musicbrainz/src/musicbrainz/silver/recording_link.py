import logging
from pathlib import Path

import polars as pl

logger = logging.getLogger(__name__)


def recording_link(bronze_dir: Path, output_dir: Path) -> Path:
    url = pl.read_parquet(bronze_dir / "url.parquet")
    l_recording_url = pl.read_parquet(bronze_dir / "l_recording_url.parquet")
    link = pl.read_parquet(bronze_dir / "link.parquet")
    link_type = pl.read_parquet(bronze_dir / "link_type.parquet")

    result = (
        l_recording_url.join(url, left_on="entity1", right_on="id", how="inner")
        .join(link, left_on="link", right_on="id", how="inner")
        .join(link_type, left_on="link_type", right_on="id", how="inner", suffix="_link_type")
        .select(
            pl.col("entity0").alias("recording_id"),
            pl.col("url").alias("url"),
            pl.col("name").alias("link_type"),
        )
        .unique()
    )

    output_dir.mkdir(parents=True, exist_ok=True)
    output_path = output_dir / "1_recording_link.parquet"
    result.write_parquet(output_path)
    logger.info("wrote %d rows to %s", result.height, output_path)
    return output_path
