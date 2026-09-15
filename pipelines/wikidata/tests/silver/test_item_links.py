from pathlib import Path

import polars as pl
import pytest

from wikidata.silver import item_links as sl

BRONZE_ROWS = [
    {"item_id": "Q11399", "item_label": "rock music", "parent_id": "Q9778", "parent_label": "popular music"},
    {"item_id": "Q9778", "item_label": "popular music", "parent_id": None, "parent_label": None},
    {"item_id": "Q132733254", "item_label": "Q132733254", "parent_id": "Q999999999", "parent_label": "Q999999999"},
]


def _write_bronze(tmp_path: Path) -> Path:
    bronze_path = tmp_path / "wikidata_genre_tree.parquet"
    pl.DataFrame(BRONZE_ROWS).write_parquet(bronze_path)
    return bronze_path


def _write_overrides(tmp_path: Path, rows: list[dict] | None = None) -> Path:
    overrides_path = tmp_path / "manual_label_overrides.csv"
    schema = {"item_id": pl.Utf8, "display_label": pl.Utf8, "reason": pl.Utf8}
    pl.DataFrame(rows or [], schema=schema).write_csv(overrides_path)
    return overrides_path


def test_add_item_links_derives_urls_from_qids(tmp_path: Path) -> None:
    bronze_path = _write_bronze(tmp_path)
    overrides_path = _write_overrides(tmp_path)
    output_dir = tmp_path / "silver"

    result = sl.add_item_links(bronze_path, output_dir, overrides_path)

    assert result == output_dir / "1_item_links.parquet"
    rows = pl.read_parquet(result).sort("item_id").to_dicts()
    assert rows == [
        {
            "item_id": "Q11399",
            "item_label": "rock music",
            "item_display_label": "rock music",
            "parent_id": "Q9778",
            "parent_label": "popular music",
            "parent_display_label": "popular music",
            "item_url": "https://www.wikidata.org/wiki/Q11399",
            "parent_url": "https://www.wikidata.org/wiki/Q9778",
            "has_item_label": True,
            "has_parent_label": True,
        },
        {
            "item_id": "Q132733254",
            "item_label": "Q132733254",
            "item_display_label": "Q132733254",
            "parent_id": "Q999999999",
            "parent_label": "Q999999999",
            "parent_display_label": "Q999999999",
            "item_url": "https://www.wikidata.org/wiki/Q132733254",
            "parent_url": "https://www.wikidata.org/wiki/Q999999999",
            "has_item_label": False,
            "has_parent_label": False,
        },
        {
            "item_id": "Q9778",
            "item_label": "popular music",
            "item_display_label": "popular music",
            "parent_id": None,
            "parent_label": None,
            "parent_display_label": None,
            "item_url": "https://www.wikidata.org/wiki/Q9778",
            "parent_url": None,
            "has_item_label": True,
            "has_parent_label": None,
        },
    ]


def test_add_item_links_creates_output_dir(tmp_path: Path) -> None:
    bronze_path = _write_bronze(tmp_path)
    overrides_path = _write_overrides(tmp_path)
    output_dir = tmp_path / "does" / "not" / "exist"

    sl.add_item_links(bronze_path, output_dir, overrides_path)

    assert output_dir.is_dir()


def test_add_item_links_applies_display_label_override(tmp_path: Path) -> None:
    bronze_path = _write_bronze(tmp_path)
    overrides_path = _write_overrides(
        tmp_path, [{"item_id": "Q9778", "display_label": "Mainstream Pop", "reason": "curated rename"}]
    )
    output_dir = tmp_path / "silver"

    result = sl.add_item_links(bronze_path, output_dir, overrides_path)

    rows = pl.read_parquet(result).sort("item_id").to_dicts()
    item_row = next(r for r in rows if r["item_id"] == "Q9778")
    assert item_row["item_label"] == "popular music"
    assert item_row["item_display_label"] == "Mainstream Pop"

    child_row = next(r for r in rows if r["item_id"] == "Q11399")
    assert child_row["parent_label"] == "popular music"
    assert child_row["parent_display_label"] == "Mainstream Pop"


def test_add_item_links_raises_on_unknown_override_item_id(tmp_path: Path) -> None:
    bronze_path = _write_bronze(tmp_path)
    overrides_path = _write_overrides(tmp_path, [{"item_id": "Q999", "display_label": "Nope", "reason": "x"}])
    output_dir = tmp_path / "silver"

    with pytest.raises(ValueError, match="not found in the genre tree"):
        sl.add_item_links(bronze_path, output_dir, overrides_path)


def test_add_item_links_raises_on_duplicate_override_item_id(tmp_path: Path) -> None:
    bronze_path = _write_bronze(tmp_path)
    overrides_path = _write_overrides(
        tmp_path,
        [
            {"item_id": "Q9778", "display_label": "A", "reason": "x"},
            {"item_id": "Q9778", "display_label": "B", "reason": "y"},
        ],
    )
    output_dir = tmp_path / "silver"

    with pytest.raises(ValueError, match="duplicate item_id"):
        sl.add_item_links(bronze_path, output_dir, overrides_path)


def test_add_item_links_raises_on_blank_display_label(tmp_path: Path) -> None:
    bronze_path = _write_bronze(tmp_path)
    overrides_path = _write_overrides(tmp_path, [{"item_id": "Q9778", "display_label": "", "reason": "x"}])
    output_dir = tmp_path / "silver"

    with pytest.raises(ValueError, match="blank 'display_label'"):
        sl.add_item_links(bronze_path, output_dir, overrides_path)
