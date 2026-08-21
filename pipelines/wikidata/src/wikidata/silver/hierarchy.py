import logging
from pathlib import Path

import polars as pl

logger = logging.getLogger(__name__)

OUTPUT_COLUMNS = ["item_id", "item_label", "parent_id", "parent_label", "relation_type"]


def prune_genre_hierarchy(genre_parents_path: Path, output_dir: Path) -> Path:
    logger.info("pruning genre hierarchy from %s", genre_parents_path)
    df = pl.read_parquet(genre_parents_path)

    is_genre_edge = pl.col("is_genre") & (pl.col("parent_id").is_null() | pl.col("parent_is_genre"))
    genre_edges = df.filter(is_genre_edge)

    # Wikidata's P279/P361 graph isn't a strict tree: ~43% of genre items have more than one
    # surviving genre parent, and only 1 item in the whole extension has a "preferred rank" P279
    # statement to disambiguate with (checked live). No reliable signal exists, so — provisionally,
    # pending a real product/curation decision — keep only the lowest-QID parent per item. This is
    # a tâtonnement placeholder, not a considered rule; see SCHEMA.md.
    single_parent = (
        genre_edges.with_columns(parent_numeric_id=pl.col("parent_id").str.slice(1).cast(pl.Int64, strict=False))
        .sort(["item_id", "parent_numeric_id"])
        .unique(subset="item_id", keep="first")
        .select(OUTPUT_COLUMNS)
    )

    output_dir.mkdir(parents=True, exist_ok=True)
    output_path = output_dir / "3_hierarchy.parquet"
    single_parent.write_parquet(output_path)
    logger.info(
        "wrote %d rows to %s (%d non-genre edges dropped, %d multi-parent edges collapsed to lowest QID)",
        single_parent.height,
        output_path,
        df.height - genre_edges.height,
        genre_edges.height - single_parent.height,
    )

    return output_path
