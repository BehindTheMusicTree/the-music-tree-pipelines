from pathlib import Path

import polars as pl
import pytest

from wikidata.silver import regional_overview_classification as sc

ITEM_LINKS_ROWS = [
    {
        "item_id": "Q11399",
        "item_label": "rock music",
        "parent_id": "Q9778",
        "parent_label": "popular music",
        "item_url": "https://www.wikidata.org/wiki/Q11399",
        "parent_url": "https://www.wikidata.org/wiki/Q9778",
    },
    {
        "item_id": "Q3868594",
        "item_label": "music of Kenya",
        "parent_id": None,
        "parent_label": None,
        "item_url": "https://www.wikidata.org/wiki/Q3868594",
        "parent_url": None,
    },
]


def _write_item_links(tmp_path: Path) -> Path:
    item_links_path = tmp_path / "1_item_links.parquet"
    pl.DataFrame(ITEM_LINKS_ROWS).write_parquet(item_links_path)
    return item_links_path


def _write_manual_additions(tmp_path: Path, rows: list[dict] | None = None) -> Path:
    manual_additions_path = tmp_path / "manual_regional_overview_additions.csv"
    pl.DataFrame(rows or [], schema={"item_id": pl.Utf8, "item_label": pl.Utf8, "reason": pl.Utf8}).write_csv(
        manual_additions_path
    )
    return manual_additions_path


def test_classify_regional_from_overviews_flags_regional_overview_items(tmp_path: Path) -> None:
    item_links_path = _write_item_links(tmp_path)
    manual_additions_path = _write_manual_additions(tmp_path)
    output_dir = tmp_path / "silver"

    result = sc.classify_regional_from_overviews(item_links_path, manual_additions_path, output_dir)

    assert result == output_dir / "2_regional_overview_classification.parquet"
    rows = pl.read_parquet(result).sort("item_id").to_dicts()
    assert rows == [
        {
            "item_id": "Q11399",
            "item_label": "rock music",
            "parent_id": "Q9778",
            "parent_label": "popular music",
            "item_url": "https://www.wikidata.org/wiki/Q11399",
            "parent_url": "https://www.wikidata.org/wiki/Q9778",
            "is_regional_overview": False,
            "classification_reason": None,
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
        },
    ]


def test_classify_regional_from_overviews_creates_output_dir(tmp_path: Path) -> None:
    item_links_path = _write_item_links(tmp_path)
    manual_additions_path = _write_manual_additions(tmp_path)
    output_dir = tmp_path / "does" / "not" / "exist"

    sc.classify_regional_from_overviews(item_links_path, manual_additions_path, output_dir)

    assert output_dir.is_dir()


def test_classify_regional_from_overviews_promotes_orphan_music_of_parent(tmp_path: Path) -> None:
    item_links_path = tmp_path / "1_item_links.parquet"
    pl.DataFrame(
        [
            *ITEM_LINKS_ROWS,
            {
                "item_id": "Q28371127",
                "item_label": "cymrucana",
                "parent_id": "Q6942327",
                "parent_label": "music of Wales",
                "item_url": "https://www.wikidata.org/wiki/Q28371127",
                "parent_url": "https://www.wikidata.org/wiki/Q6942327",
            },
        ]
    ).write_parquet(item_links_path)
    manual_additions_path = _write_manual_additions(tmp_path)
    output_dir = tmp_path / "silver"

    result = sc.classify_regional_from_overviews(item_links_path, manual_additions_path, output_dir)

    rows = pl.read_parquet(result).sort("item_id").to_dicts()
    promoted = next(row for row in rows if row["item_id"] == "Q6942327")
    assert promoted == {
        "item_id": "Q6942327",
        "item_label": "music of Wales",
        "parent_id": None,
        "parent_label": None,
        "item_url": "https://www.wikidata.org/wiki/Q6942327",
        "parent_url": None,
        "is_regional_overview": True,
        "classification_reason": "regional_overview",
    }


def test_classify_regional_from_overviews_adds_manual_overview_item_missing_from_bronze(tmp_path: Path) -> None:
    item_links_path = _write_item_links(tmp_path)
    manual_additions_path = _write_manual_additions(
        tmp_path,
        [
            {
                "item_id": "Q16147503",
                "item_label": "music of Dominica",
                "reason": "never P31 music genre nor a parent_label in Bronze; real Wikidata QID looked up by hand",
            }
        ],
    )
    output_dir = tmp_path / "silver"

    result = sc.classify_regional_from_overviews(item_links_path, manual_additions_path, output_dir)

    rows = pl.read_parquet(result).sort("item_id").to_dicts()
    added = next(row for row in rows if row["item_id"] == "Q16147503")
    assert added == {
        "item_id": "Q16147503",
        "item_label": "music of Dominica",
        "parent_id": None,
        "parent_label": None,
        "item_url": "https://www.wikidata.org/wiki/Q16147503",
        "parent_url": None,
        "is_regional_overview": True,
        "classification_reason": "regional_overview",
    }


def test_classify_regional_from_overviews_adds_manual_overview_item_with_synthetic_id(tmp_path: Path) -> None:
    item_links_path = _write_item_links(tmp_path)
    manual_additions_path = _write_manual_additions(
        tmp_path,
        [
            {
                "item_id": "LOCAL:indigenous-americas",
                "item_label": "music of Indigenous peoples of the Americas",
                "reason": "synthetic (no matching Wikidata overview item)",
            }
        ],
    )
    output_dir = tmp_path / "silver"

    result = sc.classify_regional_from_overviews(item_links_path, manual_additions_path, output_dir)

    rows = pl.read_parquet(result).sort("item_id").to_dicts()
    added = next(row for row in rows if row["item_id"] == "LOCAL:indigenous-americas")
    assert added == {
        "item_id": "LOCAL:indigenous-americas",
        "item_label": "music of Indigenous peoples of the Americas",
        "parent_id": None,
        "parent_label": None,
        "item_url": "https://www.wikidata.org/wiki/LOCAL:indigenous-americas",
        "parent_url": None,
        "is_regional_overview": True,
        "classification_reason": "regional_overview",
    }


def test_classify_regional_from_overviews_rejects_manual_addition_without_music_of_prefix(tmp_path: Path) -> None:
    item_links_path = _write_item_links(tmp_path)
    manual_additions_path = _write_manual_additions(
        tmp_path, [{"item_id": "Q1", "item_label": "not a regional overview", "reason": "bad row"}]
    )
    output_dir = tmp_path / "silver"

    with pytest.raises(ValueError, match="item_label"):
        sc.classify_regional_from_overviews(item_links_path, manual_additions_path, output_dir)


def test_classify_regional_from_overviews_rejects_manual_addition_with_blank_item_label(tmp_path: Path) -> None:
    item_links_path = _write_item_links(tmp_path)
    manual_additions_path = _write_manual_additions(
        tmp_path, [{"item_id": "Q1", "item_label": None, "reason": "bad row"}]
    )
    output_dir = tmp_path / "silver"

    with pytest.raises(ValueError, match="blank"):
        sc.classify_regional_from_overviews(item_links_path, manual_additions_path, output_dir)


def test_classify_regional_from_overviews_rejects_manual_addition_with_empty_item_label(tmp_path: Path) -> None:
    item_links_path = _write_item_links(tmp_path)
    manual_additions_path = _write_manual_additions(
        tmp_path, [{"item_id": "Q1", "item_label": "  ", "reason": "bad row"}]
    )
    output_dir = tmp_path / "silver"

    with pytest.raises(ValueError, match="blank"):
        sc.classify_regional_from_overviews(item_links_path, manual_additions_path, output_dir)


def test_classify_regional_from_overviews_rejects_manual_addition_with_empty_item_id(tmp_path: Path) -> None:
    item_links_path = _write_item_links(tmp_path)
    manual_additions_path = _write_manual_additions(
        tmp_path, [{"item_id": "", "item_label": "music of nowhere", "reason": "bad row"}]
    )
    output_dir = tmp_path / "silver"

    with pytest.raises(ValueError, match="blank"):
        sc.classify_regional_from_overviews(item_links_path, manual_additions_path, output_dir)


def test_classify_regional_from_overviews_rejects_manual_addition_already_in_bronze(tmp_path: Path) -> None:
    item_links_path = _write_item_links(tmp_path)
    manual_additions_path = _write_manual_additions(
        tmp_path, [{"item_id": "Q3868594", "item_label": "music of Kenya", "reason": "already present"}]
    )
    output_dir = tmp_path / "silver"

    with pytest.raises(ValueError, match="already present"):
        sc.classify_regional_from_overviews(item_links_path, manual_additions_path, output_dir)


def test_classify_regional_from_overviews_rejects_manual_addition_already_in_bronze_with_whitespace(
    tmp_path: Path,
) -> None:
    item_links_path = _write_item_links(tmp_path)
    manual_additions_path = _write_manual_additions(
        tmp_path, [{"item_id": " Q3868594 ", "item_label": " music of Kenya ", "reason": "already present"}]
    )
    output_dir = tmp_path / "silver"

    with pytest.raises(ValueError, match="already present"):
        sc.classify_regional_from_overviews(item_links_path, manual_additions_path, output_dir)
