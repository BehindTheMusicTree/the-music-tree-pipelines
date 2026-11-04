from pathlib import Path

import polars as pl

from common.quality_checks import check_non_empty, check_null_rate, check_unique_key

TREE_CANONICAL = "canonical"
TREE_REGIONAL = "regional"


def load_genre_trees(wikidata_silver_dir: Path) -> pl.DataFrame:
    return load_genre_targets(
        wikidata_silver_dir / "7_canonical_hierarchy.parquet",
        wikidata_silver_dir / "8_regional_hierarchy.parquet",
        wikidata_silver_dir / "4_regional_classification.parquet",
    )


def load_genre_targets(canonical_path: Path, regional_path: Path, regional_classification_path: Path) -> pl.DataFrame:
    """Both genre trees' items, one row each: `item_id, item_label, parent_id, is_umbrella, genre_tree,
    is_overview`. A regional item is never an umbrella; `is_overview` flags the regional "music of <place>" items,
    which stay in the tree (as ancestors) but are no song's genre."""
    frames = []
    for path, tree in ((canonical_path, TREE_CANONICAL), (regional_path, TREE_REGIONAL)):
        hierarchy = pl.read_parquet(path)
        check_non_empty(hierarchy, path.name)
        check_null_rate(hierarchy, "item_label", path.name)
        is_umbrella = pl.col("is_umbrella") if tree == TREE_CANONICAL else pl.lit(False)
        frames.append(
            hierarchy.select("item_id", "item_label", "parent_id", is_umbrella=is_umbrella, genre_tree=pl.lit(tree))
        )
    overview_ids = (
        pl.read_parquet(regional_classification_path, columns=["item_id", "is_regional_overview"])
        .filter(pl.col("is_regional_overview"))
        .get_column("item_id")
        .unique()
    )
    targets = pl.concat(frames).with_columns(is_overview=pl.col("item_id").is_in(overview_ids.implode()))
    check_unique_key(targets, "item_id", "canonical and regional hierarchies")
    return targets
