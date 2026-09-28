import logging
from pathlib import Path

import polars as pl
import pytest

from wikidata.silver import canonical_roots as sr

HIERARCHY_ROWS = [
    # rock music: root, no parent
    {
        "item_id": "Q11399",
        "item_label": "rock music",
        "item_url": "https://www.wikidata.org/wiki/Q11399",
        "parent_id": None,
        "parent_label": None,
        "parent_url": None,
        "relation_type": None,
    },
    # popular music: root, no parent
    {
        "item_id": "Q9778",
        "item_label": "popular music",
        "item_url": "https://www.wikidata.org/wiki/Q9778",
        "parent_id": None,
        "parent_label": None,
        "parent_url": None,
        "relation_type": None,
    },
    # heavy metal: has a parent, not a root
    {
        "item_id": "Q483352",
        "item_label": "heavy metal",
        "item_url": "https://www.wikidata.org/wiki/Q483352",
        "parent_id": "Q11399",
        "parent_label": "rock music",
        "parent_url": "https://www.wikidata.org/wiki/Q11399",
        "relation_type": "P279",
    },
    # jazz: parent_id points at "Q373342" (popular music), which has no row of its own in this
    # file (a dead-end/phantom parent) -- jazz is a root too, not just items with a null parent_id
    {
        "item_id": "Q8341",
        "item_label": "jazz",
        "item_url": "https://www.wikidata.org/wiki/Q8341",
        "parent_id": "Q373342",
        "parent_label": "popular music",
        "parent_url": "https://www.wikidata.org/wiki/Q373342",
        "relation_type": "P279",
    },
]


def _write_hierarchy(tmp_path: Path) -> Path:
    hierarchy_path = tmp_path / "7_canonical_hierarchy.parquet"
    pl.DataFrame(HIERARCHY_ROWS).write_parquet(hierarchy_path)
    return hierarchy_path


def _write_accepted_roots(tmp_path: Path, item_ids: list[str]) -> Path:
    accepted_roots_path = tmp_path / "manual_accepted_canonical_roots.csv"
    pl.DataFrame({"item_id": item_ids, "item_label": item_ids}).write_csv(accepted_roots_path)
    return accepted_roots_path


def test_extract_canonical_roots_keeps_only_parentless_items(tmp_path: Path) -> None:
    hierarchy_path = _write_hierarchy(tmp_path)
    accepted_roots_path = _write_accepted_roots(tmp_path, ["Q8341", "Q9778", "Q11399"])
    output_dir = tmp_path / "silver"

    result = sr.extract_canonical_roots(hierarchy_path, accepted_roots_path, output_dir)

    assert result == output_dir / "9_canonical_roots.parquet"
    rows = pl.read_parquet(result).to_dicts()
    assert rows == [
        {
            "item_id": "Q8341",
            "item_label": "jazz",
            "item_url": "https://www.wikidata.org/wiki/Q8341",
            "is_accepted": True,
        },
        {
            "item_id": "Q9778",
            "item_label": "popular music",
            "item_url": "https://www.wikidata.org/wiki/Q9778",
            "is_accepted": True,
        },
        {
            "item_id": "Q11399",
            "item_label": "rock music",
            "item_url": "https://www.wikidata.org/wiki/Q11399",
            "is_accepted": True,
        },
    ]


def test_extract_canonical_roots_creates_output_dir(tmp_path: Path) -> None:
    hierarchy_path = _write_hierarchy(tmp_path)
    accepted_roots_path = _write_accepted_roots(tmp_path, ["Q8341", "Q9778", "Q11399"])
    output_dir = tmp_path / "does" / "not" / "exist"

    sr.extract_canonical_roots(hierarchy_path, accepted_roots_path, output_dir)

    assert output_dir.is_dir()


def test_extract_canonical_roots_flags_unaccepted_new_root(tmp_path: Path, caplog: pytest.LogCaptureFixture) -> None:
    hierarchy_path = _write_hierarchy(tmp_path)
    accepted_roots_path = _write_accepted_roots(tmp_path, ["Q9778", "Q11399"])
    output_dir = tmp_path / "silver"

    with caplog.at_level(logging.WARNING):
        result = sr.extract_canonical_roots(hierarchy_path, accepted_roots_path, output_dir)

    flags = dict(pl.read_parquet(result).select("item_id", "is_accepted").iter_rows())
    assert flags == {"Q8341": False, "Q9778": True, "Q11399": True}
    assert "1 unaccepted canonical root(s), flagged for review: ['Q8341 (jazz)']" in caplog.text
