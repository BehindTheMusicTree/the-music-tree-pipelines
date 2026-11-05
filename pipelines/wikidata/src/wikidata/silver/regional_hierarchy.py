import logging
from pathlib import Path

import polars as pl

from wikidata.silver.hierarchy_utils import OUTPUT_COLUMNS, promote_orphans_to_roots

logger = logging.getLogger(__name__)


def prune_regional_hierarchy(canonical_parents_path: Path, output_dir: Path) -> Path:
    logger.info("pruning regional hierarchy from %s", canonical_parents_path)
    df = pl.read_parquet(canonical_parents_path)

    parent_is_regional = df.select(
        pl.col("item_id").alias("parent_id"), pl.col("is_regional").alias("parent_is_regional")
    ).unique()

    # Regional: everything flagged `is_regional`, which includes the "music of <place>" seed items
    # themselves — they're regional genre nodes here, not dropped, even though `is_regional_overview`
    # is True for them.
    regional_items = df.filter(pl.col("is_regional")).join(parent_is_regional, on="parent_id", how="left")

    # `regional_items` includes the seed items themselves alongside actual regional genres, so most
    # items keep their real parent chain (e.g. morna -> "music of Cape Verde") instead of losing it.
    # An item whose parent edges all lead outside the regional set entirely (a genuine top-level seed
    # with no parent at all, e.g. a continent-level "music of X" with nothing above it) still becomes
    # a root of the regional graph rather than vanishing. parent_selection.py already collapsed each
    # regional item to one anchor parent upstream (its regional genre parent, else its first overview),
    # so no further multi-parent collapse is needed here.
    is_regional_edge = pl.col("parent_id").is_null() | pl.col("parent_is_regional")
    kept = regional_items.filter(is_regional_edge).select(OUTPUT_COLUMNS)
    regional = promote_orphans_to_roots(regional_items, kept)

    output_dir.mkdir(parents=True, exist_ok=True)
    regional_path = output_dir / "8_regional_hierarchy.parquet"
    regional.write_parquet(regional_path)
    logger.info(
        "wrote %d rows to %s (%d items)",
        regional.height,
        regional_path,
        regional_items.select("item_id").n_unique(),
    )

    return regional_path
