"""Prints row/item counts and breakdowns for the wikidata Silver Parquet outputs.

Read-only: computes stats from the existing Silver Parquet files, no new data is fetched or
written.
"""

from pathlib import Path

import polars as pl
from common.env import load_pipeline_env, require_env, resolve_pipeline_path

import wikidata


def profile_genre_classification(silver_path: Path) -> None:
    df = pl.read_parquet(silver_path)
    genre = df.filter(pl.col("is_genre"))
    tagged = df.filter(~pl.col("is_genre"))

    print(f"rows: {df.height} ({df.select('item_id').n_unique()} distinct items)")
    print(f"is_genre=True: {genre.height} rows, {genre.select('item_id').n_unique()} distinct items")
    print(f"is_genre=False: {tagged.height} rows, {tagged.select('item_id').n_unique()} distinct items")
    print()
    print("by classification_reason (rows):")
    print(df.group_by("classification_reason").len().sort("len", descending=True))


def profile_regional_classification(silver_path: Path) -> None:
    df = pl.read_parquet(silver_path)
    items = df.select("item_id", "is_regional", "regional_reason").unique(subset="item_id")

    print()
    print(f"items: {items.height}")
    print("by is_regional (distinct items, null = not a genre and not a regional_overview seed):")
    print(items.group_by("is_regional").len().sort("len", descending=True))
    print("by regional_reason (distinct items):")
    print(items.group_by("regional_reason").len().sort("len", descending=True))


def profile_genre_parents(silver_path: Path) -> None:
    df = pl.read_parquet(silver_path)

    print()
    print(f"rows: {df.height} ({df.select('item_id').n_unique()} distinct items)")
    print("by parent_is_genre (rows, null = root item with no parent):")
    print(df.group_by("parent_is_genre").len().sort("len", descending=True))


def profile_hierarchy(genre_parents_path: Path, hierarchy_path: Path, regional_hierarchy_path: Path) -> None:
    parents_df = pl.read_parquet(genre_parents_path)
    hierarchy_df = pl.read_parquet(hierarchy_path)
    regional_hierarchy_df = pl.read_parquet(regional_hierarchy_path)

    genre_items = parents_df.filter(pl.col("is_genre")).select("item_id").unique()
    surviving_items = pl.concat([hierarchy_df.select("item_id"), regional_hierarchy_df.select("item_id")]).unique()
    vanished = genre_items.join(surviving_items, on="item_id", how="anti")

    print()
    print(f"canonical rows: {hierarchy_df.height} ({hierarchy_df.select('item_id').n_unique()} distinct items)")
    print(
        f"regional rows: {regional_hierarchy_df.height} ({regional_hierarchy_df.select('item_id').n_unique()} distinct items)"
    )
    print(f"genre items with zero surviving rows in either output: {vanished.height}")


if __name__ == "__main__":
    load_pipeline_env(wikidata.__file__)
    silver_dir = resolve_pipeline_path(wikidata.__file__, require_env("SILVER_OUTPUT_DIR"))
    profile_genre_classification(silver_dir / "1_regional_overview_classification.parquet")
    profile_regional_classification(silver_dir / "2_regional_classification.parquet")
    profile_genre_parents(silver_dir / "3_genre_parents.parquet")
    profile_hierarchy(
        silver_dir / "3_genre_parents.parquet",
        silver_dir / "4_hierarchy.parquet",
        silver_dir / "4_regional_hierarchy.parquet",
    )
