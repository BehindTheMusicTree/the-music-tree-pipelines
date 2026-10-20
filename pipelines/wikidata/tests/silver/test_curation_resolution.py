import json
from pathlib import Path
from unittest.mock import MagicMock

import polars as pl
import pytest
import tenacity

from wikidata import wikidata_client
from wikidata.silver import curation_resolution as cr
from wikidata.silver.non_genre_pruning import prune_non_genre_items

ITEM_COLUMNS = ("item_id", "item_label", "reason")
LIST_COLUMNS = {
    **dict.fromkeys(cr.QID_COLUMNS, ITEM_COLUMNS),
    "label_overrides": ("item_id", "display_label", "reason"),
    "accepted_canonical_roots": ("item_id", "item_label"),
    "main_parent": ("item_id", "item_label", "reason", "parent_item_id", "exclude_other_parents"),
    "regional_overrides": ("item_id", "item_label", "reason", "overview_item_id", "exclude_other_parents"),
    "regional_overview_additions": ITEM_COLUMNS,
    "canonical_parent_additions": ITEM_COLUMNS,
}


@pytest.fixture(autouse=True)
def _fast_retries(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(wikidata_client._get_entities.retry, "wait", tenacity.wait_none())


def _setup(
    tmp_path: Path,
    rows: dict[str, list[dict]],
    tree_ids: list[str],
    indigenous_ids: list[str] = (),
    orphan_parent_ids: list[str] = (),
) -> Path:
    curation_dir = tmp_path / "curation"
    curation_dir.mkdir()
    for name, columns in LIST_COLUMNS.items():
        df = pl.DataFrame(rows.get(name, []), schema=dict.fromkeys(columns, pl.Utf8))
        df.write_csv(curation_dir / f"manual_{name}.csv")
    tree = [{"item_id": i, "parent_id": None, "parent_label": None} for i in tree_ids]
    tree += [{"item_id": tree_ids[0], "parent_id": i, "parent_label": f"music of {i}"} for i in orphan_parent_ids]
    pl.DataFrame(tree, schema={"item_id": pl.Utf8, "parent_id": pl.Utf8, "parent_label": pl.Utf8}).write_parquet(
        tmp_path / "tree.parquet"
    )
    pl.DataFrame({"item_id": list(indigenous_ids)}, schema={"item_id": pl.Utf8}).write_parquet(
        tmp_path / "indigenous.parquet"
    )
    return curation_dir


def _resolve(tmp_path: Path, curation_dir: Path) -> dict:
    path = cr.resolve_curation_qids(
        tmp_path / "tree.parquet", tmp_path / "indigenous.parquet", curation_dir, tmp_path / "silver"
    )
    assert path == tmp_path / "silver" / "curation_resolutions.json"
    return json.loads(path.read_text())


def _mock_api(monkeypatch: pytest.MonkeyPatch, entities: dict) -> MagicMock:
    def get(*args: object, params: dict, **kwargs: object) -> MagicMock:
        response = MagicMock()
        response.json.return_value = {"entities": {i: entities[i] for i in params["ids"].split("|") if i in entities}}
        return response

    httpx_get = MagicMock(side_effect=get)
    monkeypatch.setattr(wikidata_client.httpx, "get", httpx_get)
    return httpx_get


def _redirect(source: str, target: str) -> dict:
    return {"id": target, "redirects": {"from": source, "to": target}}


MODIFIED = "2026-05-01T10:00:00Z"


def _claims(pid: str, *targets: str) -> dict:
    return {pid: [{"mainsnak": {"datavalue": {"value": {"id": t}}}, "rank": "normal"} for t in targets]}


def _entity(item_id: str, label: str, claims: dict | None = None, redirect_from: str | None = None) -> dict:
    redirects = {"redirects": {"from": redirect_from, "to": item_id}} if redirect_from else {}
    return {
        "id": item_id,
        "labels": {"en": {"language": "en", "value": label}},
        "modified": MODIFIED,
        "claims": claims or {},
        **redirects,
    }


def _item(item_id: str) -> dict:
    return {"item_id": item_id, "item_label": item_id, "reason": "r"}


def _read(curation_dir: Path, name: str) -> list[dict]:
    return pl.read_csv(curation_dir / f"manual_{name}.csv", infer_schema=False).to_dicts()


def test_no_unknown_ids_skips_http_and_writes_empty_report(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    curation_dir = _setup(
        tmp_path,
        {
            "technique_genres": [_item("Q1")],
            "main_parent": [{**_item("Q1"), "parent_item_id": "LOCAL:x", "exclude_other_parents": None}],
            "canonical_parent_additions": [_item("LOCAL:x")],
        },
        ["Q1"],
    )
    httpx_get = _mock_api(monkeypatch, {})

    assert _resolve(tmp_path, curation_dir) == {"remaps": [], "review": []}
    httpx_get.assert_not_called()


def test_orphan_overview_parent_is_known_only_to_regional_overrides(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    override = {**_item("Q1"), "overview_item_id": "Q9", "exclude_other_parents": None}
    curation_dir = _setup(
        tmp_path, {"regional_overrides": [{**override, "item_id": "Q9"}]}, ["Q1"], orphan_parent_ids=["Q9"]
    )
    httpx_get = _mock_api(monkeypatch, {})

    assert _resolve(tmp_path, curation_dir) == {"remaps": [], "review": []}
    httpx_get.assert_not_called()

    (tmp_path / "b").mkdir()
    curation_dir = _setup(tmp_path / "b", {"technique_genres": [_item("Q9")]}, ["Q1"], orphan_parent_ids=["Q9"])
    httpx_get = _mock_api(monkeypatch, {"Q9": {"id": "Q9"}})

    assert [r["reason"] for r in _resolve(tmp_path / "b", curation_dir)["review"]] == ["not_in_tree"]
    httpx_get.assert_called_once()


def test_remaps_redirect_and_pruning_passes(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    curation_dir = _setup(
        tmp_path,
        {
            "technique_genres": [_item("Q126287874")],
            "main_parent": [{**_item("Q1"), "parent_item_id": "Q7", "exclude_other_parents": "true"}],
        },
        ["Q1", "Q64152411", "Q8"],
    )
    _mock_api(monkeypatch, {"Q126287874": _redirect("Q126287874", "Q64152411"), "Q7": _redirect("Q7", "Q8")})

    assert _resolve(tmp_path, curation_dir) == {
        "remaps": [
            {"list": "main_parent", "key": "Q1", "column": "parent_item_id", "from": "Q7", "to": "Q8"},
            {
                "list": "technique_genres",
                "key": "Q126287874",
                "column": "item_id",
                "from": "Q126287874",
                "to": "Q64152411",
            },
        ],
        "review": [],
    }
    assert _read(curation_dir, "technique_genres") == [
        {"item_id": "Q64152411", "item_label": "Q126287874", "reason": "r"}
    ]
    assert _read(curation_dir, "main_parent")[0]["parent_item_id"] == "Q8"

    item_links = tmp_path / "1_item_links.parquet"
    pl.DataFrame({"item_id": ["Q1", "Q64152411", "Q8"]}).write_parquet(item_links)
    output = prune_non_genre_items(
        item_links,
        *(curation_dir / f"manual_{name}.csv" for name in ("theme_genres", "technique_genres", "out_of_scope_genres")),
        curation_dir / "manual_umbrella_canonical_genres.csv",
        curation_dir / "manual_duplicate_genres.csv",
        tmp_path / "silver",
    )
    assert pl.read_parquet(output).get_column("item_id").to_list() == ["Q1", "Q8"]


def test_review_reasons_drop_rows_with_evidence(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    curation_dir = _setup(
        tmp_path,
        {
            "theme_genres": [_item("Q1"), _item("Q2"), _item("Q3"), _item("Q4"), _item("Q5")],
            "indigenous_to_exclusions": [_item("Q6")],
        },
        ["Q5", "Q10", "Q11"],
        indigenous_ids=["Q11"],
    )
    _mock_api(
        monkeypatch,
        {
            "Q1": {"id": "Q1", "missing": ""},
            "Q2": _entity("Q2", "Two", {**_claims("P31", "Q50"), **_claims("P279", "Q51", "Q52")}),
            "Q3": _entity("Q99", "Ninety-nine", _claims("P31", "Q50"), redirect_from="Q3"),
            "Q4": _entity("Q5", "Five", redirect_from="Q4"),
            "Q6": _entity("Q10", "Ten", redirect_from="Q6"),
            "Q50": {"id": "Q50", "labels": {"fr": {"language": "fr", "value": "classe"}}},
            "Q51": {"id": "Q51", "labels": {}},
            "Q52": {"id": "Q52", "labels": {"en": {"language": "en", "value": "Parent"}}},
        },
    )

    report = _resolve(tmp_path, curation_dir)

    assert report["remaps"] == []
    assert report["review"] == [
        {
            "list": "indigenous_to_exclusions",
            "key": "Q6",
            "column": "item_id",
            "itemId": "Q6",
            "reason": "not_in_tree",
            "target": "Q10",
            "evidence": {"label": "Ten", "modified": MODIFIED, "hasIndigenousTo": False},
        },
        {"list": "theme_genres", "key": "Q1", "column": "item_id", "itemId": "Q1", "reason": "missing"},
        {
            "list": "theme_genres",
            "key": "Q2",
            "column": "item_id",
            "itemId": "Q2",
            "reason": "not_in_tree",
            "evidence": {
                "label": "Two",
                "modified": MODIFIED,
                "instanceOf": [{"id": "Q50", "label": "classe"}],
                "subclassOf": [{"id": "Q51", "label": None}, {"id": "Q52", "label": "Parent"}],
            },
        },
        {
            "list": "theme_genres",
            "key": "Q3",
            "column": "item_id",
            "itemId": "Q3",
            "reason": "not_in_tree",
            "target": "Q99",
            "evidence": {
                "label": "Ninety-nine",
                "modified": MODIFIED,
                "instanceOf": [{"id": "Q50", "label": "classe"}],
                "subclassOf": [],
            },
        },
        {
            "list": "theme_genres",
            "key": "Q4",
            "column": "item_id",
            "itemId": "Q4",
            "reason": "redirect_conflict",
            "target": "Q5",
            "evidence": {"label": "Five", "modified": MODIFIED, "conflictList": "theme_genres"},
        },
    ]
    assert [row["item_id"] for row in _read(curation_dir, "theme_genres")] == ["Q5"]
    assert _read(curation_dir, "indigenous_to_exclusions") == []


def test_indigenous_item_with_p2341_still_out_of_reference_reports_it(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    curation_dir = _setup(tmp_path, {"indigenous_to_exclusions": [_item("Q1")]}, [], indigenous_ids=[])
    _mock_api(monkeypatch, {"Q1": _entity("Q1", "One", _claims("P2341", "Q7"))})

    report = _resolve(tmp_path, curation_dir)

    assert report["review"][0]["evidence"] == {"label": "One", "modified": MODIFIED, "hasIndigenousTo": True}


def test_exclusive_list_collision_is_reviewed(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    curation_dir = _setup(
        tmp_path,
        {"technique_genres": [_item("Q1")], "duplicate_genres": [_item("Q2")], "label_overrides": []},
        ["Q2"],
    )
    _mock_api(monkeypatch, {"Q1": _entity("Q2", "Two", redirect_from="Q1")})

    report = _resolve(tmp_path, curation_dir)

    assert report["review"] == [
        {
            "list": "technique_genres",
            "key": "Q1",
            "column": "item_id",
            "itemId": "Q1",
            "reason": "redirect_conflict",
            "target": "Q2",
            "evidence": {"label": "Two", "modified": MODIFIED, "conflictList": "duplicate_genres"},
        }
    ]
    assert _read(curation_dir, "technique_genres") == []


def test_non_exclusive_list_may_share_target_with_exclusive_list(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    curation_dir = _setup(
        tmp_path,
        {"umbrella_canonical_genres": [_item("Q1")], "duplicate_genres": [_item("Q2")]},
        ["Q2"],
    )
    _mock_api(monkeypatch, {"Q1": _redirect("Q1", "Q2")})

    report = _resolve(tmp_path, curation_dir)

    assert report["review"] == []
    assert [row["item_id"] for row in _read(curation_dir, "umbrella_canonical_genres")] == ["Q2"]


def test_parent_remap_onto_pruned_or_own_item_is_reviewed(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    curation_dir = _setup(
        tmp_path,
        {
            "main_parent": [
                {**_item("Q1"), "parent_item_id": "Q2", "exclude_other_parents": None},
                {**_item("Q3"), "parent_item_id": "Q4", "exclude_other_parents": None},
            ],
            "umbrella_canonical_genres": [_item("Q5")],
        },
        ["Q1", "Q3", "Q5"],
    )
    _mock_api(
        monkeypatch, {"Q2": _entity("Q1", "One", redirect_from="Q2"), "Q4": _entity("Q5", "Five", redirect_from="Q4")}
    )

    report = _resolve(tmp_path, curation_dir)

    assert report["remaps"] == []
    assert [(r["key"], r["reason"], r["target"], r["evidence"]["conflictList"]) for r in report["review"]] == [
        ("Q1", "redirect_conflict", "Q1", "main_parent"),
        ("Q3", "redirect_conflict", "Q5", "umbrella_canonical_genres"),
    ]
    assert _read(curation_dir, "main_parent") == []
