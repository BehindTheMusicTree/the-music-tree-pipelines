import logging
from pathlib import Path

import polars as pl

logger = logging.getLogger(__name__)

OUTPUT_COLUMNS = ["item_id", "item_label", "parent_id", "parent_label", "relation_type"]


def _collapse_to_lowest_qid(edges: pl.DataFrame) -> pl.DataFrame:
    # Wikidata's P279/P361 graph isn't a strict tree: ~43% of genre items have more than one
    # surviving genre parent, and only 1 item in the whole extension has a "preferred rank" P279
    # statement to disambiguate with (checked live). No reliable signal exists, so — provisionally,
    # pending a real product/curation decision — keep only the lowest-QID parent per item. This is
    # a tâtonnement placeholder, not a considered rule; see SCHEMA.md.
    return (
        edges.with_columns(parent_numeric_id=pl.col("parent_id").str.slice(1).cast(pl.Int64, strict=False))
        .sort(["item_id", "parent_numeric_id"])
        .unique(subset="item_id", keep="first")
        .select(OUTPUT_COLUMNS)
    )


def _prune_canonical(items: pl.DataFrame) -> pl.DataFrame:
    is_genre_edge = pl.col("parent_id").is_null() | pl.col("parent_is_genre")
    return _collapse_to_lowest_qid(items.filter(is_genre_edge))


def _prune_regional(items: pl.DataFrame) -> pl.DataFrame:
    # Unlike the canonical graph, an item whose parent edges all lead outside the regional set
    # (typically its `regional_overview` seed, e.g. "music of Cape Verde", which isn't a genre
    # itself) doesn't vanish — it becomes a root of the regional graph instead, so items like morna
    # still show up somewhere rather than disappearing silently, same as opera-style items do today
    # in the canonical graph.
    is_regional_edge = pl.col("parent_id").is_null() | pl.col("parent_is_regional")
    collapsed = _collapse_to_lowest_qid(items.filter(is_regional_edge))

    orphans = (
        items.select("item_id", "item_label")
        .unique(subset="item_id")
        .join(collapsed.select("item_id"), on="item_id", how="anti")
        .with_columns(
            parent_id=pl.lit(None, dtype=pl.Utf8),
            parent_label=pl.lit(None, dtype=pl.Utf8),
            relation_type=pl.lit(None, dtype=pl.Utf8),
        )
        .select(OUTPUT_COLUMNS)
    )
    return pl.concat([collapsed, orphans])


def prune_genre_hierarchy(regional_classification_path: Path, output_dir: Path) -> tuple[Path, Path]:
    logger.info("pruning genre hierarchy from %s", regional_classification_path)
    df = pl.read_parquet(regional_classification_path)

    parent_is_regional = df.select(
        pl.col("item_id").alias("parent_id"), pl.col("is_regional").alias("parent_is_regional")
    ).unique()
    genre_items = df.filter(pl.col("is_genre")).join(parent_is_regional, on="parent_id", how="left")

    canonical = _prune_canonical(genre_items.filter(~pl.col("is_regional")))
    regional = _prune_regional(genre_items.filter(pl.col("is_regional")))

    output_dir.mkdir(parents=True, exist_ok=True)
    canonical_path = output_dir / "4_hierarchy.parquet"
    regional_path = output_dir / "4_regional_hierarchy.parquet"
    canonical.write_parquet(canonical_path)
    regional.write_parquet(regional_path)
    logger.info(
        "wrote %d rows to %s (canonical) and %d rows to %s (regional), from %d genre items (%d regional)",
        canonical.height,
        canonical_path,
        regional.height,
        regional_path,
        genre_items.select("item_id").n_unique(),
        genre_items.filter(pl.col("is_regional")).select("item_id").n_unique(),
    )

    return canonical_path, regional_path
