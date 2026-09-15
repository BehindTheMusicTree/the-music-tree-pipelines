import logging
from pathlib import Path

import polars as pl

logger = logging.getLogger(__name__)

WIKIDATA_ITEM_URL_PREFIX = "https://www.wikidata.org/wiki/"

# Committed alongside the code (not a gitignored bronze/silver output) because it's hand-curated, not
# fetched from Wikidata: a data expert's pick of an alternate display name for a node in the exported
# genre tree (e.g. "pop music" -> "Mainstream Pop"), with a `reason` column explaining why. This only
# ever affects `item_display_label`/`parent_display_label` (what gold's genre_tree_builder emits as a
# node's "name") — `item_label`/`parent_label` (the real Wikidata label, used for genre-tag matching in
# gold's genre_match.py and for all internal classification logic) are never touched by it.
MANUAL_LABEL_OVERRIDES_PATH = Path(__file__).parent / "manual_label_overrides.csv"


def _load_display_labels(df: pl.DataFrame, manual_label_overrides: pl.DataFrame) -> pl.DataFrame:
    csv_name = "manual_label_overrides.csv"

    for column in ("item_id", "display_label"):
        blank = manual_label_overrides.filter(pl.col(column).is_null() | (pl.col(column).str.strip_chars() == ""))
        if not blank.is_empty():
            raise ValueError(f"{csv_name} has row(s) with a null/blank '{column}'")

    ids = manual_label_overrides.select("item_id").to_series().to_list()
    if len(ids) != len(set(ids)):
        raise ValueError(f"{csv_name} contains duplicate item_id rows")

    known_item_ids = set(df.select("item_id").unique().to_series())
    unknown_ids = [item_id for item_id in ids if item_id not in known_item_ids]
    if unknown_ids:
        raise ValueError(f"{csv_name} rows reference item_id(s) not found in the genre tree: {unknown_ids}")

    return manual_label_overrides.select("item_id", "display_label")


def add_item_links(bronze_path: Path, output_dir: Path, manual_label_overrides_path: Path) -> Path:
    logger.info("adding item links to %s", bronze_path)
    df = pl.read_parquet(bronze_path)

    manual_label_overrides = pl.read_csv(manual_label_overrides_path)
    display_labels = _load_display_labels(df, manual_label_overrides)

    df = df.join(display_labels, on="item_id", how="left").rename({"display_label": "item_display_label"})
    df = df.join(
        display_labels.rename({"item_id": "parent_id", "display_label": "parent_display_label"}),
        on="parent_id",
        how="left",
    )

    df = df.with_columns(
        item_url=pl.lit(WIKIDATA_ITEM_URL_PREFIX) + pl.col("item_id"),
        parent_url=pl.when(pl.col("parent_id").is_not_null())
        .then(pl.lit(WIKIDATA_ITEM_URL_PREFIX) + pl.col("parent_id"))
        .otherwise(None),
        has_item_label=pl.col("item_label").is_not_null() & (pl.col("item_label") != pl.col("item_id")),
        has_parent_label=pl.when(pl.col("parent_id").is_not_null())
        .then(pl.col("parent_label").is_not_null() & (pl.col("parent_label") != pl.col("parent_id")))
        .otherwise(None),
        item_display_label=pl.coalesce("item_display_label", "item_label"),
        parent_display_label=pl.when(pl.col("parent_id").is_not_null())
        .then(pl.coalesce("parent_display_label", "parent_label"))
        .otherwise(None),
    )

    output_dir.mkdir(parents=True, exist_ok=True)
    output_path = output_dir / "1_item_links.parquet"
    df.write_parquet(output_path)
    logger.info("wrote %d rows to %s", df.height, output_path)

    return output_path
