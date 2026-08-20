from pathlib import Path

import polars as pl

from wikidata.silver import genre_parents as sg

CLASSIFICATION_ROWS = [
    # rock music -> popular music: popular music is a real genre (is_genre=True)
    {
        "item_id": "Q11399",
        "item_label": "rock music",
        "parent_id": "Q9778",
        "parent_label": "popular music",
        "is_genre": True,
        "exclusion_reason": None,
    },
    # popular music: root item, no parent
    {
        "item_id": "Q9778",
        "item_label": "popular music",
        "parent_id": None,
        "parent_label": None,
        "is_genre": True,
        "exclusion_reason": None,
    },
    # opera -> composed musical work: parent isn't in the genre extension at all
    {
        "item_id": "Q1344",
        "item_label": "opera",
        "parent_id": "Q207628",
        "parent_label": "composed musical work",
        "is_genre": True,
        "exclusion_reason": None,
    },
    # some subgenre -> music of Kenya: parent is in the genre extension but excluded by step 1
    {
        "item_id": "Q999999",
        "item_label": "some subgenre",
        "parent_id": "Q3868594",
        "parent_label": "music of Kenya",
        "is_genre": True,
        "exclusion_reason": None,
    },
    {
        "item_id": "Q3868594",
        "item_label": "music of Kenya",
        "parent_id": None,
        "parent_label": None,
        "is_genre": False,
        "exclusion_reason": "regional_overview",
    },
]


def _write_classification(tmp_path: Path) -> Path:
    classification_path = tmp_path / "1_classification.parquet"
    pl.DataFrame(CLASSIFICATION_ROWS).write_parquet(classification_path)
    return classification_path


def test_flag_genre_parents_marks_parent_status(tmp_path: Path) -> None:
    classification_path = _write_classification(tmp_path)
    output_dir = tmp_path / "silver"

    result = sg.flag_genre_parents(classification_path, output_dir)

    assert result == output_dir / "2_genre_parents.parquet"
    parent_is_genre_by_item = {row["item_id"]: row["parent_is_genre"] for row in pl.read_parquet(result).to_dicts()}
    assert parent_is_genre_by_item == {
        "Q11399": True,  # parent (popular music) is_genre=True
        "Q9778": None,  # root item, no parent
        "Q1344": False,  # parent not in the genre extension at all
        "Q999999": False,  # parent in the genre extension but is_genre=False
        "Q3868594": None,  # root item, no parent
    }


def test_flag_genre_parents_creates_output_dir(tmp_path: Path) -> None:
    classification_path = _write_classification(tmp_path)
    output_dir = tmp_path / "does" / "not" / "exist"

    sg.flag_genre_parents(classification_path, output_dir)

    assert output_dir.is_dir()
