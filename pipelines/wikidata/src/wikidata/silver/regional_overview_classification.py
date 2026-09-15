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
# These items are also the seeds for "4_regional_classification", which propagates
# `is_regional` through parent relationships: genres whose parent is one of these
# items are classified as regional genres (e.g. morna, fado). The seed items
# themselves are also classified as regional genre nodes.
#
# Other non-genre entities are present in the Bronze data, such as musical forms
# (e.g. fugue) and ensemble/format labels (e.g. big band music), but are not
# handled by this classification step.
REGIONAL_OVERVIEW_PREFIX = "music of "

# Committed alongside the code (not a gitignored bronze/silver output) because it's hand-curated,
# not fetched from Wikidata: a "music of <place>" overview item that never appears in Bronze at
# all (not even as a `parent_label` for orphan-promotion above to pick up, e.g. because no genre in
# the dataset happens to declare it as a P279/P361 parent) can be added here by a data expert who
# looked up its real Wikidata QID (or, as a last resort when no matching Wikidata item exists,
# a synthetic `LOCAL:`-prefixed id), so it becomes a legal `manual_regional_overrides.csv`
# `overview_item_id` target. See DESIGN.md#22-3_regional_overview_classification.
MANUAL_OVERVIEW_ADDITIONS_PATH = Path(__file__).parent / "manual_regional_overview_additions.csv"

# Committed alongside the code for the same reason as MANUAL_OVERVIEW_ADDITIONS_PATH, but for the
# opposite case: an item that already has its own row in Bronze/`1_item_links` (usually a real
# genre with a `P279`/`P361` parent edge) that a data expert has decided belongs in the regional
# graph as a non-genre overview node instead — e.g. "European folk music" (Q98528192), a
# continent-wide folk-music umbrella rather than a specific style. Unlike
# `manual_regional_overview_additions.csv`, `item_label` here does NOT need the "music of " prefix
# (that's the whole point — these are exactly the items the prefix rule can't catch), but `item_id`
# MUST already be present in the genre tree, with a matching `item_label`, and not already flagged
# `is_regional_overview` by the prefix rule. See DESIGN.md#22-3_regional_overview_classification.
MANUAL_OVERVIEW_RECLASSIFICATIONS_PATH = Path(__file__).parent / "manual_overview_reclassifications.csv"
MANUAL_OVERVIEW_RECLASSIFICATION_REASON = "manual_overview_reclassification"


def _add_manual_overview_items(df: pl.DataFrame, manual_additions: pl.DataFrame) -> pl.DataFrame:
    if manual_additions.is_empty():
        return df

    blank = manual_additions.filter(
        pl.col("item_id").is_null()
        | (pl.col("item_id").str.strip_chars() == "")
        | pl.col("item_label").is_null()
        | (pl.col("item_label").str.strip_chars() == "")
    )
    if not blank.is_empty():
        raise ValueError(
            "manual_regional_overview_additions.csv rows must not have a blank item_id or item_label: "
            f"{blank.select('item_id').to_series().to_list()}"
        )

    manual_additions = manual_additions.with_columns(
        item_id=pl.col("item_id").str.strip_chars(), item_label=pl.col("item_label").str.strip_chars()
    )

    non_prefixed = manual_additions.filter(~pl.col("item_label").str.starts_with(REGIONAL_OVERVIEW_PREFIX))
    if not non_prefixed.is_empty():
        raise ValueError(
            "manual_regional_overview_additions.csv rows must have an item_label starting with "
            f"'{REGIONAL_OVERVIEW_PREFIX}': {non_prefixed.select('item_id').to_series().to_list()}"
        )

    added_ids = manual_additions.select("item_id").to_series().to_list()
    if len(added_ids) != len(set(added_ids)):
        raise ValueError("manual_regional_overview_additions.csv contains duplicate item_id rows")

    known_item_ids = set(df.select("item_id").unique().to_series())
    already_present = [item_id for item_id in added_ids if item_id in known_item_ids]
    if already_present:
        raise ValueError(
            "manual_regional_overview_additions.csv rows already present in the genre tree "
            f"(remove them, they don't need manual addition): {already_present}"
        )

    added = manual_additions.with_columns(
        item_display_label=pl.col("item_label"),
        parent_id=pl.lit(None, dtype=pl.Utf8),
        parent_label=pl.lit(None, dtype=pl.Utf8),
        parent_display_label=pl.lit(None, dtype=pl.Utf8),
        relation_type=pl.lit(None, dtype=pl.Utf8),
        item_url=pl.lit(WIKIDATA_ITEM_URL_PREFIX) + pl.col("item_id"),
        parent_url=pl.lit(None, dtype=pl.Utf8),
        has_item_label=pl.lit(True),
        has_parent_label=pl.lit(None, dtype=pl.Boolean),
    ).select(df.columns)
    logger.info("added %d manual 'music of' overview item(s) missing from Bronze entirely", added.height)

    return pl.concat([df, added])


def _reclassify_existing_overview_items(df: pl.DataFrame, reclassifications: pl.DataFrame) -> set[str]:
    if reclassifications.is_empty():
        return set()

    blank = reclassifications.filter(
        pl.col("item_id").is_null()
        | (pl.col("item_id").str.strip_chars() == "")
        | pl.col("item_label").is_null()
        | (pl.col("item_label").str.strip_chars() == "")
    )
    if not blank.is_empty():
        raise ValueError(
            "manual_overview_reclassifications.csv rows must not have a blank item_id or item_label: "
            f"{blank.select('item_id').to_series().to_list()}"
        )

    reclassifications = reclassifications.with_columns(
        item_id=pl.col("item_id").str.strip_chars(), item_label=pl.col("item_label").str.strip_chars()
    )

    reclass_ids = reclassifications.select("item_id").to_series().to_list()
    if len(reclass_ids) != len(set(reclass_ids)):
        raise ValueError("manual_overview_reclassifications.csv contains duplicate item_id rows")

    known_labels = dict(df.select("item_id", "item_label").unique(subset="item_id").iter_rows())
    unknown_ids = [item_id for item_id in reclass_ids if item_id not in known_labels]
    if unknown_ids:
        raise ValueError(
            "manual_overview_reclassifications.csv rows reference item_id(s) not found in the genre tree "
            f"(this file is only for items already present in Bronze; use manual_regional_overview_additions.csv "
            f"for items missing entirely): {unknown_ids}"
        )

    mismatched = [
        (item_id, item_label, known_labels[item_id])
        for item_id, item_label in reclassifications.select("item_id", "item_label").iter_rows()
        if known_labels[item_id] != item_label
    ]
    if mismatched:
        raise ValueError(
            "manual_overview_reclassifications.csv rows have an item_label that doesn't match the genre tree's "
            f"item_label for that item_id: {mismatched}"
        )

    already_overview = [
        item_id for item_id in reclass_ids if known_labels[item_id].startswith(REGIONAL_OVERVIEW_PREFIX)
    ]
    if already_overview:
        raise ValueError(
            "manual_overview_reclassifications.csv rows already flagged is_regional_overview by the "
            f"'{REGIONAL_OVERVIEW_PREFIX}' prefix rule (remove them, they don't need reclassification): "
            f"{already_overview}"
        )

    return set(reclass_ids)


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
        .select(item_id="parent_id", item_label="parent_label", item_display_label="parent_display_label")
        .unique(subset="item_id")
    )
    if orphan_parents.is_empty():
        return df

    promoted = orphan_parents.with_columns(
        item_display_label=pl.coalesce("item_display_label", "item_label"),
        parent_id=pl.lit(None, dtype=pl.Utf8),
        parent_label=pl.lit(None, dtype=pl.Utf8),
        parent_display_label=pl.lit(None, dtype=pl.Utf8),
        relation_type=pl.lit(None, dtype=pl.Utf8),
        item_url=pl.lit(WIKIDATA_ITEM_URL_PREFIX) + pl.col("item_id"),
        parent_url=pl.lit(None, dtype=pl.Utf8),
        has_item_label=pl.col("item_label").is_not_null() & (pl.col("item_label") != pl.col("item_id")),
        has_parent_label=pl.lit(None, dtype=pl.Boolean),
    ).select(df.columns)
    logger.info("promoted %d orphan 'music of' parent(s) to their own root row", promoted.height)

    return pl.concat([df, promoted])


def classify_regional_from_overviews(
    non_genre_pruning_path: Path, manual_additions_path: Path, reclassifications_path: Path, output_dir: Path
) -> Path:
    logger.info("classifying regional from overviews %s", non_genre_pruning_path)
    df = pl.read_parquet(non_genre_pruning_path)
    df = _promote_orphan_overview_parents(df)
    manual_additions_schema = {"item_id": pl.Utf8, "item_label": pl.Utf8, "reason": pl.Utf8}
    df = _add_manual_overview_items(df, pl.read_csv(manual_additions_path, schema_overrides=manual_additions_schema))
    reclass_ids = _reclassify_existing_overview_items(
        df, pl.read_csv(reclassifications_path, schema_overrides=manual_additions_schema)
    )

    reclass_ids_list = sorted(reclass_ids)
    is_regional_overview = pl.col("item_label").str.starts_with(REGIONAL_OVERVIEW_PREFIX) | pl.col("item_id").is_in(
        reclass_ids_list
    )
    df = df.with_columns(
        is_regional_overview=is_regional_overview,
        classification_reason=pl.when(pl.col("item_label").str.starts_with(REGIONAL_OVERVIEW_PREFIX))
        .then(pl.lit("regional_overview"))
        .when(pl.col("item_id").is_in(reclass_ids_list))
        .then(pl.lit(MANUAL_OVERVIEW_RECLASSIFICATION_REASON))
        .otherwise(None),
    )

    output_dir.mkdir(parents=True, exist_ok=True)
    output_path = output_dir / "3_regional_overview_classification.parquet"
    df.write_parquet(output_path)
    logger.info(
        "wrote %d rows to %s (%d tagged regional overview)",
        df.height,
        output_path,
        df.filter(pl.col("is_regional_overview")).height,
    )

    return output_path
