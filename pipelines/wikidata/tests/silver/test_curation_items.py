import json
from pathlib import Path

import polars as pl

from wikidata.silver.curation_items import write_curation_items

TREE_ROWS = [
    {"item_id": "Q11399", "item_label": "rock music", "parent_id": "Q9778", "parent_label": "popular music"},
    {"item_id": "Q11399", "item_label": "rock music", "parent_id": "Q5", "parent_label": "Q5"},
    {"item_id": "Q9778", "item_label": "popular music", "parent_id": None, "parent_label": None},
]


def test_write_curation_items_lists_every_labelled_item_once(tmp_path: Path) -> None:
    tree_path = tmp_path / "wikidata_genre_tree.parquet"
    pl.DataFrame(TREE_ROWS).write_parquet(tree_path)

    output_path = write_curation_items(tree_path, tmp_path / "silver")

    assert json.loads(output_path.read_text()) == {
        "items": [{"id": "Q11399", "label": "rock music"}, {"id": "Q9778", "label": "popular music"}]
    }
