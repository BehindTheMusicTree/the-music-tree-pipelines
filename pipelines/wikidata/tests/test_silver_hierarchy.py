from pathlib import Path

import polars as pl

from wikidata.silver import hierarchy as sh

GENRE_PARENTS_ROWS = [
    # rock music -> popular music: genre -> genre parent, kept
    {
        "item_id": "Q11399",
        "item_label": "rock music",
        "parent_id": "Q9778",
        "parent_label": "popular music",
        "relation_type": "P279",
        "is_genre": True,
        "exclusion_reason": None,
        "parent_is_genre": True,
    },
    # popular music: root item, no parent, kept
    {
        "item_id": "Q9778",
        "item_label": "popular music",
        "parent_id": None,
        "parent_label": None,
        "relation_type": None,
        "is_genre": True,
        "exclusion_reason": None,
        "parent_is_genre": None,
    },
    # opera -> composed musical work: genre -> non-genre parent, dropped
    {
        "item_id": "Q1344",
        "item_label": "opera",
        "parent_id": "Q207628",
        "parent_label": "composed musical work",
        "relation_type": "P279",
        "is_genre": True,
        "exclusion_reason": None,
        "parent_is_genre": False,
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
    },
]


def _write_genre_parents(tmp_path: Path) -> Path:
    genre_parents_path = tmp_path / "2_genre_parents.parquet"
    pl.DataFrame(GENRE_PARENTS_ROWS).write_parquet(genre_parents_path)
    return genre_parents_path


def test_prune_genre_hierarchy_keeps_single_parent_per_item(tmp_path: Path) -> None:
    genre_parents_path = _write_genre_parents(tmp_path)
    output_dir = tmp_path / "silver"

    result = sh.prune_genre_hierarchy(genre_parents_path, output_dir)

    assert result == output_dir / "3_hierarchy.parquet"
    df = pl.read_parquet(result)
    assert df.columns == sh.OUTPUT_COLUMNS

    parent_by_item = {row["item_id"]: row["parent_id"] for row in df.to_dicts()}
    assert parent_by_item == {
        "Q11399": "Q9778",  # genre -> genre parent
        "Q9778": None,  # root item
        "Q999999": "Q11399",  # non-genre parent edge dropped, genre parent edge survives
        "Q42": "Q9",  # lowest numeric QID wins over Q100
    }
    # opera (genre -> non-genre parent) and music of Kenya (non-genre item) both vanish entirely
    assert "Q1344" not in parent_by_item
    assert "Q3868594" not in parent_by_item


def test_prune_genre_hierarchy_creates_output_dir(tmp_path: Path) -> None:
    genre_parents_path = _write_genre_parents(tmp_path)
    output_dir = tmp_path / "does" / "not" / "exist"

    sh.prune_genre_hierarchy(genre_parents_path, output_dir)

    assert output_dir.is_dir()
