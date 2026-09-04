from pathlib import Path

import polars as pl
import pytest

from wikidata.silver import hierarchy as sh

GENRE_PARENTS_ROWS = [
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
        "item_url": "https://www.wikidata.org/wiki/Q9778",
        "parent_url": None,
        "is_regional_overview": False,
        "classification_reason": None,
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
        "item_url": "https://www.wikidata.org/wiki/Q1344",
        "parent_url": "https://www.wikidata.org/wiki/Q207628",
        "is_regional_overview": False,
        "classification_reason": None,
        "parent_is_genre": False,
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
        "parent_is_genre": None,
        "is_regional": True,
        "regional_reason": "seed",
    },
    # some subgenre: one genre parent (kept) + one non-genre parent (dropped) — canonical only,
    # is_regional set directly here (this file tests hierarchy.py in isolation, not the cascade)
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
        "item_url": "https://www.wikidata.org/wiki/Q999999",
        "parent_url": "https://www.wikidata.org/wiki/Q3868594",
        "is_regional_overview": False,
        "classification_reason": None,
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
        "item_url": "https://www.wikidata.org/wiki/Q42",
        "parent_url": "https://www.wikidata.org/wiki/Q100",
        "is_regional_overview": False,
        "classification_reason": None,
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
        "item_url": "https://www.wikidata.org/wiki/Q42",
        "parent_url": "https://www.wikidata.org/wiki/Q9",
        "is_regional_overview": False,
        "classification_reason": None,
        "parent_is_genre": True,
        "is_regional": False,
        "regional_reason": None,
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
        "parent_is_genre": None,
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
        "item_url": "https://www.wikidata.org/wiki/Q182142",
        "parent_url": "https://www.wikidata.org/wiki/Q1198360",
        "is_regional_overview": False,
        "classification_reason": None,
        "parent_is_genre": True,
        "is_regional": True,
        "regional_reason": "inherited",
    },
]


def _write_genre_parents(tmp_path: Path, rows: list[dict] = GENRE_PARENTS_ROWS) -> Path:
    genre_parents_path = tmp_path / "4_genre_parents.parquet"
    pl.DataFrame(rows).write_parquet(genre_parents_path)
    return genre_parents_path


def _write_manual_theme_genres(tmp_path: Path, rows: list[dict]) -> Path:
    manual_theme_genres_path = tmp_path / "manual_theme_genres.csv"
    schema = {"item_id": pl.Utf8, "item_label": pl.Utf8, "reason": pl.Utf8}
    pl.DataFrame(rows, schema=schema).write_csv(manual_theme_genres_path)
    return manual_theme_genres_path


def test_prune_genre_hierarchy_keeps_single_parent_per_item(tmp_path: Path) -> None:
    genre_parents_path = _write_genre_parents(tmp_path)
    manual_theme_genres_path = _write_manual_theme_genres(tmp_path, [])
    output_dir = tmp_path / "silver"

    canonical_path, regional_path = sh.prune_genre_hierarchy(genre_parents_path, manual_theme_genres_path, output_dir)

    assert canonical_path == output_dir / "5_hierarchy.parquet"
    assert regional_path == output_dir / "5_regional_hierarchy.parquet"

    canonical_df = pl.read_parquet(canonical_path)
    assert canonical_df.columns == sh.OUTPUT_COLUMNS

    parent_by_item = {row["item_id"]: row["parent_id"] for row in canonical_df.to_dicts()}
    assert parent_by_item == {
        "Q11399": "Q9778",  # genre -> genre parent
        "Q9778": None,  # root item
        "Q999999": "Q11399",  # non-genre parent edge dropped, genre parent edge survives
        "Q42": "Q9",  # lowest numeric QID wins over Q100
    }
    # opera (genre -> non-genre parent) and music of Kenya (seed item) both vanish entirely
    assert "Q1344" not in parent_by_item
    assert "Q3868594" not in parent_by_item
    # regional items never appear in the canonical output
    assert "Q1198360" not in parent_by_item
    assert "Q182142" not in parent_by_item

    # item_url is always populated; parent_url follows parent_id
    urls_by_item = {row["item_id"]: (row["item_url"], row["parent_url"]) for row in canonical_df.to_dicts()}
    assert urls_by_item["Q11399"] == ("https://www.wikidata.org/wiki/Q11399", "https://www.wikidata.org/wiki/Q9778")
    assert urls_by_item["Q9778"] == ("https://www.wikidata.org/wiki/Q9778", None)


def test_prune_genre_hierarchy_regional_items_land_in_regional_output(tmp_path: Path) -> None:
    genre_parents_path = _write_genre_parents(tmp_path)
    manual_theme_genres_path = _write_manual_theme_genres(tmp_path, [])
    output_dir = tmp_path / "silver"

    _, regional_path = sh.prune_genre_hierarchy(genre_parents_path, manual_theme_genres_path, output_dir)

    regional_df = pl.read_parquet(regional_path)
    assert regional_df.columns == sh.OUTPUT_COLUMNS

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


def test_prune_genre_hierarchy_creates_output_dir(tmp_path: Path) -> None:
    genre_parents_path = _write_genre_parents(tmp_path)
    manual_theme_genres_path = _write_manual_theme_genres(tmp_path, [])
    output_dir = tmp_path / "does" / "not" / "exist"

    sh.prune_genre_hierarchy(genre_parents_path, manual_theme_genres_path, output_dir)

    assert output_dir.is_dir()


def test_prune_genre_hierarchy_drops_theme_items_from_both_trees(tmp_path: Path) -> None:
    genre_parents_path = _write_genre_parents(tmp_path)
    # Q11399 (rock music) is canonical, Q1198360 (morna) is regional — flag both as themes.
    manual_theme_genres_path = _write_manual_theme_genres(
        tmp_path,
        [
            {"item_id": "Q11399", "item_label": "rock music", "reason": "test"},
            {"item_id": "Q1198360", "item_label": "morna", "reason": "test"},
        ],
    )
    output_dir = tmp_path / "silver"

    canonical_path, regional_path = sh.prune_genre_hierarchy(genre_parents_path, manual_theme_genres_path, output_dir)

    canonical_df = pl.read_parquet(canonical_path)
    canonical_parent_by_item = {row["item_id"]: row["parent_id"] for row in canonical_df.to_dicts()}
    # the theme item itself is gone entirely
    assert "Q11399" not in canonical_parent_by_item
    # its child's edge into the theme parent is severed, dropping the child too (mirrors the
    # existing genre -> non-genre parent behavior, e.g. opera above)
    assert "Q999999" not in canonical_parent_by_item

    regional_df = pl.read_parquet(regional_path)
    regional_parent_by_item = {row["item_id"]: row["parent_id"] for row in regional_df.to_dicts()}
    # the theme item itself is gone entirely
    assert "Q1198360" not in regional_parent_by_item
    # its child (fado) loses that parent edge and is promoted to an orphan root instead of vanishing
    assert regional_parent_by_item["Q182142"] is None


def test_prune_genre_hierarchy_raises_on_unknown_theme_item_id(tmp_path: Path) -> None:
    genre_parents_path = _write_genre_parents(tmp_path)
    manual_theme_genres_path = _write_manual_theme_genres(
        tmp_path, [{"item_id": "Q0000000", "item_label": "not in the tree", "reason": "test"}]
    )
    output_dir = tmp_path / "silver"

    with pytest.raises(ValueError, match="Q0000000"):
        sh.prune_genre_hierarchy(genre_parents_path, manual_theme_genres_path, output_dir)


def test_prune_genre_hierarchy_raises_on_blank_theme_item_id(tmp_path: Path) -> None:
    genre_parents_path = _write_genre_parents(tmp_path)
    manual_theme_genres_path = _write_manual_theme_genres(
        tmp_path, [{"item_id": "", "item_label": "blank id", "reason": "test"}]
    )
    output_dir = tmp_path / "silver"

    with pytest.raises(ValueError, match="null/blank"):
        sh.prune_genre_hierarchy(genre_parents_path, manual_theme_genres_path, output_dir)
