from pathlib import Path

import polars as pl

from gold.umbrella_candidates import export_umbrella_candidates


def test_export_umbrella_candidates_ranks_non_umbrella_parents_by_direct_songs(tmp_path: Path) -> None:
    hierarchy_path = tmp_path / "7_canonical_hierarchy.parquet"
    pl.DataFrame(
        {
            "item_id": ["Q1", "Q2", "Q3", "Q4", "Q5"],
            "item_label": ["techno", "detroit techno", "acid techno", "house", "rock"],
            "parent_id": [None, "Q1", "Q2", None, None],
            "is_umbrella": [False, False, False, True, False],
        }
    ).write_parquet(hierarchy_path)
    genre_match_path = tmp_path / "1_genre_match.parquet"
    pl.DataFrame({"wikidata_genre_item_id": ["Q1", "Q1", "Q1", "Q2", "Q3", None, "Q4"]}).write_parquet(genre_match_path)

    result = export_umbrella_candidates(hierarchy_path, genre_match_path, tmp_path / "gold")

    assert pl.read_csv(result).to_dicts() == [
        {"item_id": "Q1", "item_label": "techno", "child_count": 1, "direct_song_count": 3, "descendant_song_count": 2},
        {
            "item_id": "Q2",
            "item_label": "detroit techno",
            "child_count": 1,
            "direct_song_count": 1,
            "descendant_song_count": 1,
        },
    ]
