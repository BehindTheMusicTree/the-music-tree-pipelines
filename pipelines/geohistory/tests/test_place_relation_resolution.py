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
