from unittest.mock import MagicMock

import pytest

from wikidata import wikidata_client


def _mock_response(bindings: list[dict]) -> MagicMock:
    response = MagicMock()
    response.json.return_value = {"results": {"bindings": bindings}}
    return response


def test_run_query_sends_query_and_headers(monkeypatch: pytest.MonkeyPatch) -> None:
    httpx_get = MagicMock(return_value=_mock_response([]))
    monkeypatch.setattr(wikidata_client.httpx, "get", httpx_get)

    wikidata_client.run_query("SELECT * WHERE {}", timeout=10.0)

    httpx_get.assert_called_once_with(
        wikidata_client.SPARQL_ENDPOINT,
        params={"query": "SELECT * WHERE {}"},
        headers={"User-Agent": wikidata_client.USER_AGENT, "Accept": "application/sparql-results+json"},
        timeout=10.0,
    )


def test_run_query_raises_status_errors(monkeypatch: pytest.MonkeyPatch) -> None:
    response = _mock_response([])
    monkeypatch.setattr(wikidata_client.httpx, "get", MagicMock(return_value=response))

    wikidata_client.run_query("SELECT * WHERE {}")

    response.raise_for_status.assert_called_once()


def test_run_query_parses_bindings_and_defaults_unbound_optional_fields_to_none(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    bindings = [
        {
            "item": {"value": "http://www.wikidata.org/entity/Q11399"},
            "itemLabel": {"value": "rock music"},
            "parent": {"value": "http://www.wikidata.org/entity/Q188451"},
            "parentLabel": {"value": "music genre"},
        },
        {
            "item": {"value": "http://www.wikidata.org/entity/Q188451"},
            "itemLabel": {"value": "music genre"},
        },
    ]
    monkeypatch.setattr(wikidata_client.httpx, "get", MagicMock(return_value=_mock_response(bindings)))

    result = wikidata_client.run_query(wikidata_client.GENRE_TREE_QUERY)

    assert result == [
        {
            "item": "http://www.wikidata.org/entity/Q11399",
            "itemLabel": "rock music",
            "parent": "http://www.wikidata.org/entity/Q188451",
            "parentLabel": "music genre",
        },
        {
            "item": "http://www.wikidata.org/entity/Q188451",
            "itemLabel": "music genre",
            "parent": None,
            "parentLabel": None,
        },
    ]
