from pathlib import Path

import polars as pl

from wikidata.silver import hierarchy as sh

REGIONAL_CLASSIFICATION_ROWS = [
    # rock music -> popular music: genre -> genre parent, kept in canonical
    {
        "item_id": "Q11399",
        "item_label": "rock music",
        "parent_id": "Q9778",
        "parent_label": "popular music",
        "relation_type": "P279",
        "is_genre": True,
        "exclusion_reason": None,
        "parent_is_genre": True,
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
        "is_genre": True,
        "exclusion_reason": None,
        "parent_is_genre": None,
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
        "is_genre": True,
        "exclusion_reason": None,
        "parent_is_genre": False,
        "is_regional": False,
        "regional_reason": None,
    },
    # music of Kenya: non-genre item, dropped even though it's a root
    {
        "item_id": "Q3868594",
        "item_label": "music of Kenya",
        "parent_id": None,
        "parent_label": None,
        "relation_type": None,
        "is_genre": False,
        "exclusion_reason": "regional_overview",
        "parent_is_genre": None,
        "is_regional": None,
        "regional_reason": None,
    },
    # some subgenre: one genre parent (kept) + one non-genre parent (dropped)
    {
        "item_id": "Q999999",
        "item_label": "some subgenre",
        "parent_id": "Q11399",
        "parent_label": "rock music",
        "relation_type": "P279",
        "is_genre": True,
        "exclusion_reason": None,
        "parent_is_genre": True,
        "is_regional": False,
        "regional_reason": None,
    },
    {
        "item_id": "Q999999",
        "item_label": "some subgenre",
        "parent_id": "Q3868594",
        "parent_label": "music of Kenya",
        "relation_type": "P279",
        "is_genre": True,
        "exclusion_reason": None,
        "parent_is_genre": False,
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
        "is_genre": True,
        "exclusion_reason": None,
        "parent_is_genre": True,
        "is_regional": False,
        "regional_reason": None,
    },
    {
        "item_id": "Q42",
        "item_label": "some multi-parent genre",
        "parent_id": "Q9",
        "parent_label": "another genre parent",
        "relation_type": "P279",
        "is_genre": True,
        "exclusion_reason": None,
        "parent_is_genre": True,
        "is_regional": False,
        "regional_reason": None,
    },
    # music of Cape Verde: regional_overview seed
    {
        "item_id": "Q1053970",
        "item_label": "music of Cape Verde",
        "parent_id": None,
        "parent_label": None,
        "relation_type": None,
        "is_genre": False,
        "exclusion_reason": "regional_overview",
        "parent_is_genre": None,
        "is_regional": None,
        "regional_reason": None,
    },
    # morna: direct regional genre, only parent is the (non-genre) seed itself — excluded from
    # canonical, and promoted to a root within the regional output rather than vanishing
    {
        "item_id": "Q1198360",
        "item_label": "morna",
        "parent_id": "Q1053970",
        "parent_label": "music of Cape Verde",
        "relation_type": "P279",
        "is_genre": True,
        "exclusion_reason": None,
        "parent_is_genre": False,
        "is_regional": True,
        "regional_reason": "direct",
    },
    # fado: inherited regional genre, parent is morna (already regional) — kept under morna in the
    # regional output, excluded from canonical
    {
        "item_id": "Q182142",
        "item_label": "fado",
        "parent_id": "Q1198360",
        "parent_label": "morna",
        "relation_type": "P279",
        "is_genre": True,
        "exclusion_reason": None,
        "parent_is_genre": True,
        "is_regional": True,
        "regional_reason": "inherited",
    },
]


def _write_regional_classification(tmp_path: Path) -> Path:
    regional_classification_path = tmp_path / "3_regional_classification.parquet"
    pl.DataFrame(REGIONAL_CLASSIFICATION_ROWS).write_parquet(regional_classification_path)
    return regional_classification_path


def test_prune_genre_hierarchy_keeps_single_parent_per_item(tmp_path: Path) -> None:
    regional_classification_path = _write_regional_classification(tmp_path)
    output_dir = tmp_path / "silver"

    canonical_path, regional_path = sh.prune_genre_hierarchy(regional_classification_path, output_dir)

    assert canonical_path == output_dir / "4_hierarchy.parquet"
    assert regional_path == output_dir / "4_regional_hierarchy.parquet"

    canonical_df = pl.read_parquet(canonical_path)
    assert canonical_df.columns == sh.OUTPUT_COLUMNS

    parent_by_item = {row["item_id"]: row["parent_id"] for row in canonical_df.to_dicts()}
    assert parent_by_item == {
        "Q11399": "Q9778",  # genre -> genre parent
        "Q9778": None,  # root item
        "Q999999": "Q11399",  # non-genre parent edge dropped, genre parent edge survives
        "Q42": "Q9",  # lowest numeric QID wins over Q100
    }
    # opera (genre -> non-genre parent) and music of Kenya (non-genre item) both vanish entirely
    assert "Q1344" not in parent_by_item
    assert "Q3868594" not in parent_by_item
    # regional items never appear in the canonical output
    assert "Q1198360" not in parent_by_item
    assert "Q182142" not in parent_by_item


def test_prune_genre_hierarchy_regional_items_land_in_regional_output(tmp_path: Path) -> None:
    regional_classification_path = _write_regional_classification(tmp_path)
    output_dir = tmp_path / "silver"

    _, regional_path = sh.prune_genre_hierarchy(regional_classification_path, output_dir)

    regional_df = pl.read_parquet(regional_path)
    assert regional_df.columns == sh.OUTPUT_COLUMNS

    parent_by_item = {row["item_id"]: row["parent_id"] for row in regional_df.to_dicts()}
    assert parent_by_item == {
        # morna's only parent is the non-genre seed itself, so it's promoted to a root here
        # instead of vanishing the way opera does in the canonical graph
        "Q1198360": None,
        "Q182142": "Q1198360",  # fado kept under morna
    }
    # non-regional items never appear in the regional output
    assert "Q11399" not in parent_by_item
    assert "Q9778" not in parent_by_item


def test_prune_genre_hierarchy_creates_output_dir(tmp_path: Path) -> None:
    regional_classification_path = _write_regional_classification(tmp_path)
    output_dir = tmp_path / "does" / "not" / "exist"

    sh.prune_genre_hierarchy(regional_classification_path, output_dir)

    assert output_dir.is_dir()
