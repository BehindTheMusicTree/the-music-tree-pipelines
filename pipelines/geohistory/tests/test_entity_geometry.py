import json

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
    ("ne_fj", 1, "fj", shapely.union_all([shapely.box(177, -18, 180, -16), shapely.box(-180, -18, -178, -16)])),
    ("ne_ru", 1, "ru", shapely.union_all([shapely.box(30, 50, 180, 70), shapely.box(-180, 64, -170, 68)])),
    ("ne_wide", 1, "wide", shapely.box(-100, 0, 100, 10)),
    (
        "ne_c_shape",
        1,
        "cs",
        shapely.Polygon([(40, 0), (50, 0), (50, 1), (41, 1), (41, 9), (50, 9), (50, 10), (40, 10)]),
    ),
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
    entities = {entity for entity, *_ in footprint} | {entity for _, _, entity, _ in NE}
    pl.DataFrame(
        [(entity, "language" if entity == "lang" else "admin_unit") for entity in sorted(entities)],
        schema={"entity_id": pl.String, "kind": pl.String},
        orient="row",
    ).write_parquet(silver / "1_cultural_entity.parquet")
    pl.DataFrame(
        schema={"entity_id": pl.String, "geometry_wkb": pl.Binary, "source": pl.String, "complete": pl.Boolean}
    ).write_parquet(silver / "2_place_reference_geometry.parquet")
    entity_geometry(bronze, silver)
    return {r["entity_id"]: r for r in pl.read_parquet(silver / "8_entity_geometry.parquet").to_dicts()}


def test_natural_earth_polygon_is_used_as_is(tmp_path):
    rows = build(tmp_path, [("a", "a", "self")])
    assert rows["a"]["source"] == "natural_earth"
    assert rows["a"]["bbox"] == [0.0, 0.0, 1.0, 1.0]
    assert rows["a"]["centroid"] == pytest.approx([0.5, 0.5], abs=1e-3)


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


def test_only_drawn_kinds_get_a_geometry(tmp_path):
    rows = build(tmp_path, [("a", "a", "self"), ("lang", "a", "point")])
    assert "a" in rows
    assert "lang" not in rows


def test_bbox_spanning_the_antimeridian_east_exceeds_180(tmp_path):
    rows = build(tmp_path, [("fj", "fj", "self")])
    assert rows["fj"]["bbox"] == [177.0, -18.0, 182.0, -16.0]
    assert rows["fj"]["main_bbox"][0] == 177.0


def test_bbox_of_a_mainland_with_a_far_east_part_keeps_the_narrow_side(tmp_path):
    rows = build(tmp_path, [("ru", "ru", "self")])
    assert rows["ru"]["bbox"] == [30.0, 50.0, 190.0, 70.0]
    assert rows["ru"]["main_bbox"] == [30.0, 50.0, 180.0, 70.0]


def test_wide_geometry_without_a_crossing_keeps_its_plain_bbox(tmp_path):
    rows = build(tmp_path, [("wide", "wide", "self")])
    assert rows["wide"]["bbox"] == [-100.0, 0.0, 100.0, 10.0]


def test_anchor_point_of_a_c_shaped_region_lies_inside_it(tmp_path):
    rows = build(tmp_path, [("cs", "cs", "self")])
    x, y = rows["cs"]["centroid"]
    assert shapely.Point(x, y).within(shapely.from_wkb(rows["cs"]["geometry_wkb"]))


def test_western_hemisphere_only_bbox_is_returned_unshifted():
    from geohistory.silver.entity_geometry import _bbox

    for west in (-125.71262367719233, -100.1234567891, -3.3333333333, -179.9999999):
        box = shapely.box(west, 10, west + 0.5, 20)
        assert _bbox(box) == list(shapely.bounds(box))


def test_point_location_does_not_define_a_region_boundary(tmp_path):
    rows = build(tmp_path, [("a", "a", "self"), ("b", "a", "point")])
    assert "b" not in rows
    report = json.loads((tmp_path / "silver/8_entity_geometry_report.json").read_text())
    assert "b" in report["missingBoundaries"]
