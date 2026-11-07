import pytest

from geohistory.silver.place_relation_resolution import SpatialEvidence, resolve_relations


def evidence(source, statement_id, extent, *, scoped=False):
    return SpatialEvidence("child", "parent", source, statement_id, extent, scoped)


def test_consolidates_evidence_before_source_priority_and_preserves_conflict():
    rows = [evidence("p361", "a", "full"), evidence("p706", "b", "partial", scoped=True)]
    decision = resolve_relations(rows, {})[0]
    assert decision.extent == "unknown"
    assert decision.conflict
    assert resolve_relations(rows[::-1] + rows, {}) == [decision]
    overridden = resolve_relations(rows, {("child", "parent"): ("partial", "reviewed")})[0]
    assert overridden.extent == "partial"
    assert overridden.conflict
    assert overridden.override_reason == "reviewed"


def test_scope_alone_is_not_evidence_of_full_containment():
    decision = resolve_relations([evidence("p706", "a", "unknown", scoped=True)], {})[0]
    assert decision.extent == "unknown"


def test_override_can_reject_without_a_statement_and_requires_reason():
    decision = resolve_relations([], {("child", "parent"): ("reject", "reviewed")})[0]
    assert decision.extent == "reject"
    assert not decision.evidence
    with pytest.raises(ValueError, match="invalid place relation override"):
        resolve_relations([], {("child", "parent"): ("full", " ")})


def test_scoped_subdivision_does_not_replace_subject_boundary():
    import shapely

    from geohistory.silver.place_relation_geometry import ReferenceGeometry
    from geohistory.silver.place_relation_resolution import PlaceStatement, resolve_statement_evidence

    island = shapely.box(0, 0, 10, 10)
    other_island = shapely.box(20, 0, 30, 10)
    references = {
        "country": ReferenceGeometry(shapely.union_all([island, other_island]), True, "admin", "independent"),
        "island": ReferenceGeometry(island, True, "land", "independent"),
        "subdivision": ReferenceGeometry(
            shapely.union_all([shapely.box(0, 0, 5, 10), other_island]), True, "admin", "independent"
        ),
    }
    statements = [
        PlaceStatement("country", "island", "p706", "scoped", True),
        PlaceStatement("country", "island", "p361", "unscoped", False),
    ]
    rows = resolve_statement_evidence(statements, references, precision=0.001)
    decision = resolve_relations(rows, {})[0]
    assert decision.extent == "partial"
    assert {item.statement_id for item in decision.evidence} == {"scoped", "unscoped"}
    assert (
        resolve_statement_evidence(
            [PlaceStatement("subdivision", "island", "p706", "part", True)], references, precision=0.001
        )[0].extent
        == "partial"
    )
    assert resolve_statement_evidence(statements, {}, precision=0.001)[0].extent == "unknown"
    historical = [PlaceStatement("country", "island", "p706", "historical", False, 1700, 1800)]
    assert resolve_statement_evidence(historical, references, precision=0.001)[0].extent == "unknown"
