import polars as pl
import pytest
import shapely
from geohistory.silver.entity_geometry import entity_geometry

NE = [
    ("ne_a", 1, "a", shapely.box(0, 0, 1, 1)),
    ("ne_b", 1, "b", shapely.box(1, 0, 2, 1)),
    ("ne_c", 1, "c", shapely.box(5, 5, 6, 6)),
    ("ne_bow", 1, "bow", shapely.Polygon([(10, 0), (12, 2), (12, 0), (10, 2)])),
    ("ne_fr", 0, "fr", shapely.box(0, 0, 2, 1)),
    ("ne_islands", 1, "islands", shapely.union_all([shapely.box(20, 0, 24, 4), shapely.box(30, 0, 31, 1)])),
]


def build(tmp_path, footprint, containment=()):
    bronze, silver = tmp_path / "bronze", tmp_path / "silver"
    bronze.mkdir()
    silver.mkdir()
    pl.DataFrame(
        [(ne_id, level, geometry.wkb) for ne_id, level, _, geometry in NE],
        schema={"ne_ids": pl.String, "level": pl.Int8, "geometry_wkb": pl.Binary},
        orient="row",
    ).rename({"ne_ids": "ne_id"}).write_parquet(bronze / "natural_earth_admin.parquet")
    pl.DataFrame(
        [(ne_id, level, entity, None) for ne_id, level, entity, _ in NE],
        schema={"ne_id": pl.String, "level": pl.Int8, "entity_id": pl.String, "country_entity_id": pl.String},
        orient="row",
    ).write_parquet(silver / "1_entity_ne_admin.parquet")
    pl.DataFrame(
        containment, schema={"parent_id": pl.String, "child_id": pl.String, "source": pl.String}, orient="row"
    ).write_parquet(silver / "2_place_containment.parquet")
    pl.DataFrame(
        footprint, schema={"entity_id": pl.String, "admin_id": pl.String, "rule": pl.String}, orient="row"
    ).write_parquet(silver / "8_entity_footprint.parquet")
    entity_geometry(bronze, silver)
    return {r["entity_id"]: r for r in pl.read_parquet(silver / "8_entity_geometry.parquet").to_dicts()}


def test_natural_earth_polygon_is_used_as_is(tmp_path):
    rows = build(tmp_path, [("a", "a", "self")])
    assert rows["a"]["source"] == "natural_earth"
    assert rows["a"]["bbox"] == [0.0, 0.0, 1.0, 1.0]
    assert rows["a"]["centroid"] == [0.5, 0.5]


def test_cover_unions_tiles_without_holes(tmp_path):
    rows = build(tmp_path, [("reg", "a", "cover"), ("reg", "b", "cover")])
    geometry = shapely.from_wkb(rows["reg"]["geometry_wkb"])
    assert rows["reg"]["source"] == "footprint"
    assert geometry.equals(shapely.box(0, 0, 2, 1))
    assert len(shapely.get_parts(geometry)) == 1


def test_nested_admin_units_are_not_counted_twice(tmp_path):
    rows = build(
        tmp_path,
        [("reg", "fr", "cover"), ("reg", "a", "cover")],
        containment=[("fr", "a", "p131")],
    )
    assert shapely.from_wkb(rows["reg"]["geometry_wkb"]).equals(shapely.box(0, 0, 2, 1))


def test_admin_without_polygon_is_tiled_by_its_children(tmp_path):
    rows = build(
        tmp_path,
        [("reg", "mid", "self")],
        containment=[("mid", "a", "p131"), ("mid", "b", "p131")],
    )
    assert shapely.from_wkb(rows["reg"]["geometry_wkb"]).equals(shapely.box(0, 0, 2, 1))
    assert rows["reg"]["source"] == "footprint"


def test_main_bbox_and_centroid_follow_the_largest_part(tmp_path):
    rows = build(tmp_path, [("isl", "islands", "self")])
    assert rows["isl"]["bbox"] == [20.0, 0.0, 31.0, 4.0]
    assert rows["isl"]["main_bbox"] == [20.0, 0.0, 24.0, 4.0]
    assert rows["isl"]["centroid"] == [22.0, 2.0]


def test_self_intersecting_source_polygon_is_repaired(tmp_path):
    rows = build(tmp_path, [("bow", "bow", "self")])
    assert shapely.is_valid(shapely.from_wkb(rows["bow"]["geometry_wkb"]))


def test_footprint_without_any_polygon_raises(tmp_path):
    with pytest.raises(ValueError, match="none of its admin units has a polygon"):
        build(tmp_path, [("reg", "ghost", "cover")])
