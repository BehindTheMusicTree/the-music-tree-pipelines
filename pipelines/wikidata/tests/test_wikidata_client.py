import json
from unittest.mock import MagicMock

import httpx
import pytest
import tenacity

from wikidata import wikidata_client


@pytest.fixture(autouse=True)
def _fast_retries(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(wikidata_client.run_query.retry, "wait", tenacity.wait_none())


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
            "relation": {"value": "P279"},
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
            "relation": "P279",
        },
        {
            "item": "http://www.wikidata.org/entity/Q188451",
            "itemLabel": "music genre",
            "parent": None,
            "parentLabel": None,
            "relation": None,
        },
    ]


def test_run_query_retries_transient_errors_then_succeeds(monkeypatch: pytest.MonkeyPatch) -> None:
    truncated_response = MagicMock()
    truncated_response.json.side_effect = json.JSONDecodeError("Unterminated string", "", 0)
    httpx_get = MagicMock(side_effect=[truncated_response, _mock_response([])])
    monkeypatch.setattr(wikidata_client.httpx, "get", httpx_get)

    result = wikidata_client.run_query("SELECT * WHERE {}")

    assert result == []
    assert httpx_get.call_count == 2


def test_run_query_reraises_after_exhausting_retries(monkeypatch: pytest.MonkeyPatch) -> None:
    response = MagicMock()
    response.json.side_effect = json.JSONDecodeError("Unterminated string", "", 0)
    httpx_get = MagicMock(return_value=response)
    monkeypatch.setattr(wikidata_client.httpx, "get", httpx_get)

    with pytest.raises(json.JSONDecodeError):
        wikidata_client.run_query("SELECT * WHERE {}")

    assert httpx_get.call_count == wikidata_client.run_query.retry.stop.max_attempt_number


def test_run_query_uses_given_variables_to_extract_bindings(monkeypatch: pytest.MonkeyPatch) -> None:
    bindings = [
        {
            "item": {"value": "http://www.wikidata.org/entity/Q10376827"},
            "indigenousTo": {"value": "http://www.wikidata.org/entity/Q49103"},
            "indigenousToLabel": {"value": "Han Chinese"},
        }
    ]
    monkeypatch.setattr(wikidata_client.httpx, "get", MagicMock(return_value=_mock_response(bindings)))

    result = wikidata_client.run_query(
        wikidata_client.INDIGENOUS_TO_QUERY, variables=wikidata_client.INDIGENOUS_TO_QUERY_VARIABLES
    )

    assert result == [
        {
            "item": "http://www.wikidata.org/entity/Q10376827",
            "indigenousTo": "http://www.wikidata.org/entity/Q49103",
            "indigenousToLabel": "Han Chinese",
        }
    ]


def test_run_query_retries_transport_errors(monkeypatch: pytest.MonkeyPatch) -> None:
    httpx_get = MagicMock(side_effect=[httpx.ConnectError("boom"), _mock_response([])])
    monkeypatch.setattr(wikidata_client.httpx, "get", httpx_get)

    result = wikidata_client.run_query("SELECT * WHERE {}")

    assert result == []
    assert httpx_get.call_count == 2
