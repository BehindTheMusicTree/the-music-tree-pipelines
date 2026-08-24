import logging
from pathlib import Path

import polars as pl

logger = logging.getLogger(__name__)

# "music of <place>" items are Wikidata's national/regional music overview
# articles (e.g. "music of Kenya", "music of France"). Wikidata classifies them
# as P31 "instance of" music genre, but this step flags them non-genre
# (is_regional_overview = True) rather than treating them as genre nodes — they
# represent the music of a country or region as a whole rather than a specific
# musical style. They are NOT dropped: they stay in the dataset and later
# become regional-tree nodes via `is_regional` in step 2. There are ~300 such
# items among ~6,300 items.
#
# These items are also the seeds for "2_regional_classification", which propagates
# `is_regional` through parent relationships: genres whose parent is one of these
# items are classified as regional genres (e.g. morna, fado). The seed items
# themselves are also classified as regional genre nodes.
#
# Other non-genre entities are present in the Bronze data, such as musical forms
# (e.g. fugue) and ensemble/format labels (e.g. big band music), but are not
# handled by this first classification step.
REGIONAL_OVERVIEW_PREFIX = "music of "


def classify_regional_from_overviews(bronze_path: Path, output_dir: Path) -> Path:
    logger.info("classifying regional from overviews %s", bronze_path)
    df = pl.read_parquet(bronze_path)

    is_regional_overview = pl.col("item_label").str.starts_with(REGIONAL_OVERVIEW_PREFIX)
    df = df.with_columns(
        is_regional_overview=is_regional_overview,
        classification_reason=pl.when(is_regional_overview).then(pl.lit("regional_overview")).otherwise(None),
    )

    output_dir.mkdir(parents=True, exist_ok=True)
    output_path = output_dir / "1_regional_overview_classification.parquet"
    df.write_parquet(output_path)
    logger.info(
        "wrote %d rows to %s (%d tagged regional overview)",
        df.height,
        output_path,
        df.filter(pl.col("is_regional_overview")).height,
    )

    return output_path
