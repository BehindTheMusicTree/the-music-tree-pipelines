from pathlib import Path

import httpx
import polars as pl
import pytest

from curation import ingest

BASE_URL = "https://grow.example/v0/"


def _export() -> dict[str, list[dict[str, str]]]:
    export: dict[str, list[dict[str, str]]] = {name: [] for name in ingest.CURATION_LISTS}
    export["main_parent"] = [
        {
            "item_id": "Q9794",
            "item_label": "reggae",
            "reason": 'regrouped under "Reggae/Dub", see DESIGN.md',
            "parent_item_id": "LOCAL:reggae-dub",
            "exclude_other_parents": "true",
        },
        {
            "item_id": "Q1",
            "item_label": "rock",
            "reason": "r",
            "parent_item_id": "Q2",
            "exclude_other_parents": "",
        },
    ]
    return export


def _mock_get(monkeypatch: pytest.MonkeyPatch, status_code: int, payload: object) -> list[httpx.Request]:
    requests: list[httpx.Request] = []

    def fake_get(url: str, headers: dict[str, str], timeout: int) -> httpx.Response:
        request = httpx.Request("GET", url, headers=headers)
        requests.append(request)
        return httpx.Response(status_code, json=payload, request=request)

    monkeypatch.setattr(ingest.httpx, "get", fake_get)
    return requests


def test_fetch_and_write_round_trips_csvs(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    requests = _mock_get(monkeypatch, 200, _export())

    paths = ingest.write_curation_csvs(ingest.fetch_curation_export(BASE_URL, "secret"), tmp_path)

    assert str(requests[0].url) == f"{BASE_URL}curation/export/"
    assert requests[0].headers["X-API-Key"] == "secret"
    assert sorted(path.name for path in paths) == sorted(f"manual_{name}.csv" for name in ingest.CURATION_LISTS)

    main_parent = pl.read_csv(tmp_path / "manual_main_parent.csv")
    assert main_parent.columns == list(ingest.CURATION_LISTS["main_parent"])
    assert main_parent.get_column("exclude_other_parents").to_list() == [True, None]
    assert main_parent.get_column("reason")[0] == 'regrouped under "Reggae/Dub", see DESIGN.md'

    empty = pl.read_csv(tmp_path / "manual_genre_alias.csv")
    assert empty.is_empty()
    assert empty.columns == ["musicbrainz_genre_name", "wikidata_genre_name", "reason"]


def test_fetch_raises_on_non_2xx(monkeypatch: pytest.MonkeyPatch) -> None:
    _mock_get(monkeypatch, 403, {"detail": "forbidden"})

    with pytest.raises(httpx.HTTPStatusError):
        ingest.fetch_curation_export(BASE_URL, "wrong")


def test_write_raises_on_missing_list(tmp_path: Path) -> None:
    export = _export()
    del export["theme_genres"]

    with pytest.raises(ValueError, match="missing list.*theme_genres"):
        ingest.write_curation_csvs(export, tmp_path)


def test_write_raises_on_column_mismatch(tmp_path: Path) -> None:
    export = _export()
    export["genre_alias"] = [{"wikidata_genre_name": "rock", "musicbrainz_genre_name": "rock", "reason": "r"}]

    with pytest.raises(ValueError, match="genre_alias"):
        ingest.write_curation_csvs(export, tmp_path)
