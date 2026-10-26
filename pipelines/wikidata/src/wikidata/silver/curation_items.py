import json
import logging
from pathlib import Path

import polars as pl

logger = logging.getLogger(__name__)


def write_curation_items(tree_path: Path, output_dir: Path) -> Path:
    """Write curation_items.json: every Bronze item (pruned ones included) with its label, which grow-the-music-tree-api
    uses to name curation item ids that are not in the canonical tree. Items whose label is only the QID fallback
    carry no name and are left out."""
    tree = pl.read_parquet(tree_path)
    items = (
        pl.concat(
            [
                tree.select(pl.col("item_id").alias("id"), pl.col("item_label").alias("label")),
                tree.select(pl.col("parent_id").alias("id"), pl.col("parent_label").alias("label")),
            ]
        )
        .drop_nulls()
        .filter(pl.col("label") != pl.col("id"))
        .unique(subset="id", keep="first", maintain_order=True)
        .sort("id")
    )

    output_dir.mkdir(parents=True, exist_ok=True)
    output_path = output_dir / "curation_items.json"
    output_path.write_text(json.dumps({"items": items.to_dicts()}, indent=2))
    logger.info("wrote %s (%d item(s))", output_path, items.height)
    return output_path
