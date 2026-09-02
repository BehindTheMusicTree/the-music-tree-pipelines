import logging
from pathlib import Path

import polars as pl

logger = logging.getLogger(__name__)

WIKIDATA_ITEM_URL_PREFIX = "https://www.wikidata.org/wiki/"

# Committed alongside the code (not a gitignored bronze/silver output) because it's hand-curated,
# not fetched from Wikidata: genres that slip through the automated seed/indigenous_to/
# country_of_origin classification below (e.g. roots with no P279/P361 parent and no P2341/P495
# value) get added here by a data expert reviewing 5_hierarchy's root list, with a `reason` for
# each entry. `overview_item_id` is the QID of the `regional_overview` item (e.g. "music of Japan")
# the override item nests under in 5_regional_hierarchy — required, since these override items
# typically have no P279/P361 parent and would otherwise surface as their own orphan root in the
# regional tree instead of sitting under their region. See SCHEMA.md#3_regional_classification.
MANUAL_OVERRIDES_PATH = Path(__file__).parent / "manual_regional_overrides.csv"


def _apply_overview_overrides(df: pl.DataFrame, manual_overrides: pl.DataFrame) -> pl.DataFrame:
    if "overview_item_id" not in manual_overrides.columns:
        raise ValueError("manual_regional_overrides.csv is missing the required 'overview_item_id' column")
    missing = manual_overrides.filter(
        pl.col("overview_item_id").is_null() | (pl.col("overview_item_id").str.strip_chars() == "")
    )
    if not missing.is_empty():
        missing_ids = missing.select("item_id").to_series().to_list()
        raise ValueError(f"manual_regional_overrides.csv rows missing required 'overview_item_id': {missing_ids}")
    overrides = manual_overrides.with_columns(overview_item_id=pl.col("overview_item_id").str.strip_chars())

    known_item_ids = set(df.select("item_id").unique().to_series())
    unknown_item_ids = [
        item_id for item_id in overrides.select("item_id").unique().to_series() if item_id not in known_item_ids
    ]
    if unknown_item_ids:
        raise ValueError(
            f"manual_regional_overrides.csv rows reference item_id(s) not found in the genre tree: {unknown_item_ids}"
        )
    unknown_overview_item_ids = [
        overview_item_id
        for overview_item_id in overrides.select("overview_item_id").unique().to_series()
        if overview_item_id not in known_item_ids
    ]
    if unknown_overview_item_ids:
        raise ValueError(
            "manual_regional_overrides.csv rows reference overview_item_id(s) not found in the genre tree: "
            f"{unknown_overview_item_ids}"
        )
    regional_overview_ids = set(df.filter(pl.col("is_regional_overview")).select("item_id").unique().to_series())
    non_regional_overview_ids = [
        overview_item_id
        for overview_item_id in overrides.select("overview_item_id").unique().to_series()
        if overview_item_id not in regional_overview_ids
    ]
    if non_regional_overview_ids:
        raise ValueError(
            "manual_regional_overrides.csv rows reference overview_item_id(s) not flagged is_regional_overview "
            f"in the genre tree: {non_regional_overview_ids}"
        )

    overview_labels = (
        df.select("item_id", "item_label")
        .unique(subset="item_id")
        .rename({"item_id": "overview_item_id", "item_label": "overview_item_label"})
    )
    item_columns = [c for c in df.columns if c not in ("parent_id", "parent_label", "parent_url", "relation_type")]
    synthetic_edges = (
        overrides.select("item_id", "overview_item_id")
        .join(overview_labels, on="overview_item_id", how="left")
        .join(df.select(item_columns).unique(subset="item_id"), on="item_id", how="left")
        .with_columns(
            parent_id=pl.col("overview_item_id"),
            parent_label=pl.col("overview_item_label"),
            parent_url=pl.lit(WIKIDATA_ITEM_URL_PREFIX) + pl.col("overview_item_id"),
            relation_type=pl.lit("manual_override_parent"),
        )
    )
    if "has_parent_label" in df.columns:
        synthetic_edges = synthetic_edges.with_columns(
            has_parent_label=pl.col("overview_item_label").is_not_null()
            & (pl.col("overview_item_label") != pl.col("overview_item_id"))
        )
    synthetic_edges = synthetic_edges.select(df.columns)

    overridden_ids = set(overrides.select("item_id").unique().to_series())
    df = df.filter(~(pl.col("item_id").is_in(list(overridden_ids)) & pl.col("parent_id").is_null()))
    return pl.concat([df, synthetic_edges])


def classify_regional_genres(
    regional_overview_classification_path: Path,
    indigenous_to_path: Path,
    country_of_origin_path: Path,
    manual_overrides_path: Path,
    output_dir: Path,
) -> Path:
    logger.info("classifying regional genres in %s", regional_overview_classification_path)
    df = pl.read_parquet(regional_overview_classification_path)
    indigenous_ids = set(pl.read_parquet(indigenous_to_path).select("item_id").unique().to_series())
    country_ids = set(pl.read_parquet(country_of_origin_path).select("item_id").unique().to_series())
    manual_overrides = pl.read_csv(manual_overrides_path)
    manual_override_ids = set(manual_overrides.select("item_id").unique().to_series())
    df = _apply_overview_overrides(df, manual_overrides)

    # Seeds: the "music of <place>" items themselves, plus every item Wikidata's P2341
    # ("indigenous to") or P495 ("country of origin") flags as belonging to a specific people or
    # country (e.g. "Han Chinese music" -> "Han Chinese people", "fado" -> "Portugal", see bronze
    # wikidata_genre_indigenous_to.parquet / wikidata_genre_country_of_origin.parquet), plus
    # anything a data expert has hand-flagged in manual_regional_overrides.csv for genres none of
    # the automated sources catch. All four sets are tagged non-genre or nationally/ethnically-
    # specific in their own right but are not excluded from the regional graph — they're regional
    # genre nodes themselves (see hierarchy.py), and together form the seed set every other
    # regional flag propagates from. A genre item is "direct" regional if any one of its parent
    # edges points at a seed — not all of them, since e.g. "European folk music" has one parent
    # into "music of Europe" (a seed) and another into "traditional folk music" (clean), and is
    # still considered regional. Regional status then cascades to children layer by layer: any
    # genre item with a parent edge into an already-regional item is "inherited" regional, repeated
    # until no new items are found.
    seed_ids = set(
        df.filter(pl.col("classification_reason") == "regional_overview").select("item_id").unique().to_series()
    )
    source_ids = seed_ids | indigenous_ids | country_ids | manual_override_ids
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

    regional_ids = set(direct_ids) | indigenous_ids | country_ids | manual_override_ids
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
        .when(pl.col("item_id").is_in(list(manual_override_ids)))
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
        .when(pl.col("item_id").is_in(list(manual_override_ids)))
        .then(pl.lit("manual_override"))
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
