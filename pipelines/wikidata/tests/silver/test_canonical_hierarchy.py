from pathlib import Path

import polars as pl

from wikidata.silver import canonical_hierarchy as ch
from wikidata.silver.hierarchy_utils import OUTPUT_COLUMNS

CANONICAL_PARENTS_ROWS = [
    # rock music -> popular music: genre -> genre parent, kept in canonical
    {
        "item_id": "Q11399",
        "item_label": "rock music",
        "parent_id": "Q9778",
        "parent_label": "popular music",
        "relation_type": "P279",
        "item_url": "https://www.wikidata.org/wiki/Q11399",
        "parent_url": "https://www.wikidata.org/wiki/Q9778",
        "is_regional_overview": False,
        "classification_reason": None,
        "parent_is_canonical": True,
        "is_regional": False,
        "regional_reason": None,
    },
    # popular music: root item, no parent, kept in canonical
    {
        "item_id": "Q9778",
        "item_label": "popular music",
        "parent_id": None,
        "parent_label": None,
        "relation_type": None,
        "item_url": "https://www.wikidata.org/wiki/Q9778",
        "parent_url": None,
        "is_regional_overview": False,
        "classification_reason": None,
        "parent_is_canonical": None,
        "is_regional": False,
        "regional_reason": None,
    },
    # opera -> composed musical work: genre -> non-genre parent, dropped entirely (not promoted to root)
    {
        "item_id": "Q1344",
        "item_label": "opera",
        "parent_id": "Q207628",
        "parent_label": "composed musical work",
        "relation_type": "P279",
        "item_url": "https://www.wikidata.org/wiki/Q1344",
        "parent_url": "https://www.wikidata.org/wiki/Q207628",
        "is_regional_overview": False,
        "classification_reason": None,
        "parent_is_canonical": False,
        "is_regional": False,
        "regional_reason": None,
    },
    # music of Kenya: seed item, regional — never reaches the canonical output
    {
        "item_id": "Q3868594",
        "item_label": "music of Kenya",
        "parent_id": None,
        "parent_label": None,
        "relation_type": None,
        "item_url": "https://www.wikidata.org/wiki/Q3868594",
        "parent_url": None,
        "is_regional_overview": True,
        "classification_reason": "regional_overview",
        "parent_is_canonical": None,
        "is_regional": True,
        "regional_reason": "seed",
    },
    # some subgenre: one genre parent (kept) + one non-genre parent (dropped) — canonical only,
    # is_regional set directly here (this file tests canonical_hierarchy.py in isolation, not the cascade)
    {
        "item_id": "Q999999",
        "item_label": "some subgenre",
        "parent_id": "Q11399",
        "parent_label": "rock music",
        "relation_type": "P279",
        "item_url": "https://www.wikidata.org/wiki/Q999999",
        "parent_url": "https://www.wikidata.org/wiki/Q11399",
        "is_regional_overview": False,
        "classification_reason": None,
        "parent_is_canonical": True,
        "is_regional": False,
        "regional_reason": None,
    },
    {
        "item_id": "Q999999",
        "item_label": "some subgenre",
        "parent_id": "Q3868594",
        "parent_label": "music of Kenya",
        "relation_type": "P279",
        "item_url": "https://www.wikidata.org/wiki/Q999999",
        "parent_url": "https://www.wikidata.org/wiki/Q3868594",
        "is_regional_overview": False,
        "classification_reason": None,
        "parent_is_canonical": False,
        "is_regional": False,
        "regional_reason": None,
    },
    # multi-parent item with two genre-only parents: lowest numeric QID wins (Q9 over Q100, despite
    # the reverse lexicographic order)
    {
        "item_id": "Q42",
        "item_label": "some multi-parent genre",
        "parent_id": "Q100",
        "parent_label": "a genre parent",
        "relation_type": "P279",
        "item_url": "https://www.wikidata.org/wiki/Q42",
        "parent_url": "https://www.wikidata.org/wiki/Q100",
        "is_regional_overview": False,
        "classification_reason": None,
        "parent_is_canonical": True,
        "is_regional": False,
        "regional_reason": None,
    },
    {
        "item_id": "Q42",
        "item_label": "some multi-parent genre",
        "parent_id": "Q9",
        "parent_label": "another genre parent",
        "relation_type": "P279",
        "item_url": "https://www.wikidata.org/wiki/Q42",
        "parent_url": "https://www.wikidata.org/wiki/Q9",
        "is_regional_overview": False,
        "classification_reason": None,
        "parent_is_canonical": True,
        "is_regional": False,
        "regional_reason": None,
    },
]

CANONICAL_PARENTS_ROWS = [
    {**row, "item_display_label": row["item_label"], "parent_display_label": row["parent_label"]}
    for row in CANONICAL_PARENTS_ROWS
]


def _write_canonical_parents(tmp_path: Path, rows: list[dict] | None = None) -> Path:
    canonical_parents_path = tmp_path / "5_canonical_parents.parquet"
    pl.DataFrame(CANONICAL_PARENTS_ROWS if rows is None else rows).write_parquet(canonical_parents_path)
    return canonical_parents_path


def test_prune_canonical_hierarchy_keeps_single_parent_per_item(tmp_path: Path) -> None:
    canonical_parents_path = _write_canonical_parents(tmp_path)
    output_dir = tmp_path / "silver"

    canonical_path = ch.prune_canonical_hierarchy(canonical_parents_path, output_dir)

    assert canonical_path == output_dir / "7_canonical_hierarchy.parquet"

    canonical_df = pl.read_parquet(canonical_path)
    assert canonical_df.columns == OUTPUT_COLUMNS

    parent_by_item = {row["item_id"]: row["parent_id"] for row in canonical_df.to_dicts()}
    assert parent_by_item == {
        "Q11399": "Q9778",  # genre -> genre parent
        "Q9778": None,  # root item
        "Q999999": "Q11399",  # non-genre parent edge dropped, genre parent edge survives
        "Q42": "Q9",  # lowest numeric QID wins over Q100
        "Q1344": None,  # opera: genre -> non-genre parent, promoted to an orphan root
    }
    # music of Kenya (seed item, regional) never reaches the canonical output
    assert "Q3868594" not in parent_by_item

    # item_url is always populated; parent_url follows parent_id
    urls_by_item = {row["item_id"]: (row["item_url"], row["parent_url"]) for row in canonical_df.to_dicts()}
    assert urls_by_item["Q11399"] == ("https://www.wikidata.org/wiki/Q11399", "https://www.wikidata.org/wiki/Q9778")
    assert urls_by_item["Q9778"] == ("https://www.wikidata.org/wiki/Q9778", None)


def test_prune_canonical_hierarchy_creates_output_dir(tmp_path: Path) -> None:
    canonical_parents_path = _write_canonical_parents(tmp_path)
    output_dir = tmp_path / "does" / "not" / "exist"

    ch.prune_canonical_hierarchy(canonical_parents_path, output_dir)

    assert output_dir.is_dir()
