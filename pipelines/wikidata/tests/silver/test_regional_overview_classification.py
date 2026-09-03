from pathlib import Path

import polars as pl

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


def test_classify_regional_from_overviews_flags_regional_overview_items(tmp_path: Path) -> None:
    item_links_path = _write_item_links(tmp_path)
    output_dir = tmp_path / "silver"

    result = sc.classify_regional_from_overviews(item_links_path, output_dir)

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
    output_dir = tmp_path / "does" / "not" / "exist"

    sc.classify_regional_from_overviews(item_links_path, output_dir)

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
    output_dir = tmp_path / "silver"

    result = sc.classify_regional_from_overviews(item_links_path, output_dir)

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
