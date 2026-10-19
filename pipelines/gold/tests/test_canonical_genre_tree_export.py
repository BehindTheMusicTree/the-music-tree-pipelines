import json
from collections.abc import Callable
from pathlib import Path

import polars as pl
import pytest

from gold.canonical_genre_tree_export import export_canonical_genre_tree

CURATION_EXPORTED_ON = "2026-10-16T08:00:00+00:00"

TREE_ROWS = [
    {"item_id": "Q1", "item_label": "rock", "parent_id": None},
    {"item_id": "Q2", "item_label": "punk rock", "parent_id": "Q1"},
    {"item_id": "Q3", "item_label": "hardcore punk", "parent_id": "Q2"},
    {"item_id": "Q4", "item_label": "pop rock", "parent_id": "Q1"},
    {"item_id": "Q5", "item_label": "jazz", "parent_id": None},
    {"item_id": "Q7", "item_label": "Mainstream Pop", "parent_id": None},
]

REGIONAL_ROWS = [{"item_id": "Q10", "item_label": "music of Brazil", "parent_id": None}]


def _hierarchy() -> pl.DataFrame:
    return pl.DataFrame(TREE_ROWS)


def _write_secondary_parents(wikidata_silver_dir: Path, rows: list[dict[str, str]]) -> None:
    pl.DataFrame(rows, schema={"item_id": pl.Utf8, "parent_id": pl.Utf8}).write_parquet(
        wikidata_silver_dir / "5_secondary_parents.parquet"
    )


def _write_canonical_roots(wikidata_silver_dir: Path, unaccepted_ids: set[str]) -> None:
    roots = [row["item_id"] for row in TREE_ROWS if row["parent_id"] is None]
    pl.DataFrame({"item_id": roots, "is_accepted": [item_id not in unaccepted_ids for item_id in roots]}).write_parquet(
        wikidata_silver_dir / "9_canonical_roots.parquet"
    )
    pl.DataFrame(REGIONAL_ROWS).write_parquet(wikidata_silver_dir / "8_regional_hierarchy.parquet")


def _write_pop_side_csv(path: Path, rows: list[dict[str, str]]) -> None:
    columns = ["root_genre_name", "pop_child_genre_name", "reason"]
    data = {column: [row[column] for row in rows] for column in columns}
    pl.DataFrame(data, schema={column: pl.Utf8 for column in columns}).write_csv(path)


def test_export_canonical_genre_tree_writes_tree_shape(tmp_path: Path, curation_dir: Path) -> None:
    wikidata_silver_dir = tmp_path / "wikidata_silver"
    wikidata_silver_dir.mkdir()
    _hierarchy().write_parquet(wikidata_silver_dir / "7_canonical_hierarchy.parquet")
    _write_canonical_roots(wikidata_silver_dir, set())
    _write_secondary_parents(wikidata_silver_dir, [])
    output_dir = tmp_path / "gold"
    pop_side_path = tmp_path / "manual_canonical_genre_pop_side.csv"
    _write_pop_side_csv(pop_side_path, [])

    result = export_canonical_genre_tree(
        wikidata_silver_dir, output_dir, pop_side_path, curation_dir, CURATION_EXPORTED_ON
    )

    assert result == output_dir / "1_canonical_genre_tree.json"
    tree = json.loads(result.read_text())
    assert {node["name"] for node in tree["tree"]} == {"rock", "jazz", "Mainstream Pop"}
    assert tree["curationExportedOn"] == CURATION_EXPORTED_ON


def test_export_canonical_genre_tree_creates_output_dir(tmp_path: Path, curation_dir: Path) -> None:
    wikidata_silver_dir = tmp_path / "wikidata_silver"
    wikidata_silver_dir.mkdir()
    _hierarchy().write_parquet(wikidata_silver_dir / "7_canonical_hierarchy.parquet")
    _write_canonical_roots(wikidata_silver_dir, set())
    _write_secondary_parents(wikidata_silver_dir, [])
    output_dir = tmp_path / "does" / "not" / "exist"
    pop_side_path = tmp_path / "manual_canonical_genre_pop_side.csv"
    _write_pop_side_csv(pop_side_path, [])

    export_canonical_genre_tree(wikidata_silver_dir, output_dir, pop_side_path, curation_dir, CURATION_EXPORTED_ON)

    assert output_dir.is_dir()


def test_export_canonical_genre_tree_raises_on_schema_violation(
    tmp_path: Path, curation_dir: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import gold.canonical_genre_tree_export as module

    monkeypatch.setattr(
        module,
        "build_genre_tree",
        lambda hierarchy, pop_sides, **kwargs: {"tree": [{"name": "rock"}, {"name": "Mainstream Pop"}]},
    )
    wikidata_silver_dir = tmp_path / "wikidata_silver"
    wikidata_silver_dir.mkdir()
    _hierarchy().write_parquet(wikidata_silver_dir / "7_canonical_hierarchy.parquet")
    _write_canonical_roots(wikidata_silver_dir, set())
    _write_secondary_parents(wikidata_silver_dir, [])
    pop_side_path = tmp_path / "manual_canonical_genre_pop_side.csv"
    _write_pop_side_csv(pop_side_path, [])

    with pytest.raises(ValueError, match="schema validation"):
        module.export_canonical_genre_tree(
            wikidata_silver_dir, tmp_path / "gold", pop_side_path, curation_dir, CURATION_EXPORTED_ON
        )


def test_export_canonical_genre_tree_marks_pop_side(tmp_path: Path, curation_dir: Path) -> None:
    wikidata_silver_dir = tmp_path / "wikidata_silver"
    wikidata_silver_dir.mkdir()
    _hierarchy().write_parquet(wikidata_silver_dir / "7_canonical_hierarchy.parquet")
    _write_canonical_roots(wikidata_silver_dir, set())
    _write_secondary_parents(wikidata_silver_dir, [])
    output_dir = tmp_path / "gold"
    pop_side_path = tmp_path / "manual_canonical_genre_pop_side.csv"
    _write_pop_side_csv(pop_side_path, [{"root_genre_name": "rock", "pop_child_genre_name": "pop rock", "reason": "x"}])

    result = export_canonical_genre_tree(
        wikidata_silver_dir, output_dir, pop_side_path, curation_dir, CURATION_EXPORTED_ON
    )

    tree = json.loads(result.read_text())
    rock = next(node for node in tree["tree"] if node["name"] == "rock")
    pop_rock = next(child for child in rock["children"] if child["name"] == "pop rock")
    assert pop_rock["side"] == "pop"


def test_export_canonical_genre_tree_raises_when_no_mainstream_pop_root(tmp_path: Path, curation_dir: Path) -> None:
    wikidata_silver_dir = tmp_path / "wikidata_silver"
    wikidata_silver_dir.mkdir()
    rows = [row for row in TREE_ROWS if row["item_label"] != "Mainstream Pop"]
    pl.DataFrame(rows).write_parquet(wikidata_silver_dir / "7_canonical_hierarchy.parquet")
    _write_canonical_roots(wikidata_silver_dir, set())
    _write_secondary_parents(wikidata_silver_dir, [])
    pop_side_path = tmp_path / "manual_canonical_genre_pop_side.csv"
    _write_pop_side_csv(pop_side_path, [])

    with pytest.raises(ValueError, match="Mainstream Pop"):
        export_canonical_genre_tree(
            wikidata_silver_dir, tmp_path / "gold", pop_side_path, curation_dir, CURATION_EXPORTED_ON
        )


def test_export_canonical_genre_tree_raises_on_unknown_root(tmp_path: Path, curation_dir: Path) -> None:
    wikidata_silver_dir = tmp_path / "wikidata_silver"
    wikidata_silver_dir.mkdir()
    _hierarchy().write_parquet(wikidata_silver_dir / "7_canonical_hierarchy.parquet")
    _write_canonical_roots(wikidata_silver_dir, set())
    _write_secondary_parents(wikidata_silver_dir, [])
    pop_side_path = tmp_path / "manual_canonical_genre_pop_side.csv"
    _write_pop_side_csv(
        pop_side_path, [{"root_genre_name": "punk rock", "pop_child_genre_name": "hardcore punk", "reason": "x"}]
    )

    with pytest.raises(ValueError, match="not found among canonical roots"):
        export_canonical_genre_tree(
            wikidata_silver_dir, tmp_path / "gold", pop_side_path, curation_dir, CURATION_EXPORTED_ON
        )


def test_export_canonical_genre_tree_raises_on_non_direct_child(tmp_path: Path, curation_dir: Path) -> None:
    wikidata_silver_dir = tmp_path / "wikidata_silver"
    wikidata_silver_dir.mkdir()
    _hierarchy().write_parquet(wikidata_silver_dir / "7_canonical_hierarchy.parquet")
    _write_canonical_roots(wikidata_silver_dir, set())
    _write_secondary_parents(wikidata_silver_dir, [])
    pop_side_path = tmp_path / "manual_canonical_genre_pop_side.csv"
    _write_pop_side_csv(
        pop_side_path, [{"root_genre_name": "rock", "pop_child_genre_name": "hardcore punk", "reason": "x"}]
    )

    with pytest.raises(ValueError, match="not a direct child of root"):
        export_canonical_genre_tree(
            wikidata_silver_dir, tmp_path / "gold", pop_side_path, curation_dir, CURATION_EXPORTED_ON
        )


def test_export_canonical_genre_tree_raises_on_duplicate_row(tmp_path: Path, curation_dir: Path) -> None:
    wikidata_silver_dir = tmp_path / "wikidata_silver"
    wikidata_silver_dir.mkdir()
    _hierarchy().write_parquet(wikidata_silver_dir / "7_canonical_hierarchy.parquet")
    _write_canonical_roots(wikidata_silver_dir, set())
    _write_secondary_parents(wikidata_silver_dir, [])
    pop_side_path = tmp_path / "manual_canonical_genre_pop_side.csv"
    _write_pop_side_csv(
        pop_side_path,
        [
            {"root_genre_name": "rock", "pop_child_genre_name": "pop rock", "reason": "x"},
            {"root_genre_name": "rock", "pop_child_genre_name": "pop rock", "reason": "y"},
        ],
    )

    with pytest.raises(ValueError, match="duplicate"):
        export_canonical_genre_tree(
            wikidata_silver_dir, tmp_path / "gold", pop_side_path, curation_dir, CURATION_EXPORTED_ON
        )


def test_export_canonical_genre_tree_raises_on_no_core_child_left(tmp_path: Path, curation_dir: Path) -> None:
    wikidata_silver_dir = tmp_path / "wikidata_silver"
    wikidata_silver_dir.mkdir()
    _hierarchy().write_parquet(wikidata_silver_dir / "7_canonical_hierarchy.parquet")
    _write_canonical_roots(wikidata_silver_dir, set())
    _write_secondary_parents(wikidata_silver_dir, [])
    pop_side_path = tmp_path / "manual_canonical_genre_pop_side.csv"
    _write_pop_side_csv(
        pop_side_path,
        [
            {"root_genre_name": "rock", "pop_child_genre_name": "pop rock", "reason": "x"},
            {"root_genre_name": "rock", "pop_child_genre_name": "punk rock", "reason": "y"},
        ],
    )

    with pytest.raises(ValueError, match="no core .non-pop. child left"):
        export_canonical_genre_tree(
            wikidata_silver_dir, tmp_path / "gold", pop_side_path, curation_dir, CURATION_EXPORTED_ON
        )


def test_export_canonical_genre_tree_marks_multiple_pop_sides(tmp_path: Path, curation_dir: Path) -> None:
    wikidata_silver_dir = tmp_path / "wikidata_silver"
    wikidata_silver_dir.mkdir()
    rows = [*TREE_ROWS, {"item_id": "Q6", "item_label": "soft rock", "parent_id": "Q1"}]
    pl.DataFrame(rows).write_parquet(wikidata_silver_dir / "7_canonical_hierarchy.parquet")
    _write_canonical_roots(wikidata_silver_dir, set())
    _write_secondary_parents(wikidata_silver_dir, [])
    output_dir = tmp_path / "gold"
    pop_side_path = tmp_path / "manual_canonical_genre_pop_side.csv"
    _write_pop_side_csv(
        pop_side_path,
        [
            {"root_genre_name": "rock", "pop_child_genre_name": "pop rock", "reason": "x"},
            {"root_genre_name": "rock", "pop_child_genre_name": "soft rock", "reason": "y"},
        ],
    )

    result = export_canonical_genre_tree(
        wikidata_silver_dir, output_dir, pop_side_path, curation_dir, CURATION_EXPORTED_ON
    )

    tree = json.loads(result.read_text())
    rock = next(node for node in tree["tree"] if node["name"] == "rock")
    sides = {child["name"]: child.get("side") for child in rock["children"]}
    assert sides == {"punk rock": None, "pop rock": "pop", "soft rock": "pop"}


def test_export_canonical_genre_tree_emits_only_canonical_secondary_parents(tmp_path: Path, curation_dir: Path) -> None:
    wikidata_silver_dir = tmp_path / "wikidata_silver"
    wikidata_silver_dir.mkdir()
    _hierarchy().write_parquet(wikidata_silver_dir / "7_canonical_hierarchy.parquet")
    _write_canonical_roots(wikidata_silver_dir, set())
    _write_secondary_parents(
        wikidata_silver_dir,
        [
            {"item_id": "Q4", "parent_id": "Q7"},
            {"item_id": "Q4", "parent_id": "Q7"},
            {"item_id": "Q3", "parent_id": "Q99"},
            {"item_id": "Q98", "parent_id": "Q1"},
        ],
    )
    pop_side_path = tmp_path / "manual_canonical_genre_pop_side.csv"
    _write_pop_side_csv(pop_side_path, [])

    tree = json.loads(
        export_canonical_genre_tree(
            wikidata_silver_dir, tmp_path / "gold", pop_side_path, curation_dir, CURATION_EXPORTED_ON
        ).read_text()
    )

    assert tree["treeName"] == "canonical"
    rock = next(node for node in tree["tree"] if node["name"] == "rock")
    pop_rock = next(child for child in rock["children"] if child["name"] == "pop rock")
    assert pop_rock["secondaryParents"] == ["Q7"]
    assert "primaryParents" not in pop_rock
    assert "secondaryParents" not in rock["children"][0]["children"][0]  # hardcore punk -> unknown Q99


def test_export_canonical_genre_tree_flags_unaccepted_roots(tmp_path: Path, curation_dir: Path) -> None:
    wikidata_silver_dir = tmp_path / "wikidata_silver"
    wikidata_silver_dir.mkdir()
    _hierarchy().write_parquet(wikidata_silver_dir / "7_canonical_hierarchy.parquet")
    _write_canonical_roots(wikidata_silver_dir, {"Q5"})
    _write_secondary_parents(wikidata_silver_dir, [])
    pop_side_path = tmp_path / "manual_canonical_genre_pop_side.csv"
    _write_pop_side_csv(pop_side_path, [])

    tree = json.loads(
        export_canonical_genre_tree(
            wikidata_silver_dir, tmp_path / "gold", pop_side_path, curation_dir, CURATION_EXPORTED_ON
        ).read_text()
    )

    flags = {node["name"]: node.get("isUnacceptedRoot") for node in tree["tree"]}
    assert flags == {"jazz": True, "rock": None, "Mainstream Pop": None}


MBID_A = "00000000-0000-0000-0000-00000000000a"
MBID_B = "00000000-0000-0000-0000-00000000000b"


def _export_with_secondary_edges(tmp_path: Path, curation_dir: Path) -> dict:
    wikidata_silver_dir = tmp_path / "wikidata_silver"
    wikidata_silver_dir.mkdir()
    _hierarchy().write_parquet(wikidata_silver_dir / "7_canonical_hierarchy.parquet")
    _write_canonical_roots(wikidata_silver_dir, set())
    _write_secondary_parents(wikidata_silver_dir, [{"item_id": "Q4", "parent_id": "Q7"}])
    pop_side_path = tmp_path / "manual_canonical_genre_pop_side.csv"
    _write_pop_side_csv(pop_side_path, [])
    result = export_canonical_genre_tree(
        wikidata_silver_dir, tmp_path / "gold", pop_side_path, curation_dir, CURATION_EXPORTED_ON
    )
    return json.loads(result.read_text())


def _rock_child(tree: dict, name: str) -> dict:
    rock = next(node for node in tree["tree"] if node["name"] == "rock")
    return next(child for child in rock["children"] if child["name"] == name)


def test_export_canonical_genre_tree_applies_secondary_parent_rules(
    tmp_path: Path, curation_dir: Path, write_curation: Callable[[str, list[dict[str, str]]], None]
) -> None:
    write_curation(
        "secondary_parents",
        [
            {"item_id": "Q4", "parent_id": "Q7", "action": "remove", "reason": "x"},
            {"item_id": "Q2", "parent_id": "Q5", "action": "add", "reason": "y"},
            {"item_id": "Q10", "parent_id": "Q1", "action": "add", "reason": "other tree"},
        ],
    )

    tree = _export_with_secondary_edges(tmp_path, curation_dir)

    assert "secondaryParents" not in _rock_child(tree, "pop rock")
    assert _rock_child(tree, "punk rock")["secondaryParents"] == ["Q5"]


def test_export_canonical_genre_tree_ignores_removal_of_regional_parent(
    tmp_path: Path, curation_dir: Path, write_curation: Callable[[str, list[dict[str, str]]], None]
) -> None:
    write_curation("secondary_parents", [{"item_id": "Q4", "parent_id": "Q10", "action": "remove", "reason": "x"}])

    tree = _export_with_secondary_edges(tmp_path, curation_dir)

    assert _rock_child(tree, "pop rock")["secondaryParents"] == ["Q7"]


def test_export_canonical_genre_tree_emits_essential_tracks_by_position(
    tmp_path: Path, curation_dir: Path, write_curation: Callable[[str, list[dict[str, str]]], None]
) -> None:
    write_curation(
        "essential_tracks",
        [
            {"item_id": "Q4", "musicbrainz_recording_id": MBID_A, "position": "2", "reason": "x"},
            {"item_id": "Q4", "musicbrainz_recording_id": MBID_B, "position": "1", "reason": "x"},
        ],
    )

    tree = _export_with_secondary_edges(tmp_path, curation_dir)

    assert _rock_child(tree, "pop rock")["essentialTracks"] == [MBID_B, MBID_A]
    assert "essentialTracks" not in _rock_child(tree, "punk rock")


def test_export_canonical_genre_tree_emits_summary_override(
    tmp_path: Path, curation_dir: Path, write_curation: Callable[[str, list[dict[str, str]]], None]
) -> None:
    write_curation("summary_overrides", [{"item_id": "Q4", "summary": "Rock with pop hooks.", "reason": "x"}])

    tree = _export_with_secondary_edges(tmp_path, curation_dir)

    assert _rock_child(tree, "pop rock")["summary"] == "Rock with pop hooks."


@pytest.mark.parametrize(
    ("name", "row", "match"),
    [
        ("secondary_parents", {"item_id": "Q4", "parent_id": "Q7", "action": "move", "reason": "x"}, "malformed"),
        ("secondary_parents", {"item_id": "Q2", "parent_id": "Q7", "action": "remove", "reason": "x"}, "removes"),
        ("secondary_parents", {"item_id": "Q2", "parent_id": "Q10", "action": "add", "reason": "x"}, "outside"),
        ("secondary_parents", {"item_id": "Q2", "parent_id": "Q2", "action": "add", "reason": "x"}, "to itself"),
        ("secondary_parents", {"item_id": "Q2", "parent_id": "Q1", "action": "add", "reason": "x"}, "main parent"),
        (
            "essential_tracks",
            {"item_id": "Q4", "musicbrainz_recording_id": "nope", "position": "1", "reason": "x"},
            "malformed",
        ),
        ("summary_overrides", {"item_id": "Q404", "summary": "s", "reason": "x"}, "not found in either"),
        ("summary_overrides", {"item_id": "Q4", "summary": "s"}, "null/blank 'reason'"),
    ],
)
def test_export_canonical_genre_tree_raises_on_bad_curation_row(
    tmp_path: Path,
    curation_dir: Path,
    write_curation: Callable[[str, list[dict[str, str]]], None],
    name: str,
    row: dict[str, str],
    match: str,
) -> None:
    write_curation(name, [row])

    with pytest.raises(ValueError, match=match):
        _export_with_secondary_edges(tmp_path, curation_dir)


def test_export_canonical_genre_tree_raises_on_duplicate_essential_track_position(
    tmp_path: Path, curation_dir: Path, write_curation: Callable[[str, list[dict[str, str]]], None]
) -> None:
    write_curation(
        "essential_tracks",
        [
            {"item_id": "Q4", "musicbrainz_recording_id": MBID_A, "position": "1", "reason": "x"},
            {"item_id": "Q4", "musicbrainz_recording_id": MBID_B, "position": "1", "reason": "x"},
        ],
    )

    with pytest.raises(ValueError, match="duplicate \\('item_id', 'position'\\)"):
        _export_with_secondary_edges(tmp_path, curation_dir)
