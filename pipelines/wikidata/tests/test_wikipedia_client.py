from unittest.mock import MagicMock

import pytest

from wikidata import wikipedia_client


def _response(query: dict) -> MagicMock:
    response = MagicMock()
    response.json.return_value = {"query": query}
    return response


def test_fetch_intros_batches_titles_by_twenty(monkeypatch: pytest.MonkeyPatch) -> None:
    get = MagicMock(return_value=_response({"pages": []}))
    monkeypatch.setattr(wikipedia_client.httpx, "get", get)

    wikipedia_client.fetch_intros([f"T{i}" for i in range(45)])

    assert get.call_count == 3


def test_fetch_intros_maps_normalization_redirect_and_missing_page(monkeypatch: pytest.MonkeyPatch) -> None:
    query = {
        "normalized": [{"from": "rock music", "to": "Rock music"}],
        "redirects": [{"from": "Rock music", "to": "Rock"}],
        "pages": [{"title": "Rock", "extract": " Rock is a genre. \n"}, {"title": "Nope", "missing": True}],
    }
    monkeypatch.setattr(wikipedia_client.httpx, "get", MagicMock(return_value=_response(query)))

    assert wikipedia_client.fetch_intros(["rock music", "Nope"]) == {"rock music": "Rock is a genre.", "Nope": None}
