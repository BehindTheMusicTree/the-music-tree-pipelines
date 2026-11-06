import pytest

from wikidata import wikidata_client
from wikidata.place_statements import normalize_statements


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
