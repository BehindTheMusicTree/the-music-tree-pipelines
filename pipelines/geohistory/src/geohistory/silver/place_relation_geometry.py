"""Compare independent reference boundaries without promoting overlaps to containment."""

from dataclasses import dataclass
from typing import Literal

import shapely
from shapely.geometry.base import BaseGeometry

Extent = Literal["full", "partial", "unknown"]


@dataclass(frozen=True)
class ReferenceGeometry:
    geometry: BaseGeometry
    complete: bool
    source: str
    boundary_kind: Literal["independent", "point", "succession"]
    derived_from: frozenset[tuple[str, str]] = frozenset()
    start_year: int | None = None
    end_year: int | None = None


def resolve_geometry(
    child: ReferenceGeometry,
    parent: ReferenceGeometry,
    pair: tuple[str, str],
    *,
    precision: float,
    start_year: int | None = None,
    end_year: int | None = None,
) -> Extent:
    """Return only conclusions supported by independent boundaries.

    ``precision`` is the coordinate grid of the source, in its CRS units. A point-derived
    footprint must never be supplied as a reference boundary. A complete child covered by
    a known subset of its parent is fully contained, but an incomplete parent cannot prove
    that any part of the child lies outside it.
    """
    if precision <= 0:
        raise ValueError("precision must be positive")
    if child.boundary_kind != "independent" or parent.boundary_kind != "independent":
        return "unknown"
    if pair in child.derived_from or pair in parent.derived_from:
        return "unknown"
    if not child.complete:
        return "unknown"
    for reference in (child, parent):
        if start_year is not None and (reference.start_year is None or reference.start_year > start_year):
            return "unknown"
        if end_year is not None and (reference.end_year is None or reference.end_year < end_year):
            return "unknown"
    child_geom = shapely.set_precision(shapely.make_valid(child.geometry), precision)
    parent_geom = shapely.set_precision(shapely.make_valid(parent.geometry), precision)
    if child_geom.is_empty or parent_geom.is_empty or child_geom.area == 0 or parent_geom.area == 0:
        return "unknown"
    if shapely.covers(parent_geom, child_geom):
        return "full"
    if (
        parent.complete
        and shapely.intersection(child_geom, parent_geom).area > 0
        and shapely.difference(child_geom, parent_geom).area > 0
    ):
        return "partial"
    return "unknown"
