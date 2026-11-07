import pytest
import polars as pl

from wikidata import wikidata_client
from wikidata.ingest import ingest_place_statements
from wikidata.place_statements import (
    discover_place_parts,
    disambiguate_no_values,
    normalize_qualifiers,
    normalize_statements,
)


@pytest.fixture(autouse=True)
def mock_metadata_fetch(monkeypatch):
    monkeypatch.setattr(wikidata_client, "fetch_place_metadata_entities", lambda ids: {qid: {"id": qid} for qid in ids})


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


def test_normalize_rejects_invalid_or_conflicting_results():
    with pytest.raises(ValueError, match="invalid geographic statement"):
        normalize_statements([statement("s", snak="value", value=None)])
    with pytest.raises(ValueError, match="invalid geographic statement"):
        normalize_statements([statement("s", snak="invalid")])
    assert normalize_statements([statement("s"), statement("s")]).height == 1
    with pytest.raises(ValueError, match="conflicting"):
        normalize_statements([statement("s"), statement("s", value="Q3")])


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
        if "wd:Q4" in query:
            return []
        if variables == wikidata_client.PLACE_STATEMENT_QUERY_VARIABLES:
            return [statement("s1"), statement("s2", value="Q3")]
        return [qualifier]

    monkeypatch.setattr(wikidata_client, "run_query", run_query)
    monkeypatch.setattr(wikidata_client, "fetch_entities", lambda ids: {qid: None for qid in ids})
    statements_path, qualifiers_path, parts_path = ingest_place_statements(tmp_path, ["Q1"])
    assert pl.read_parquet(statements_path).height == 2
    assert pl.read_parquet(qualifiers_path).select("statement_id", "value").rows() == [("s1", "Q4")]
    assert pl.read_parquet(parts_path).select("requested_id", "status").rows() == [("Q4", "missing")]


def test_discovery_deduplicates_parts_and_tracks_redirects_and_missing():
    links = {"Q1": ["Q2", "Q2", "Q3", "Q4"], "Q5": ["Q1"]}

    def load(ids):
        return pl.DataFrame(
            [("P518", "wikibase-item", "value", target) for source in ids for target in links.get(source, [])],
            schema={
                "qualifier_property": pl.String,
                "value_type": pl.String,
                "snak_type": pl.String,
                "value": pl.String,
            },
            orient="row",
        )

    def fetch(ids):
        return {
            qid: None
            if qid == "Q3"
            else wikidata_client.EntityInfo("Q5" if qid == "Q2" else qid, None, None, [], [], False)
            for qid in ids
        }

    result = discover_place_parts(["Q1"], load, fetch, max_parts=4, max_depth=1)
    assert result.select("requested_id", "resolved_id", "status").rows() == [
        ("Q1", "Q1", "found"),
        ("Q2", "Q5", "redirect"),
        ("Q3", None, "missing"),
        ("Q4", "Q4", "found"),
    ]
    with pytest.raises(ValueError, match="count limit"):
        discover_place_parts(["Q1"], load, fetch, max_parts=2, max_depth=2)
    with pytest.raises(ValueError, match="depth limit"):
        discover_place_parts(["Q1"], load, fetch, max_parts=3, max_depth=0)


def test_ingest_rejects_conflicting_statement_ids_across_discovery_batches(tmp_path, monkeypatch):
    from wikidata import ingest
    from wikidata.place_statements import QUALIFIER_SCHEMA

    qualifier = pl.DataFrame(
        [("s1", "P518", "Q4", "wikibase-item", "value", None)],
        schema=QUALIFIER_SCHEMA,
        orient="row",
    )

    def fetch_statements(ids):
        if ids == ["Q1"]:
            return normalize_statements([statement("s1")]), qualifier
        return normalize_statements([statement("s1", value="Q3")]), pl.DataFrame(schema=QUALIFIER_SCHEMA)

    monkeypatch.setattr(ingest, "_fetch_place_statements", fetch_statements)
    monkeypatch.setattr(
        wikidata_client,
        "fetch_entities",
        lambda ids: {qid: wikidata_client.EntityInfo(qid, None, None, [], [], False) for qid in ids},
    )
    with pytest.raises(ValueError, match="conflicting geographic statement id across discovery batches"):
        ingest_place_statements(tmp_path, ["Q1"])
    assert not list(tmp_path.iterdir())


@pytest.mark.parametrize("conflicting", [False, True])
def test_ingest_preserves_qualifier_multiplicity_across_repeated_statements(tmp_path, monkeypatch, conflicting):
    from wikidata import ingest
    from wikidata.place_statements import QUALIFIER_SCHEMA

    scope = ("s1", "P518", "Q4", "wikibase-item", "value", None)
    repeated = ("s1", "P580", None, "time", "novalue", None)
    first = pl.DataFrame([scope, repeated, repeated], schema=QUALIFIER_SCHEMA, orient="row")
    second = pl.DataFrame(
        [scope, repeated] if conflicting else [scope, repeated, repeated], schema=QUALIFIER_SCHEMA, orient="row"
    )
    monkeypatch.setattr(
        ingest,
        "_fetch_place_statements",
        lambda ids: (normalize_statements([statement("s1")]), first if ids == ["Q1"] else second),
    )
    monkeypatch.setattr(
        wikidata_client,
        "fetch_entities",
        lambda ids: {qid: wikidata_client.EntityInfo(qid, None, None, [], [], False) for qid in ids},
    )
    if conflicting:
        with pytest.raises(ValueError, match="conflicting geographic qualifiers"):
            ingest_place_statements(tmp_path, ["Q1"])
        assert not list(tmp_path.iterdir())
    else:
        _, qualifiers_path, _ = ingest_place_statements(tmp_path, ["Q1"])
        assert pl.read_parquet(qualifiers_path).height == 3


def test_place_metadata_preserves_coordinates_classification_redirects_and_missing():
    import json

    from wikidata.place_statements import place_metadata

    claims = {
        "P625": [{"rank": "preferred", "qualifiers": {"P580": []}, "mainsnak": {"snaktype": "somevalue"}}],
        "P31": [{"id": "classification"}],
        "P279": [],
        "P706": [],
    }
    result = place_metadata(
        ["Q3", "Q1", "Q1"],
        lambda ids: {
            "Q1": {"id": "Q2", "lastrevid": 42, "modified": "date", "claims": claims},
            "Q3": {"missing": ""},
        },
    )
    assert result["requested_id"].to_list() == ["Q1", "Q3"]
    assert result["resolved_id"].to_list() == ["Q2", None]
    assert result["status"].to_list() == ["redirect", "missing"]
    assert result["revision_id"].to_list() == [42, None]
    assert json.loads(result["claims_json"][0]) == {key: value for key, value in claims.items() if key != "P706"}
    with pytest.raises(ValueError, match="every requested"):
        place_metadata(["Q1"], lambda _: {})
