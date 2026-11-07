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


def test_physical_qid_and_independent_coordinate_must_identify_same_land(tmp_path):
    import polars as pl

    from geohistory.silver.reference_land import reference_island_geometries

    island, neighbor = shapely.box(0, 0, 4, 4), shapely.box(10, 0, 14, 4)
    pl.DataFrame({"level": [0], "geometry_wkb": [shapely.union_all([island, neighbor]).wkb]}).write_parquet(
        tmp_path / "natural_earth_admin.parquet"
    )
    pl.DataFrame(
        {
            "wikidata_id": ["Q1", "Q2", "Q3"],
            "kind": ["Island", "Island", "Island group"],
            "geometry_wkb": [island.wkb, neighbor.wkb, island.wkb],
        }
    ).write_parquet(tmp_path / "natural_earth_physical.parquet")
    crosswalk = pl.DataFrame({"wikidata_id": ["Q1", "Q2", "Q3"], "entity_id": ["one", "wrong", "group"]})
    entities = pl.DataFrame(
        {"entity_id": ["one", "wrong", "group"], "latitude": [2.0, 2.0, 2.0], "longitude": [2.0, 2.0, 2.0]}
    )
    result = reference_island_geometries(tmp_path, crosswalk, entities)
    assert set(result) == {"one"}
    assert shapely.equals(result["one"], island)


def test_composed_boundaries_keep_completeness_and_circular_dependencies():
    from geohistory.silver.place_relation_geometry import ReferenceGeometry, resolve_geometry
    from geohistory.silver.reference_land import composed_references

    first = ReferenceGeometry(shapely.box(0, 0, 1, 1), True, "admin", "independent")
    second = ReferenceGeometry(shapely.box(1, 0, 2, 1), True, "admin", "independent")
    members = {("a", "region"), ("b", "region"), ("region", "outer")}
    result = composed_references({"a": first, "b": second}, members, complete_entities=set())
    assert not result["region"].complete
    assert not result["outer"].complete
    assert result["outer"].derived_from == frozenset(members)
    assert resolve_geometry(first, result["region"], ("a", "region"), precision=0.001) == "unknown"
    result = composed_references({"a": first, "b": second}, members, complete_entities={"region", "outer"})
    assert result["outer"].complete
    assert shapely.equals(result["outer"].geometry, shapely.box(0, 0, 2, 1))
    assert composed_references({}, {("a", "b"), ("b", "a")}, complete_entities=set()) == {}
