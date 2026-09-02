import logging
from pathlib import Path

import polars as pl

logger = logging.getLogger(__name__)


def extract_canonical_roots(hierarchy_path: Path, output_dir: Path) -> Path:
    logger.info("extracting canonical roots from %s", hierarchy_path)
    df = pl.read_parquet(hierarchy_path)

    roots = df.filter(pl.col("parent_id").is_null()).select("item_id", "item_label", "item_url").sort("item_label")

    output_dir.mkdir(parents=True, exist_ok=True)
    output_path = output_dir / "6_canonical_roots.parquet"
    roots.write_parquet(output_path)
    logger.info("wrote %d rows to %s", roots.height, output_path)

    return output_path
