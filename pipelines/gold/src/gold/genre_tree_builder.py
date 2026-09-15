import polars as pl

from common.quality_checks import check_non_empty, check_null_rate, check_row_count_delta, check_unique_key


def _count_tree_nodes(nodes: list[dict]) -> int:
    return sum(1 + _count_tree_nodes(node["children"]) for node in nodes)


def build_genre_tree(hierarchy: pl.DataFrame, pop_sides: dict[str, set[str]] | None = None) -> dict:
    check_non_empty(hierarchy, "hierarchy")
    check_null_rate(hierarchy, "item_id", "hierarchy")
    check_unique_key(hierarchy, "item_id", "hierarchy")

    children_by_parent: dict[str, list[str]] = {}
    for row in hierarchy.iter_rows(named=True):
        children_by_parent.setdefault(row["parent_id"], []).append(row["item_id"])

    labels_by_id = dict(hierarchy.select("item_id", "item_label").unique(subset="item_id").iter_rows())

    # A `parent_id` can point at a label that never has its own row (only ever appears as a parent
    # value) — such a parent is a dead end, not a real ancestor, so the item pointing at it is a
    # root just as much as one with a null parent_id. Mirrors canonical_roots.py's root definition.
    known_item_ids = set(hierarchy.select("item_id").unique().to_series().to_list())

    def build_node(item_id: str, label: str) -> dict:
        return {
            "name": label,
            "children": [
                build_node(child_id, labels_by_id[child_id]) for child_id in children_by_parent.get(item_id, [])
            ],
        }

    roots = (
        hierarchy.filter(pl.col("parent_id").is_null() | ~pl.col("parent_id").is_in(known_item_ids))
        .select("item_id", "item_label")
        .unique(subset="item_id")
        .sort("item_label")
    )

    def build_root(item_id: str, label: str) -> dict:
        node = build_node(item_id, label)
        # `side` (the-music-tree-genre-kit's pop/core distinction) is only meaningful for a root's
        # direct children, hence marking it here rather than inside build_node's recursion.
        pop_children = (pop_sides or {}).get(label, set())
        for child in node["children"]:
            if child["name"] in pop_children:
                child["side"] = "pop"
        return node

    tree = [build_root(row["item_id"], row["item_label"]) for row in roots.iter_rows(named=True)]

    # A parent_id cycle (A -> B -> A) leaves both items out of `roots` (each has a known parent) and
    # unreachable from any real root, silently dropping them from the tree instead of raising.
    check_row_count_delta(hierarchy.select("item_id").n_unique(), _count_tree_nodes(tree), "genre tree build")

    return {"tree": tree}
