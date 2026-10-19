import json
import logging
from pathlib import Path

import polars as pl
from jsonschema import ValidationError, validate

from gold.genre_tree_builder import (
    apply_secondary_parent_rules,
    build_genre_tree,
    edges_to_parent_map,
    load_extra_parent_edges,
    load_tree_curation,
)

GENRE_TREE_SCHEMA_PATH = Path(__file__).parent / "schemas" / "genre_tree.schema.json"

logger = logging.getLogger(__name__)


def export_regional_genre_tree(
    wikidata_silver_dir: Path,
    output_dir: Path,
    curation_dir: Path,
    curation_exported_on: str,
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
    curation = load_tree_curation(curation_dir, item_ids, item_ids | canonical_ids)
    # An extra parent edge feeds tracks to its parent by default (primaryParents); a curated `add`
    # makes it classification only (secondaryParents).
    primary_edges, secondary_edges = apply_secondary_parent_rules(
        extra_edges, curation.secondary_parent_rules, hierarchy, item_ids | canonical_ids
    )
    tree = {
        "treeName": "regional",
        "curationExportedOn": curation_exported_on,
        **build_genre_tree(
            hierarchy,
            primary_parents=edges_to_parent_map(primary_edges),
            secondary_parents=edges_to_parent_map(secondary_edges),
            external_ids=canonical_ids,
            essential_tracks=curation.essential_tracks,
            summaries=curation.summaries,
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
