import shapely

from geohistory.silver.reference_land import IslandLandIndex


def test_island_component_requires_unique_interior_hit_and_identity():
    island = shapely.box(0, 0, 2, 2)
    land = shapely.MultiPolygon([island, shapely.box(3, 0, 4, 1)])
    point = shapely.Point(1, 1)
    index = IslandLandIndex(land)
    result = index.select(point, is_simple_island=True, verify_identity=lambda part: part.equals(island))
    assert result.equals(island)
    assert index.select(point, is_simple_island=True, verify_identity=lambda _: False) is None
    assert index.select(point, is_simple_island=False, verify_identity=lambda _: True) is None
    assert index.select(shapely.Point(2, 1), is_simple_island=True, verify_identity=lambda _: True) is None
    assert index.select(shapely.Point(2.5, 1), is_simple_island=True, verify_identity=lambda _: True) is None


def test_invalid_land_and_non_points_are_indeterminate():
    invalid = shapely.Polygon([(0, 0), (2, 2), (2, 0), (0, 2)])
    assert (
        IslandLandIndex(invalid).select(shapely.Point(1, 1), is_simple_island=True, verify_identity=lambda _: True)
        is None
    )
    assert (
        IslandLandIndex(shapely.box(0, 0, 2, 2)).select(
            shapely.LineString([(0, 0), (1, 1)]), is_simple_island=True, verify_identity=lambda _: True
        )
        is None
    )


def test_touching_components_and_antimeridian_require_verified_identity():
    touching = shapely.MultiPolygon([shapely.box(0, 0, 1, 1), shapely.box(1, 1, 2, 2)])
    assert (
        IslandLandIndex(touching).select(shapely.Point(1, 1), is_simple_island=True, verify_identity=lambda _: True)
        is None
    )

    east, west = shapely.box(179, 0, 180, 1), shapely.box(-180, 0, -179, 1)
    index = IslandLandIndex(shapely.MultiPolygon([east, west]))
    assert index.select(shapely.Point(179.5, 0.5), is_simple_island=True, verify_identity=lambda _: False) is None
