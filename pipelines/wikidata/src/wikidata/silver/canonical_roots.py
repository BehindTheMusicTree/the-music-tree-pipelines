import logging
from pathlib import Path

import polars as pl

logger = logging.getLogger(__name__)


def extract_canonical_roots(hierarchy_path: Path, output_dir: Path) -> Path:
    logger.info("extracting canonical roots from %s", hierarchy_path)
    df = pl.read_parquet(hierarchy_path)

    # A `parent_id` can point at a label that never has its own row in this file (e.g. "popular
    # music" only ever appears as a parent value, never as an item that itself resolved down to a
    # parent) — such a parent is a dead end, not a real ancestor, so the item pointing at it is a
    # root just as much as one with a null parent_id.
    known_item_ids = df.select("item_id").unique().to_series().to_list()
    is_root = pl.col("parent_id").is_null() | ~pl.col("parent_id").is_in(known_item_ids)
    roots = df.filter(is_root).select("item_id", "item_label", "item_url").sort("item_label")

    output_dir.mkdir(parents=True, exist_ok=True)
    output_path = output_dir / "6_canonical_roots.parquet"
    roots.write_parquet(output_path)
    logger.info("wrote %d rows to %s", roots.height, output_path)

    return output_path
