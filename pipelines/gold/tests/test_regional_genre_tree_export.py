import json
from pathlib import Path

import polars as pl
import pytest

from gold.regional_genre_tree_export import export_regional_genre_tree

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


def _write_demotion_csv(path: Path, rows: list[dict[str, str]]) -> Path:
    columns = ["item_id", "item_label", "parent_id", "parent_label", "reason"]
    data = {column: [row.get(column, "") for row in rows] for column in columns}
    pl.DataFrame(data, schema={column: pl.Utf8 for column in columns}).write_csv(path)
    return path


def test_export_regional_genre_tree_writes_tree_shape(tmp_path: Path) -> None:
    wikidata_silver_dir = _write_silver(tmp_path)
    output_dir = tmp_path / "gold"

    result = export_regional_genre_tree(wikidata_silver_dir, output_dir)

    assert result == output_dir / "1_regional_genre_tree.json"
    tree = json.loads(result.read_text())
    assert {node["name"] for node in tree["tree"]} == {"music of Brazil", "music of Cape Verde"}


def test_export_regional_genre_tree_creates_output_dir(tmp_path: Path) -> None:
    wikidata_silver_dir = _write_silver(tmp_path)
    output_dir = tmp_path / "does" / "not" / "exist"

    export_regional_genre_tree(wikidata_silver_dir, output_dir)

    assert output_dir.is_dir()


def test_export_regional_genre_tree_raises_on_schema_violation(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    import gold.regional_genre_tree_export as module

    monkeypatch.setattr(module, "build_genre_tree", lambda hierarchy, **kwargs: {"tree": [{"name": "samba"}]})
    wikidata_silver_dir = _write_silver(tmp_path)

    with pytest.raises(ValueError, match="schema validation"):
        module.export_regional_genre_tree(wikidata_silver_dir, tmp_path / "gold")


EXTRA_EDGES = [
    {"item_id": "Q11", "parent_id": "Q12"},
    {"item_id": "Q11", "parent_id": "Q1"},
    {"item_id": "Q12", "parent_id": "Q99"},
]


def test_export_regional_genre_tree_emits_extra_parents_as_primary_by_default(tmp_path: Path) -> None:
    wikidata_silver_dir = _write_silver(tmp_path, EXTRA_EDGES)
    demotion_path = _write_demotion_csv(tmp_path / "demotions.csv", [])

    tree = json.loads(export_regional_genre_tree(wikidata_silver_dir, tmp_path / "gold", demotion_path).read_text())

    assert tree["allowsMultiplePrimaryParents"] is True
    samba = next(node for node in tree["tree"] if node["name"] == "music of Brazil")["children"][0]
    assert samba["primaryParents"] == ["Q1", "Q12"]
    assert "secondaryParents" not in samba
    cape_verde = next(node for node in tree["tree"] if node["name"] == "music of Cape Verde")
    assert "primaryParents" not in cape_verde


def test_export_regional_genre_tree_demotes_listed_edge_to_secondary(tmp_path: Path) -> None:
    wikidata_silver_dir = _write_silver(tmp_path, EXTRA_EDGES)
    demotion_path = _write_demotion_csv(
        tmp_path / "demotions.csv", [{"item_id": "Q11", "parent_id": "Q1", "reason": "classification only"}]
    )

    tree = json.loads(export_regional_genre_tree(wikidata_silver_dir, tmp_path / "gold", demotion_path).read_text())

    samba = next(node for node in tree["tree"] if node["name"] == "music of Brazil")["children"][0]
    assert samba["primaryParents"] == ["Q12"]
    assert samba["secondaryParents"] == ["Q1"]


def test_export_regional_genre_tree_raises_on_stale_demotion(tmp_path: Path) -> None:
    wikidata_silver_dir = _write_silver(tmp_path, EXTRA_EDGES)
    demotion_path = _write_demotion_csv(
        tmp_path / "demotions.csv", [{"item_id": "Q12", "parent_id": "Q99", "reason": "not exported"}]
    )

    with pytest.raises(ValueError, match="not extra parent edges"):
        export_regional_genre_tree(wikidata_silver_dir, tmp_path / "gold", demotion_path)


def test_export_regional_genre_tree_raises_on_blank_demotion_reason(tmp_path: Path) -> None:
    wikidata_silver_dir = _write_silver(tmp_path, EXTRA_EDGES)
    demotion_path = _write_demotion_csv(tmp_path / "demotions.csv", [{"item_id": "Q11", "parent_id": "Q1"}])

    with pytest.raises(ValueError, match="null/blank 'reason'"):
        export_regional_genre_tree(wikidata_silver_dir, tmp_path / "gold", demotion_path)
