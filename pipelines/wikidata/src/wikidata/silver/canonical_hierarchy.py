import logging
from pathlib import Path

import polars as pl

from wikidata.silver.hierarchy_utils import OUTPUT_COLUMNS, promote_orphans_to_roots

logger = logging.getLogger(__name__)


def _flag_umbrellas(df: pl.DataFrame, canonical: pl.DataFrame, manual_umbrella_genres: pl.DataFrame) -> pl.DataFrame:
    ids = manual_umbrella_genres.get_column("item_id").to_list()
    regional_ids = set(df.filter(pl.col("is_regional")).get_column("item_id"))
    canonical_ids = set(canonical.get_column("item_id"))
    parent_ids = set(canonical.get_column("parent_id").drop_nulls())
    unknown = sorted(i for i in ids if i not in canonical_ids and i not in regional_ids)
    if unknown:
        raise ValueError(f"manual_umbrella_genres.csv rows reference item_id(s) not found in the genre tree: {unknown}")
    regional = sorted(i for i in ids if i in regional_ids)
    if regional:
        raise ValueError(f"manual_umbrella_genres.csv rows reference regional item_id(s): {regional}")
    leaves = sorted(i for i in ids if i not in parent_ids)
    if leaves:
        raise ValueError(
            f"manual_umbrella_genres.csv rows reference leaf item_id(s), which have no sub-genre: {leaves}"
        )
    return canonical.with_columns(is_umbrella=pl.col("item_id").is_in(ids))


def prune_canonical_hierarchy(
    canonical_parents_path: Path, manual_umbrella_genres_path: Path, output_dir: Path
) -> Path:
    logger.info("pruning canonical hierarchy from %s", canonical_parents_path)
    df = pl.read_parquet(canonical_parents_path)

    parent_is_regional = df.select(
        pl.col("item_id").alias("parent_id"), pl.col("is_regional").alias("parent_is_regional")
    ).unique()

    # Canonical: genres that are not regional
    canonical_items = df.filter(~pl.col("is_regional")).join(parent_is_regional, on="parent_id", how="left")

    # Keep only edges to a canonical parent, or items with no parent at all. An item whose sole
    # parent edge points to a non-genre item (e.g. "electronic music" -> "music") loses its only
    # row here and drops out of `kept` entirely; promote_orphans_to_roots below adds it back as a
    # root by diffing against the unfiltered canonical_items. main_parent_selection.py already
    # collapsed each item to a single parent edge upstream, so no further multi-parent collapse is
    # needed here.
    parent_is_valid_genre_or_absent = pl.col("parent_id").is_null() | pl.col("parent_is_canonical")
    kept = canonical_items.filter(parent_is_valid_genre_or_absent).select(OUTPUT_COLUMNS)
    canonical = promote_orphans_to_roots(canonical_items, kept)
    canonical = _flag_umbrellas(df, canonical, pl.read_csv(manual_umbrella_genres_path))

    output_dir.mkdir(parents=True, exist_ok=True)
    canonical_path = output_dir / "7_canonical_hierarchy.parquet"
    canonical.write_parquet(canonical_path)
    logger.info(
        "wrote %d rows to %s (%d items)",
        canonical.height,
        canonical_path,
        canonical_items.select("item_id").n_unique(),
    )

    return canonical_path
