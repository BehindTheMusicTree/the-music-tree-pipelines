import logging
from pathlib import Path

import polars as pl

logger = logging.getLogger(__name__)

# "music of <place>" items are Wikidata's national/regional music overview articles (e.g. "music
# of Kenya", "music of France") — they're P31 "music genre" in the raw data but describe a
# country's music scene as a whole, not a genre. ~300 of ~6,300 items as of this writing. These are
# also the seed set the "3_regional_classification" step propagates "is_regional" down from — any
# genre item with a parent edge into one of these becomes a regional genre (e.g. "morna", "fado").
# Other non-genre categories exist in the Bronze data too (musical forms like "fugue",
# ensemble/format labels like "big band music") but aren't covered by this first classification pass.
REGIONAL_OVERVIEW_PREFIX = "music of "


def classify_genre_tree(bronze_path: Path, output_dir: Path) -> Path:
    logger.info("classifying %s", bronze_path)
    df = pl.read_parquet(bronze_path)

    is_regional_overview = pl.col("item_label").str.starts_with(REGIONAL_OVERVIEW_PREFIX)
    df = df.with_columns(
        is_genre=~is_regional_overview,
        exclusion_reason=pl.when(is_regional_overview).then(pl.lit("regional_overview")).otherwise(None),
    )

    output_dir.mkdir(parents=True, exist_ok=True)
    output_path = output_dir / "1_classification.parquet"
    df.write_parquet(output_path)
    logger.info("wrote %d rows to %s (%d excluded)", df.height, output_path, df.filter(~pl.col("is_genre")).height)

    return output_path
