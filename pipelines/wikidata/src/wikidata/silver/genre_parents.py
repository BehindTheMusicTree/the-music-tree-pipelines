import logging
from pathlib import Path

import polars as pl

logger = logging.getLogger(__name__)

WIKIDATA_ITEM_URL_PREFIX = "https://www.wikidata.org/wiki/"

# Committed alongside the code (not a gitignored bronze/silver output) because it's hand-curated,
# not fetched from Wikidata: canonical (non-regional) genres with no P279/P361 parent that a data
# expert has identified as a subgenre of an existing canonical genre elsewhere in the tree (e.g. a
# cross-national fusion genre with no single national/ethnic home, so manual_regional_overrides.csv
# doesn't apply) get added here, with a `reason` for each entry. Unlike manual_regional_overrides.csv,
# this does not affect is_regional/regional_reason — it only supplies a missing canonical parent
# edge. `parent_item_id` must reference another genre item already in the tree (not a
# `is_regional_overview` item — that's what manual_regional_overrides.csv is for).
# See SCHEMA.md#4_genre_parents.
MANUAL_CANONICAL_PARENTS_PATH = Path(__file__).parent / "manual_canonical_parents.csv"


def _apply_canonical_parent_overrides(df: pl.DataFrame, manual_parents: pl.DataFrame) -> pl.DataFrame:
    if "parent_item_id" not in manual_parents.columns:
        raise ValueError("manual_canonical_parents.csv is missing the required 'parent_item_id' column")
    manual_parents = manual_parents.with_columns(pl.col("parent_item_id").cast(pl.Utf8))
    missing = manual_parents.filter(
        pl.col("parent_item_id").is_null() | (pl.col("parent_item_id").str.strip_chars() == "")
    )
    if not missing.is_empty():
        missing_ids = missing.select("item_id").to_series().to_list()
        raise ValueError(f"manual_canonical_parents.csv rows missing required 'parent_item_id': {missing_ids}")
    overrides = manual_parents.with_columns(parent_item_id=pl.col("parent_item_id").str.strip_chars())

    known_item_ids = set(df.select("item_id").unique().to_series())
    unknown_item_ids = [
        item_id for item_id in overrides.select("item_id").unique().to_series() if item_id not in known_item_ids
    ]
    if unknown_item_ids:
        raise ValueError(
            f"manual_canonical_parents.csv rows reference item_id(s) not found in the genre tree: {unknown_item_ids}"
        )
    unknown_parent_item_ids = [
        parent_item_id
        for parent_item_id in overrides.select("parent_item_id").unique().to_series()
        if parent_item_id not in known_item_ids
    ]
    if unknown_parent_item_ids:
        raise ValueError(
            "manual_canonical_parents.csv rows reference parent_item_id(s) not found in the genre tree: "
            f"{unknown_parent_item_ids}"
        )
    regional_overview_ids = set(df.filter(pl.col("is_regional_overview")).select("item_id").unique().to_series())
    overview_parent_item_ids = [
        parent_item_id
        for parent_item_id in overrides.select("parent_item_id").unique().to_series()
        if parent_item_id in regional_overview_ids
    ]
    if overview_parent_item_ids:
        raise ValueError(
            "manual_canonical_parents.csv rows reference parent_item_id(s) flagged is_regional_overview in the "
            f"genre tree (use manual_regional_overrides.csv for those): {overview_parent_item_ids}"
        )

    parent_labels = (
        df.select("item_id", "item_label")
        .unique(subset="item_id")
        .rename({"item_id": "parent_item_id", "item_label": "parent_item_label"})
    )
    item_columns = [c for c in df.columns if c not in ("parent_id", "parent_label", "parent_url", "relation_type")]
    synthetic_edges = (
        overrides.select("item_id", "parent_item_id")
        .join(parent_labels, on="parent_item_id", how="left")
        .join(df.select(item_columns).unique(subset="item_id"), on="item_id", how="left")
        .with_columns(
            parent_id=pl.col("parent_item_id"),
            parent_label=pl.col("parent_item_label"),
            parent_url=pl.lit(WIKIDATA_ITEM_URL_PREFIX) + pl.col("parent_item_id"),
            relation_type=pl.lit("manual_canonical_parent"),
        )
    )
    if "has_parent_label" in df.columns:
        synthetic_edges = synthetic_edges.with_columns(
            has_parent_label=pl.col("parent_item_label").is_not_null()
            & (pl.col("parent_item_label") != pl.col("parent_item_id"))
        )
    synthetic_edges = synthetic_edges.select(df.columns)

    overridden_ids = set(overrides.select("item_id").unique().to_series())
    df = df.filter(~(pl.col("item_id").is_in(list(overridden_ids)) & pl.col("parent_id").is_null()))
    return pl.concat([df, synthetic_edges])


def flag_genre_parents(
    regional_classification_path: Path, manual_canonical_parents_path: Path, output_dir: Path
) -> Path:
    logger.info("flagging genre-only parents in %s", regional_classification_path)
    df = pl.read_parquet(regional_classification_path)
    manual_canonical_parents = pl.read_csv(manual_canonical_parents_path)
    df = _apply_canonical_parent_overrides(df, manual_canonical_parents)

    genre_ids = df.filter(~pl.col("is_regional_overview")).select("item_id").unique().to_series().to_list()
    df = df.with_columns(
        parent_is_genre=pl.when(pl.col("parent_id").is_null())
        .then(None)
        .otherwise(pl.col("parent_id").is_in(genre_ids))
    )

    output_dir.mkdir(parents=True, exist_ok=True)
    output_path = output_dir / "4_genre_parents.parquet"
    df.write_parquet(output_path)
    logger.info(
        "wrote %d rows to %s (%d non-genre parent edges flagged)",
        df.height,
        output_path,
        df.filter(~pl.col("parent_is_genre")).height,
    )

    return output_path
