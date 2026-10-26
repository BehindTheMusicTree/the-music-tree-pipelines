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
        headers={
            "User-Agent": wikidata_client.USER_AGENT,
            "Accept": "application/sparql-results+json",
            "Cache-Control": "no-cache",
        },
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


def _entities_response(entities: dict) -> MagicMock:
    response = MagicMock()
    response.json.return_value = {"entities": entities, "success": 1}
    return response


def test_fetch_entities_resolves_redirects_and_missing_in_batches(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(wikidata_client._get_entities.retry, "wait", tenacity.wait_none())
    monkeypatch.setattr(wikidata_client, "WBGETENTITIES_MAX_IDS", 2)
    httpx_get = MagicMock(
        side_effect=[
            _entities_response(
                {
                    "Q1": {"id": "Q2", "redirects": {"from": "Q1", "to": "Q2"}},
                    "Q3": {"id": "Q3", "missing": ""},
                }
            ),
            _entities_response({"Q4": {"id": "Q4"}}),
        ]
    )
    monkeypatch.setattr(wikidata_client.httpx, "get", httpx_get)

    result = wikidata_client.fetch_entities(["Q1", "Q3", "Q4"])
    assert {qid: info and info.id for qid, info in result.items()} == {"Q1": "Q2", "Q3": None, "Q4": "Q4"}
    assert httpx_get.call_count == 2
    assert httpx_get.call_args_list[0].kwargs["params"] == {
        "action": "wbgetentities",
        "ids": "Q1|Q3",
        "props": "info|labels|claims",
        "languages": "en|mul|es|fr|de|pt|it|nl|ru|ja|zh",
        "format": "json",
    }
    assert httpx_get.call_args_list[0].kwargs["headers"] == {"User-Agent": wikidata_client.USER_AGENT}


def test_fetch_entities_retries_transport_errors(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(wikidata_client._get_entities.retry, "wait", tenacity.wait_none())
    httpx_get = MagicMock(side_effect=[httpx.ConnectError("boom"), _entities_response({"Q1": {"id": "Q1"}})])
    monkeypatch.setattr(wikidata_client.httpx, "get", httpx_get)

    assert wikidata_client.fetch_entities(["Q1"])["Q1"].id == "Q1"
    assert httpx_get.call_count == 2


def test_fetch_entities_extracts_label_modified_and_claims(monkeypatch: pytest.MonkeyPatch) -> None:
    def statement(value: str, rank: str = "normal") -> dict:
        return {"mainsnak": {"datavalue": {"value": {"id": value}}}, "rank": rank}

    entity = {
        "id": "Q1",
        "modified": "2026-05-01T10:00:00Z",
        "labels": {"fr": {"value": "un"}, "es": {"value": "uno"}},
        "claims": {
            "P31": [statement("Q5"), statement("Q6", "deprecated"), {"mainsnak": {"snaktype": "novalue"}}],
            "P279": [statement("Q7")],
            "P2341": [statement("Q8")],
        },
    }
    monkeypatch.setattr(wikidata_client.httpx, "get", MagicMock(return_value=_entities_response({"Q1": entity})))

    assert wikidata_client.fetch_entities(["Q1"]) == {
        "Q1": wikidata_client.EntityInfo("Q1", "uno", "2026-05-01T10:00:00Z", ["Q5"], ["Q7"], True)
    }


def test_fetch_entities_keeps_only_preferred_claims_when_present(monkeypatch: pytest.MonkeyPatch) -> None:
    def statement(value: str, rank: str) -> dict:
        return {"mainsnak": {"datavalue": {"value": {"id": value}}}, "rank": rank}

    entity = {"id": "Q1", "claims": {"P31": [statement("Q5", "normal"), statement("Q6", "preferred")]}}
    monkeypatch.setattr(wikidata_client.httpx, "get", MagicMock(return_value=_entities_response({"Q1": entity})))

    assert wikidata_client.fetch_entities(["Q1"])["Q1"].instance_of == ["Q6"]


def test_fetch_labels_returns_none_without_label(monkeypatch: pytest.MonkeyPatch) -> None:
    entities = {"Q1": {"id": "Q1", "labels": {"en": {"value": "one"}}}, "Q2": {"id": "Q2", "labels": {}}}
    monkeypatch.setattr(wikidata_client.httpx, "get", MagicMock(return_value=_entities_response(entities)))

    assert wikidata_client.fetch_labels(["Q1", "Q2"]) == {"Q1": "one", "Q2": None}


def test_fetch_entities_raises_on_api_error(monkeypatch: pytest.MonkeyPatch) -> None:
    response = MagicMock()
    response.json.return_value = {"error": {"code": "no-such-entity"}}
    monkeypatch.setattr(wikidata_client.httpx, "get", MagicMock(return_value=response))

    with pytest.raises(ValueError, match="no-such-entity"):
        wikidata_client.fetch_entities(["Q1"])


def test_fetch_entities_raises_on_absent_entity(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(wikidata_client.httpx, "get", MagicMock(return_value=_entities_response({})))

    with pytest.raises(ValueError, match="no entity"):
        wikidata_client.fetch_entities(["Q1"])


def test_fetch_descriptions_extracts_english_description_and_enwiki_sitelink(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(wikidata_client, "WBGETENTITIES_MAX_IDS", 2)
    enwiki = {"site": "enwiki", "title": "Rock music", "url": "https://en.wikipedia.org/wiki/Rock_music"}
    httpx_get = MagicMock(
        side_effect=[
            _entities_response(
                {
                    "Q11399": {
                        "descriptions": {"en": {"value": "genre of popular music"}, "fr": {"value": "genre"}},
                        "sitelinks": {"enwiki": enwiki},
                    },
                    "Q2": {"descriptions": {"fr": {"value": "genre"}}, "sitelinks": {"enwiki": enwiki}},
                }
            ),
            _entities_response({"Q3": {"descriptions": {"en": {"value": "music of a region"}}}}),
        ]
    )
    monkeypatch.setattr(wikidata_client.httpx, "get", httpx_get)

    result = wikidata_client.fetch_descriptions(["Q11399", "Q2", "Q3"])

    assert result == {
        "Q11399": wikidata_client.Description("genre of popular music", "Rock music", enwiki["url"]),
        "Q2": wikidata_client.Description(None, "Rock music", enwiki["url"]),
        "Q3": wikidata_client.Description("music of a region", None, None),
    }
    params = httpx_get.call_args_list[0].kwargs["params"]
    assert (params["ids"], params["props"], params["sitefilter"]) == (
        "Q11399|Q2",
        "descriptions|sitelinks/urls",
        "enwiki",
    )


def test_cultural_entity_query_requires_glottocode_only_for_languoids() -> None:
    languoid = wikidata_client.cultural_entity_query("languoid")
    culture = wikidata_client.cultural_entity_query("archaeological_culture")

    assert "OPTIONAL" not in languoid
    assert "OPTIONAL { ?item wdt:P1394 ?glottocode . }" in culture
    assert "wd:Q465299" in culture


def test_cultural_entity_claim_query_reads_date_precision_and_skips_deprecated_statements() -> None:
    date_query = wikidata_client.cultural_entity_claim_query("historical_country", "P571")
    item_query = wikidata_client.cultural_entity_claim_query("historical_country", "P1366")

    assert "wikibase:timePrecision ?precision" in date_query
    assert "DeprecatedRank" in date_query
    assert "?item wdt:P1366 ?value ." in item_query
    assert "precision" not in item_query.split("WHERE", 1)[1]


def test_cultural_entity_query_selects_the_period_targets_of_cultures_and_countries() -> None:
    period = wikidata_client.cultural_entity_query("time_period")

    assert "wdt:P2348 ?item" in period
    assert "wd:Q465299" in period
    assert "wd:Q3024240" in period
