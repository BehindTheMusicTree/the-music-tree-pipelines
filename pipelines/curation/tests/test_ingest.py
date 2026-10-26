import json
from pathlib import Path
from typing import Any

import httpx
import polars as pl
import pytest

from curation import ingest

BASE_URL = "https://grow.example/v0/"


EXPORTED_ON = "2026-10-16T08:00:00+00:00"


def _export() -> dict[str, Any]:
    export: dict[str, Any] = {name: [] for name in ingest.CURATION_LISTS}
    export["exported_on"] = EXPORTED_ON
    export["main_parent"] = [
        {
            "item_id": "Q9794",
            "reason": 'regrouped under "Reggae/Dub", see DESIGN.md',
            "parent_item_id": "LOCAL:reggae-dub",
            "exclude_other_parents": "true",
        },
        {
            "item_id": "Q1",
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
    assert sorted(path.name for path in paths) == sorted(
        [f"manual_{name}.csv" for name in ingest.CURATION_LISTS] + ["manual_export.json"]
    )
    assert json.loads((tmp_path / "manual_export.json").read_text()) == {"exportedOn": EXPORTED_ON}

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
    export["genre_alias"] = [{"wikidata_genre_name": "rock", "musicbrainz_genre_name": "rock"}]

    with pytest.raises(ValueError, match="genre_alias"):
        ingest.write_curation_csvs(export, tmp_path)


def test_write_accepts_api_column_order(tmp_path: Path) -> None:
    export = _export()
    export["secondary_parents"] = [
        {
            "item_id": "Q1",
            "parent_id": "Q2",
            "action": "add",
            "reason": "r",
        }
    ]
    export["song_genre"] = [{"musicbrainz_recording_id": "rec-1", "genre_item_id": "Q1", "reason": ""}]

    ingest.write_curation_csvs(export, tmp_path)

    secondary = pl.read_csv(tmp_path / "manual_secondary_parents.csv")
    assert secondary.columns == list(ingest.CURATION_LISTS["secondary_parents"])
    assert secondary.row(0) == ("Q1", "Q2", "add", "r")
    song_genre = pl.read_csv(tmp_path / "manual_song_genre.csv")
    assert song_genre.columns == list(ingest.CURATION_LISTS["song_genre"])
    assert song_genre.row(0) == ("rec-1", "Q1", None)


def test_write_raises_on_missing_exported_on(tmp_path: Path) -> None:
    export = _export()
    del export["exported_on"]

    with pytest.raises(ValueError, match="exported_on"):
        ingest.write_curation_csvs(export, tmp_path)

    assert not tmp_path.joinpath("manual_export.json").exists()
