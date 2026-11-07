import polars as pl
import pytest

from geohistory.silver.place_statements import best_place_statements


def inputs():
    statements = pl.DataFrame(
        [
            ("a", "Q1", "P706", "Q2", "normal", "value"),
            ("b", "Q1", "P706", "Q3", "preferred", "value"),
            ("c", "Q1", "P706", None, "preferred", "somevalue"),
            ("d", "Q1", "P361", None, "normal", "novalue"),
            ("e", "Q1", "P361", "Q4", "deprecated", "value"),
        ],
        schema=["statement_id", "subject_item_id", "property", "object_item_id", "rank", "snak_type"],
        orient="row",
    )
    qualifiers = pl.DataFrame(
        [
            ("a", "P518", "Q5", "wikibase-item", None),
            ("b", "P518", "Q6", "wikibase-item", None),
            ("b", "P518", "Q7", "wikibase-item", None),
            ("e", "P580", "1800", "time", 9),
        ],
        schema=["statement_id", "qualifier_property", "value", "value_type", "time_precision"],
        orient="row",
    )
    return statements, qualifiers


def test_best_rank_is_per_subject_and_property_and_qualifiers_stay_on_statement():
    statements, qualifiers = inputs()
    selected, selected_qualifiers = best_place_statements(statements, qualifiers)
    assert selected["statement_id"].to_list() == ["d", "b", "c"]
    assert selected_qualifiers.select("statement_id", "value").rows() == [("b", "Q6"), ("b", "Q7")]
    shuffled, shuffled_qualifiers = best_place_statements(statements.reverse(), qualifiers.reverse())
    assert selected.equals(shuffled)
    assert selected_qualifiers.equals(shuffled_qualifiers)


def test_invalid_statements_and_orphan_qualifiers_fail():
    statements, qualifiers = inputs()
    with pytest.raises(ValueError, match="unique"):
        best_place_statements(pl.concat([statements, statements.head(1)]), qualifiers)
    with pytest.raises(ValueError, match="unknown statement"):
        best_place_statements(statements.slice(1), qualifiers)
    with pytest.raises(ValueError, match="snak type"):
        best_place_statements(statements.with_columns(pl.lit("invalid").alias("snak_type")), qualifiers)
