import pytest
from shapely.geometry import box

from geohistory.silver.place_relation_geometry import ReferenceGeometry, resolve_geometry

PAIR = ("child", "parent")


def reference(geometry, **kwargs):
    return ReferenceGeometry(
        geometry,
        source="test",
        complete=kwargs.pop("complete", True),
        boundary_kind=kwargs.pop("boundary_kind", "independent"),
        **kwargs,
    )


def test_full_partial_contact_and_disjoint():
    parent = reference(box(0, 0, 2, 2))
    assert resolve_geometry(reference(box(0, 0, 1, 1)), parent, PAIR, precision=0.001) == "full"
    assert resolve_geometry(reference(box(1, 0, 3, 1)), parent, PAIR, precision=0.001) == "partial"
    assert resolve_geometry(reference(box(2, 0, 3, 1)), parent, PAIR, precision=0.001) == "unknown"
    assert resolve_geometry(reference(box(3, 0, 4, 1)), parent, PAIR, precision=0.001) == "unknown"


def test_incomplete_circular_and_historical_boundaries_do_not_prove_extent():
    child = reference(box(0, 0, 1, 1))
    parent = box(0, 0, 2, 2)
    assert resolve_geometry(child, reference(parent, complete=False), PAIR, precision=0.001) == "full"
    assert (
        resolve_geometry(reference(box(1, 0, 3, 1)), reference(parent, complete=False), PAIR, precision=0.001)
        == "unknown"
    )
    assert (
        resolve_geometry(reference(child.geometry, complete=False), reference(parent), PAIR, precision=0.001)
        == "unknown"
    )
    assert resolve_geometry(child, reference(parent, boundary_kind="point"), PAIR, precision=0.001) == "unknown"
    assert (
        resolve_geometry(child, reference(parent, derived_from=frozenset({PAIR})), PAIR, precision=0.001) == "unknown"
    )
    assert (
        resolve_geometry(child, reference(parent), PAIR, precision=0.001, start_year=1800, end_year=1900) == "unknown"
    )
    dated_child = reference(child.geometry, start_year=1700, end_year=2000)
    dated_parent = reference(parent, start_year=1700, end_year=2000)
    assert resolve_geometry(dated_child, dated_parent, PAIR, precision=0.001, start_year=1800, end_year=1900) == "full"


def test_precision_must_be_positive():
    geom = reference(box(0, 0, 1, 1))
    with pytest.raises(ValueError, match="precision"):
        resolve_geometry(geom, geom, PAIR, precision=0)
