import json
import logging
from pathlib import Path

import polars as pl
from jsonschema import ValidationError, validate

from gold.genre_tree_builder import build_genre_tree, edges_to_parent_map, load_extra_parent_edges

GENRE_TREE_SCHEMA_PATH = Path(__file__).parent / "schemas" / "genre_tree.schema.json"

# Committed alongside the code (not a gitignored gold output): a regional item's extra Wikidata
# parent edges are exported as primaryParents (tracks flow into that parent's playlist) by default;
# a data expert lists an (item_id, parent_id) edge here to demote it to a secondaryParents
# classification-only link instead.
MANUAL_REGIONAL_SECONDARY_PARENTS_PATH = Path(__file__).parent / "manual_regional_secondary_parents.csv"

logger = logging.getLogger(__name__)


def _load_demoted_edges(manual_regional_secondary_parents_path: Path, extra_edges: pl.DataFrame) -> pl.DataFrame:
    df = pl.read_csv(
        manual_regional_secondary_parents_path, schema_overrides={"item_id": pl.Utf8, "parent_id": pl.Utf8}
    )
    csv_name = manual_regional_secondary_parents_path.name

    for column in ("item_id", "parent_id", "reason"):
        if not df.filter(pl.col(column).is_null() | (pl.col(column).str.strip_chars() == "")).is_empty():
            raise ValueError(f"{csv_name} has row(s) with a null/blank '{column}'")

    demoted = df.select("item_id", "parent_id")
    if demoted.is_duplicated().any():
        raise ValueError(f"{csv_name} has duplicate (item_id, parent_id) row(s)")

    stale = demoted.join(extra_edges, on=["item_id", "parent_id"], how="anti")
    if not stale.is_empty():
        raise ValueError(
            f"{csv_name} references edge(s) that are not extra parent edges of the regional tree: {stale.rows()}"
        )

    return demoted


def export_regional_genre_tree(
    wikidata_silver_dir: Path,
    output_dir: Path,
    manual_regional_secondary_parents_path: Path = MANUAL_REGIONAL_SECONDARY_PARENTS_PATH,
) -> Path:
    hierarchy_path = wikidata_silver_dir / "8_regional_hierarchy.parquet"
    logger.info("building regional genre tree from %s", hierarchy_path)
    hierarchy = pl.read_parquet(hierarchy_path)

    # Imported after the canonical tree, so a ref may resolve against either tree's rows.
    item_ids = set(hierarchy.get_column("item_id"))
    canonical_ids = set(pl.read_parquet(wikidata_silver_dir / "7_canonical_hierarchy.parquet").get_column("item_id"))
    extra_edges = load_extra_parent_edges(
        wikidata_silver_dir / "5_secondary_parents.parquet", item_ids, item_ids | canonical_ids
    )
    demoted = _load_demoted_edges(manual_regional_secondary_parents_path, extra_edges)
    tree = {
        "allowsMultiplePrimaryParents": True,
        **build_genre_tree(
            hierarchy,
            primary_parents=edges_to_parent_map(extra_edges.join(demoted, on=["item_id", "parent_id"], how="anti")),
            secondary_parents=edges_to_parent_map(extra_edges.join(demoted, on=["item_id", "parent_id"], how="semi")),
            external_ids=canonical_ids,
        ),
    }

    schema = json.loads(GENRE_TREE_SCHEMA_PATH.read_text())
    try:
        validate(tree, schema)
    except ValidationError as e:
        raise ValueError(f"regional genre tree failed schema validation ({GENRE_TREE_SCHEMA_PATH}): {e.message}")

    output_dir.mkdir(parents=True, exist_ok=True)
    output_path = output_dir / "1_regional_genre_tree.json"
    output_path.write_text(json.dumps(tree, indent=2, ensure_ascii=False))
    logger.info("wrote %d root(s) to %s", len(tree["tree"]), output_path)
    return output_path
