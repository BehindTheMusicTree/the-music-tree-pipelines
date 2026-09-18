"""On-demand flattening of the canonical genre tree export into genre-tree-view's playground fixture.

Not run as part of `gold.__main__` — invoked directly (by the `infrastructure` repo's daily
pipeline run) after `export_canonical_genre_tree` and `genre_match` have produced
`1_canonical_genre_tree.json` and `1_genre_match.parquet`, mirroring
`musicbrainz/scripts/export_songs_json.py`'s on-demand precedent. Reads that nested
`{"tree": [...]}` export and writes the flat `GenreTreeNode[]` shape
(`packages/genre-tree-view/src/types.ts` in the `genre-tree-view` repo) genre-tree-view's
playground consumes, with `itemCount` per node rolled up from `1_genre_match.parquet`'s resolved
song matches (a node's count includes its own direct matches plus every descendant's).

Usage:
    uv run --package gold python -m gold.playground_fixture_export <canonical_tree.json> <genre_match.parquet> <output.json>
"""

import argparse
import json
import logging
from pathlib import Path

import polars as pl
from jsonschema import ValidationError, validate

from gold.canonical_genre_tree_export import GENRE_TREE_SCHEMA_PATH
from gold.genre_slug import slugify_genre_name

logger = logging.getLogger(__name__)


def _direct_item_counts(genre_match_path: Path) -> dict[str, int]:
    matched = pl.read_parquet(genre_match_path)
    resolved = matched.filter(~pl.col("match_method").is_in(["unmatched", "accepted_non_genre"]))
    counts = resolved.group_by("wikidata_genre_name").len()
    return dict(counts.iter_rows())


def _flatten(
    nodes: list[dict], parent_id: str | None, seen_ids: dict[str, str], direct_counts: dict[str, int]
) -> tuple[list[dict], int]:
    flat: list[dict] = []
    subtree_total = 0
    for node in nodes:
        node_id = slugify_genre_name(node["name"])
        if node_id in seen_ids and seen_ids[node_id] != node["name"]:
            raise ValueError(f"genre names {seen_ids[node_id]!r} and {node['name']!r} both slugify to id {node_id!r}")
        seen_ids[node_id] = node["name"]

        child_flat, child_total = _flatten(node["children"], node_id, seen_ids, direct_counts)
        item_count = direct_counts.get(node["name"], 0) + child_total

        flat_node = {"id": node_id, "parentId": parent_id, "name": node["name"], "itemCount": item_count}
        if "side" in node:
            flat_node["side"] = node["side"]
        flat.append(flat_node)
        flat.extend(child_flat)
        subtree_total += item_count
    return flat, subtree_total


def export_playground_fixture(canonical_tree_path: Path, genre_match_path: Path, output_path: Path) -> Path:
    logger.info("flattening canonical genre tree %s for the genre-tree-view playground", canonical_tree_path)
    tree = json.loads(canonical_tree_path.read_text())

    schema = json.loads(GENRE_TREE_SCHEMA_PATH.read_text())
    try:
        validate(tree, schema)
    except ValidationError as e:
        raise ValueError(f"canonical genre tree failed schema validation ({GENRE_TREE_SCHEMA_PATH}): {e.message}")

    direct_counts = _direct_item_counts(genre_match_path)
    fixture, _ = _flatten(tree["tree"], parent_id=None, seen_ids={}, direct_counts=direct_counts)

    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(json.dumps(fixture, indent=2, ensure_ascii=False))
    logger.info("wrote %d node(s) to %s", len(fixture), output_path)
    return output_path


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)

    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("canonical_tree", type=Path)
    parser.add_argument("genre_match", type=Path)
    parser.add_argument("output", type=Path)
    args = parser.parse_args()

    export_playground_fixture(args.canonical_tree, args.genre_match, args.output)
