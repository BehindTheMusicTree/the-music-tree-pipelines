"""Prints row/item counts and breakdowns for the wikidata Silver Parquet outputs.

Read-only: computes stats from the existing Silver Parquet files, no new data is fetched or
written.
"""

from pathlib import Path

import polars as pl
from common.env import load_pipeline_env, require_env

import wikidata


def profile_classification(silver_path: Path) -> None:
    df = pl.read_parquet(silver_path)
    genre = df.filter(pl.col("is_genre"))
    excluded = df.filter(~pl.col("is_genre"))

    print(f"rows: {df.height} ({df.select('item_id').n_unique()} distinct items)")
    print(f"is_genre=True: {genre.height} rows, {genre.select('item_id').n_unique()} distinct items")
    print(f"is_genre=False: {excluded.height} rows, {excluded.select('item_id').n_unique()} distinct items")
    print()
    print("by exclusion_reason (rows):")
    print(df.group_by("exclusion_reason").len().sort("len", descending=True))


def profile_genre_parents(silver_path: Path) -> None:
    df = pl.read_parquet(silver_path)

    print()
    print(f"rows: {df.height} ({df.select('item_id').n_unique()} distinct items)")
    print("by parent_is_genre (rows, null = root item with no parent):")
    print(df.group_by("parent_is_genre").len().sort("len", descending=True))


if __name__ == "__main__":
    load_pipeline_env(wikidata.__file__)
    silver_dir = Path(require_env("SILVER_OUTPUT_DIR"))
    profile_classification(silver_dir / "1_classification.parquet")
    profile_genre_parents(silver_dir / "2_genre_parents.parquet")
