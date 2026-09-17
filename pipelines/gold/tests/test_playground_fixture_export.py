import json
from pathlib import Path

import polars as pl
import pytest

from gold.playground_fixture_export import export_playground_fixture

TREE = {
    "tree": [
        {
            "name": "Electronic",
            "children": [
                {"name": "Techno", "children": []},
                {"name": "Electropop", "side": "pop", "children": []},
            ],
        },
        {"name": "Mainstream Pop", "children": []},
    ]
}

GENRE_MATCH_ROWS = [
    {
        "title": "A",
        "artist": "X",
        "youtube_video_id": "a",
        "genre_name": "techno",
        "wikidata_genre_name": "Techno",
        "match_method": "exact",
    },
    {
        "title": "B",
        "artist": "X",
        "youtube_video_id": "b",
        "genre_name": "techno",
        "wikidata_genre_name": "Techno",
        "match_method": "exact",
    },
    {
        "title": "C",
        "artist": "X",
        "youtube_video_id": "c",
        "genre_name": "electropop",
        "wikidata_genre_name": "Electropop",
        "match_method": "exact",
    },
    {
        "title": "D",
        "artist": "X",
        "youtube_video_id": "d",
        "genre_name": "some tag",
        "wikidata_genre_name": None,
        "match_method": "unmatched",
    },
    {
        "title": "E",
        "artist": "X",
        "youtube_video_id": "e",
        "genre_name": "asmr",
        "wikidata_genre_name": None,
        "match_method": "accepted_non_genre",
    },
]


def _write_tree(tmp_path: Path, tree: dict = TREE) -> Path:
    path = tmp_path / "1_canonical_genre_tree.json"
    path.write_text(json.dumps(tree))
    return path


_GENRE_MATCH_SCHEMA = {
    "title": pl.Utf8,
    "artist": pl.Utf8,
    "youtube_video_id": pl.Utf8,
    "genre_name": pl.Utf8,
    "match_method": pl.Utf8,
    "wikidata_genre_name": pl.Utf8,
}


def _write_genre_match(tmp_path: Path, rows: list[dict] | None = None) -> Path:
    path = tmp_path / "1_genre_match.parquet"
    data = rows if rows is not None else GENRE_MATCH_ROWS
    pl.DataFrame(data, schema=_GENRE_MATCH_SCHEMA).write_parquet(path)
    return path


def test_export_playground_fixture_flattens_nested_tree(tmp_path: Path) -> None:
    tree_path = _write_tree(tmp_path)
    genre_match_path = _write_genre_match(tmp_path, rows=[])

    result = export_playground_fixture(tree_path, genre_match_path, tmp_path / "playground" / "genre-tree.json")

    fixture = json.loads(result.read_text())
    assert fixture == [
        {"id": "electronic", "parentId": None, "name": "Electronic", "itemCount": 0},
        {"id": "techno", "parentId": "electronic", "name": "Techno", "itemCount": 0},
        {
            "id": "electropop",
            "parentId": "electronic",
            "name": "Electropop",
            "itemCount": 0,
            "side": "pop",
        },
        {"id": "mainstream-pop", "parentId": None, "name": "Mainstream Pop", "itemCount": 0},
    ]


def test_export_playground_fixture_rolls_up_item_counts_to_ancestors(tmp_path: Path) -> None:
    tree_path = _write_tree(tmp_path)
    genre_match_path = _write_genre_match(tmp_path)

    result = export_playground_fixture(tree_path, genre_match_path, tmp_path / "genre-tree.json")

    fixture = {node["id"]: node["itemCount"] for node in json.loads(result.read_text())}
    assert fixture == {"electronic": 3, "techno": 2, "electropop": 1, "mainstream-pop": 0}


def test_export_playground_fixture_creates_output_dir(tmp_path: Path) -> None:
    tree_path = _write_tree(tmp_path)
    genre_match_path = _write_genre_match(tmp_path, rows=[])
    output_path = tmp_path / "does" / "not" / "exist" / "genre-tree.json"

    export_playground_fixture(tree_path, genre_match_path, output_path)

    assert output_path.is_file()


def test_export_playground_fixture_raises_on_schema_violation(tmp_path: Path) -> None:
    tree_path = _write_tree(tmp_path, tree={"tree": [{"name": "Rock"}]})
    genre_match_path = _write_genre_match(tmp_path, rows=[])

    with pytest.raises(ValueError, match="schema validation"):
        export_playground_fixture(tree_path, genre_match_path, tmp_path / "genre-tree.json")


def test_export_playground_fixture_raises_on_slug_collision(tmp_path: Path) -> None:
    tree_path = _write_tree(
        tmp_path,
        tree={"tree": [{"name": "R&B", "children": []}, {"name": "R B", "children": []}]},
    )
    genre_match_path = _write_genre_match(tmp_path, rows=[])

    with pytest.raises(ValueError, match="both slugify to id"):
        export_playground_fixture(tree_path, genre_match_path, tmp_path / "genre-tree.json")
