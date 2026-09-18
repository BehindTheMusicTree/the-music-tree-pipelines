import json
from pathlib import Path

import polars as pl
import pytest

from gold.canonical_genre_tree_export import export_canonical_genre_tree

TREE_ROWS = [
    {"item_id": "Q1", "item_label": "rock", "parent_id": None},
    {"item_id": "Q2", "item_label": "punk rock", "parent_id": "Q1"},
    {"item_id": "Q3", "item_label": "hardcore punk", "parent_id": "Q2"},
    {"item_id": "Q4", "item_label": "pop rock", "parent_id": "Q1"},
    {"item_id": "Q5", "item_label": "jazz", "parent_id": None},
    {"item_id": "Q7", "item_label": "Mainstream Pop", "parent_id": None},
]


def _hierarchy() -> pl.DataFrame:
    return pl.DataFrame(TREE_ROWS)


def _write_pop_side_csv(path: Path, rows: list[dict[str, str]]) -> None:
    columns = ["root_genre_name", "pop_child_genre_name", "reason"]
    data = {column: [row[column] for row in rows] for column in columns}
    pl.DataFrame(data, schema={column: pl.Utf8 for column in columns}).write_csv(path)


def test_export_canonical_genre_tree_writes_tree_shape(tmp_path: Path) -> None:
    wikidata_silver_dir = tmp_path / "wikidata_silver"
    wikidata_silver_dir.mkdir()
    _hierarchy().write_parquet(wikidata_silver_dir / "7_canonical_hierarchy.parquet")
    output_dir = tmp_path / "gold"
    pop_side_path = tmp_path / "manual_canonical_genre_pop_side.csv"
    _write_pop_side_csv(pop_side_path, [])

    result = export_canonical_genre_tree(wikidata_silver_dir, output_dir, pop_side_path)

    assert result == output_dir / "1_canonical_genre_tree.json"
    tree = json.loads(result.read_text())
    assert {node["name"] for node in tree["tree"]} == {"rock", "jazz", "Mainstream Pop"}


def test_export_canonical_genre_tree_creates_output_dir(tmp_path: Path) -> None:
    wikidata_silver_dir = tmp_path / "wikidata_silver"
    wikidata_silver_dir.mkdir()
    _hierarchy().write_parquet(wikidata_silver_dir / "7_canonical_hierarchy.parquet")
    output_dir = tmp_path / "does" / "not" / "exist"
    pop_side_path = tmp_path / "manual_canonical_genre_pop_side.csv"
    _write_pop_side_csv(pop_side_path, [])

    export_canonical_genre_tree(wikidata_silver_dir, output_dir, pop_side_path)

    assert output_dir.is_dir()


def test_export_canonical_genre_tree_raises_on_schema_violation(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import gold.canonical_genre_tree_export as module

    monkeypatch.setattr(
        module,
        "build_genre_tree",
        lambda hierarchy, pop_sides: {"tree": [{"name": "rock"}, {"name": "Mainstream Pop"}]},
    )
    wikidata_silver_dir = tmp_path / "wikidata_silver"
    wikidata_silver_dir.mkdir()
    _hierarchy().write_parquet(wikidata_silver_dir / "7_canonical_hierarchy.parquet")
    pop_side_path = tmp_path / "manual_canonical_genre_pop_side.csv"
    _write_pop_side_csv(pop_side_path, [])

    with pytest.raises(ValueError, match="schema validation"):
        module.export_canonical_genre_tree(wikidata_silver_dir, tmp_path / "gold", pop_side_path)


def test_export_canonical_genre_tree_marks_pop_side(tmp_path: Path) -> None:
    wikidata_silver_dir = tmp_path / "wikidata_silver"
    wikidata_silver_dir.mkdir()
    _hierarchy().write_parquet(wikidata_silver_dir / "7_canonical_hierarchy.parquet")
    output_dir = tmp_path / "gold"
    pop_side_path = tmp_path / "manual_canonical_genre_pop_side.csv"
    _write_pop_side_csv(pop_side_path, [{"root_genre_name": "rock", "pop_child_genre_name": "pop rock", "reason": "x"}])

    result = export_canonical_genre_tree(wikidata_silver_dir, output_dir, pop_side_path)

    tree = json.loads(result.read_text())
    rock = next(node for node in tree["tree"] if node["name"] == "rock")
    pop_rock = next(child for child in rock["children"] if child["name"] == "pop rock")
    assert pop_rock["side"] == "pop"


def test_export_canonical_genre_tree_raises_when_no_mainstream_pop_root(tmp_path: Path) -> None:
    wikidata_silver_dir = tmp_path / "wikidata_silver"
    wikidata_silver_dir.mkdir()
    rows = [row for row in TREE_ROWS if row["item_label"] != "Mainstream Pop"]
    pl.DataFrame(rows).write_parquet(wikidata_silver_dir / "7_canonical_hierarchy.parquet")
    pop_side_path = tmp_path / "manual_canonical_genre_pop_side.csv"
    _write_pop_side_csv(pop_side_path, [])

    with pytest.raises(ValueError, match="Mainstream Pop"):
        export_canonical_genre_tree(wikidata_silver_dir, tmp_path / "gold", pop_side_path)


def test_export_canonical_genre_tree_raises_on_unknown_root(tmp_path: Path) -> None:
    wikidata_silver_dir = tmp_path / "wikidata_silver"
    wikidata_silver_dir.mkdir()
    _hierarchy().write_parquet(wikidata_silver_dir / "7_canonical_hierarchy.parquet")
    pop_side_path = tmp_path / "manual_canonical_genre_pop_side.csv"
    _write_pop_side_csv(
        pop_side_path, [{"root_genre_name": "punk rock", "pop_child_genre_name": "hardcore punk", "reason": "x"}]
    )

    with pytest.raises(ValueError, match="not found among canonical roots"):
        export_canonical_genre_tree(wikidata_silver_dir, tmp_path / "gold", pop_side_path)


def test_export_canonical_genre_tree_raises_on_non_direct_child(tmp_path: Path) -> None:
    wikidata_silver_dir = tmp_path / "wikidata_silver"
    wikidata_silver_dir.mkdir()
    _hierarchy().write_parquet(wikidata_silver_dir / "7_canonical_hierarchy.parquet")
    pop_side_path = tmp_path / "manual_canonical_genre_pop_side.csv"
    _write_pop_side_csv(
        pop_side_path, [{"root_genre_name": "rock", "pop_child_genre_name": "hardcore punk", "reason": "x"}]
    )

    with pytest.raises(ValueError, match="not a direct child of root"):
        export_canonical_genre_tree(wikidata_silver_dir, tmp_path / "gold", pop_side_path)


def test_export_canonical_genre_tree_raises_on_duplicate_row(tmp_path: Path) -> None:
    wikidata_silver_dir = tmp_path / "wikidata_silver"
    wikidata_silver_dir.mkdir()
    _hierarchy().write_parquet(wikidata_silver_dir / "7_canonical_hierarchy.parquet")
    pop_side_path = tmp_path / "manual_canonical_genre_pop_side.csv"
    _write_pop_side_csv(
        pop_side_path,
        [
            {"root_genre_name": "rock", "pop_child_genre_name": "pop rock", "reason": "x"},
            {"root_genre_name": "rock", "pop_child_genre_name": "pop rock", "reason": "y"},
        ],
    )

    with pytest.raises(ValueError, match="duplicate"):
        export_canonical_genre_tree(wikidata_silver_dir, tmp_path / "gold", pop_side_path)


def test_export_canonical_genre_tree_raises_on_no_core_child_left(tmp_path: Path) -> None:
    wikidata_silver_dir = tmp_path / "wikidata_silver"
    wikidata_silver_dir.mkdir()
    _hierarchy().write_parquet(wikidata_silver_dir / "7_canonical_hierarchy.parquet")
    pop_side_path = tmp_path / "manual_canonical_genre_pop_side.csv"
    _write_pop_side_csv(
        pop_side_path,
        [
            {"root_genre_name": "rock", "pop_child_genre_name": "pop rock", "reason": "x"},
            {"root_genre_name": "rock", "pop_child_genre_name": "punk rock", "reason": "y"},
        ],
    )

    with pytest.raises(ValueError, match="no core .non-pop. child left"):
        export_canonical_genre_tree(wikidata_silver_dir, tmp_path / "gold", pop_side_path)


def test_export_canonical_genre_tree_raises_on_slug_collision(tmp_path: Path) -> None:
    wikidata_silver_dir = tmp_path / "wikidata_silver"
    wikidata_silver_dir.mkdir()
    rows = [
        *TREE_ROWS,
        {"item_id": "Q8", "item_label": "Blues/Rock", "parent_id": "Q7"},
        {"item_id": "Q9", "item_label": "blues rock", "parent_id": "Q7"},
    ]
    pl.DataFrame(rows).write_parquet(wikidata_silver_dir / "7_canonical_hierarchy.parquet")
    pop_side_path = tmp_path / "manual_canonical_genre_pop_side.csv"
    _write_pop_side_csv(pop_side_path, [])

    with pytest.raises(ValueError, match=r"'Blues/Rock' and 'blues rock' both slugify to id 'blues-rock'"):
        export_canonical_genre_tree(wikidata_silver_dir, tmp_path / "gold", pop_side_path)


def test_export_canonical_genre_tree_marks_multiple_pop_sides(tmp_path: Path) -> None:
    wikidata_silver_dir = tmp_path / "wikidata_silver"
    wikidata_silver_dir.mkdir()
    rows = [*TREE_ROWS, {"item_id": "Q6", "item_label": "soft rock", "parent_id": "Q1"}]
    pl.DataFrame(rows).write_parquet(wikidata_silver_dir / "7_canonical_hierarchy.parquet")
    output_dir = tmp_path / "gold"
    pop_side_path = tmp_path / "manual_canonical_genre_pop_side.csv"
    _write_pop_side_csv(
        pop_side_path,
        [
            {"root_genre_name": "rock", "pop_child_genre_name": "pop rock", "reason": "x"},
            {"root_genre_name": "rock", "pop_child_genre_name": "soft rock", "reason": "y"},
        ],
    )

    result = export_canonical_genre_tree(wikidata_silver_dir, output_dir, pop_side_path)

    tree = json.loads(result.read_text())
    rock = next(node for node in tree["tree"] if node["name"] == "rock")
    sides = {child["name"]: child.get("side") for child in rock["children"]}
    assert sides == {"punk rock": None, "pop rock": "pop", "soft rock": "pop"}
