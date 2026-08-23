from pathlib import Path

import polars as pl

from wikidata.silver import genre_classification as sc

BRONZE_ROWS = [
    {"item_id": "Q11399", "item_label": "rock music", "parent_id": "Q9778", "parent_label": "popular music"},
    {"item_id": "Q3868594", "item_label": "music of Kenya", "parent_id": None, "parent_label": None},
]


def _write_bronze(tmp_path: Path) -> Path:
    bronze_path = tmp_path / "wikidata_genre_tree.parquet"
    pl.DataFrame(BRONZE_ROWS).write_parquet(bronze_path)
    return bronze_path


def test_classify_genre_tree_flags_regional_overview_items(tmp_path: Path) -> None:
    bronze_path = _write_bronze(tmp_path)
    output_dir = tmp_path / "silver"

    result = sc.classify_regional_from_overviews(bronze_path, output_dir)

    assert result == output_dir / "1_regional_overview_classification.parquet"
    rows = pl.read_parquet(result).sort("item_id").to_dicts()
    assert rows == [
        {
            "item_id": "Q11399",
            "item_label": "rock music",
            "parent_id": "Q9778",
            "parent_label": "popular music",
            "is_genre": True,
            "classification_reason": None,
        },
        {
            "item_id": "Q3868594",
            "item_label": "music of Kenya",
            "parent_id": None,
            "parent_label": None,
            "is_genre": False,
            "classification_reason": "regional_overview",
        },
    ]


def test_classify_genre_tree_creates_output_dir(tmp_path: Path) -> None:
    bronze_path = _write_bronze(tmp_path)
    output_dir = tmp_path / "does" / "not" / "exist"

    sc.classify_regional_from_overviews(bronze_path, output_dir)

    assert output_dir.is_dir()
