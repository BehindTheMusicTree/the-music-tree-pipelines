import logging
from pathlib import Path

import polars as pl

from wikidata.silver.regional_overview_classification import MANUAL_OVERVIEW_RECLASSIFICATION_REASON

logger = logging.getLogger(__name__)

WIKIDATA_ITEM_URL_PREFIX = "https://www.wikidata.org/wiki/"

# Committed alongside the code (not a gitignored bronze/silver output) because it's hand-curated,
# not fetched from Wikidata: genres that slip through the automated seed/indigenous_to/
# country_of_origin classification below (e.g. roots with no P279/P361 parent and no P2341/P495
# value) get added here by a data expert reviewing 8_canonical_roots list, with a `reason` for
# each entry. `overview_item_id` is the `item_id` of the `regional_overview` item (e.g. "music of
# Japan" — normally a real QID, but may be a synthetic `LOCAL:` id, see regional_overview_classification.py)
# the override item nests under in 7_regional_hierarchy — required, since these override items
# typically have no P279/P361 parent and would otherwise surface as their own orphan root in the
# regional tree instead of sitting under their region. See DESIGN.md#23-4_regional_classification.
MANUAL_OVERRIDES_PATH = Path(__file__).parent / "manual_regional_overrides.csv"

# Committed alongside the code (not a gitignored bronze/silver output) because it's hand-curated,
# not fetched from Wikidata: a data expert's pick for an item's *main* parent (see
# main_parent_selection.py), overriding whatever the automated lowest-QID heuristic would otherwise
# pick among that item's candidate parent edges — or, for a genuinely parentless root, giving it a
# parent it never had. Applied here, before the regional cascade below runs, so the injected edge is
# just another edge to cascade through: is_regional flows to the child the same way it would through
# a real Bronze edge (direct if the parent is itself a seed, inherited if the parent is already
# regional via cascade, or the item stays canonical if the parent is too) — no separate validation of
# the parent's regional-ness is needed, unlike a scheme that ran this after the cascade and had to
# check the parent's flavor by hand. `parent_item_id` must reference another genre item already in
# the tree; it must not be `is_regional_overview` (that's what manual_regional_overrides.csv, with
# its overview_item_id column, is for). See DESIGN.md#24-5_canonical_parents.
#
# An optional `exclude_other_parents` column (blank/"false" by default) drops an item's *other*
# candidate parent edges entirely, rather than just supplying an extra one — needed when an item has
# a genuine conflicting edge into the regional seed set (e.g. "reggae" -> "music of Jamaica" via
# P279) that would otherwise keep it `is_regional=True` via the cascade below regardless of the
# manual override, since is_regional is computed from *any* of an item's parent edges, not just its
# eventual main parent.
MANUAL_MAIN_PARENT_PATH = Path(__file__).parent / "manual_main_parent.csv"

# Tags a synthetic edge injected from manual_main_parent.csv so main_parent_selection.py can prefer
# it over the item's other candidate parent edges when picking a main parent.
MANUAL_MAIN_PARENT_RELATION_TYPE = "manual_main_parent"

# Committed alongside the code for the same reason as the paths above: a canonical (non-regional,
# non-overview) grouping node that has no real Wikidata QID of its own — e.g. "Reggae/Dub", a
# grouping Gold-layer wants but that no single Wikidata item represents — so a data expert can still
# give real genre items (like "reggae" and "dub music") a real parent to nest under in
# manual_main_parent.csv. `item_id` must be a synthetic `LOCAL:`-prefixed id (never a fabricated
# QID-shaped id) and must not already be present in the genre tree. Applied before
# _apply_manual_main_parent so the new node is a legal parent_item_id target. See
# DESIGN.md#23-4_regional_classification.
MANUAL_CANONICAL_PARENT_ADDITIONS_PATH = Path(__file__).parent / "manual_canonical_parent_additions.csv"
MANUAL_CANONICAL_PARENT_ADDITION_REASON = "manual_canonical_parent_addition"
LOCAL_ID_PREFIX = "LOCAL:"

# Committed alongside the code for the same reason as the paths above: P2341 ("indigenous to") is
# treated as an automatic regional-seed signal below, but — like P495 ("country of origin", see the
# comment in classify_regional_genres for why that property is excluded entirely) — it's sometimes
# set on a broad canonical umbrella genre pointing at a whole continent rather than a specific
# people (e.g. "classical music" -> Europe), which would wrongly pull it into the regional tree as
# an orphan root instead of nesting it under its real canonical parent. Unlike P495 this can't be
# blanket-excluded (P2341 correctly identifies real regional genres like "Han Chinese music" that
# have no other regional signal at all), so this file lets a data expert exclude specific
# false-positive item_ids from the indigenous_to seed source only, one at a time. CSV columns:
# item_id,item_label,reason. `item_id` must already carry a P2341 value in Bronze
# wikidata_genre_indigenous_to.parquet — the pipeline raises otherwise, since an exclusion with
# nothing to exclude is almost certainly a stale/typo'd entry.
MANUAL_INDIGENOUS_TO_EXCLUSIONS_PATH = Path(__file__).parent / "manual_indigenous_to_exclusions.csv"


def _add_manual_canonical_parent_items(df: pl.DataFrame, manual_additions: pl.DataFrame) -> pl.DataFrame:
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
            "manual_canonical_parent_additions.csv rows must not have a blank item_id or item_label: "
            f"{blank.select('item_id').to_series().to_list()}"
        )

    manual_additions = manual_additions.with_columns(
        item_id=pl.col("item_id").str.strip_chars(), item_label=pl.col("item_label").str.strip_chars()
    )

    non_local = manual_additions.filter(~pl.col("item_id").str.starts_with(LOCAL_ID_PREFIX))
    if not non_local.is_empty():
        raise ValueError(
            f"manual_canonical_parent_additions.csv rows must have an item_id starting with '{LOCAL_ID_PREFIX}' "
            f"(never a fabricated QID-shaped id): {non_local.select('item_id').to_series().to_list()}"
        )

    added_ids = manual_additions.select("item_id").to_series().to_list()
    if len(added_ids) != len(set(added_ids)):
        raise ValueError("manual_canonical_parent_additions.csv contains duplicate item_id rows")

    known_item_ids = set(df.select("item_id").unique().to_series())
    already_present = [item_id for item_id in added_ids if item_id in known_item_ids]
    if already_present:
        raise ValueError(
            "manual_canonical_parent_additions.csv rows already present in the genre tree "
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
        is_regional_overview=pl.lit(False),
        classification_reason=pl.lit(MANUAL_CANONICAL_PARENT_ADDITION_REASON),
    ).select(df.columns)
    logger.info("added %d manual synthetic canonical parent node(s)", added.height)

    return pl.concat([df, added])


def _apply_manual_main_parent(df: pl.DataFrame, manual_parents: pl.DataFrame) -> pl.DataFrame:
    if "parent_item_id" not in manual_parents.columns:
        raise ValueError("manual_main_parent.csv is missing the required 'parent_item_id' column")
    if "item_id" not in manual_parents.columns:
        raise ValueError("manual_main_parent.csv is missing the required 'item_id' column")
    manual_parents = manual_parents.with_columns(
        pl.col("item_id").cast(pl.Utf8).str.strip_chars(), pl.col("parent_item_id").cast(pl.Utf8)
    )
    blank_item_id = manual_parents.filter(pl.col("item_id").is_null() | (pl.col("item_id") == ""))
    if not blank_item_id.is_empty():
        raise ValueError("manual_main_parent.csv has row(s) with a null/blank 'item_id'")
    missing = manual_parents.filter(
        pl.col("parent_item_id").is_null() | (pl.col("parent_item_id").str.strip_chars() == "")
    )
    if not missing.is_empty():
        missing_ids = missing.select("item_id").to_series().to_list()
        raise ValueError(f"manual_main_parent.csv rows missing required 'parent_item_id': {missing_ids}")
    overrides = manual_parents.with_columns(parent_item_id=pl.col("parent_item_id").str.strip_chars())

    override_item_ids = overrides.select("item_id").to_series().to_list()
    if len(override_item_ids) != len(set(override_item_ids)):
        raise ValueError("manual_main_parent.csv contains duplicate item_id rows")

    known_item_ids = set(df.select("item_id").unique().to_series())
    unknown_item_ids = [
        item_id for item_id in overrides.select("item_id").unique().to_series() if item_id not in known_item_ids
    ]
    if unknown_item_ids:
        raise ValueError(
            f"manual_main_parent.csv rows reference item_id(s) not found in the genre tree: {unknown_item_ids}"
        )
    unknown_parent_item_ids = [
        parent_item_id
        for parent_item_id in overrides.select("parent_item_id").unique().to_series()
        if parent_item_id not in known_item_ids
    ]
    if unknown_parent_item_ids:
        raise ValueError(
            "manual_main_parent.csv rows reference parent_item_id(s) not found in the genre tree: "
            f"{unknown_parent_item_ids}"
        )
    regional_overview_ids = set(df.filter(pl.col("is_regional_overview")).select("item_id").unique().to_series())
    overview_parent_item_ids = [
        parent_item_id
        for parent_item_id in overrides.select("parent_item_id").unique().to_series()
        if parent_item_id in regional_overview_ids
    ]
    if overview_parent_item_ids:
        raise ValueError(
            "manual_main_parent.csv rows reference parent_item_id(s) flagged is_regional_overview in the "
            f"genre tree (use manual_regional_overrides.csv for those): {overview_parent_item_ids}"
        )
    overridden_ids = set(overrides.select("item_id").unique().to_series())
    non_canonical_item_ids = sorted(
        df.filter(pl.col("item_id").is_in(list(overridden_ids)) & pl.col("is_regional_overview"))
        .select("item_id")
        .unique()
        .to_series()
    )
    if non_canonical_item_ids:
        raise ValueError(
            "manual_main_parent.csv rows reference item_id(s) flagged is_regional_overview in the genre "
            f"tree — this backstop is not for overview items: {non_canonical_item_ids}"
        )

    if "exclude_other_parents" in overrides.columns:
        exclude_ids = set(
            overrides.filter(
                pl.col("exclude_other_parents").cast(pl.Utf8).str.strip_chars().str.to_lowercase() == "true"
            )
            .select("item_id")
            .unique()
            .to_series()
        )
    else:
        exclude_ids = set()

    parent_labels = (
        df.select("item_id", "item_label")
        .unique(subset="item_id")
        .rename({"item_id": "parent_item_id", "item_label": "parent_item_label"})
    )
    parent_display_labels = (
        df.select("item_id", "item_display_label")
        .unique(subset="item_id")
        .rename({"item_id": "parent_item_id", "item_display_label": "parent_item_display_label"})
    )
    item_columns = [
        c
        for c in df.columns
        if c not in ("parent_id", "parent_label", "parent_display_label", "parent_url", "relation_type")
    ]
    synthetic_edges = (
        overrides.select("item_id", "parent_item_id")
        .join(parent_labels, on="parent_item_id", how="left")
        .join(parent_display_labels, on="parent_item_id", how="left")
        .join(df.select(item_columns).unique(subset="item_id"), on="item_id", how="left")
        .with_columns(
            parent_id=pl.col("parent_item_id"),
            parent_label=pl.col("parent_item_label"),
            parent_display_label=pl.col("parent_item_display_label"),
            parent_url=pl.lit(WIKIDATA_ITEM_URL_PREFIX) + pl.col("parent_item_id"),
            relation_type=pl.lit(MANUAL_MAIN_PARENT_RELATION_TYPE),
        )
    )
    if "has_parent_label" in df.columns:
        synthetic_edges = synthetic_edges.with_columns(
            has_parent_label=pl.col("parent_item_label").is_not_null()
            & (pl.col("parent_item_label") != pl.col("parent_item_id"))
        )
    synthetic_edges = synthetic_edges.select(df.columns)

    df = df.filter(
        ~(
            pl.col("item_id").is_in(list(overridden_ids))
            & (pl.col("parent_id").is_null() | pl.col("item_id").is_in(list(exclude_ids)))
        )
    )
    return pl.concat([df, synthetic_edges])


def _apply_overview_overrides(df: pl.DataFrame, manual_overrides: pl.DataFrame) -> pl.DataFrame:
    if "overview_item_id" not in manual_overrides.columns:
        raise ValueError("manual_regional_overrides.csv is missing the required 'overview_item_id' column")
    manual_overrides = manual_overrides.with_columns(pl.col("overview_item_id").cast(pl.Utf8))
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

    if "exclude_other_parents" in overrides.columns:
        exclude_ids = set(
            overrides.filter(
                pl.col("exclude_other_parents").cast(pl.Utf8).str.strip_chars().str.to_lowercase() == "true"
            )
            .select("item_id")
            .unique()
            .to_series()
        )
    else:
        exclude_ids = set()

    overview_labels = (
        df.select("item_id", "item_label")
        .unique(subset="item_id")
        .rename({"item_id": "overview_item_id", "item_label": "overview_item_label"})
    )
    overview_display_labels = (
        df.select("item_id", "item_display_label")
        .unique(subset="item_id")
        .rename({"item_id": "overview_item_id", "item_display_label": "overview_item_display_label"})
    )
    item_columns = [
        c
        for c in df.columns
        if c not in ("parent_id", "parent_label", "parent_display_label", "parent_url", "relation_type")
    ]
    synthetic_edges = (
        overrides.select("item_id", "overview_item_id")
        .join(overview_labels, on="overview_item_id", how="left")
        .join(overview_display_labels, on="overview_item_id", how="left")
        .join(df.select(item_columns).unique(subset="item_id"), on="item_id", how="left")
        .with_columns(
            parent_id=pl.col("overview_item_id"),
            parent_label=pl.col("overview_item_label"),
            parent_display_label=pl.col("overview_item_display_label"),
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
    df = df.filter(
        ~(
            pl.col("item_id").is_in(list(overridden_ids))
            & (pl.col("parent_id").is_null() | pl.col("item_id").is_in(list(exclude_ids)))
        )
    )
    return pl.concat([df, synthetic_edges])


def _apply_indigenous_to_exclusions(indigenous_ids: set[str], exclusions: pl.DataFrame) -> set[str]:
    if exclusions.is_empty():
        return indigenous_ids

    exclusion_ids = set(exclusions.select("item_id").unique().to_series())
    unknown_ids = sorted(exclusion_ids - indigenous_ids)
    if unknown_ids:
        raise ValueError(
            "manual_indigenous_to_exclusions.csv rows reference item_id(s) with no P2341 value in "
            f"wikidata_genre_indigenous_to.parquet: {unknown_ids}"
        )
    return indigenous_ids - exclusion_ids


def classify_regional_genres(
    regional_overview_classification_path: Path,
    indigenous_to_path: Path,
    manual_overrides_path: Path,
    manual_canonical_parent_additions_path: Path,
    manual_main_parent_path: Path,
    manual_indigenous_to_exclusions_path: Path,
    output_dir: Path,
) -> Path:
    logger.info("classifying regional genres in %s", regional_overview_classification_path)
    df = pl.read_parquet(regional_overview_classification_path)

    indigenous_ids = set(pl.read_parquet(indigenous_to_path).select("item_id").unique().to_series())
    indigenous_ids = _apply_indigenous_to_exclusions(indigenous_ids, pl.read_csv(manual_indigenous_to_exclusions_path))
    manual_overrides = pl.read_csv(manual_overrides_path)
    manual_override_ids = set(manual_overrides.select("item_id").unique().to_series())
    df = _apply_overview_overrides(df, manual_overrides)
    manual_canonical_additions_schema = {"item_id": pl.Utf8, "item_label": pl.Utf8, "reason": pl.Utf8}
    df = _add_manual_canonical_parent_items(
        df, pl.read_csv(manual_canonical_parent_additions_path, schema_overrides=manual_canonical_additions_schema)
    )
    manual_main_parent = pl.read_csv(manual_main_parent_path)
    df = _apply_manual_main_parent(df, manual_main_parent)

    # Seeds: the "music of <place>" items themselves (plus items reclassified into that same
    # non-genre-overview role via manual_overview_reclassifications.csv, e.g. "European folk music"
    # — see regional_overview_classification.py), plus every item Wikidata's P2341 ("indigenous to")
    # flags as belonging to a specific people (e.g. "Han Chinese music" -> "Han Chinese people", see
    # bronze wikidata_genre_indigenous_to.parquet), plus anything a data expert has hand-flagged in
    # manual_regional_overrides.csv for genres none of the automated sources catch. P495 ("country of
    # origin") is deliberately not used as a seed source: it's set on broad canonical umbrella genres
    # too (jazz -> United States, heavy metal music -> United Kingdom), which would wrongly cascade
    # regional status onto their real subgenres. All remaining sets are tagged non-genre or
    # nationally/ethnically-specific in their own right but are not excluded from the regional graph
    # — they're regional genre nodes themselves (see hierarchy.py), and together form the seed set
    # every other regional flag propagates from. A genre item is "direct" regional if any one of its
    # parent edges points at a seed — not all of them, since e.g. "Australian rock" has one parent
    # into "rock music" (clean) and another into "music of Australia" (a seed), and is still
    # considered regional. Regional status then cascades to children layer by layer: any genre item
    # with a parent edge into an already-regional item is "inherited" regional, repeated until no new
    # items are found.
    seed_ids = set(
        df.filter(pl.col("classification_reason").is_in(["regional_overview", MANUAL_OVERVIEW_RECLASSIFICATION_REASON]))
        .select("item_id")
        .unique()
        .to_series()
    )
    source_ids = seed_ids | indigenous_ids | manual_override_ids
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

    regional_ids = set(direct_ids) | indigenous_ids | manual_override_ids
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
        .when(pl.col("item_id").is_in(list(manual_override_ids)))
        .then(pl.lit(True))
        .when(pl.col("item_id").is_in(list(direct_ids)))
        .then(pl.lit(True))
        .otherwise(pl.col("item_id").is_in(list(regional_ids))),
        regional_reason=pl.when(pl.col("item_id").is_in(list(seed_ids)))
        .then(pl.lit("seed"))
        .when(pl.col("item_id").is_in(list(indigenous_ids)))
        .then(pl.lit("indigenous_to"))
        .when(pl.col("item_id").is_in(list(manual_override_ids)))
        .then(pl.lit("manual_override"))
        .when(pl.col("item_id").is_in(list(direct_ids)))
        .then(pl.lit("direct"))
        .when(pl.col("item_id").is_in(list(regional_ids)))
        .then(pl.lit("inherited"))
        .otherwise(None),
    )

    output_dir.mkdir(parents=True, exist_ok=True)
    output_path = output_dir / "4_regional_classification.parquet"
    df.write_parquet(output_path)
    regional_counts = df.filter(pl.col("is_regional")).unique("item_id").group_by("regional_reason").len().to_dicts()
    logger.info(
        "wrote %d rows to %s (regional items by reason: %s)",
        df.height,
        output_path,
        regional_counts,
    )

    return output_path
