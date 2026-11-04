import json
import logging
from pathlib import Path

import polars as pl
from jsonschema import ValidationError, validate

from gold.genre_tree_builder import (
    apply_secondary_parent_rules,
    build_genre_tree,
    edges_to_parent_map,
    load_descriptions,
    load_extra_parent_edges,
    load_selected_parent_edges,
    load_tree_curation,
)

GENRE_TREE_SCHEMA_PATH = Path(__file__).parent / "schemas" / "genre_tree.schema.json"

logger = logging.getLogger(__name__)


def export_regional_genre_tree(
    wikidata_silver_dir: Path,
    output_dir: Path,
    curation_dir: Path,
    descriptions_path: Path,
    curation_exported_on: str,
) -> Path:
    hierarchy_path = wikidata_silver_dir / "8_regional_hierarchy.parquet"
    logger.info("building regional genre tree from %s", hierarchy_path)
    hierarchy = pl.read_parquet(hierarchy_path)

    # Imported after the canonical tree, so a ref may resolve against either tree's rows.
    item_ids = set(hierarchy.get_column("item_id"))
    canonical_hierarchy = pl.read_parquet(wikidata_silver_dir / "7_canonical_hierarchy.parquet")
    canonical_ids = set(canonical_hierarchy.get_column("item_id"))
    ref_ids = item_ids | canonical_ids
    parent_selection_path = wikidata_silver_dir / "5_parent_selection.parquet"
    # A parent picked on another axis than the anchor (the canonical parent, the other overviews) feeds
    # tracks to it (primaryParents); a secondary parent is classification only (secondaryParents), as is
    # a curated `add`.
    selected_edges = load_selected_parent_edges(parent_selection_path, item_ids, ref_ids)
    secondary_candidates = load_extra_parent_edges(
        wikidata_silver_dir / "5_secondary_parents.parquet", item_ids, ref_ids
    )
    curation = load_tree_curation(curation_dir, item_ids, ref_ids)
    untouched_edges, added_edges = apply_secondary_parent_rules(
        pl.concat([selected_edges, secondary_candidates]), curation.secondary_parent_rules, hierarchy, ref_ids
    )
    primary_edges = untouched_edges.join(selected_edges, on=["item_id", "parent_id"], how="semi")
    secondary_edges = pl.concat(
        [untouched_edges.join(secondary_candidates, on=["item_id", "parent_id"], how="semi"), added_edges]
    )

    # The canonical import runs before the regional rows exist, so a canonical item's regional parents
    # (only a canonical_overrides item keeps any) ride on this import as crossTreeSecondaryParents.
    canonical_curation = load_tree_curation(curation_dir, canonical_ids, ref_ids)
    cross_untouched, cross_added = apply_secondary_parent_rules(
        pl.concat(
            [
                load_selected_parent_edges(parent_selection_path, canonical_ids, item_ids),
                load_extra_parent_edges(wikidata_silver_dir / "5_secondary_parents.parquet", canonical_ids, item_ids),
            ]
        ),
        canonical_curation.secondary_parent_rules.filter(pl.col("parent_id").is_in(list(item_ids))),
        canonical_hierarchy,
        item_ids,
    )
    cross_tree_parents = edges_to_parent_map(pl.concat([cross_untouched, cross_added]))

    tree = {
        "treeName": "regional",
        "curationExportedOn": curation_exported_on,
        **build_genre_tree(
            hierarchy,
            primary_parents=edges_to_parent_map(primary_edges),
            secondary_parents=edges_to_parent_map(secondary_edges),
            external_ids=canonical_ids,
            essential_songs=curation.essential_songs,
            summaries=curation.summaries,
            scenes=curation.scenes,
            descriptions=load_descriptions(descriptions_path),
        ),
        "crossTreeSecondaryParents": [
            {"id": item_id, "secondaryParents": parent_ids}
            for item_id, parent_ids in sorted(cross_tree_parents.items())
        ],
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
