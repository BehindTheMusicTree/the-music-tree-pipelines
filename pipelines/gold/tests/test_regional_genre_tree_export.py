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
    {"item_id": "Q13", "item_label": "forró", "parent_id": "Q10"},
]


CANONICAL_ROWS = [
    {"item_id": "Q1", "item_label": "rock", "parent_id": None},
    {"item_id": "Q2", "item_label": "baroque", "parent_id": None},
]

SELECTION_SCHEMA = {
    "item_id": pl.Utf8,
    "parent_id": pl.Utf8,
    "canonical_parent_id": pl.Utf8,
    "regional_genre_parent_id": pl.Utf8,
    "regional_overview_ids": pl.List(pl.Utf8),
}


def _selection(item_id: str, parent_id: str | None = None, canonical=None, regional_genre=None, overviews=()):
    return {
        "item_id": item_id,
        "parent_id": parent_id,
        "canonical_parent_id": canonical,
        "regional_genre_parent_id": regional_genre,
        "regional_overview_ids": list(overviews),
    }


# Samba is anchored on its first overview (Q10); its canonical parent (Q1) and other overview (Q12) are
# selected parents, Q13 is a regional genre parent that lost its axis.
SELECTION_ROWS = [
    _selection("Q10"),
    _selection("Q11", "Q10", canonical="Q1", overviews=["Q10", "Q12"]),
    _selection("Q12", canonical="Q99"),
    _selection("Q13", "Q10", overviews=["Q10"]),
    _selection("Q1"),
    _selection("Q2"),
]
SECONDARY_ROWS = [{"item_id": "Q11", "parent_id": "Q13"}, {"item_id": "Q12", "parent_id": "Q98"}]


def _hierarchy() -> pl.DataFrame:
    return pl.DataFrame(TREE_ROWS)


def _write_silver(
    tmp_path: Path,
    selection_rows: list[dict] | None = None,
    secondary_rows: list[dict[str, str]] | None = None,
) -> Path:
    wikidata_silver_dir = tmp_path / "wikidata_silver"
    wikidata_silver_dir.mkdir()
    _hierarchy().write_parquet(wikidata_silver_dir / "8_regional_hierarchy.parquet")
    pl.DataFrame(
        {"item_id": [row["item_id"] for row in TREE_ROWS], "is_regional_overview": [True, False, True, False]}
    ).write_parquet(wikidata_silver_dir / "4_regional_classification.parquet")
    pl.DataFrame(
        [{**row, "is_umbrella": False} for row in CANONICAL_ROWS], schema_overrides={"parent_id": pl.Utf8}
    ).write_parquet(wikidata_silver_dir / "7_canonical_hierarchy.parquet")
    pl.DataFrame(selection_rows or [], schema=SELECTION_SCHEMA).write_parquet(
        wikidata_silver_dir / "5_parent_selection.parquet"
    )
    pl.DataFrame(secondary_rows or [], schema={"item_id": pl.Utf8, "parent_id": pl.Utf8}).write_parquet(
        wikidata_silver_dir / "5_secondary_parents.parquet"
    )
    return wikidata_silver_dir


def _export(tmp_path: Path, curation_dir: Path, descriptions_path: Path, **silver) -> dict:
    wikidata_silver_dir = _write_silver(tmp_path, **silver)
    return json.loads(
        export_regional_genre_tree(
            wikidata_silver_dir, tmp_path / "gold", curation_dir, descriptions_path, CURATION_EXPORTED_ON
        ).read_text()
    )


def _samba(tree: dict) -> dict:
    brazil = next(node for node in tree["tree"] if node["name"] == "music of Brazil")
    return next(node for node in brazil["children"] if node["name"] == "samba")


def test_export_regional_genre_tree_writes_tree_shape(
    tmp_path: Path, curation_dir: Path, descriptions_path: Path
) -> None:
    wikidata_silver_dir = _write_silver(tmp_path)
    output_dir = tmp_path / "gold"

    result = export_regional_genre_tree(
        wikidata_silver_dir, output_dir, curation_dir, descriptions_path, CURATION_EXPORTED_ON
    )

    assert result == output_dir / "1_regional_genre_tree.json"
    tree = json.loads(result.read_text())
    assert {node["name"] for node in tree["tree"]} == {"music of Brazil", "music of Cape Verde"}
    assert tree["curationExportedOn"] == CURATION_EXPORTED_ON


def test_export_regional_genre_tree_flags_overviews_with_sub_genres_as_umbrellas(
    tmp_path: Path, curation_dir: Path, descriptions_path: Path
) -> None:
    tree = _export(tmp_path, curation_dir, descriptions_path)

    by_name = {node["name"]: node for node in tree["tree"]}
    assert by_name["music of Brazil"]["isUmbrella"] is True
    assert "isUmbrella" not in by_name["music of Cape Verde"]
    assert "isUmbrella" not in _samba(tree)


def test_export_regional_genre_tree_creates_output_dir(
    tmp_path: Path, curation_dir: Path, descriptions_path: Path
) -> None:
    wikidata_silver_dir = _write_silver(tmp_path)
    output_dir = tmp_path / "does" / "not" / "exist"

    export_regional_genre_tree(wikidata_silver_dir, output_dir, curation_dir, descriptions_path, CURATION_EXPORTED_ON)

    assert output_dir.is_dir()


def test_export_regional_genre_tree_raises_on_schema_violation(
    tmp_path: Path, curation_dir: Path, descriptions_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import gold.regional_genre_tree_export as module

    monkeypatch.setattr(module, "build_genre_tree", lambda hierarchy, **kwargs: {"tree": [{"name": "samba"}]})
    wikidata_silver_dir = _write_silver(tmp_path)

    with pytest.raises(ValueError, match="schema validation"):
        module.export_regional_genre_tree(
            wikidata_silver_dir,
            tmp_path / "gold",
            curation_dir,
            descriptions_path,
            CURATION_EXPORTED_ON,
        )


AXES = {"selection_rows": SELECTION_ROWS, "secondary_rows": SECONDARY_ROWS}


def test_export_regional_genre_tree_splits_selected_and_secondary_parents(
    tmp_path: Path, curation_dir: Path, descriptions_path: Path
) -> None:
    tree = _export(tmp_path, curation_dir, descriptions_path, **AXES)

    assert tree["treeName"] == "regional"
    samba = _samba(tree)
    assert samba["primaryParents"] == ["Q1", "Q12"]
    assert samba["secondaryParents"] == ["Q13"]
    cape_verde = next(node for node in tree["tree"] if node["name"] == "music of Cape Verde")
    assert "primaryParents" not in cape_verde
    assert "secondaryParents" not in cape_verde
    assert tree["crossTreeSecondaryParents"] == []


def test_export_regional_genre_tree_moves_added_selected_edge_to_secondary(
    tmp_path: Path,
    curation_dir: Path,
    descriptions_path: Path,
    write_curation: Callable[[str, list[dict[str, str]]], None],
) -> None:
    write_curation(
        "secondary_parents",
        [
            {"item_id": "Q11", "parent_id": "Q1", "action": "add", "reason": "classification only"},
            {"item_id": "Q11", "parent_id": "Q12", "action": "remove", "reason": "wrong"},
            {"item_id": "Q11", "parent_id": "Q13", "action": "remove", "reason": "wrong"},
        ],
    )

    samba = _samba(_export(tmp_path, curation_dir, descriptions_path, **AXES))

    assert "primaryParents" not in samba
    assert samba["secondaryParents"] == ["Q1"]


def test_export_regional_genre_tree_emits_cross_tree_secondary_parents(
    tmp_path: Path,
    curation_dir: Path,
    descriptions_path: Path,
    write_curation: Callable[[str, list[dict[str, str]]], None],
) -> None:
    selection_rows = [*SELECTION_ROWS[:4], _selection("Q1", regional_genre="Q13"), _selection("Q2")]
    secondary_rows = [*SECONDARY_ROWS, {"item_id": "Q1", "parent_id": "Q11"}, {"item_id": "Q1", "parent_id": "Q2"}]
    write_curation(
        "secondary_parents",
        [
            {"item_id": "Q2", "parent_id": "Q10", "action": "add", "reason": "baroque in Brazil"},
            {"item_id": "Q1", "parent_id": "Q11", "action": "remove", "reason": "wrong"},
        ],
    )

    tree = _export(
        tmp_path, curation_dir, descriptions_path, selection_rows=selection_rows, secondary_rows=secondary_rows
    )

    assert tree["crossTreeSecondaryParents"] == [
        {"id": "Q1", "secondaryParents": ["Q13"]},
        {"id": "Q2", "secondaryParents": ["Q10"]},
    ]


def test_export_regional_genre_tree_reports_stale_secondary_parent_removal_as_satisfied(
    tmp_path: Path,
    curation_dir: Path,
    descriptions_path: Path,
    write_curation: Callable[[str, list[dict[str, str]]], None],
) -> None:
    write_curation("secondary_parents", [{"item_id": "Q12", "parent_id": "Q1", "action": "remove", "reason": "x"}])

    _export(tmp_path, curation_dir, descriptions_path, **AXES)

    report = pl.read_csv(tmp_path / "gold" / "1_regional_secondary_parent_rules_report.csv")
    assert report.rows() == [("regional", "Q12", "Q1", "remove", "satisfied")]


def test_export_regional_genre_tree_ignores_removal_of_unresolvable_parent(
    tmp_path: Path,
    curation_dir: Path,
    descriptions_path: Path,
    write_curation: Callable[[str, list[dict[str, str]]], None],
) -> None:
    write_curation("secondary_parents", [{"item_id": "Q12", "parent_id": "Q99", "action": "remove", "reason": "x"}])

    samba = _samba(_export(tmp_path, curation_dir, descriptions_path, **AXES))

    assert samba["primaryParents"] == ["Q1", "Q12"]


@pytest.mark.parametrize(
    ("row", "outcome"),
    [
        ({"item_id": "Q11", "parent_id": "Q99", "action": "add", "reason": "x"}, "unresolved"),
        ({"item_id": "Q11", "parent_id": "Q10", "action": "add", "reason": "x"}, "satisfied"),
    ],
)
def test_export_regional_genre_tree_skips_secondary_parent_add_it_cannot_apply(
    tmp_path: Path,
    curation_dir: Path,
    descriptions_path: Path,
    write_curation: Callable[[str, list[dict[str, str]]], None],
    row: dict[str, str],
    outcome: str,
) -> None:
    write_curation("secondary_parents", [row])

    tree = _export(tmp_path, curation_dir, descriptions_path, **AXES)

    assert row["parent_id"] not in json.dumps(tree.get("crossTreeSecondaryParents"))
    report = pl.read_csv(tmp_path / "gold" / "1_regional_secondary_parent_rules_report.csv")
    assert report.rows() == [("regional", "Q11", row["parent_id"], "add", outcome)]


def test_export_regional_genre_tree_emits_essential_songs_and_summary(
    tmp_path: Path,
    curation_dir: Path,
    descriptions_path: Path,
    write_curation: Callable[[str, list[dict[str, str]]], None],
) -> None:
    wikidata_silver_dir = _write_silver(tmp_path)
    recording_id = "00000000-0000-0000-0000-00000000000a"
    write_curation(
        "essential_songs",
        [{"item_id": "Q11", "musicbrainz_recording_id": recording_id, "position": "1", "reason": "x"}],
    )
    write_curation("summary_overrides", [{"item_id": "Q1", "summary": "canonical only", "reason": "x"}])

    tree = json.loads(
        export_regional_genre_tree(
            wikidata_silver_dir, tmp_path / "gold", curation_dir, descriptions_path, CURATION_EXPORTED_ON
        ).read_text()
    )

    samba = next(node for node in tree["tree"] if node["name"] == "music of Brazil")["children"][0]
    assert samba["essentialSongs"] == [recording_id]
    assert all("summary" not in node for node in tree["tree"])
