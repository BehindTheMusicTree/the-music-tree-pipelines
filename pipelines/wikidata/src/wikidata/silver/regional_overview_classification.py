import logging
from pathlib import Path

import polars as pl

logger = logging.getLogger(__name__)

WIKIDATA_ITEM_URL_PREFIX = "https://www.wikidata.org/wiki/"

# "music of <place>" items are Wikidata's national/regional music overview
# articles (e.g. "music of Kenya", "music of France"). Most are classified P31
# "instance of" music genre in Bronze, but this step flags them non-genre
# (is_regional_overview = True) rather than treating them as genre nodes — they
# represent the music of a country or region as a whole rather than a specific
# musical style. Some (e.g. "music of Wales") are never classified P31 music
# genre themselves and so never get their own Bronze row — those are promoted
# to a root row below before this rule runs, so they're covered too. They are
# NOT dropped: they stay in the dataset and later become regional-tree nodes
# via `is_regional` in step 3. There are ~300 such items among ~6,300 items.
#
# These items are also the seeds for "3_regional_classification", which propagates
# `is_regional` through parent relationships: genres whose parent is one of these
# items are classified as regional genres (e.g. morna, fado). The seed items
# themselves are also classified as regional genre nodes.
#
# Other non-genre entities are present in the Bronze data, such as musical forms
# (e.g. fugue) and ensemble/format labels (e.g. big band music), but are not
# handled by this classification step.
REGIONAL_OVERVIEW_PREFIX = "music of "


def _promote_orphan_overview_parents(df: pl.DataFrame) -> pl.DataFrame:
    """Some "music of <place>" items (e.g. "music of Wales") are never themselves classified P31
    instance-of music genre, so Bronze never fetches their own row — they only ever show up as a
    parent_label on their subgenres (e.g. "Welsh folk music"). That makes them invisible to the
    prefix classification below and illegal as a manual_regional_overrides.csv overview_item_id
    target. Promoting them to their own root row (parent_id=null, same as any other unparented item)
    is mechanical - the id/label pair is already in the data - so it's done for every such item here,
    rather than one at a time by hand."""
    known_item_ids = set(df.select("item_id").unique().to_series())
    orphan_parents = (
        df.filter(
            pl.col("parent_id").is_not_null()
            & pl.col("parent_label").str.starts_with(REGIONAL_OVERVIEW_PREFIX)
            & ~pl.col("parent_id").is_in(list(known_item_ids))
        )
        .select(item_id="parent_id", item_label="parent_label")
        .unique(subset="item_id")
    )
    if orphan_parents.is_empty():
        return df

    promoted = orphan_parents.with_columns(
        parent_id=pl.lit(None, dtype=pl.Utf8),
        parent_label=pl.lit(None, dtype=pl.Utf8),
        relation_type=pl.lit(None, dtype=pl.Utf8),
        item_url=pl.lit(WIKIDATA_ITEM_URL_PREFIX) + pl.col("item_id"),
        parent_url=pl.lit(None, dtype=pl.Utf8),
        has_item_label=pl.col("item_label").is_not_null() & (pl.col("item_label") != pl.col("item_id")),
        has_parent_label=pl.lit(None, dtype=pl.Boolean),
    ).select(df.columns)
    logger.info("promoted %d orphan 'music of' parent(s) to their own root row", promoted.height)

    return pl.concat([df, promoted])


def classify_regional_from_overviews(item_links_path: Path, output_dir: Path) -> Path:
    logger.info("classifying regional from overviews %s", item_links_path)
    df = pl.read_parquet(item_links_path)
    df = _promote_orphan_overview_parents(df)

    is_regional_overview = pl.col("item_label").str.starts_with(REGIONAL_OVERVIEW_PREFIX)
    df = df.with_columns(
        is_regional_overview=is_regional_overview,
        classification_reason=pl.when(is_regional_overview).then(pl.lit("regional_overview")).otherwise(None),
    )

    output_dir.mkdir(parents=True, exist_ok=True)
    output_path = output_dir / "2_regional_overview_classification.parquet"
    df.write_parquet(output_path)
    logger.info(
        "wrote %d rows to %s (%d tagged regional overview)",
        df.height,
        output_path,
        df.filter(pl.col("is_regional_overview")).height,
    )

    return output_path
