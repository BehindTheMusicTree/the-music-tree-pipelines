import pytest
import shapely

from geohistory.silver.entity_coverage import reference_coverage
from geohistory.silver.place_relation_geometry import ReferenceGeometry


def test_coverage_clips_partial_admins_and_excludes_boundary_contacts():
    island = shapely.box(0, 0, 10, 10)
    foreign = shapely.box(20, 0, 30, 10)
    admins = {
        "country": shapely.union_all([shapely.box(0, 0, 5, 10), foreign]),
        "other": shapely.box(5, 0, 10, 10),
        "touch": shapely.box(10, 0, 15, 10),
    }
    references = {"island": ReferenceGeometry(island, True, "land", "independent")}
    frame = reference_coverage(references, admins, precision=0.001)
    assert frame.select("admin_id", "extent").rows() == [("country", "partial"), ("other", "full")]
    geometry = shapely.union_all(shapely.from_wkb(frame["geometry_wkb"].to_list()))
    assert shapely.equals(geometry, island)
    assert shapely.intersection(geometry, foreign).is_empty
    assert frame.equals(reference_coverage(references, dict(reversed(list(admins.items()))), precision=0.001))


def test_point_and_succession_footprints_cannot_become_reference_coverage():
    for kind in ("point", "succession"):
        reference = ReferenceGeometry(shapely.box(0, 0, 1, 1), True, "fallback", kind)
        assert reference_coverage({"x": reference}, {"a": reference.geometry}, precision=0.001).is_empty()
    with pytest.raises(ValueError, match="precision"):
        reference_coverage({}, {}, precision=float("nan"))
