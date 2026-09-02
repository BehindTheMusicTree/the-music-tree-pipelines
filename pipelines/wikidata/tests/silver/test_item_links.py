from pathlib import Path

import polars as pl

from wikidata.silver import item_links as sl

BRONZE_ROWS = [
    {"item_id": "Q11399", "item_label": "rock music", "parent_id": "Q9778", "parent_label": "popular music"},
    {"item_id": "Q9778", "item_label": "popular music", "parent_id": None, "parent_label": None},
    {"item_id": "Q132733254", "item_label": "Q132733254", "parent_id": "Q9778", "parent_label": "Q9778"},
]


def _write_bronze(tmp_path: Path) -> Path:
    bronze_path = tmp_path / "wikidata_genre_tree.parquet"
    pl.DataFrame(BRONZE_ROWS).write_parquet(bronze_path)
    return bronze_path


def test_add_item_links_derives_urls_from_qids(tmp_path: Path) -> None:
    bronze_path = _write_bronze(tmp_path)
    output_dir = tmp_path / "silver"

    result = sl.add_item_links(bronze_path, output_dir)

    assert result == output_dir / "1_item_links.parquet"
    rows = pl.read_parquet(result).sort("item_id").to_dicts()
    assert rows == [
        {
            "item_id": "Q11399",
            "item_label": "rock music",
            "parent_id": "Q9778",
            "parent_label": "popular music",
            "item_url": "https://www.wikidata.org/wiki/Q11399",
            "parent_url": "https://www.wikidata.org/wiki/Q9778",
            "has_item_label": True,
            "has_parent_label": True,
        },
        {
            "item_id": "Q132733254",
            "item_label": "Q132733254",
            "parent_id": "Q9778",
            "parent_label": "Q9778",
            "item_url": "https://www.wikidata.org/wiki/Q132733254",
            "parent_url": "https://www.wikidata.org/wiki/Q9778",
            "has_item_label": False,
            "has_parent_label": False,
        },
        {
            "item_id": "Q9778",
            "item_label": "popular music",
            "parent_id": None,
            "parent_label": None,
            "item_url": "https://www.wikidata.org/wiki/Q9778",
            "parent_url": None,
            "has_item_label": True,
            "has_parent_label": None,
        },
    ]


def test_add_item_links_creates_output_dir(tmp_path: Path) -> None:
    bronze_path = _write_bronze(tmp_path)
    output_dir = tmp_path / "does" / "not" / "exist"

    sl.add_item_links(bronze_path, output_dir)

    assert output_dir.is_dir()
