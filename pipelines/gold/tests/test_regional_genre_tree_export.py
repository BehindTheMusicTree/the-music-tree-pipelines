import json
from collections.abc import Callable
from pathlib import Path

import polars as pl
import pytest

from gold.regional_genre_tree_export import export_regional_genre_tree

CURATION_EXPORTED_ON = "2026-10-16T08:00:00+00:00"

TREE_ROWS = [
    {"item_id": "Q10", "item_label": "music of Brazil", "parent_id": None},
    {"item_id": "Q11", "item_label": "samba", "parent_id": "Q10"},
    {"item_id": "Q12", "item_label": "music of Cape Verde", "parent_id": None},
]


CANONICAL_ROWS = [{"item_id": "Q1", "item_label": "rock", "parent_id": None}]


def _hierarchy() -> pl.DataFrame:
    return pl.DataFrame(TREE_ROWS)


def _write_silver(tmp_path: Path, secondary_rows: list[dict[str, str]] | None = None) -> Path:
    wikidata_silver_dir = tmp_path / "wikidata_silver"
    wikidata_silver_dir.mkdir()
    _hierarchy().write_parquet(wikidata_silver_dir / "8_regional_hierarchy.parquet")
    pl.DataFrame(CANONICAL_ROWS).write_parquet(wikidata_silver_dir / "7_canonical_hierarchy.parquet")
    pl.DataFrame(secondary_rows or [], schema={"item_id": pl.Utf8, "parent_id": pl.Utf8}).write_parquet(
        wikidata_silver_dir / "5_secondary_parents.parquet"
    )
    return wikidata_silver_dir


def test_export_regional_genre_tree_writes_tree_shape(tmp_path: Path, curation_dir: Path) -> None:
    wikidata_silver_dir = _write_silver(tmp_path)
    output_dir = tmp_path / "gold"

    result = export_regional_genre_tree(wikidata_silver_dir, output_dir, curation_dir, CURATION_EXPORTED_ON)

    assert result == output_dir / "1_regional_genre_tree.json"
    tree = json.loads(result.read_text())
    assert {node["name"] for node in tree["tree"]} == {"music of Brazil", "music of Cape Verde"}
    assert tree["curationExportedOn"] == CURATION_EXPORTED_ON


def test_export_regional_genre_tree_creates_output_dir(tmp_path: Path, curation_dir: Path) -> None:
    wikidata_silver_dir = _write_silver(tmp_path)
    output_dir = tmp_path / "does" / "not" / "exist"

    export_regional_genre_tree(wikidata_silver_dir, output_dir, curation_dir, CURATION_EXPORTED_ON)

    assert output_dir.is_dir()


def test_export_regional_genre_tree_raises_on_schema_violation(
    tmp_path: Path, curation_dir: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import gold.regional_genre_tree_export as module

    monkeypatch.setattr(module, "build_genre_tree", lambda hierarchy, **kwargs: {"tree": [{"name": "samba"}]})
    wikidata_silver_dir = _write_silver(tmp_path)

    with pytest.raises(ValueError, match="schema validation"):
        module.export_regional_genre_tree(
            wikidata_silver_dir,
            tmp_path / "gold",
            curation_dir,
            CURATION_EXPORTED_ON,
        )


EXTRA_EDGES = [
    {"item_id": "Q11", "parent_id": "Q12"},
    {"item_id": "Q11", "parent_id": "Q1"},
    {"item_id": "Q12", "parent_id": "Q99"},
]


def test_export_regional_genre_tree_emits_extra_parents_as_primary_by_default(
    tmp_path: Path, curation_dir: Path
) -> None:
    wikidata_silver_dir = _write_silver(tmp_path, EXTRA_EDGES)

    tree = json.loads(
        export_regional_genre_tree(
            wikidata_silver_dir, tmp_path / "gold", curation_dir, CURATION_EXPORTED_ON
        ).read_text()
    )

    assert tree["treeName"] == "regional"
    samba = next(node for node in tree["tree"] if node["name"] == "music of Brazil")["children"][0]
    assert samba["primaryParents"] == ["Q1", "Q12"]
    assert "secondaryParents" not in samba
    cape_verde = next(node for node in tree["tree"] if node["name"] == "music of Cape Verde")
    assert "primaryParents" not in cape_verde


def test_export_regional_genre_tree_moves_added_extra_edge_to_secondary(
    tmp_path: Path, curation_dir: Path, write_curation: Callable[[str, list[dict[str, str]]], None]
) -> None:
    wikidata_silver_dir = _write_silver(tmp_path, EXTRA_EDGES)
    write_curation(
        "secondary_parents",
        [
            {"item_id": "Q11", "parent_id": "Q1", "action": "add", "reason": "classification only"},
            {"item_id": "Q11", "parent_id": "Q12", "action": "remove", "reason": "wrong"},
        ],
    )

    tree = json.loads(
        export_regional_genre_tree(
            wikidata_silver_dir, tmp_path / "gold", curation_dir, CURATION_EXPORTED_ON
        ).read_text()
    )

    samba = next(node for node in tree["tree"] if node["name"] == "music of Brazil")["children"][0]
    assert "primaryParents" not in samba
    assert samba["secondaryParents"] == ["Q1"]


def test_export_regional_genre_tree_raises_on_stale_secondary_parent_removal(
    tmp_path: Path, curation_dir: Path, write_curation: Callable[[str, list[dict[str, str]]], None]
) -> None:
    wikidata_silver_dir = _write_silver(tmp_path, EXTRA_EDGES)
    write_curation("secondary_parents", [{"item_id": "Q12", "parent_id": "Q99", "action": "remove", "reason": "x"}])

    with pytest.raises(ValueError, match="not extra parent edges"):
        export_regional_genre_tree(wikidata_silver_dir, tmp_path / "gold", curation_dir, CURATION_EXPORTED_ON)


def test_export_regional_genre_tree_emits_essential_tracks_and_summary(
    tmp_path: Path, curation_dir: Path, write_curation: Callable[[str, list[dict[str, str]]], None]
) -> None:
    wikidata_silver_dir = _write_silver(tmp_path)
    recording_id = "00000000-0000-0000-0000-00000000000a"
    write_curation(
        "essential_tracks",
        [{"item_id": "Q11", "musicbrainz_recording_id": recording_id, "position": "1", "reason": "x"}],
    )
    write_curation("summary_overrides", [{"item_id": "Q1", "summary": "canonical only", "reason": "x"}])

    tree = json.loads(
        export_regional_genre_tree(
            wikidata_silver_dir, tmp_path / "gold", curation_dir, CURATION_EXPORTED_ON
        ).read_text()
    )

    samba = next(node for node in tree["tree"] if node["name"] == "music of Brazil")["children"][0]
    assert samba["essentialTracks"] == [recording_id]
    assert all("summary" not in node for node in tree["tree"])
