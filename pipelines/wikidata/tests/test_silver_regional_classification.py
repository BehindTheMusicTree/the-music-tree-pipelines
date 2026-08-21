from pathlib import Path

import polars as pl

from wikidata.silver import regional_classification as sr

GENRE_PARENTS_ROWS = [
    # music of Portugal: the seed itself — a regional_overview item, not a genre
    {
        "item_id": "Q2579987",
        "item_label": "music of Portugal",
        "parent_id": None,
        "parent_label": None,
        "relation_type": None,
        "is_genre": False,
        "exclusion_reason": "regional_overview",
        "parent_is_genre": None,
    },
    # Portuguese folk music: multi-parent — one edge into the seed (direct), one into a clean genre
    # parent. Still regional overall: ANY parent being regional is enough, not ALL.
    {
        "item_id": "Q106556293",
        "item_label": "Portuguese folk music",
        "parent_id": "Q2579987",
        "parent_label": "music of Portugal",
        "relation_type": "P279",
        "is_genre": True,
        "exclusion_reason": None,
        "parent_is_genre": False,
    },
    {
        "item_id": "Q106556293",
        "item_label": "Portuguese folk music",
        "parent_id": "Q98528192",
        "parent_label": "European folk music",
        "relation_type": "P279",
        "is_genre": True,
        "exclusion_reason": None,
        "parent_is_genre": True,
    },
    # fado: two hops from the seed via Portuguese folk music — inherited, not direct
    {
        "item_id": "Q185676",
        "item_label": "fado",
        "parent_id": "Q106556293",
        "parent_label": "Portuguese folk music",
        "relation_type": "P279",
        "is_genre": True,
        "exclusion_reason": None,
        "parent_is_genre": True,
    },
    # jazz -> popular music: no regional ancestor anywhere, stays clean
    {
        "item_id": "Q8341",
        "item_label": "jazz",
        "parent_id": "Q9778",
        "parent_label": "popular music",
        "relation_type": "P279",
        "is_genre": True,
        "exclusion_reason": None,
        "parent_is_genre": True,
    },
    # popular music: root item, no parent, clean
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
]


def _write_genre_parents(tmp_path: Path) -> Path:
    genre_parents_path = tmp_path / "2_genre_parents.parquet"
    pl.DataFrame(GENRE_PARENTS_ROWS).write_parquet(genre_parents_path)
    return genre_parents_path


def test_classify_regional_genres_cascades_from_seeds(tmp_path: Path) -> None:
    genre_parents_path = _write_genre_parents(tmp_path)
    output_dir = tmp_path / "silver"

    result = sr.classify_regional_genres(genre_parents_path, output_dir)

    assert result == output_dir / "3_regional_classification.parquet"
    df = pl.read_parquet(result)

    by_item = {row["item_id"]: (row["is_regional"], row["regional_reason"]) for row in df.unique("item_id").to_dicts()}
    assert by_item == {
        "Q2579987": (None, None),  # non-genre seed item, concept doesn't apply
        "Q106556293": (True, "direct"),  # multi-parent, one regional edge is enough
        "Q185676": (True, "inherited"),  # fado, two hops from the seed
        "Q8341": (False, None),  # jazz, no regional ancestor
        "Q9778": (False, None),  # root item, no parent
    }


def test_classify_regional_genres_creates_output_dir(tmp_path: Path) -> None:
    genre_parents_path = _write_genre_parents(tmp_path)
    output_dir = tmp_path / "does" / "not" / "exist"

    sr.classify_regional_genres(genre_parents_path, output_dir)

    assert output_dir.is_dir()
