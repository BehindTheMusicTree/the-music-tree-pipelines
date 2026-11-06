"""Consolidate spatial evidence before choosing a containment source."""

from collections import defaultdict
from dataclasses import dataclass
from typing import Literal

from geohistory.silver.place_relation_geometry import Extent

Decision = Literal["full", "partial", "unknown", "reject"]


@dataclass(frozen=True)
class SpatialEvidence:
    child_id: str
    parent_id: str
    source: str
    statement_id: str
    extent: Extent
    scoped: bool = False


@dataclass(frozen=True)
class RelationDecision:
    child_id: str
    parent_id: str
    extent: Decision
    evidence: tuple[SpatialEvidence, ...]
    conflict: bool
    override_reason: str | None = None


def resolve_relations(
    evidence: list[SpatialEvidence], overrides: dict[tuple[str, str], tuple[Decision, str]]
) -> list[RelationDecision]:
    """Resolve each pair without letting source priority erase contradictory evidence."""
    groups: dict[tuple[str, str], set[SpatialEvidence]] = defaultdict(set)
    for item in evidence:
        if not item.child_id or not item.parent_id or item.child_id == item.parent_id:
            raise ValueError("spatial evidence requires two distinct entity ids")
        if not item.source or not item.statement_id or item.extent not in {"full", "partial", "unknown"}:
            raise ValueError("invalid spatial evidence")
        groups[(item.child_id, item.parent_id)].add(item)
    for pair, (extent, override_reason) in overrides.items():
        if (
            not all(pair)
            or pair[0] == pair[1]
            or extent not in {"full", "partial", "reject"}
            or not override_reason.strip()
        ):
            raise ValueError(f"invalid place relation override: {pair}")
        groups.setdefault(pair, set())

    decisions = []
    for (child, parent), items in sorted(groups.items()):
        ordered = tuple(sorted(items, key=lambda item: (item.source, item.statement_id, item.extent, item.scoped)))
        proven = {item.extent for item in ordered if item.extent != "unknown"}
        conflict = len(proven) > 1
        reason: str | None
        if (child, parent) in overrides:
            extent, reason = overrides[(child, parent)]
        else:
            extent = next(iter(proven)) if len(proven) == 1 else "unknown"
            reason = None
        decisions.append(RelationDecision(child, parent, extent, ordered, conflict, reason))
    return decisions
