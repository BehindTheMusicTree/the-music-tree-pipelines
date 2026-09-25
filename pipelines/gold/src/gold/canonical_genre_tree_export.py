import json
import logging
from pathlib import Path

import polars as pl
from jsonschema import ValidationError, validate

from gold.genre_tree_builder import build_genre_tree, edges_to_parent_map, load_extra_parent_edges

GENRE_TREE_SCHEMA_PATH = Path(__file__).parent / "schemas" / "genre_tree.schema.json"

# grow-the-music-tree-api requires every genre tree it imports to have a root named exactly this —
# see its GenreManager.assert_mainstream_pop_root_present. Failing here, before export, is cheaper
# than failing the downstream import.
CANONICAL_MAINSTREAM_POP_ROOT_NAME = "Mainstream Pop"

# Committed alongside the code (not a gitignored gold output): a data expert's curated pick of which
# direct child of each canonical root is the-music-tree-genre-kit's "pop" side (crossover/mainstream
# branch, e.g. Electropop under Electronic) — everything else defaults to "core". Wikidata has no
# notion of this distinction, so it can't be derived automatically.
MANUAL_CANONICAL_GENRE_POP_SIDE_PATH = Path(__file__).parent / "manual_canonical_genre_pop_side.csv"

logger = logging.getLogger(__name__)


def _blank_mask(df: pl.DataFrame, column: str) -> pl.Expr:
    return pl.col(column).is_null() | (pl.col(column).str.strip_chars() == "")


def _load_pop_sides(manual_canonical_genre_pop_side_path: Path, hierarchy: pl.DataFrame) -> dict[str, set[str]]:
    df = pl.read_csv(manual_canonical_genre_pop_side_path)
    csv_name = manual_canonical_genre_pop_side_path.name

    for column in ("root_genre_name", "pop_child_genre_name", "reason"):
        if not df.filter(_blank_mask(df, column)).is_empty():
            raise ValueError(f"{csv_name} has row(s) with a null/blank '{column}'")

    rows = df.select("root_genre_name", "pop_child_genre_name").rows()
    if len(rows) != len(set(rows)):
        raise ValueError(f"{csv_name} has duplicate (root_genre_name, pop_child_genre_name) row(s)")

    roots = df.select("root_genre_name").to_series().to_list()

    known_item_ids = set(hierarchy.select("item_id").unique().to_series().to_list())
    root_rows = hierarchy.filter(pl.col("parent_id").is_null() | ~pl.col("parent_id").is_in(known_item_ids))
    root_id_by_label = dict(root_rows.select("item_label", "item_id").unique(subset="item_label").iter_rows())

    unknown_roots = sorted(set(roots) - set(root_id_by_label))
    if unknown_roots:
        raise ValueError(f"{csv_name} references root_genre_name(s) not found among canonical roots: {unknown_roots}")

    pop_sides: dict[str, set[str]] = {}
    for row in df.iter_rows(named=True):
        root_label, child_label = row["root_genre_name"], row["pop_child_genre_name"]
        direct_children = set(
            hierarchy.filter(pl.col("parent_id") == root_id_by_label[root_label]).select("item_label").to_series()
        )
        if child_label not in direct_children:
            raise ValueError(
                f"{csv_name}: pop_child_genre_name '{child_label}' is not a direct child of root '{root_label}'"
            )
        pop_sides.setdefault(root_label, set()).add(child_label)

    for root_label, pop_children in pop_sides.items():
        direct_children = set(
            hierarchy.filter(pl.col("parent_id") == root_id_by_label[root_label]).select("item_label").to_series()
        )
        if pop_children >= direct_children:
            raise ValueError(
                f"{csv_name}: root '{root_label}' has no core (non-pop) child left — "
                f"all {len(direct_children)} direct child(ren) marked pop"
            )

    return pop_sides


def export_canonical_genre_tree(
    wikidata_silver_dir: Path,
    output_dir: Path,
    manual_canonical_genre_pop_side_path: Path = MANUAL_CANONICAL_GENRE_POP_SIDE_PATH,
) -> Path:
    hierarchy_path = wikidata_silver_dir / "7_canonical_hierarchy.parquet"
    logger.info("building canonical genre tree from %s", hierarchy_path)
    hierarchy = pl.read_parquet(hierarchy_path)
    pop_sides = _load_pop_sides(manual_canonical_genre_pop_side_path, hierarchy)
    # Imported first into grow, so a secondary ref can only resolve against this tree's own rows —
    # an edge to a regional (or unexported) item is dropped here rather than failing the import.
    item_ids = set(hierarchy.get_column("item_id"))
    secondary_edges = load_extra_parent_edges(wikidata_silver_dir / "5_secondary_parents.parquet", item_ids, item_ids)
    tree = {
        "allowsMultiplePrimaryParents": False,
        **build_genre_tree(hierarchy, pop_sides, secondary_parents=edges_to_parent_map(secondary_edges)),
    }

    if not any(node["name"] == CANONICAL_MAINSTREAM_POP_ROOT_NAME for node in tree["tree"]):
        raise ValueError(
            f"canonical genre tree has no root named {CANONICAL_MAINSTREAM_POP_ROOT_NAME!r} (source: {hierarchy_path})"
        )

    schema = json.loads(GENRE_TREE_SCHEMA_PATH.read_text())
    try:
        validate(tree, schema)
    except ValidationError as e:
        raise ValueError(f"canonical genre tree failed schema validation ({GENRE_TREE_SCHEMA_PATH}): {e.message}")

    output_dir.mkdir(parents=True, exist_ok=True)
    output_path = output_dir / "1_canonical_genre_tree.json"
    output_path.write_text(json.dumps(tree, indent=2, ensure_ascii=False))
    logger.info("wrote %d root(s) to %s", len(tree["tree"]), output_path)
    return output_path
