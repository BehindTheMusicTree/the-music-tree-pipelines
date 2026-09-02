from pathlib import Path

import polars as pl

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
]


def _write_hierarchy(tmp_path: Path) -> Path:
    hierarchy_path = tmp_path / "5_hierarchy.parquet"
    pl.DataFrame(HIERARCHY_ROWS).write_parquet(hierarchy_path)
    return hierarchy_path


def test_extract_canonical_roots_keeps_only_parentless_items(tmp_path: Path) -> None:
    hierarchy_path = _write_hierarchy(tmp_path)
    output_dir = tmp_path / "silver"

    result = sr.extract_canonical_roots(hierarchy_path, output_dir)

    assert result == output_dir / "6_canonical_roots.parquet"
    rows = pl.read_parquet(result).to_dicts()
    assert rows == [
        {
            "item_id": "Q9778",
            "item_label": "popular music",
            "item_url": "https://www.wikidata.org/wiki/Q9778",
        },
        {
            "item_id": "Q11399",
            "item_label": "rock music",
            "item_url": "https://www.wikidata.org/wiki/Q11399",
        },
    ]


def test_extract_canonical_roots_creates_output_dir(tmp_path: Path) -> None:
    hierarchy_path = _write_hierarchy(tmp_path)
    output_dir = tmp_path / "does" / "not" / "exist"

    sr.extract_canonical_roots(hierarchy_path, output_dir)

    assert output_dir.is_dir()
