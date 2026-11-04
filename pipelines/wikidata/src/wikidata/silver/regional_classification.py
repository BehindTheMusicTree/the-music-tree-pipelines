import logging
from pathlib import Path

import polars as pl

from wikidata.silver.regional_overview_classification import MANUAL_OVERVIEW_RECLASSIFICATION_REASON

logger = logging.getLogger(__name__)

WIKIDATA_ITEM_URL_PREFIX = "https://www.wikidata.org/wiki/"

# Three curated parent lists (in grow-the-music-tree-api, pulled into Bronze by the curation pipeline), one
# per parent axis parent_selection.py picks: manual_canonical_parent.csv (an item's canonical parent),
# manual_regional_genre_parent.csv (its regional genre parent) and manual_regional_overviews.csv (one of its
# "music of <place>" overviews, keyed by (item_id, overview_item_id)). Each row becomes a synthetic edge here,
# before the cascade below, so is_regional flows through it like through a Bronze edge. Its
# `exclude_other_parents` flag is only carried on that edge: parent_selection.py drops the item's other
# parents of the same axis, which never changes is_regional since the edge kept is of that same kind.
# Leaving an item's edges into the regional graph is canonical_overrides' job. See
# DESIGN.md#23-4_regional_classification.
MANUAL_CANONICAL_PARENT_RELATION_TYPE = "manual_canonical_parent"
MANUAL_REGIONAL_GENRE_PARENT_RELATION_TYPE = "manual_regional_genre_parent"
MANUAL_REGIONAL_OVERVIEW_RELATION_TYPE = "manual_regional_overview"

# manual_canonical_overrides.csv, curated the same way: genres kept canonical although an edge leads them into the
# regional graph (e.g. a period of Western art music filed under a "music of <place>" item). The cascade
# below stops at them, so their own subgenres only turn regional through another regional edge. An id
# absent from the tree or flagged is_regional_overview raises.

# manual_canonical_parent_additions.csv, curated the same way: a canonical (non-regional,
# non-overview) grouping node that has no real Wikidata QID of its own — e.g. "Reggae/Dub", a
# grouping Gold-layer wants but that no single Wikidata item represents — so a data expert can still
# give real genre items (like "reggae" and "dub music") a real parent to nest under in
# manual_canonical_parent.csv. `item_id` must be a synthetic `LOCAL:`-prefixed id (never a fabricated
# QID-shaped id) and must not already be present in the genre tree. Applied before
# the manual parent edges so the new node is a legal parent_item_id target. See
# DESIGN.md#23-4_regional_classification.
MANUAL_CANONICAL_PARENT_ADDITION_REASON = "manual_canonical_parent_addition"
LOCAL_ID_PREFIX = "LOCAL:"

# manual_accepted_canonical_roots.csv, curated the same way (also read by canonical_roots.py): roots a
# data expert has accepted as standalone canonical genres. Every parent edge of those items is dropped
# here, before the cascade, so a Wikidata re-parenting (e.g. hip-hop moved under the regional "urban
# contemporary" on 2026-10-09) can't silently pull an accepted root into another subtree or the
# regional graph — Gold's per-root curation (manual_canonical_genre_pop_side.csv) depends on them
# staying roots. A Wikidata P2341 value on one is ignored the same way; a curated contradiction (a manual
# parent, overview or overview reclassification) or an accepted id missing from the tree (deleted/merged QID) raises,
# freezing the run until a curator or a Wikidata revert resolves it.

# manual_indigenous_to_exclusions.csv, curated the same way: P2341 ("indigenous to") is
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


def _add_manual_canonical_parent_items(df: pl.DataFrame, manual_additions: pl.DataFrame) -> pl.DataFrame:
    if manual_additions.is_empty():
        return df

    blank = manual_additions.filter(
        pl.col("item_id").is_null()
        | (pl.col("item_id").str.strip_chars() == "")
        | pl.col("label").is_null()
        | (pl.col("label").str.strip_chars() == "")
    )
    if not blank.is_empty():
        raise ValueError(
            "manual_canonical_parent_additions.csv rows must not have a blank item_id or label: "
            f"{blank.select('item_id').to_series().to_list()}"
        )

    manual_additions = manual_additions.with_columns(
        item_id=pl.col("item_id").str.strip_chars(), label=pl.col("label").str.strip_chars()
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
        item_label=pl.col("label"),
        item_display_label=pl.col("label"),
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


def _read_manual_parents(path: Path, parent_column: str) -> pl.DataFrame:
    manual = pl.read_csv(path, infer_schema=False)
    missing_columns = [c for c in ("item_id", parent_column, "exclude_other_parents") if c not in manual.columns]
    if missing_columns:
        raise ValueError(f"{path.name} is missing required column(s): {missing_columns}")
    manual = manual.select(
        pl.col("item_id").str.strip_chars(),
        pl.col(parent_column).str.strip_chars().alias("parent_item_id"),
        exclude_other_parents=pl.col("exclude_other_parents").str.strip_chars().str.to_lowercase().eq("true"),
    )
    blank = manual.filter(
        pl.col("item_id").is_null()
        | (pl.col("item_id") == "")
        | pl.col("parent_item_id").is_null()
        | (pl.col("parent_item_id") == "")
    )
    if not blank.is_empty():
        raise ValueError(f"{path.name} rows must not have a blank item_id or {parent_column}: {blank.rows()}")
    return manual.with_columns(exclude_other_parents=pl.col("exclude_other_parents").fill_null(False))


def _validate_manual_parents(
    df: pl.DataFrame, manual: pl.DataFrame, list_name: str, key_columns: list[str], parent_is_overview: bool
) -> None:
    if manual.select(key_columns).is_duplicated().any():
        raise ValueError(f"{list_name} contains duplicate {'/'.join(key_columns)} rows")
    known_ids = set(df.get_column("item_id"))
    overview_ids = set(df.filter(pl.col("is_regional_overview")).get_column("item_id"))
    item_ids = set(manual.get_column("item_id"))
    parent_ids = set(manual.get_column("parent_item_id"))
    checks = [
        (sorted(item_ids - known_ids), "item_id(s) not found in the genre tree"),
        (sorted(parent_ids - known_ids), "parent(s) not found in the genre tree"),
        (sorted(manual.filter(pl.col("item_id") == pl.col("parent_item_id")).get_column("item_id")), "self-parent(s)"),
    ]
    if parent_is_overview:
        checks.append((sorted((parent_ids & known_ids) - overview_ids), "parent(s) not flagged is_regional_overview"))
    else:
        checks.append((sorted(item_ids & overview_ids), "item_id(s) flagged is_regional_overview"))
        checks.append(
            (
                sorted(parent_ids & overview_ids),
                "parent(s) flagged is_regional_overview (use manual_regional_overviews.csv for those)",
            )
        )
    for ids, problem in checks:
        if ids:
            raise ValueError(f"{list_name} rows reference {problem}: {ids}")


def _add_manual_parent_edges(df: pl.DataFrame, manual: pl.DataFrame, relation_type: str) -> pl.DataFrame:
    parents = df.select(
        parent_id="item_id", parent_label="item_label", parent_display_label="item_display_label"
    ).unique(subset="parent_id")
    item_columns = [
        c
        for c in df.columns
        if c not in ("parent_id", "parent_label", "parent_display_label", "parent_url", "relation_type")
        and c != "exclude_other_parents"
    ]
    edges = (
        manual.rename({"parent_item_id": "parent_id"})
        .join(parents, on="parent_id", how="left")
        .join(df.select(item_columns).unique(subset="item_id"), on="item_id", how="left")
        .with_columns(
            parent_url=pl.lit(WIKIDATA_ITEM_URL_PREFIX) + pl.col("parent_id"),
            relation_type=pl.lit(relation_type),
        )
    )
    if "has_parent_label" in df.columns:
        edges = edges.with_columns(
            has_parent_label=pl.col("parent_label").is_not_null() & (pl.col("parent_label") != pl.col("parent_id"))
        )
    item_ids = manual.get_column("item_id").unique().to_list()
    replaced = manual.select("item_id", parent_id="parent_item_id")
    df = df.filter(~(pl.col("item_id").is_in(item_ids) & pl.col("parent_id").is_null())).join(
        replaced, on=["item_id", "parent_id"], how="anti"
    )
    return pl.concat([df, edges.select(df.columns)])


def _pin_accepted_roots(df: pl.DataFrame, accepted_roots: pl.DataFrame, manual_parent_ids: set[str]) -> pl.DataFrame:
    accepted_ids = set(accepted_roots.select("item_id").to_series())
    absent = sorted(accepted_ids - set(df.select("item_id").to_series()))
    if absent:
        raise ValueError(f"manual_accepted_canonical_roots.csv item_id(s) not found in the genre tree: {absent}")
    conflicting = sorted(accepted_ids & manual_parent_ids)
    if conflicting:
        raise ValueError(
            f"manual_accepted_canonical_roots.csv item_id(s) also have a curated parent or overview: {conflicting}"
        )
    pinned = (
        df.filter(pl.col("item_id").is_in(list(accepted_ids)))
        .unique(subset="item_id", keep="first")
        .with_columns(
            parent_id=pl.lit(None, dtype=pl.Utf8),
            parent_label=pl.lit(None, dtype=pl.Utf8),
            parent_display_label=pl.lit(None, dtype=pl.Utf8),
            parent_url=pl.lit(None, dtype=pl.Utf8),
            relation_type=pl.lit(None, dtype=pl.Utf8),
            has_parent_label=pl.lit(None, dtype=pl.Boolean),
            exclude_other_parents=pl.lit(None, dtype=pl.Boolean),
        )
        .select(df.columns)
    )
    return pl.concat([df.filter(~pl.col("item_id").is_in(list(accepted_ids))), pinned])


def _read_canonical_overrides(df: pl.DataFrame, path: Path) -> set[str]:
    ids = pl.read_csv(path, infer_schema=False).get_column("item_id").str.strip_chars().to_list()
    if len(ids) != len(set(ids)):
        raise ValueError(f"{path.name} contains duplicate item_id rows")
    unknown = sorted(set(ids) - set(df.get_column("item_id")))
    if unknown:
        raise ValueError(f"{path.name} rows reference item_id(s) not found in the genre tree: {unknown}")
    overviews = sorted(set(ids) & set(df.filter(pl.col("is_regional_overview")).get_column("item_id")))
    if overviews:
        raise ValueError(f"{path.name} rows reference item_id(s) flagged is_regional_overview: {overviews}")
    return set(ids)


def _validate_parent_axes(df: pl.DataFrame, manual: dict[str, pl.DataFrame]) -> None:
    is_regional = dict(df.select("item_id", "is_regional").unique(subset="item_id").iter_rows())
    for list_name, expected in (("manual_canonical_parent.csv", False), ("manual_regional_genre_parent.csv", True)):
        wrong = sorted(
            (item_id, parent_id)
            for item_id, parent_id in manual[list_name].select("item_id", "parent_item_id").iter_rows()
            if is_regional[parent_id] is not expected
        )
        if wrong:
            kind = "canonical" if expected is False else "regional"
            raise ValueError(f"{list_name} rows reference a parent that is not {kind} (item, parent): {wrong}")


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


MANUAL_PARENT_LISTS = (
    ("manual_canonical_parent.csv", "parent_item_id", ["item_id"], False, MANUAL_CANONICAL_PARENT_RELATION_TYPE),
    (
        "manual_regional_genre_parent.csv",
        "parent_item_id",
        ["item_id"],
        False,
        MANUAL_REGIONAL_GENRE_PARENT_RELATION_TYPE,
    ),
    (
        "manual_regional_overviews.csv",
        "overview_item_id",
        ["item_id", "parent_item_id"],
        True,
        MANUAL_REGIONAL_OVERVIEW_RELATION_TYPE,
    ),
)


def classify_regional_genres(
    regional_overview_classification_path: Path,
    indigenous_to_path: Path,
    manual_canonical_parent_additions_path: Path,
    manual_canonical_parent_path: Path,
    manual_regional_genre_parent_path: Path,
    manual_regional_overviews_path: Path,
    manual_canonical_overrides_path: Path,
    manual_accepted_roots_path: Path,
    manual_indigenous_to_exclusions_path: Path,
    output_dir: Path,
) -> Path:
    logger.info("classifying regional genres in %s", regional_overview_classification_path)
    df = pl.read_parquet(regional_overview_classification_path).with_columns(
        exclude_other_parents=pl.lit(None, dtype=pl.Boolean)
    )

    indigenous_ids = set(pl.read_parquet(indigenous_to_path).select("item_id").unique().to_series())
    indigenous_ids = _apply_indigenous_to_exclusions(indigenous_ids, pl.read_csv(manual_indigenous_to_exclusions_path))
    accepted_roots = pl.read_csv(manual_accepted_roots_path)
    accepted_ids = set(accepted_roots.select("item_id").to_series())
    if indigenous_ids & accepted_ids:
        logger.warning("ignoring P2341 on accepted canonical root(s): %s", sorted(indigenous_ids & accepted_ids))
        indigenous_ids -= accepted_ids
    manual_canonical_additions_schema = {"item_id": pl.Utf8, "label": pl.Utf8, "reason": pl.Utf8}
    df = _add_manual_canonical_parent_items(
        df, pl.read_csv(manual_canonical_parent_additions_path, schema_overrides=manual_canonical_additions_schema)
    )

    paths = {
        "manual_canonical_parent.csv": manual_canonical_parent_path,
        "manual_regional_genre_parent.csv": manual_regional_genre_parent_path,
        "manual_regional_overviews.csv": manual_regional_overviews_path,
    }
    manual: dict[str, pl.DataFrame] = {}
    for list_name, parent_column, key_columns, parent_is_overview, _ in MANUAL_PARENT_LISTS:
        manual[list_name] = _read_manual_parents(paths[list_name], parent_column)
        _validate_manual_parents(df, manual[list_name], list_name, key_columns, parent_is_overview)
    for list_name, _, _, _, relation_type in MANUAL_PARENT_LISTS:
        df = _add_manual_parent_edges(df, manual[list_name], relation_type)
    manual_parent_ids = set().union(*(set(m.get_column("item_id")) for m in manual.values()))
    df = _pin_accepted_roots(df, accepted_roots, manual_parent_ids)

    canonical_override_ids = _read_canonical_overrides(df, manual_canonical_overrides_path)
    if indigenous_ids & canonical_override_ids:
        logger.warning("ignoring P2341 on canonical override(s): %s", sorted(indigenous_ids & canonical_override_ids))
        indigenous_ids -= canonical_override_ids
    manual_overview_ids = set(manual["manual_regional_overviews.csv"].get_column("item_id")) - canonical_override_ids

    # Seeds: the "music of <place>" items themselves (plus items reclassified into that same
    # non-genre-overview role via manual_overview_reclassifications.csv, e.g. "European folk music"
    # — see regional_overview_classification.py), plus every item Wikidata's P2341 ("indigenous to")
    # flags as belonging to a specific people (e.g. "Han Chinese music" -> "Han Chinese people", see
    # bronze wikidata_genre_indigenous_to.parquet), plus every item a data expert filed under an overview
    # in manual_regional_overviews.csv. P495 ("country of origin") is deliberately not used as a seed
    # source: it's set on broad canonical umbrella genres too (jazz -> United States, heavy metal music
    # -> United Kingdom), which would wrongly cascade regional status onto their real subgenres. A genre
    # item is "direct" regional if any one of its parent edges points at a seed — not all of them, since
    # e.g. "Australian rock" has one parent into "rock music" (clean) and another into "music of
    # Australia" (a seed), and is still considered regional. Regional status then cascades to children
    # layer by layer: any genre item with a parent edge into an already-regional item is "inherited"
    # regional, repeated until no new items are found. manual_canonical_overrides.csv items are never
    # flagged, so the cascade does not pass through them either.
    seed_ids = set(
        df.filter(pl.col("classification_reason").is_in(["regional_overview", MANUAL_OVERVIEW_RECLASSIFICATION_REASON]))
        .select("item_id")
        .unique()
        .to_series()
    )
    source_ids = seed_ids | indigenous_ids | manual_overview_ids
    blocked_ids = list(source_ids | canonical_override_ids)
    direct_ids = set(
        df.filter(
            ~pl.col("is_regional_overview")
            & pl.col("parent_id").is_in(list(source_ids))
            & ~pl.col("item_id").is_in(blocked_ids)
        )
        .select("item_id")
        .unique()
        .to_series()
    )

    regional_ids = set(direct_ids) | indigenous_ids | manual_overview_ids
    frontier = set(regional_ids)
    while frontier:
        candidates = df.filter(
            ~pl.col("is_regional_overview")
            & pl.col("parent_id").is_in(list(frontier))
            & ~pl.col("item_id").is_in(list(regional_ids | source_ids | canonical_override_ids))
        )
        frontier = set(candidates.select("item_id").unique().to_series())
        regional_ids |= frontier

    df = df.with_columns(
        is_regional=pl.when(pl.col("item_id").is_in(list(seed_ids)))
        .then(pl.lit(True))
        .when(pl.col("is_regional_overview"))
        .then(None)
        .otherwise(pl.col("item_id").is_in(list(regional_ids))),
        regional_reason=pl.when(pl.col("item_id").is_in(list(seed_ids)))
        .then(pl.lit("seed"))
        .when(pl.col("item_id").is_in(list(indigenous_ids)))
        .then(pl.lit("indigenous_to"))
        .when(pl.col("item_id").is_in(list(manual_overview_ids)))
        .then(pl.lit(MANUAL_REGIONAL_OVERVIEW_RELATION_TYPE))
        .when(pl.col("item_id").is_in(list(direct_ids)))
        .then(pl.lit("direct"))
        .when(pl.col("item_id").is_in(list(regional_ids)))
        .then(pl.lit("inherited"))
        .otherwise(None),
    )
    _validate_parent_axes(df, manual)

    regional_roots = (
        df.filter(
            pl.col("item_id").is_in(list(accepted_ids)) & (pl.col("is_regional") | pl.col("is_regional_overview"))
        )
        .select("item_id", "regional_reason")
        .unique()
        .sort("item_id")
        .rows()
    )
    if regional_roots:
        raise ValueError(f"manual_accepted_canonical_roots.csv item_id(s) classified regional: {regional_roots}")

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
