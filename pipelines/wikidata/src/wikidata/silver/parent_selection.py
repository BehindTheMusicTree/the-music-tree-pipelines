import logging
from collections import deque
from collections.abc import Callable
from pathlib import Path

import polars as pl

from wikidata.silver.regional_classification import (
    MANUAL_CANONICAL_PARENT_RELATION_TYPE,
    MANUAL_REGIONAL_GENRE_PARENT_RELATION_TYPE,
    MANUAL_REGIONAL_OVERVIEW_RELATION_TYPE,
)

logger = logging.getLogger(__name__)

SECONDARY_PARENT_COLUMNS = [
    "item_id",
    "item_label",
    "item_display_label",
    "item_url",
    "parent_id",
    "parent_label",
    "parent_display_label",
    "parent_url",
    "relation_type",
]
EDGE_COLUMNS = [
    "parent_id",
    "parent_label",
    "parent_display_label",
    "parent_url",
    "relation_type",
    "has_parent_label",
    "exclude_other_parents",
]
CANONICAL_AXIS = "canonical"
REGIONAL_GENRE_AXIS = "regional_genre"
OVERVIEW_AXIS = "overview"
MANUAL_RELATION_TYPES = [
    MANUAL_CANONICAL_PARENT_RELATION_TYPE,
    MANUAL_REGIONAL_GENRE_PARENT_RELATION_TYPE,
    MANUAL_REGIONAL_OVERVIEW_RELATION_TYPE,
]


def _qid_sort_key(item_id: str) -> tuple[int, int, str]:
    if item_id.startswith("Q") and item_id[1:].isdigit():
        return 0, int(item_id[1:]), item_id
    return 1, 0, item_id


def _ancestor_lookup(df: pl.DataFrame) -> Callable[[str], set[str]]:
    parents: dict[str, list[str]] = {}
    for item_id, parent_id in df.filter(pl.col("parent_id").is_not_null()).select("item_id", "parent_id").iter_rows():
        parents.setdefault(item_id, []).append(parent_id)
    cache: dict[str, set[str]] = {}

    def ancestors(node: str) -> set[str]:
        if node not in cache:
            seen: set[str] = set()
            queue = deque(parents.get(node, ()))
            while queue:
                parent = queue.popleft()
                if parent not in seen:
                    seen.add(parent)
                    queue.extend(parents.get(parent, ()))
            cache[node] = seen
        return cache[node]

    return ancestors


def _select_single_axis(edges: pl.DataFrame, axis: str) -> tuple[pl.DataFrame, pl.DataFrame]:
    candidates = edges.filter(pl.col("axis") == axis)
    manual = candidates.filter(pl.col("is_manual"))
    manual_item_ids = manual.get_column("item_id").to_list()
    excluded_item_ids = manual.filter(pl.col("excludes_other_parents")).get_column("item_id").to_list()
    auto = (
        candidates.filter(~pl.col("item_id").is_in(manual_item_ids))
        .sort(
            ["item_id", "parent_is_genre_item", "parent_numeric_id", "parent_id"],
            descending=[False, True, False, False],
            nulls_last=True,
        )
        .unique(subset="item_id", keep="first", maintain_order=True)
    )
    selected = pl.concat([manual, auto])
    secondary = candidates.filter(~pl.col("item_id").is_in(excluded_item_ids)).join(
        selected.select("item_id", "parent_id"), on=["item_id", "parent_id"], how="anti"
    )
    return selected, secondary


def _reduce_overviews(
    overview_edges: pl.DataFrame, regional_genre_parents: dict[str, str], ancestors: Callable[[str], set[str]]
) -> dict[str, list[str]]:
    candidates: dict[str, list[str]] = {}
    for item_id, parent_id in overview_edges.select("item_id", "parent_id").iter_rows():
        candidates.setdefault(item_id, []).append(parent_id)

    reduced: dict[str, list[str]] = {}
    removed: list[tuple[str, str, str]] = []
    for item_id, overview_ids in candidates.items():
        genre_parent = regional_genre_parents.get(item_id)
        others = set(overview_ids) | ({genre_parent} if genre_parent else set())
        kept = []
        for overview_id in overview_ids:
            covering = sorted(
                (other for other in others if other != overview_id and overview_id in ancestors(other)),
                key=_qid_sort_key,
            )
            covering = [other for other in covering if other not in ancestors(overview_id)]
            if covering:
                removed.append((item_id, overview_id, covering[0]))
            else:
                kept.append(overview_id)
        reduced[item_id] = sorted(kept, key=_qid_sort_key)
    for item_id, overview_id, covering_id in removed:
        logger.debug("dropped overview %s of %s, an ancestor of its parent %s", overview_id, item_id, covering_id)
    logger.info("dropped %d overview parent(s) already reached through another parent of the same item", len(removed))
    return reduced


def select_parents(regional_classification_path: Path, output_dir: Path) -> tuple[Path, Path]:
    logger.info("selecting parents from %s", regional_classification_path)
    df = pl.read_parquet(regional_classification_path)

    # Wikidata's P279/P361 graph isn't a strict tree, so an item's parents are split by what the parent is
    # into three axes: its canonical parent (a canonical genre, or a parent outside the tree), its regional
    # genre parent (a regional genre) and its "music of <place>" overviews. The first two keep at most one
    # parent each, the others of the same axis going to 5_secondary_parents.parquet; every overview is kept,
    # except one already reached through another of the item's parents. A curated edge (see
    # regional_classification.py) wins its axis, and its `exclude_other_parents` drops the axis' other
    # candidates. Otherwise a parent that is itself an item of the tree wins, then the lowest QID — a
    # provisional placeholder pending curation. See DESIGN.md#24-5_parent_selection.
    items = df.unique(subset="item_id", keep="first", maintain_order=True)
    item_ids = items.get_column("item_id").to_list()
    parent_attributes = items.select(
        parent_id="item_id", parent_is_overview="is_regional_overview", parent_is_regional="is_regional"
    )
    edges = (
        df.filter(pl.col("parent_id").is_not_null())
        .with_columns(is_manual=pl.col("relation_type").is_in(MANUAL_RELATION_TYPES))
        .sort(["item_id", "parent_id", "is_manual"], descending=[False, False, True])
        .unique(subset=["item_id", "parent_id"], keep="first", maintain_order=True)
        .join(parent_attributes, on="parent_id", how="left")
        .with_columns(
            axis=pl.when(pl.col("parent_is_overview").fill_null(False))
            .then(pl.lit(OVERVIEW_AXIS))
            .when(pl.col("parent_is_regional").fill_null(False))
            .then(pl.lit(REGIONAL_GENRE_AXIS))
            .otherwise(pl.lit(CANONICAL_AXIS)),
            parent_is_genre_item=pl.col("parent_id").is_in(item_ids),
            parent_numeric_id=pl.col("parent_id").str.strip_prefix("Q").cast(pl.Int64, strict=False),
            excludes_other_parents=pl.col("exclude_other_parents").fill_null(False),
        )
    )

    canonical, canonical_secondary = _select_single_axis(edges, CANONICAL_AXIS)
    regional_genre, regional_genre_secondary = _select_single_axis(edges, REGIONAL_GENRE_AXIS)
    regional_genre_parents = dict(regional_genre.select("item_id", "parent_id").iter_rows())

    overview_edges = edges.filter(pl.col("axis") == OVERVIEW_AXIS)
    exclusive_item_ids = (
        overview_edges.filter(pl.col("is_manual") & pl.col("excludes_other_parents")).get_column("item_id").to_list()
    )
    overview_edges = overview_edges.filter(~pl.col("item_id").is_in(exclusive_item_ids) | pl.col("is_manual"))
    overviews = _reduce_overviews(overview_edges, regional_genre_parents, _ancestor_lookup(df))

    edge_columns = [c for c in EDGE_COLUMNS if c in df.columns]
    item_columns = [c for c in df.columns if c not in edge_columns]
    overview_lists = pl.DataFrame(
        {"item_id": list(overviews), "regional_overview_ids": list(overviews.values())},
        schema={"item_id": pl.Utf8, "regional_overview_ids": pl.List(pl.Utf8)},
    )
    selection = (
        items.select(item_columns)
        .join(canonical.select("item_id", canonical_parent_id="parent_id"), on="item_id", how="left")
        .join(regional_genre.select("item_id", regional_genre_parent_id="parent_id"), on="item_id", how="left")
        .join(overview_lists, on="item_id", how="left")
        .with_columns(regional_overview_ids=pl.col("regional_overview_ids").fill_null([]))
        .with_columns(
            parent_id=pl.when(pl.col("is_regional").fill_null(False))
            .then(pl.coalesce("regional_genre_parent_id", pl.col("regional_overview_ids").list.first()))
            .otherwise(pl.col("canonical_parent_id"))
        )
        .join(edges.select("item_id", *edge_columns), on=["item_id", "parent_id"], how="left")
        .select(*df.columns, "canonical_parent_id", "regional_genre_parent_id", "regional_overview_ids")
    )
    secondary = pl.concat([canonical_secondary, regional_genre_secondary]).select(SECONDARY_PARENT_COLUMNS)

    output_dir.mkdir(parents=True, exist_ok=True)
    selection_path = output_dir / "5_parent_selection.parquet"
    secondary_path = output_dir / "5_secondary_parents.parquet"
    selection.write_parquet(selection_path)
    secondary.write_parquet(secondary_path)
    logger.info(
        "wrote %d items to %s, %d secondary parent edges to %s",
        selection.height,
        selection_path,
        secondary.height,
        secondary_path,
    )

    return selection_path, secondary_path
