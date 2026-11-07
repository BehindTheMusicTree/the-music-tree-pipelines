import json

import polars as pl
import pytest

from geohistory.silver.place_classification import current_administrative_kinds


def statement(target, *, qualifiers=None, rank="normal"):
    return {
        "rank": rank,
        "mainsnak": {"snaktype": "value", "datavalue": {"value": {"id": target}}},
        "qualifiers": qualifiers or {},
    }


def time(year, precision=9):
    return {
        "snaktype": "value",
        "datavalue": {
            "value": {
                "time": f"+{year}-00-00T00:00:00Z",
                "precision": precision,
                "calendarmodel": "http://www.wikidata.org/entity/Q1985727",
            }
        },
    }


def classify(statements, classes=None):
    claims = {
        "Q1": {"P31": statements},
        "Q2": {"P279": [statement("Q56061")]},
        "Q56061": {},
        "Q1620908": {"P279": [statement("Q56061")]},
        **(classes or {}),
    }
    metadata = pl.DataFrame(
        [{"resolved_id": item, "status": "found", "claims_json": json.dumps(values)} for item, values in claims.items()]
    )
    entities = pl.DataFrame({"entity_id": ["Q1"], "kind": ["historical_region"]})
    return current_administrative_kinds(entities, metadata, as_of_year=2026)["kind"].item()


def test_current_administration_replaces_expired_historical_type():
    assert (
        classify(
            [
                statement("Q1620908", qualifiers={"P582": [time(1983)]}),
                statement("Q2", qualifiers={"P580": [time(1983)]}),
            ]
        )
        == "admin_unit"
    )


@pytest.mark.parametrize(
    "qualifiers",
    [
        {"P582": [time(1983)]},
        {"P580": [time(2027)]},
        {"P580": [time(2026)]},
        {"P580": [time(1900, 7)]},
        {"P580": [{"snaktype": "somevalue"}]},
        {"P585": [time(2025)]},
        {"P518": [{"snaktype": "novalue"}]},
    ],
)
def test_uncertain_scoped_or_expired_administrative_type_does_not_reclassify(qualifiers):
    assert classify([statement("Q2", qualifiers=qualifiers)]) == "historical_region"


def test_historical_class_is_not_current_administration_even_if_it_has_admin_ancestors():
    assert classify([statement("Q1620908")]) == "historical_region"


def test_deprecated_or_nonpreferred_class_does_not_reclassify():
    assert classify([statement("Q2", rank="deprecated")]) == "historical_region"
    assert classify([statement("Q2"), statement("Q1620908", rank="preferred")]) == "historical_region"


def test_missing_class_fails_instead_of_guessing():
    with pytest.raises(ValueError, match="rerun Wikidata Bronze"):
        classify([statement("Q999")])


def test_subclass_cycles_terminate_and_expired_subclass_is_not_used():
    assert classify([statement("Q2")], {"Q2": {"P279": [statement("Q2")]}}) == "historical_region"
    assert (
        classify(
            [statement("Q2")],
            {"Q2": {"P279": [statement("Q56061", qualifiers={"P582": [time(1983)]})]}},
        )
        == "historical_region"
    )
