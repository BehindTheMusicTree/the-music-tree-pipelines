import logging
from pathlib import Path

import polars as pl

logger = logging.getLogger(__name__)

WIKIDATA_ITEM_URL_PREFIX = "https://www.wikidata.org/wiki/"


def add_item_links(bronze_path: Path, output_dir: Path) -> Path:
    logger.info("adding item links to %s", bronze_path)
    df = pl.read_parquet(bronze_path)

    df = df.with_columns(
        item_url=pl.lit(WIKIDATA_ITEM_URL_PREFIX) + pl.col("item_id"),
        parent_url=pl.when(pl.col("parent_id").is_not_null())
        .then(pl.lit(WIKIDATA_ITEM_URL_PREFIX) + pl.col("parent_id"))
        .otherwise(None),
    )

    output_dir.mkdir(parents=True, exist_ok=True)
    output_path = output_dir / "1_item_links.parquet"
    df.write_parquet(output_path)
    logger.info("wrote %d rows to %s", df.height, output_path)

    return output_path
