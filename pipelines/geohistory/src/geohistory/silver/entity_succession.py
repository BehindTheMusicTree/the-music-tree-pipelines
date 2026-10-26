import logging
from pathlib import Path

import polars as pl

logger = logging.getLogger(__name__)


def entity_succession(wikidata_bronze_dir: Path, silver_dir: Path) -> Path:
    claims = pl.read_parquet(wikidata_bronze_dir / "wikidata_cultural_entity_claim.parquet")
    crosswalk = pl.read_parquet(silver_dir / "1_entity_wikidata_item.parquet")

    succession = (
        claims.filter(pl.col("property").is_in(["P1365", "P1366"]))
        .select(
            pl.when(pl.col("property") == "P1365")
            .then(pl.col("value"))
            .otherwise(pl.col("item_id"))
            .alias("predecessor_item"),
            pl.when(pl.col("property") == "P1365")
            .then(pl.col("item_id"))
            .otherwise(pl.col("value"))
            .alias("successor_item"),
            pl.col("property").alias("source"),
        )
        .join(
            crosswalk.rename({"entity_id": "predecessor_id", "wikidata_id": "predecessor_item"}),
            on="predecessor_item",
        )
        .join(
            crosswalk.rename({"entity_id": "successor_id", "wikidata_id": "successor_item"}),
            on="successor_item",
        )
        .filter(pl.col("predecessor_id") != pl.col("successor_id"))
        .group_by("predecessor_id", "successor_id")
        .agg(pl.col("source").unique().sort().str.join(","))
        .sort("predecessor_id", "successor_id")
    )

    output_path = silver_dir / "3_entity_succession.parquet"
    succession.write_parquet(output_path)
    logger.info("wrote %d rows to %s", succession.height, output_path)
    return output_path
