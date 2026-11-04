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
    is_leaf_overview`. A regional "music of <place>" overview with sub-genres is an umbrella (its songs are refined
    later, like a canonical umbrella's); one without any can't be (grow rejects a childless umbrella), so
    `is_leaf_overview` flags it as no song's genre. Other regional items are never umbrellas."""
    frames = []
    for path, tree in ((canonical_path, TREE_CANONICAL), (regional_path, TREE_REGIONAL)):
        hierarchy = pl.read_parquet(path)
        check_non_empty(hierarchy, path.name)
        check_null_rate(hierarchy, "item_label", path.name)
        is_umbrella = pl.col("is_umbrella") if tree == TREE_CANONICAL else pl.lit(False)
        parent_ids = hierarchy.get_column("parent_id").drop_nulls().unique()
        frames.append(
            hierarchy.select(
                "item_id",
                "item_label",
                "parent_id",
                is_umbrella=is_umbrella,
                genre_tree=pl.lit(tree),
                has_children=pl.col("item_id").is_in(parent_ids.implode()),
            )
        )
    overview_ids = (
        pl.read_parquet(regional_classification_path, columns=["item_id", "is_regional_overview"])
        .filter(pl.col("is_regional_overview"))
        .get_column("item_id")
        .unique()
    )
    is_overview = pl.col("item_id").is_in(overview_ids.implode())
    targets = (
        pl.concat(frames)
        .with_columns(
            is_umbrella=pl.col("is_umbrella") | (is_overview & pl.col("has_children")),
            is_leaf_overview=is_overview & ~pl.col("has_children"),
        )
        .drop("has_children")
    )
    check_unique_key(targets, "item_id", "canonical and regional hierarchies")
    return targets
