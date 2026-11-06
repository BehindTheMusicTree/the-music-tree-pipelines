import pytest
import polars as pl

from wikidata import wikidata_client
from wikidata.ingest import ingest_place_statements
from wikidata.place_statements import disambiguate_no_values, normalize_qualifiers, normalize_statements


def statement(identifier, *, rank="NormalRank", snak="value", value="Q2"):
    return {
        "item": "http://www.wikidata.org/entity/Q1",
        "property": "P706",
        "statement": identifier,
        "rank": f"http://wikiba.se/ontology#{rank}",
        "value": f"http://www.wikidata.org/entity/{value}" if value else None,
        "snakType": snak,
    }


def test_normalize_keeps_each_statement_rank_and_snak_type():
    rows = [
        statement("s1"),
        statement("s2", rank="PreferredRank", value="Q3"),
        statement("s3", rank="DeprecatedRank", snak="novalue", value=None),
        statement("s4", snak="somevalue", value=None),
    ]
    frame = normalize_statements(rows)
    assert frame.select("statement_id", "object_item_id", "rank", "snak_type").rows() == [
        ("s1", "Q2", "normal", "value"),
        ("s2", "Q3", "preferred", "value"),
        ("s3", None, "deprecated", "novalue"),
        ("s4", None, "normal", "somevalue"),
    ]


def test_normalize_rejects_invalid_or_duplicate_results():
    with pytest.raises(ValueError, match="invalid geographic statement"):
        normalize_statements([statement("s", snak="value", value=None)])
    with pytest.raises(ValueError, match="invalid geographic statement"):
        normalize_statements([statement("s", snak="invalid")])
    with pytest.raises(ValueError, match="duplicate"):
        normalize_statements([statement("s"), statement("s")])


def test_statement_queries_validate_and_chunk_distinct_subjects(monkeypatch):
    monkeypatch.setattr(wikidata_client, "ITEM_CLAIM_CHUNK", 2)
    queries = wikidata_client.place_statement_queries(["Q2", "Q1", "Q1", "Q3"])
    assert len(queries) == 2
    assert "VALUES ?item { wd:Q1 wd:Q2 }" in queries[0]
    assert "VALUES ?item { wd:Q3 }" in queries[1]
    for pid in wikidata_client.PLACE_STATEMENT_PIDS:
        assert f"?item p:{pid} ?statement" in queries[0]
        assert f"?statement ps:{pid} ?value" in queries[0]
    assert wikidata_client.place_statement_queries([]) == []
    with pytest.raises(ValueError, match="Wikidata QIDs"):
        wikidata_client.place_statement_queries(["not-a-qid"])


def test_qualifiers_remain_attached_and_preserve_time_and_unknown_values():
    statements = normalize_statements([statement("s1"), statement("s2", value="Q3")])
    rows = [
        {
            "statement": "s1",
            "qualifierProperty": "http://www.wikidata.org/entity/P518",
            "value": f"http://www.wikidata.org/entity/{qid}",
            "valueType": "http://wikiba.se/ontology#WikibaseItem",
            "snakType": "value",
            "precision": None,
        }
        for qid in ("Q4", "Q5")
    ]
    rows.extend(
        [
            {
                "statement": "s2",
                "qualifierProperty": "http://www.wikidata.org/entity/P580",
                "value": "+1800-01-01T00:00:00Z",
                "valueType": "http://wikiba.se/ontology#Time",
                "snakType": "value",
                "precision": "9",
            },
            {
                "statement": "s2",
                "qualifierProperty": "http://www.wikidata.org/entity/P518",
                "value": None,
                "valueType": "http://wikiba.se/ontology#WikibaseItem",
                "snakType": "somevalue",
                "precision": None,
            },
        ]
    )
    result = normalize_qualifiers(rows, statements)
    assert result.select("statement_id", "qualifier_property", "value", "snak_type", "time_precision").rows() == [
        ("s1", "P518", "Q4", "value", None),
        ("s1", "P518", "Q5", "value", None),
        ("s2", "P518", None, "somevalue", None),
        ("s2", "P580", "+1800-01-01T00:00:00Z", "value", 9),
    ]


def test_ambiguous_no_value_and_missing_precision_fail_loudly():
    statements = normalize_statements([statement("s", snak="novalue", value=None)])
    ambiguous = {
        "statement": "s",
        "qualifierProperty": "http://www.wikidata.org/entity/P706",
        "value": None,
        "valueType": "http://wikiba.se/ontology#WikibaseItem",
        "snakType": "novalue",
        "precision": None,
    }
    with pytest.raises(ValueError, match="ambiguous no-value"):
        normalize_qualifiers([ambiguous], statements)
    time = ambiguous | {
        "qualifierProperty": "http://www.wikidata.org/entity/P580",
        "value": "1800-01-01T00:00:00Z",
        "valueType": "http://wikiba.se/ontology#Time",
        "snakType": "value",
    }
    with pytest.raises(ValueError, match="missing precision"):
        normalize_qualifiers([time], statements)


def test_api_disambiguates_no_value_main_snak_from_qualifier():
    uri = "http://www.wikidata.org/entity/statement/Q1-abcdef"
    statements = normalize_statements([statement(uri, snak="novalue", value=None)])
    row = {
        "statement": uri,
        "qualifierProperty": "http://www.wikidata.org/entity/P706",
        "value": None,
        "valueType": "http://wikiba.se/ontology#WikibaseItem",
        "snakType": "novalue",
        "precision": None,
    }
    claim = {"id": "Q1$ABCDEF", "qualifiers": {}}

    def fetch(ids):
        assert ids == ["Q1"]
        return {"Q1": {"claims": {"P706": [claim]}}}

    filtered, confirmed = disambiguate_no_values([row], statements, fetch)
    assert filtered == []
    assert confirmed == set()
    claim["qualifiers"] = {"P706": [{"snaktype": "novalue"}]}
    filtered, confirmed = disambiguate_no_values([row], statements, fetch)
    assert normalize_qualifiers(filtered, statements, confirmed)["snak_type"].to_list() == ["novalue"]
    with pytest.raises(ValueError, match="missing item"):
        disambiguate_no_values([row], statements, lambda _: {})


def test_ingest_writes_distinct_statements_and_their_qualifiers(tmp_path, monkeypatch):
    qualifier = {
        "statement": "s1",
        "qualifierProperty": "http://www.wikidata.org/entity/P518",
        "value": "http://www.wikidata.org/entity/Q4",
        "valueType": "http://wikiba.se/ontology#WikibaseItem",
        "snakType": "value",
        "precision": None,
    }

    def run_query(query, *, variables, timeout):
        if variables == wikidata_client.PLACE_STATEMENT_QUERY_VARIABLES:
            return [statement("s1"), statement("s2", value="Q3")]
        return [qualifier]

    monkeypatch.setattr(wikidata_client, "run_query", run_query)
    statements_path, qualifiers_path = ingest_place_statements(tmp_path, ["Q1"])
    assert pl.read_parquet(statements_path).height == 2
    assert pl.read_parquet(qualifiers_path).select("statement_id", "value").rows() == [("s1", "Q4")]
