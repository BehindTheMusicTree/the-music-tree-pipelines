from pathlib import Path

import polars as pl

from wikidata.silver import regional_hierarchy as rh
from wikidata.silver.hierarchy_utils import OUTPUT_COLUMNS

CANONICAL_PARENTS_ROWS = [
    # rock music: non-regional, never appears in the regional output
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
    # music of Kenya: seed item, no parent of its own — appears as a root of the regional output
    # (not dropped, not excluded, just no longer a "genre")
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
    # music of Cape Verde: seed item, no parent of its own — appears as a root of the regional output
    {
        "item_id": "Q1053970",
        "item_label": "music of Cape Verde",
        "parent_id": None,
        "parent_label": None,
        "relation_type": None,
        "item_url": "https://www.wikidata.org/wiki/Q1053970",
        "parent_url": None,
        "is_regional_overview": True,
        "classification_reason": "regional_overview",
        "parent_is_canonical": None,
        "is_regional": True,
        "regional_reason": "seed",
    },
    # morna: direct regional genre, only parent is the seed itself — the seed is now a real node in
    # the regional output, so morna keeps its real parent edge instead of being promoted to a root
    {
        "item_id": "Q1198360",
        "item_label": "morna",
        "parent_id": "Q1053970",
        "parent_label": "music of Cape Verde",
        "relation_type": "P279",
        "item_url": "https://www.wikidata.org/wiki/Q1198360",
        "parent_url": "https://www.wikidata.org/wiki/Q1053970",
        "is_regional_overview": False,
        "classification_reason": None,
        "parent_is_canonical": False,
        "is_regional": True,
        "regional_reason": "direct",
    },
    # fado: inherited regional genre, parent is morna (already regional) — kept under morna in the
    # regional output
    {
        "item_id": "Q182142",
        "item_label": "fado",
        "parent_id": "Q1198360",
        "parent_label": "morna",
        "relation_type": "P279",
        "item_url": "https://www.wikidata.org/wiki/Q182142",
        "parent_url": "https://www.wikidata.org/wiki/Q1198360",
        "is_regional_overview": False,
        "classification_reason": None,
        "parent_is_canonical": True,
        "is_regional": True,
        "regional_reason": "inherited",
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


def test_prune_regional_hierarchy_items_land_in_regional_output(tmp_path: Path) -> None:
    canonical_parents_path = _write_canonical_parents(tmp_path)
    output_dir = tmp_path / "silver"

    regional_path = rh.prune_regional_hierarchy(canonical_parents_path, output_dir)

    assert regional_path == output_dir / "8_regional_hierarchy.parquet"

    regional_df = pl.read_parquet(regional_path)
    assert regional_df.columns == OUTPUT_COLUMNS

    parent_by_item = {row["item_id"]: row["parent_id"] for row in regional_df.to_dicts()}
    assert parent_by_item == {
        # both seeds are real root nodes in the regional output now, not dropped
        "Q3868594": None,
        "Q1053970": None,
        # morna keeps its real parent edge into the seed instead of being promoted to a root
        "Q1198360": "Q1053970",
        "Q182142": "Q1198360",  # fado kept under morna
    }
    # non-regional items never appear in the regional output
    assert "Q11399" not in parent_by_item
    assert "Q9778" not in parent_by_item

    # orphan-promoted seeds still get a populated item_url and a null parent_url
    urls_by_item = {row["item_id"]: (row["item_url"], row["parent_url"]) for row in regional_df.to_dicts()}
    assert urls_by_item["Q3868594"] == ("https://www.wikidata.org/wiki/Q3868594", None)
    assert urls_by_item["Q1053970"] == ("https://www.wikidata.org/wiki/Q1053970", None)


def test_prune_regional_hierarchy_creates_output_dir(tmp_path: Path) -> None:
    canonical_parents_path = _write_canonical_parents(tmp_path)
    output_dir = tmp_path / "does" / "not" / "exist"

    rh.prune_regional_hierarchy(canonical_parents_path, output_dir)

    assert output_dir.is_dir()
