import logging
from pathlib import Path

import polars as pl

from common.quality_checks import check_non_empty, check_null_rate, check_row_count_delta, check_unique_key


logger = logging.getLogger(__name__)


def load_extra_parent_edges(secondary_parents_path: Path, item_ids: set[str], ref_ids: set[str]) -> pl.DataFrame:
    """Wikidata's non-main parent edges (`5_secondary_parents.parquet`) of `item_ids`, restricted to
    parents in `ref_ids` — the rows grow-the-music-tree-api can resolve when the tree is imported."""
    edges = pl.read_parquet(secondary_parents_path).filter(pl.col("item_id").is_in(list(item_ids)))
    kept = (
        edges.filter(pl.col("parent_id").is_in(list(ref_ids)))
        .select("item_id", "parent_id")
        .unique()
        .with_columns(parent_numeric_id=pl.col("parent_id").str.slice(1).cast(pl.Int64, strict=False))
        .sort("item_id", "parent_numeric_id", "parent_id")
        .drop("parent_numeric_id")
    )
    logger.info(
        "kept %d extra parent edge(s) from %s, dropped %d pointing outside the resolvable set",
        kept.height,
        secondary_parents_path,
        edges.height - kept.height,
    )
    return kept


def edges_to_parent_map(edges: pl.DataFrame) -> dict[str, list[str]]:
    parents: dict[str, list[str]] = {}
    for item_id, parent_id in edges.select("item_id", "parent_id").iter_rows():
        parents.setdefault(item_id, []).append(parent_id)
    return parents


def _count_tree_nodes(nodes: list[dict]) -> int:
    return sum(1 + _count_tree_nodes(node["children"]) for node in nodes)


def _collect_names(nodes: list[dict]) -> list[str]:
    return [node["name"] for node in nodes] + [name for node in nodes for name in _collect_names(node["children"])]


def build_genre_tree(
    hierarchy: pl.DataFrame,
    pop_sides: dict[str, set[str]] | None = None,
    primary_parents: dict[str, list[str]] | None = None,
    secondary_parents: dict[str, list[str]] | None = None,
    external_ids: set[str] | None = None,
) -> dict:
    check_non_empty(hierarchy, "hierarchy")
    check_null_rate(hierarchy, "item_id", "hierarchy")
    check_unique_key(hierarchy, "item_id", "hierarchy")

    children_by_parent: dict[str, list[str]] = {}
    for row in hierarchy.iter_rows(named=True):
        children_by_parent.setdefault(row["parent_id"], []).append(row["item_id"])

    # `item_label` (the real Wikidata label) drives pop_sides matching below — pop_sides is keyed by
    # item_label, not any display override. `item_display_label` (a manual_label_overrides.csv pick,
    # falling back to item_label when absent) is what's actually emitted as a node's "name".
    labels_by_id = dict(hierarchy.select("item_id", "item_label").unique(subset="item_id").iter_rows())
    if "item_display_label" in hierarchy.columns:
        display_labels_by_id = dict(
            hierarchy.select("item_id", "item_display_label").unique(subset="item_id").iter_rows()
        )
        display_labels_by_id = {
            item_id: display_labels_by_id[item_id] or labels_by_id[item_id] for item_id in labels_by_id
        }
    else:
        display_labels_by_id = labels_by_id

    # A `parent_id` can point at a label that never has its own row (only ever appears as a parent
    # value) — such a parent is a dead end, not a real ancestor, so the item pointing at it is a
    # root just as much as one with a null parent_id. Mirrors canonical_roots.py's root definition.
    known_item_ids = set(hierarchy.select("item_id").unique().to_series().to_list())

    # `external_ids`: rows grow already holds from a previously imported tree (the canonical one, for
    # the regional tree), which a primaryParents/secondaryParents ref may point at too.
    resolvable_ids = known_item_ids | (external_ids or set())
    extra_parents = {"primaryParents": primary_parents or {}, "secondaryParents": secondary_parents or {}}
    for key, parents_by_item in extra_parents.items():
        unknown_items = sorted(set(parents_by_item) - known_item_ids)
        unknown_refs = sorted({ref for refs in parents_by_item.values() for ref in refs} - resolvable_ids)
        if unknown_items or unknown_refs:
            raise ValueError(f"{key} has unknown item id(s) {unknown_items} / parent ref(s) {unknown_refs}")

    def build_node(item_id: str) -> dict:
        node = {
            "id": item_id,
            "name": display_labels_by_id[item_id],
            "children": [build_node(child_id) for child_id in children_by_parent.get(item_id, [])],
        }
        for key, parents_by_item in extra_parents.items():
            if item_id in parents_by_item:
                node[key] = parents_by_item[item_id]
        return node

    roots = (
        hierarchy.filter(pl.col("parent_id").is_null() | ~pl.col("parent_id").is_in(known_item_ids))
        .select("item_id", "item_label")
        .unique(subset="item_id")
        .sort("item_label")
    )

    def build_root(item_id: str) -> dict:
        node = build_node(item_id)
        # `side` (the-music-tree-genre-kit's pop/core distinction) is only meaningful for a root's
        # direct children, hence marking it here rather than inside build_node's recursion.
        pop_children = (pop_sides or {}).get(labels_by_id[item_id], set())
        for child_id, child in zip(children_by_parent.get(item_id, []), node["children"]):
            if labels_by_id[child_id] in pop_children:
                child["side"] = "pop"
        return node

    tree = [build_root(row["item_id"]) for row in roots.iter_rows(named=True)]

    # A parent_id cycle (A -> B -> A) leaves both items out of `roots` (each has a known parent) and
    # unreachable from any real root, silently dropping them from the tree instead of raising.
    check_row_count_delta(hierarchy.select("item_id").n_unique(), _count_tree_nodes(tree), "genre tree build")

    # grow-the-music-tree-api rejects an imported tree containing duplicate node names
    # (`tree_value_duplicate`) — failing here, before export, is cheaper than failing the import.
    # Case-insensitive: a title-cased synthetic node and a lowercase real Wikidata label (e.g.
    # "Pop reggae" vs "pop reggae") are the same name to the API's own uniqueness check.
    names = _collect_names(tree)
    lowered_names = [name.lower() for name in names]
    duplicate_lowered = {name for name in lowered_names if lowered_names.count(name) > 1}
    duplicate_names = sorted({name for name in names if name.lower() in duplicate_lowered})
    if duplicate_names:
        raise ValueError(f"genre tree has duplicate node name(s) (case-insensitive): {duplicate_names}")

    return {"tree": tree}
