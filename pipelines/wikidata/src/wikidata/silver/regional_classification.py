import logging
from pathlib import Path

import polars as pl

logger = logging.getLogger(__name__)


def classify_regional_genres(genre_classification_path: Path, output_dir: Path) -> Path:
    logger.info("classifying regional genres in %s", genre_classification_path)
    df = pl.read_parquet(genre_classification_path)

    # Seeds: the "music of <place>" items themselves. They're tagged non-genre
    # (classification_reason == "regional_overview") but are not excluded from the regional
    # graph — they're regional genre nodes in their own right (see hierarchy.py), and the seed set
    # every other regional flag propagates from. A genre item is "direct" regional if any one of
    # its parent edges points at a seed — not all of them, since e.g. "European folk music" has one
    # parent into "music of Europe" (a seed) and another into "traditional folk music" (clean), and
    # is still considered regional. Regional status then cascades to children layer by layer: any
    # genre item with a parent edge into an already-regional item is "inherited" regional, repeated
    # until no new items are found.
    seed_ids = set(
        df.filter(pl.col("classification_reason") == "regional_overview").select("item_id").unique().to_series()
    )
    direct_ids = set(
        df.filter(pl.col("is_genre") & pl.col("parent_id").is_in(list(seed_ids))).select("item_id").unique().to_series()
    )

    regional_ids = set(direct_ids)
    frontier = set(direct_ids)
    while frontier:
        candidates = df.filter(
            pl.col("is_genre")
            & pl.col("parent_id").is_in(list(frontier))
            & ~pl.col("item_id").is_in(list(regional_ids))
        )
        frontier = set(candidates.select("item_id").unique().to_series())
        regional_ids |= frontier

    df = df.with_columns(
        is_regional=pl.when(pl.col("item_id").is_in(list(seed_ids)))
        .then(pl.lit(True))
        .when(~pl.col("is_genre"))
        .then(None)
        .otherwise(pl.col("item_id").is_in(list(regional_ids))),
        regional_reason=pl.when(pl.col("item_id").is_in(list(seed_ids)))
        .then(pl.lit("seed"))
        .when(pl.col("item_id").is_in(list(direct_ids)))
        .then(pl.lit("direct"))
        .when(pl.col("item_id").is_in(list(regional_ids)))
        .then(pl.lit("inherited"))
        .otherwise(None),
    )

    output_dir.mkdir(parents=True, exist_ok=True)
    output_path = output_dir / "2_regional_classification.parquet"
    df.write_parquet(output_path)
    logger.info(
        "wrote %d rows to %s (%d regional genre items: %d seed, %d direct, %d inherited)",
        df.height,
        output_path,
        len(regional_ids) + len(seed_ids),
        len(seed_ids),
        len(direct_ids),
        len(regional_ids) - len(direct_ids),
    )

    return output_path
