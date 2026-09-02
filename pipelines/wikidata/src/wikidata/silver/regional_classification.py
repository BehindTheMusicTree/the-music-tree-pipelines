import logging
from pathlib import Path

import polars as pl

logger = logging.getLogger(__name__)


def classify_regional_genres(
    regional_overview_classification_path: Path,
    indigenous_to_path: Path,
    country_of_origin_path: Path,
    output_dir: Path,
) -> Path:
    logger.info("classifying regional genres in %s", regional_overview_classification_path)
    df = pl.read_parquet(regional_overview_classification_path)
    indigenous_ids = set(pl.read_parquet(indigenous_to_path).select("item_id").unique().to_series())
    country_ids = set(pl.read_parquet(country_of_origin_path).select("item_id").unique().to_series())

    # Seeds: the "music of <place>" items themselves, plus every item Wikidata's P2341
    # ("indigenous to") or P495 ("country of origin") flags as belonging to a specific people or
    # country (e.g. "Han Chinese music" -> "Han Chinese people", "fado" -> "Portugal", see bronze
    # wikidata_genre_indigenous_to.parquet / wikidata_genre_country_of_origin.parquet). All three
    # sets are tagged non-genre or nationally/ethnically-specific in their own right but are not
    # excluded from the regional graph — they're regional genre nodes themselves (see
    # hierarchy.py), and together form the seed set every other regional flag propagates from. A
    # genre item is "direct" regional if any one of its parent edges points at a seed — not all of
    # them, since e.g. "European folk music" has one parent into "music of Europe" (a seed) and
    # another into "traditional folk music" (clean), and is still considered regional. Regional
    # status then cascades to children layer by layer: any genre item with a parent edge into an
    # already-regional item is "inherited" regional, repeated until no new items are found.
    seed_ids = set(
        df.filter(pl.col("classification_reason") == "regional_overview").select("item_id").unique().to_series()
    )
    source_ids = seed_ids | indigenous_ids | country_ids
    direct_ids = set(
        df.filter(
            ~pl.col("is_regional_overview")
            & pl.col("parent_id").is_in(list(source_ids))
            & ~pl.col("item_id").is_in(list(source_ids))
        )
        .select("item_id")
        .unique()
        .to_series()
    )

    regional_ids = set(direct_ids) | indigenous_ids | country_ids
    frontier = set(regional_ids)
    while frontier:
        candidates = df.filter(
            ~pl.col("is_regional_overview")
            & pl.col("parent_id").is_in(list(frontier))
            & ~pl.col("item_id").is_in(list(regional_ids | source_ids))
        )
        frontier = set(candidates.select("item_id").unique().to_series())
        regional_ids |= frontier

    df = df.with_columns(
        is_regional=pl.when(pl.col("item_id").is_in(list(seed_ids)))
        .then(pl.lit(True))
        .when(pl.col("is_regional_overview"))
        .then(None)
        .when(pl.col("item_id").is_in(list(indigenous_ids)))
        .then(pl.lit(True))
        .when(pl.col("item_id").is_in(list(country_ids)))
        .then(pl.lit(True))
        .when(pl.col("item_id").is_in(list(direct_ids)))
        .then(pl.lit(True))
        .otherwise(pl.col("item_id").is_in(list(regional_ids))),
        regional_reason=pl.when(pl.col("item_id").is_in(list(seed_ids)))
        .then(pl.lit("seed"))
        .when(pl.col("item_id").is_in(list(indigenous_ids)))
        .then(pl.lit("indigenous_to"))
        .when(pl.col("item_id").is_in(list(country_ids)))
        .then(pl.lit("country_of_origin"))
        .when(pl.col("item_id").is_in(list(direct_ids)))
        .then(pl.lit("direct"))
        .when(pl.col("item_id").is_in(list(regional_ids)))
        .then(pl.lit("inherited"))
        .otherwise(None),
    )

    output_dir.mkdir(parents=True, exist_ok=True)
    output_path = output_dir / "3_regional_classification.parquet"
    df.write_parquet(output_path)
    regional_counts = df.filter(pl.col("is_regional")).unique("item_id").group_by("regional_reason").len().to_dicts()
    logger.info(
        "wrote %d rows to %s (regional items by reason: %s)",
        df.height,
        output_path,
        regional_counts,
    )

    return output_path
