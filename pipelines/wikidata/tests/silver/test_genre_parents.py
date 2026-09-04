from pathlib import Path

import polars as pl
import pytest

from wikidata.silver import genre_parents as sg

REGIONAL_CLASSIFICATION_ROWS = [
    # rock music -> popular music: popular music is a real genre (is_regional_overview=False)
    {
        "item_id": "Q11399",
        "item_label": "rock music",
        "parent_id": "Q9778",
        "parent_label": "popular music",
        "item_url": "https://www.wikidata.org/wiki/Q11399",
        "parent_url": "https://www.wikidata.org/wiki/Q9778",
        "is_regional_overview": False,
        "classification_reason": None,
        "is_regional": False,
        "regional_reason": None,
        "relation_type": "P279",
    },
    # popular music: root item, no parent
    {
        "item_id": "Q9778",
        "item_label": "popular music",
        "parent_id": None,
        "parent_label": None,
        "item_url": "https://www.wikidata.org/wiki/Q9778",
        "parent_url": None,
        "is_regional_overview": False,
        "classification_reason": None,
        "is_regional": False,
        "regional_reason": None,
        "relation_type": None,
    },
    # opera -> composed musical work: parent isn't in the genre extension at all
    {
        "item_id": "Q1344",
        "item_label": "opera",
        "parent_id": "Q207628",
        "parent_label": "composed musical work",
        "item_url": "https://www.wikidata.org/wiki/Q1344",
        "parent_url": "https://www.wikidata.org/wiki/Q207628",
        "is_regional_overview": False,
        "classification_reason": None,
        "is_regional": False,
        "regional_reason": None,
        "relation_type": "P279",
    },
    # some subgenre -> music of Kenya: parent is in the genre extension but tagged non-genre
    {
        "item_id": "Q999999",
        "item_label": "some subgenre",
        "parent_id": "Q3868594",
        "parent_label": "music of Kenya",
        "item_url": "https://www.wikidata.org/wiki/Q999999",
        "parent_url": "https://www.wikidata.org/wiki/Q3868594",
        "is_regional_overview": False,
        "classification_reason": None,
        "is_regional": True,
        "regional_reason": "direct",
        "relation_type": "P279",
    },
    {
        "item_id": "Q3868594",
        "item_label": "music of Kenya",
        "parent_id": None,
        "parent_label": None,
        "item_url": "https://www.wikidata.org/wiki/Q3868594",
        "parent_url": None,
        "is_regional_overview": True,
        "classification_reason": "regional_overview",
        "is_regional": True,
        "regional_reason": "seed",
        "relation_type": None,
    },
]


def _write_regional_classification(tmp_path: Path) -> Path:
    regional_classification_path = tmp_path / "3_regional_classification.parquet"
    pl.DataFrame(REGIONAL_CLASSIFICATION_ROWS).write_parquet(regional_classification_path)
    return regional_classification_path


def _write_manual_canonical_parents(tmp_path: Path) -> Path:
    manual_canonical_parents_path = tmp_path / "manual_canonical_parents.csv"
    pl.DataFrame(
        schema={"item_id": pl.Utf8, "item_label": pl.Utf8, "reason": pl.Utf8, "parent_item_id": pl.Utf8}
    ).write_csv(manual_canonical_parents_path)
    return manual_canonical_parents_path


def test_flag_genre_parents_marks_parent_status(tmp_path: Path) -> None:
    regional_classification_path = _write_regional_classification(tmp_path)
    manual_canonical_parents_path = _write_manual_canonical_parents(tmp_path)
    output_dir = tmp_path / "silver"

    result = sg.flag_genre_parents(regional_classification_path, manual_canonical_parents_path, output_dir)

    assert result == output_dir / "4_genre_parents.parquet"
    parent_is_genre_by_item = {row["item_id"]: row["parent_is_genre"] for row in pl.read_parquet(result).to_dicts()}
    assert parent_is_genre_by_item == {
        "Q11399": True,  # parent (popular music) is_regional_overview=False
        "Q9778": None,  # root item, no parent
        "Q1344": False,  # parent not in the genre extension at all
        "Q999999": False,  # parent in the genre extension but is_regional_overview=True
        "Q3868594": None,  # root item, no parent
    }


def test_flag_genre_parents_applies_manual_canonical_parent_override(tmp_path: Path) -> None:
    regional_classification_path = _write_regional_classification(tmp_path)
    manual_canonical_parents_path = tmp_path / "manual_canonical_parents.csv"
    pl.DataFrame(
        {
            "item_id": ["Q9778"],
            "item_label": ["popular music"],
            "reason": ["test override"],
            "parent_item_id": ["Q1344"],
        }
    ).write_csv(manual_canonical_parents_path)
    output_dir = tmp_path / "silver"

    result = sg.flag_genre_parents(regional_classification_path, manual_canonical_parents_path, output_dir)

    rows_by_item = {row["item_id"]: row for row in pl.read_parquet(result).to_dicts()}
    overridden = rows_by_item["Q9778"]
    assert overridden["parent_id"] == "Q1344"
    assert overridden["parent_label"] == "opera"
    assert overridden["relation_type"] == "manual_canonical_parent"
    assert overridden["parent_is_genre"] is True


def test_flag_genre_parents_raises_on_missing_parent_item_id_column(tmp_path: Path) -> None:
    regional_classification_path = _write_regional_classification(tmp_path)
    manual_canonical_parents_path = tmp_path / "manual_canonical_parents.csv"
    pl.DataFrame({"item_id": ["Q9778"], "item_label": ["popular music"], "reason": ["test"]}).write_csv(
        manual_canonical_parents_path
    )
    output_dir = tmp_path / "silver"

    with pytest.raises(ValueError, match="parent_item_id"):
        sg.flag_genre_parents(regional_classification_path, manual_canonical_parents_path, output_dir)


def test_flag_genre_parents_raises_on_missing_item_id_column(tmp_path: Path) -> None:
    regional_classification_path = _write_regional_classification(tmp_path)
    manual_canonical_parents_path = tmp_path / "manual_canonical_parents.csv"
    pl.DataFrame({"item_label": ["popular music"], "reason": ["test"], "parent_item_id": ["Q1344"]}).write_csv(
        manual_canonical_parents_path
    )
    output_dir = tmp_path / "silver"

    with pytest.raises(ValueError, match="item_id"):
        sg.flag_genre_parents(regional_classification_path, manual_canonical_parents_path, output_dir)


def test_flag_genre_parents_raises_on_unknown_item_id(tmp_path: Path) -> None:
    regional_classification_path = _write_regional_classification(tmp_path)
    manual_canonical_parents_path = tmp_path / "manual_canonical_parents.csv"
    pl.DataFrame(
        {
            "item_id": ["Q0000000"],
            "item_label": ["not in the tree"],
            "reason": ["test"],
            "parent_item_id": ["Q1344"],
        }
    ).write_csv(manual_canonical_parents_path)
    output_dir = tmp_path / "silver"

    with pytest.raises(ValueError, match="Q0000000"):
        sg.flag_genre_parents(regional_classification_path, manual_canonical_parents_path, output_dir)


def test_flag_genre_parents_raises_on_unknown_parent_item_id(tmp_path: Path) -> None:
    regional_classification_path = _write_regional_classification(tmp_path)
    manual_canonical_parents_path = tmp_path / "manual_canonical_parents.csv"
    pl.DataFrame(
        {
            "item_id": ["Q9778"],
            "item_label": ["popular music"],
            "reason": ["test"],
            "parent_item_id": ["Q0000000"],
        }
    ).write_csv(manual_canonical_parents_path)
    output_dir = tmp_path / "silver"

    with pytest.raises(ValueError, match="Q0000000"):
        sg.flag_genre_parents(regional_classification_path, manual_canonical_parents_path, output_dir)


def test_flag_genre_parents_raises_on_parent_item_id_is_regional_overview(tmp_path: Path) -> None:
    regional_classification_path = _write_regional_classification(tmp_path)
    manual_canonical_parents_path = tmp_path / "manual_canonical_parents.csv"
    pl.DataFrame(
        {
            "item_id": ["Q9778"],
            "item_label": ["popular music"],
            "reason": ["test"],
            "parent_item_id": ["Q3868594"],
        }
    ).write_csv(manual_canonical_parents_path)
    output_dir = tmp_path / "silver"

    with pytest.raises(ValueError, match="Q3868594"):
        sg.flag_genre_parents(regional_classification_path, manual_canonical_parents_path, output_dir)


def test_flag_genre_parents_raises_on_blank_item_id(tmp_path: Path) -> None:
    regional_classification_path = _write_regional_classification(tmp_path)
    manual_canonical_parents_path = tmp_path / "manual_canonical_parents.csv"
    pl.DataFrame(
        {"item_id": [""], "item_label": ["blank id"], "reason": ["test"], "parent_item_id": ["Q1344"]}
    ).write_csv(manual_canonical_parents_path)
    output_dir = tmp_path / "silver"

    with pytest.raises(ValueError, match="null/blank"):
        sg.flag_genre_parents(regional_classification_path, manual_canonical_parents_path, output_dir)


def test_flag_genre_parents_raises_on_override_for_regional_item(tmp_path: Path) -> None:
    regional_classification_path = _write_regional_classification(tmp_path)
    manual_canonical_parents_path = tmp_path / "manual_canonical_parents.csv"
    pl.DataFrame(
        {
            "item_id": ["Q3868594"],  # music of Kenya, is_regional_overview=True
            "item_label": ["music of Kenya"],
            "reason": ["test"],
            "parent_item_id": ["Q1344"],
        }
    ).write_csv(manual_canonical_parents_path)
    output_dir = tmp_path / "silver"

    with pytest.raises(ValueError, match="Q3868594"):
        sg.flag_genre_parents(regional_classification_path, manual_canonical_parents_path, output_dir)


def test_flag_genre_parents_raises_on_override_for_non_root_item(tmp_path: Path) -> None:
    regional_classification_path = _write_regional_classification(tmp_path)
    manual_canonical_parents_path = tmp_path / "manual_canonical_parents.csv"
    pl.DataFrame(
        {
            "item_id": ["Q11399"],  # rock music, already has a parent edge to popular music
            "item_label": ["rock music"],
            "reason": ["test"],
            "parent_item_id": ["Q1344"],
        }
    ).write_csv(manual_canonical_parents_path)
    output_dir = tmp_path / "silver"

    with pytest.raises(ValueError, match="Q11399"):
        sg.flag_genre_parents(regional_classification_path, manual_canonical_parents_path, output_dir)


def test_flag_genre_parents_creates_output_dir(tmp_path: Path) -> None:
    regional_classification_path = _write_regional_classification(tmp_path)
    manual_canonical_parents_path = _write_manual_canonical_parents(tmp_path)
    output_dir = tmp_path / "does" / "not" / "exist"

    sg.flag_genre_parents(regional_classification_path, manual_canonical_parents_path, output_dir)

    assert output_dir.is_dir()
