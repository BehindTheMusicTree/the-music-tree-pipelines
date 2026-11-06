import gzip
import hashlib
import json
import math
import shutil
from pathlib import Path

import polars as pl
import pytest
import shapely
from shapely.geometry import Point, box, mapping

from gold import region_map_export
from gold.region_map_export import (
    MIN_BAND,
    SPLIT_PX,
    TILE_PX,
    TILESET_MAX_ZOOM,
    _admin_bands,
    _approximate_area_km2,
    _islands,
    _mercator_extent,
    _zone_band,
    _zoom_spanning,
    export_region_map,
)

from .conftest import REAL_RUN_TIPPECANOE


REGIONAL_SCHEMA = {"item_id": pl.String, "parent_id": pl.String}


def _append(path: Path, rows: list[tuple]) -> None:
    frame = pl.read_parquet(path)
    pl.concat([frame, pl.DataFrame(rows, schema=frame.schema, orient="row")]).write_parquet(path)


def _extend(
    geohistory: Path,
    wikidata: Path,
    entities: dict[str, tuple[str, object, str]] = {},
    containment: list[tuple[str, str]] = [],
    anchors: list[tuple[str, str]] = [],
    overviews: dict[str, int] = {},
    regional: list[tuple[str, str | None]] = [],
) -> None:
    _append(
        geohistory / "1_cultural_entity.parquet", [(e, kind, e, None, None) for e, (kind, _, _) in entities.items()]
    )
    _append(
        geohistory / "8_entity_geometry.parquet",
        [tuple(_geometry_row(e, shape, source).values()) for e, (_, shape, source) in entities.items()],
    )
    _append(geohistory / "2_place_containment.parquet", [(parent, child, "p") for parent, child in containment])
    _append(
        geohistory / "5_music_anchor.parquet",
        [(item, anchor, "P495", item in overviews, overviews.get(item)) for item, anchor in anchors],
    )
    _append(wikidata / "8_regional_hierarchy.parquet", regional)


def _regions(index_path: Path) -> dict[str, dict]:
    return {r["id"]: r for r in json.loads(index_path.read_text())["regions"]}


def _geometry_row(entity_id: str, shape, source: str = "natural_earth") -> dict:
    minx, miny, maxx, maxy = shape.bounds
    centroid = shape.centroid
    return {
        "entity_id": entity_id,
        "geometry_wkb": shapely.to_wkb(shape),
        "source": source,
        "bbox": [minx, miny, maxx, maxy],
        "main_bbox": [minx, miny, maxx, maxy],
        "centroid": [centroid.x, centroid.y],
    }


BASEMAP = [
    ("land", None, None, 0.0, box(-1, -1, 31, 11)),
    ("lakes", "Lac Léman", "Lake", 3.0, box(2, 2, 3, 3)),
    ("rivers", "Seine", "River", 2.5, shapely.LineString([(1, 1), (6, 7)])),
    ("rivers", None, "River", 7.0, shapely.LineString([(21, 1), (26, 7)])),
    ("disputed", "Claim", "Claim boundary", 4.0, shapely.LineString([(10, 0), (20, 0)])),
    ("places", "Paris", "Admin-0 capital", 1.7, Point(2, 2)),
    ("places", "Berlin", "Admin-0 capital", 12.0, Point(25, 5)),
]


def _write_basemap(geohistory: Path, features: list[tuple] = BASEMAP) -> None:
    pl.DataFrame(
        [
            (layer, name, featurecla, min_zoom, shapely.to_wkb(shape))
            for layer, name, featurecla, min_zoom, shape in features
        ],
        schema={
            "layer": pl.String,
            "name": pl.String,
            "featurecla": pl.String,
            "min_zoom": pl.Float64,
            "geometry_wkb": pl.Binary,
        },
        orient="row",
    ).write_parquet(geohistory / "8_basemap_feature.parquet")


def _write(tmp_path: Path) -> tuple[Path, Path, Path, Path]:
    geohistory, wikidata, output = tmp_path / "geohistory", tmp_path / "wikidata", tmp_path / "out"
    geohistory.mkdir()
    wikidata.mkdir()
    output.mkdir()
    pl.DataFrame(
        [
            ("FR", "country", "France", None, None),
            ("BR", "admin_unit", "Bretagne", None, None),
            ("NR", "admin_unit", "Normandie", None, None),
            ("DE", "country", "Germany", None, None),
            ("CE", "cultural_region", "Celtic nations", None, None),
            ("LG", "language", "Breton", None, None),
            ("RN", "city", "Rennes", 48.11, -1.68),
        ],
        schema=["entity_id", "kind", "label", "latitude", "longitude"],
        orient="row",
    ).write_parquet(geohistory / "1_cultural_entity.parquet")
    shapes = {
        "FR": box(0, 0, 10, 10),
        "BR": box(0, 0, 4, 4),
        "NR": box(5, 5, 9, 9),
        "DE": box(20, 0, 30, 10),
        "CE": box(0, 0, 4, 4),
    }
    pl.DataFrame(
        [_geometry_row(e, s, "natural_earth" if e != "CE" else "wikidata") for e, s in shapes.items()]
    ).write_parquet(geohistory / "8_entity_geometry.parquet")
    _write_basemap(geohistory)
    pl.DataFrame([("CE", "BR", "point")], schema=["entity_id", "admin_id", "rule"], orient="row").write_parquet(
        geohistory / "8_entity_footprint.parquet"
    )
    pl.DataFrame(
        [("FR", "BR", "p"), ("FR", "NR", "p")], schema=["parent_id", "child_id", "source"], orient="row"
    ).write_parquet(geohistory / "2_place_containment.parquet")
    pl.DataFrame(
        [
            ("Q1", "BR", "P495", False, None),
            ("Q2", "CE", "P495", False, None),
            ("Q3", "DE", "P495", False, None),
            ("Q4", "LG", "P495", False, None),
            ("Q5", "NOPE", "P495", False, None),
        ],
        schema={
            "wikidata_id": pl.String,
            "anchor_id": pl.String,
            "rule": pl.String,
            "is_overview": pl.Boolean,
            "overview_rank": pl.Int32,
        },
        orient="row",
    ).write_parquet(geohistory / "5_music_anchor.parquet")
    pl.DataFrame(
        [("Q1", None), ("Q11", "Q1"), ("Q12", "Q11")], schema=["item_id", "parent_id"], orient="row"
    ).write_parquet(wikidata / "7_canonical_hierarchy.parquet")
    pl.DataFrame({"item_id": ["Q2"], "parent_id": [None]}, schema=REGIONAL_SCHEMA).write_parquet(
        wikidata / "8_regional_hierarchy.parquet"
    )
    match = tmp_path / "match.parquet"
    pl.DataFrame({"wikidata_genre_item_id": ["Q1", "Q1", "Q11", "Q12", "Q3", None]}).write_parquet(match)
    return geohistory, wikidata, match, output


def test_export_region_map_writes_index_and_geometry(tmp_path: Path) -> None:
    geohistory, wikidata, match, output = _write(tmp_path)

    index_path = export_region_map(geohistory, wikidata, match, output)

    regions = {r["id"]: r for r in json.loads(index_path.read_text())["regions"]}
    assert set(regions) == {"FR", "BR", "DE", "CE"}
    assert regions["FR"]["childIds"] == ["BR"] and regions["FR"]["drillable"]
    assert regions["FR"]["parentId"] == "world" and regions["BR"]["parentId"] == "FR"
    assert regions["BR"]["genres"] == [{"itemId": "Q1", "rule": "P495", "relation": "canonical"}]
    assert (regions["BR"]["songCount"], regions["BR"]["genreCount"]) == (4, 3)
    assert (regions["FR"]["songCount"], regions["FR"]["genreCount"]) == (4, 3)
    assert regions["CE"]["kind"] == "zone" and regions["CE"]["genres"][0]["relation"] == "regional"
    assert regions["BR"]["zoneIds"] == ["CE"] and regions["FR"]["zoneIds"] == ["CE"]
    assert not (output / "5_region_map" / "geometry").exists()
    assert not (output / "5_region_map" / "zones").exists()
    assert "files" not in json.loads(index_path.read_text())


def _part_lines(output: Path) -> list[dict]:
    parts = sorted((output / "5_region_map" / "parts").glob("part-*.ndjson.gz"))
    return [json.loads(line) for part in parts for line in gzip.decompress(part.read_bytes()).splitlines()]


def _sha256(document: dict) -> str:
    canonical = json.dumps(document, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode()
    return hashlib.sha256(canonical).hexdigest()


def test_export_region_map_writes_the_index_alone_as_a_hashed_import_line(tmp_path: Path) -> None:
    geohistory, wikidata, match, output = _write(tmp_path)

    index_path = export_region_map(geohistory, wikidata, match, output)

    parts = sorted((output / "5_region_map" / "parts").iterdir())
    assert [p.name for p in parts] == ["part-00000.ndjson.gz"]
    lines = _part_lines(output)
    assert [line["path"] for line in lines] == ["index.json"]
    assert lines[0]["sha256"] == _sha256(lines[0]["document"])
    assert lines[0]["document"] == json.loads(index_path.read_text())


def test_export_region_map_lifts_an_orphan_behind_a_zone_parent(tmp_path: Path) -> None:
    geohistory, wikidata, match, output = _write(tmp_path)
    _extend(
        geohistory,
        wikidata,
        entities={"WL": ("country", box(0, 6, 3, 9), "wikidata"), "AN": ("admin_unit", box(1, 7, 2, 8), "x")},
        containment=[("FR", "WL"), ("WL", "AN")],
        anchors=[("Q8", "AN")],
    )

    regions = _regions(export_region_map(geohistory, wikidata, match, output))

    assert regions["AN"]["parentId"] == "FR" and "AN" in regions["FR"]["childIds"]


def test_export_region_map_nests_a_country_only_when_it_lies_inside_its_parent(tmp_path: Path) -> None:
    geohistory, wikidata, match, output = _write(tmp_path)
    _extend(
        geohistory,
        wikidata,
        entities={
            "OUT": ("country", box(40, 0, 45, 5), "natural_earth"),
            "IN": ("country", box(1, 6, 3, 8), "natural_earth"),
        },
        containment=[("FR", "OUT"), ("FR", "IN")],
        anchors=[("Q8", "OUT"), ("Q9", "IN")],
    )

    regions = _regions(export_region_map(geohistory, wikidata, match, output))

    assert regions["OUT"]["parentId"] == "world" and "OUT" not in regions["FR"]["childIds"]
    assert regions["IN"]["parentId"] == "FR"


def test_export_region_map_dissolves_a_mainland_intermediate(tmp_path: Path) -> None:
    geohistory, wikidata, match, output = _write(tmp_path)
    _extend(
        geohistory,
        wikidata,
        entities={
            "BIG": ("country", box(100, 0, 120, 10), "natural_earth"),
            "MAIN": ("admin_unit", box(100, 0, 116, 10), "x"),
            "ISL": ("admin_unit", box(118, 0, 120, 2), "x"),
            "SUB": ("admin_unit", box(101, 1, 103, 3), "x"),
        },
        containment=[("BIG", "MAIN"), ("BIG", "ISL"), ("MAIN", "SUB")],
        anchors=[("Q20", "MAIN"), ("Q21", "SUB")],
    )
    _write_basemap(geohistory, [*BASEMAP, ("land", None, None, 0.0, box(99, -1, 121, 11))])

    regions = _regions(export_region_map(geohistory, wikidata, match, output))

    assert "MAIN" not in regions
    assert regions["BIG"]["childIds"] == ["SUB"] and regions["SUB"]["parentId"] == "BIG"
    assert {g["itemId"] for g in regions["BIG"]["genres"]} == {"Q20"}
    assert "ISL" not in regions
    assert regions["BIG"]["genreCount"] == 2


def test_export_region_map_draws_a_genreless_island_but_not_a_genreless_exclave(tmp_path: Path) -> None:
    geohistory, wikidata, match, output = _write(tmp_path)
    _extend(
        geohistory,
        wikidata,
        entities={
            "BIG": ("country", box(100, 0, 110, 10), "natural_earth"),
            "ISL": ("admin_unit", box(112, 0, 113, 1), "x"),
            "TINY": ("admin_unit", box(115, 0, 115.1, 0.1), "x"),
            "EXC": ("admin_unit", box(-30, 0, -20, 10), "x"),
        },
        containment=[("BIG", "ISL"), ("BIG", "TINY"), ("BIG", "EXC")],
    )
    continent = box(-60, -40, 110, 40).difference(box(111, -1, 114, 2))
    _write_basemap(
        geohistory, [*BASEMAP, ("land", None, None, 0.0, continent), ("land", None, None, 0.0, box(112, 0, 113, 1))]
    )

    regions = _regions(export_region_map(geohistory, wikidata, match, output))

    assert (regions["ISL"]["parentId"], regions["ISL"]["genreCount"]) == ("BIG", 0)
    assert regions["TINY"]["parentId"] == "BIG"
    assert "EXC" not in regions


def test_approximate_area_km2_shrinks_a_degree_cell_with_latitude() -> None:
    equator = _approximate_area_km2(box(0, -0.5, 1, 0.5))

    assert equator == pytest.approx(111.32**2, rel=1e-4)
    assert _approximate_area_km2(box(0, 59.5, 1, 60.5)) == pytest.approx(equator / 2, rel=1e-3)


def _island_ids(shapes: dict[str, shapely.Geometry], parents: dict[str, str], land: list) -> set[str]:
    return _islands(parents, shapes.__getitem__, land)


def test_islands_flags_a_subdivision_off_its_parents_main_mass_and_off_any_continent() -> None:
    mainland, island = box(0, 0, 4, 4), box(6, 0, 7, 1)
    shapes = {"FR": box(0, 0, 7, 4), "IDF": box(0, 0, 2, 2), "COR": box(6, 0, 7, 1)}

    assert _island_ids(shapes, {"IDF": "FR", "COR": "FR"}, [mainland, island]) == {"COR"}


def test_islands_does_not_flag_a_subdivision_on_a_continent_off_its_parents_main_mass() -> None:
    europe, south_america = box(0, 40, 4, 44), box(-60, -30, 0, 10)
    shapes = {"FR": box(0, 40, 4, 44), "GF": box(-54, 2, -52, 5)}

    assert _islands({"GF": "FR"}, shapes.__getitem__, [europe, south_america]) == set()


@pytest.mark.parametrize(
    ("half_side", "is_island"),
    # a 13° square at the equator is Greenland-sized (~2.1M km2), a 25° one Australia-sized (~7.7M km2)
    [(6.5, True), (12.5, False)],
    ids=["greenland-sized-mass-is-an-island", "australia-sized-mass-is-a-continent"],
)
def test_islands_treats_only_masses_from_the_continent_threshold_up_as_continents(
    half_side: float, is_island: bool
) -> None:
    home, mass = box(100, 0, 101, 1), box(-half_side, -half_side, half_side, half_side)
    shapes = {"DK": box(100, 0, 101, 1), "GL": box(-1, -1, 1, 1)}

    assert _islands({"GL": "DK"}, shapes.__getitem__, [home, mass]) == ({"GL"} if is_island else set())


def test_islands_flags_a_subdivision_on_no_mapped_land() -> None:
    shapes = {"GB": box(0, 0, 4, 4), "VG": box(10, 10, 10.1, 10.1)}

    assert _islands({"VG": "GB"}, shapes.__getitem__, [box(0, 0, 4, 4)]) == {"VG"}


@pytest.mark.parametrize(
    ("child", "is_island"),
    [(box(3, 0, 5.2, 1), False), (box(3.8, 0, 6, 1), True)],
    ids=["mostly-on-the-main-mass", "mostly-on-the-other-mass"],
)
def test_islands_assigns_a_straddling_subdivision_to_the_mass_it_mostly_covers(
    child: shapely.Geometry, is_island: bool
) -> None:
    main, other = box(0, 0, 4, 4), box(5, 0, 6, 1)
    shapes = {"P": box(0, 0, 6, 4), "C": child}

    assert _islands({"C": "P"}, shapes.__getitem__, [main, other]) == ({"C"} if is_island else set())


def test_islands_takes_the_parents_main_mass_by_overlap_not_by_first_hit() -> None:
    small, large = box(0, 0, 1, 1), box(2, 0, 10, 8)
    # the parent touches both masses; its main mass is the large one, so a child on the small one is an island
    shapes = {"P": box(0, 0, 10, 8), "ON_SMALL": box(0, 0, 1, 1), "ON_LARGE": box(3, 3, 4, 4)}

    assert _islands({"ON_SMALL": "P", "ON_LARGE": "P"}, shapes.__getitem__, [small, large]) == {"ON_SMALL"}


def test_export_region_map_brings_a_genreless_islands_genreless_ancestors_along(tmp_path: Path) -> None:
    geohistory, wikidata, match, output = _write(tmp_path)
    _extend(
        geohistory,
        wikidata,
        entities={
            "BIG": ("country", box(100, 0, 110, 10), "natural_earth"),
            "MID": ("admin_unit", box(100, 0, 104, 4), "x"),
            "SIB": ("admin_unit", box(105, 5, 108, 8), "x"),
            "ISL": ("admin_unit", box(112, 0, 113, 1), "x"),
        },
        containment=[("BIG", "MID"), ("BIG", "SIB"), ("MID", "ISL")],
    )
    continent = box(-60, -40, 110, 40).difference(box(111, -1, 114, 2))
    _write_basemap(
        geohistory, [*BASEMAP, ("land", None, None, 0.0, continent), ("land", None, None, 0.0, box(112, 0, 113, 1))]
    )

    regions = _regions(export_region_map(geohistory, wikidata, match, output))

    assert (regions["ISL"]["parentId"], regions["MID"]["parentId"]) == ("MID", "BIG")
    assert (regions["MID"]["childIds"], regions["MID"]["genreCount"]) == (["ISL"], 0)
    assert regions["BIG"]["childIds"] == ["MID"]
    assert "SIB" not in regions


def test_export_region_map_lists_only_drawn_regions_as_zone_members(tmp_path: Path) -> None:
    geohistory, wikidata, match, output = _write(tmp_path)
    _append(geohistory / "8_entity_footprint.parquet", [("CE", "NR", "cover")])

    regions = _regions(export_region_map(geohistory, wikidata, match, output))

    assert "NR" not in regions
    assert regions["CE"]["childIds"] == ["BR"]


def test_export_region_map_makes_a_country_with_only_genreless_subdivisions_not_drillable(tmp_path: Path) -> None:
    geohistory, wikidata, match, output = _write(tmp_path)
    _extend(
        geohistory,
        wikidata,
        entities={"VU": ("country", box(12, 0, 16, 4), "natural_earth"), "SUB": ("admin_unit", box(12, 0, 13, 1), "x")},
        containment=[("VU", "SUB")],
    )

    regions = _regions(export_region_map(geohistory, wikidata, match, output))

    assert "SUB" not in regions
    assert (regions["VU"]["childIds"], regions["VU"]["drillable"]) == ([], False)


def _chained_mainland_regions(tmp_path: Path, big: str, main: str, sub: str) -> dict[str, dict]:
    geohistory, wikidata, match, output = _write(tmp_path)
    _extend(
        geohistory,
        wikidata,
        entities={
            big: ("country", box(100, 0, 120, 10), "natural_earth"),
            main: ("admin_unit", box(100, 0, 116, 10), "x"),
            "ISL": ("admin_unit", box(118, 0, 120, 2), "x"),
            sub: ("admin_unit", box(100, 0, 114, 10), "x"),
            "ISL2": ("admin_unit", box(115, 0, 116, 1), "x"),
        },
        containment=[(big, main), (big, "ISL"), (main, sub), (main, "ISL2")],
        anchors=[("Q20", main), ("Q21", sub), ("Q22", "ISL"), ("Q23", "ISL2")],
    )
    return _regions(export_region_map(geohistory, wikidata, match, output))


def test_export_region_map_dissolves_a_chained_mainland_whichever_id_sorts_first(tmp_path: Path) -> None:
    (tmp_path / "a").mkdir()
    (tmp_path / "b").mkdir()
    first = _chained_mainland_regions(tmp_path / "a", big="AAA", main="BBB", sub="CCC")
    last = _chained_mainland_regions(tmp_path / "b", big="CCC", main="BBB", sub="AAA")

    assert set(first) - {"FR", "BR", "NR", "DE", "CE"} == {"AAA", "ISL", "ISL2"}
    assert first["AAA"]["childIds"] == ["ISL", "ISL2"]
    assert {g["itemId"] for g in first["AAA"]["genres"]} == {"Q20", "Q21"}
    assert set(last) - {"FR", "BR", "NR", "DE", "CE"} == {"CCC", "ISL", "ISL2"}
    assert last["CCC"]["childIds"] == ["ISL", "ISL2"]
    assert {g["itemId"] for g in last["CCC"]["genres"]} == {"Q20", "Q21"}


def test_export_region_map_does_not_dissolve_a_country_into_an_admin_unit(tmp_path: Path) -> None:
    geohistory, wikidata, match, output = _write(tmp_path)
    _extend(
        geohistory,
        wikidata,
        entities={
            "ADM": ("admin_unit", box(100, 0, 120, 10), "x"),
            "CTY": ("country", box(100, 0, 118, 10), "natural_earth"),
            "SML": ("admin_unit", box(119, 0, 120, 1), "x"),
        },
        containment=[("ADM", "CTY"), ("ADM", "SML")],
        anchors=[("Q20", "CTY"), ("Q21", "SML")],
    )

    regions = _regions(export_region_map(geohistory, wikidata, match, output))

    assert regions["ADM"]["childIds"] == ["CTY", "SML"]


def test_export_region_map_dissolves_only_the_largest_mainland_claimant(tmp_path: Path) -> None:
    geohistory, wikidata, match, output = _write(tmp_path)
    _extend(
        geohistory,
        wikidata,
        entities={
            "PAR": ("country", box(100, 0, 104, 10), "natural_earth"),
            "AAA": ("admin_unit", box(100, 0, 103, 10), "x"),
            "ZZZ": ("admin_unit", box(100, 0, 104, 10), "x"),
        },
        containment=[("PAR", "AAA"), ("PAR", "ZZZ")],
        anchors=[("Q20", "AAA"), ("Q21", "ZZZ")],
    )

    regions = _regions(export_region_map(geohistory, wikidata, match, output))

    assert "ZZZ" not in regions and "AAA" in regions["PAR"]["childIds"]


def test_export_region_map_keeps_a_mainland_with_a_small_share(tmp_path: Path) -> None:
    geohistory, wikidata, match, output = _write(tmp_path)

    regions = _regions(export_region_map(geohistory, wikidata, match, output))

    assert "BR" in regions["FR"]["childIds"] and regions["BR"]["parentId"] == "FR"


def test_export_region_map_inherits_the_region_of_the_nearest_anchored_regional_parent(tmp_path: Path) -> None:
    geohistory, wikidata, match, output = _write(tmp_path)
    _extend(
        geohistory,
        wikidata,
        anchors=[("Q33", "DE")],
        regional=[("Q31", "Q2"), ("Q32", "Q31"), ("Q33", "Q2")],
    )

    regions = _regions(export_region_map(geohistory, wikidata, match, output))

    inherited = [g for g in regions["CE"]["genres"] if g["rule"] == "regional_parent"]
    assert inherited == [
        {"itemId": "Q31", "rule": "regional_parent", "relation": "regional"},
        {"itemId": "Q32", "rule": "regional_parent", "relation": "regional"},
    ]
    assert regions["DE"]["genres"][-1] == {"itemId": "Q33", "rule": "P495", "relation": "regional"}


def test_export_region_map_lets_an_unplaced_anchor_genre_inherit_its_regional_parent(tmp_path: Path) -> None:
    geohistory, wikidata, match, output = _write(tmp_path)
    _extend(geohistory, wikidata, regional=[("Q4", "Q2")])

    regions = _regions(export_region_map(geohistory, wikidata, match, output))

    assert {"itemId": "Q4", "rule": "regional_parent", "relation": "regional"} in regions["CE"]["genres"]
    unplaced = pl.read_csv(output / "5_region_map_unplaced.csv")
    assert unplaced["wikidata_id"].to_list() == ["Q5"]
    assert json.loads((output / "5_region_map_report.json").read_text())["unplacedGenreCount"] == 1


def test_export_region_map_reports_unplaced_genres(tmp_path: Path) -> None:
    geohistory, wikidata, match, output = _write(tmp_path)

    export_region_map(geohistory, wikidata, match, output)

    unplaced = pl.read_csv(output / "5_region_map_unplaced.csv").sort("wikidata_id")
    assert unplaced.select("wikidata_id", "reason").rows() == [
        ("Q4", "anchor has no footprint"),
        ("Q5", "anchor has no footprint"),
    ]
    report = json.loads((output / "5_region_map_report.json").read_text())
    assert report["unplacedGenreCount"] == 2
    assert set(report) == {"unplacedGenreCount", "regionsWithoutOverview", "tileset"}


def test_export_region_map_places_footprinted_anchors_on_their_admin_regions(tmp_path: Path) -> None:
    geohistory, wikidata, match, output = _write(tmp_path)
    entities = pl.read_parquet(geohistory / "1_cultural_entity.parquet")
    pl.concat(
        [
            entities,
            pl.DataFrame(
                [("EG", "ethnic_group", "Javanese", None, None), ("CV", "civilization", "Rome", None, None)],
                schema=entities.schema,
                orient="row",
            ),
        ]
    ).write_parquet(geohistory / "1_cultural_entity.parquet")
    footprint = pl.read_parquet(geohistory / "8_entity_footprint.parquet")
    pl.concat(
        [footprint, pl.DataFrame([("EG", "NR", "point"), ("CV", "NR", "point")], schema=footprint.schema, orient="row")]
    ).write_parquet(geohistory / "8_entity_footprint.parquet")
    anchors = pl.read_parquet(geohistory / "5_music_anchor.parquet")
    pl.concat(
        [
            anchors,
            pl.DataFrame(
                [("Q6", "EG", "P495", False, None), ("Q7", "CV", "P495", False, None)],
                schema=anchors.schema,
                orient="row",
            ),
        ]
    ).write_parquet(geohistory / "5_music_anchor.parquet")

    index_path = export_region_map(geohistory, wikidata, match, output)

    regions = {r["id"]: r for r in json.loads(index_path.read_text())["regions"]}
    genres = regions["NR"]["genres"]
    assert genres == [{"itemId": "Q6", "rule": "P495", "relation": "tradition"}]
    unplaced = pl.read_csv(output / "5_region_map_unplaced.csv")
    assert ("Q7", "anchor is not a place") in unplaced.select("wikidata_id", "reason").rows()


def test_export_region_map_writes_the_index_as_its_canonical_bytes(tmp_path: Path) -> None:
    geohistory, wikidata, match, output = _write(tmp_path)

    index_path = export_region_map(geohistory, wikidata, match, output)

    index_line = next(line for line in _part_lines(output) if line["path"] == "index.json")
    assert hashlib.sha256(index_path.read_bytes()).hexdigest() == index_line["sha256"]


def test_export_region_map_raises_on_oversized_index(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    geohistory, wikidata, match, output = _write(tmp_path)
    monkeypatch.setattr(region_map_export, "INDEX_SIZE_LIMIT_BYTES", 10)

    with pytest.raises(ValueError, match="index.json is [0-9]+ bytes, above the 10 cap"):
        export_region_map(geohistory, wikidata, match, output)


def test_export_region_map_warns_on_an_index_near_its_cap(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    geohistory, wikidata, match, output = _write(tmp_path)
    export_region_map(geohistory, wikidata, match, output)
    size = (output / "5_region_map" / "index.json").stat().st_size
    monkeypatch.setattr(region_map_export, "INDEX_SIZE_LIMIT_BYTES", size + 1)

    with caplog.at_level("WARNING"):
        export_region_map(geohistory, wikidata, match, output)

    assert "index.json is" in caplog.text and f"of the {size + 1} cap" in caplog.text


def test_export_region_map_raises_without_regions(tmp_path: Path) -> None:
    geohistory, wikidata, match, output = _write(tmp_path)
    pl.DataFrame(
        schema={
            "wikidata_id": pl.String,
            "anchor_id": pl.String,
            "rule": pl.String,
            "is_overview": pl.Boolean,
            "overview_rank": pl.Int32,
        }
    ).write_parquet(geohistory / "5_music_anchor.parquet")

    with pytest.raises(ValueError, match="no region"):
        export_region_map(geohistory, wikidata, match, output)


def test_export_region_map_raises_without_a_land_layer(tmp_path: Path) -> None:
    geohistory, wikidata, match, output = _write(tmp_path)
    _write_basemap(geohistory, [feature for feature in BASEMAP if feature[0] != "land"])

    with pytest.raises(ValueError, match="no land layer"):
        export_region_map(geohistory, wikidata, match, output)


def _layer(path: Path) -> list[dict]:
    return [json.loads(line) for line in path.read_text().splitlines()]


def test_export_region_map_builds_a_content_addressed_tileset_referenced_by_the_index(
    tmp_path: Path, fake_tippecanoe: list[list[str]], monkeypatch: pytest.MonkeyPatch
) -> None:
    geohistory, wikidata, match, output = _write(tmp_path)
    layers: dict[str, list[dict]] = {}
    inner = region_map_export._run_tippecanoe

    def capture(arguments: list[str]) -> None:
        for name, path in (a.split(":", 1) for a in arguments if a.count(":") == 1 and not a.startswith("-")):
            layers[name] = _layer(Path(path))
        inner(arguments)

    monkeypatch.setattr(region_map_export, "_run_tippecanoe", capture)

    index_path = export_region_map(geohistory, wikidata, match, output)

    tileset = json.loads(index_path.read_text())["tileset"]
    tile_file = output / "5_region_map" / "regions" / f"{tileset}.pmtiles"
    assert hashlib.sha256(tile_file.read_bytes()).hexdigest() == tileset
    assert [a for a in fake_tippecanoe[0] if a.startswith(("-Z", "-z", "--detect", "--simpl", "--visval"))] == [
        "-Z0",
        "-z10",
        "--detect-shared-borders",
        "--simplification=8",
        "--visvalingam",
        "--simplify-only-low-zooms",
    ]
    index_regions = {r["id"]: r for r in json.loads(index_path.read_text())["regions"]}
    assert {f["properties"]["id"] for f in layers["regions"]} == {"FR", "BR", "DE"}
    drawn = {f["properties"]["id"]: f for f in layers["regions"]}
    for region_id, feature in drawn.items():
        index_region = index_regions[region_id]
        assert {k: feature["properties"][k] for k in ("id", "parentId", "kind", "name")} == {
            k: index_region[k] for k in ("id", "parentId", "kind", "name")
        }
    france_split = drawn["FR"]["properties"]["max_zoom"]
    assert france_split == pytest.approx(4.48, abs=0.01)
    assert drawn["FR"]["properties"] | {"max_zoom": None} == {
        "id": "FR", "parentId": "world", "kind": "country", "name": "France", "depth": 0, "min_zoom": 0.0,
        "max_zoom": None,
    }  # fmt: skip
    assert drawn["BR"]["properties"] == {
        "id": "BR", "parentId": "FR", "kind": "admin_unit", "name": "Bretagne", "depth": 1, "min_zoom": france_split,
    }  # fmt: skip
    assert "max_zoom" not in drawn["DE"]["properties"]
    assert drawn["BR"]["tippecanoe"] == {"minzoom": 4} and drawn["FR"]["tippecanoe"] == {"minzoom": 0}
    (zone,) = layers["zones"]
    assert zone["properties"]["id"] == "CE" and zone["properties"]["name"] == "Celtic nations"
    assert zone["properties"]["max_zoom"] - zone["properties"]["min_zoom"] == pytest.approx(
        math.log2(region_map_export.ZONE_MAX_PX / region_map_export.ZONE_MIN_PX)
    )
    (zone_label,) = layers["zone_labels"]
    assert zone_label["properties"] == zone["properties"]
    assert zone_label["geometry"] == {"type": "Point", "coordinates": index_regions["CE"]["centroid"]}
    assert list(layers) == [
        "regions", "region_labels", "zones", "zone_labels", "land", "lakes", "rivers", "disputed", "places",
    ]  # fmt: skip
    labelled = {f["properties"]["id"]: f for f in layers["region_labels"]}
    assert set(labelled) == {"FR", "BR", "DE"}
    assert labelled["BR"]["properties"] == {
        "id": "BR", "parentId": "FR", "name": "Bretagne", "depth": 1, "min_zoom": france_split,
    }  # fmt: skip
    assert labelled["FR"]["properties"]["max_zoom"] == pytest.approx(france_split + region_map_export.LABEL_OVERLAP)
    assert labelled["BR"]["geometry"] == {"type": "Point", "coordinates": index_regions["BR"]["centroid"]}
    assert layers["land"][0]["properties"] == {}
    assert [f["properties"] for f in layers["rivers"]] == [
        {"min_zoom": 2.5, "name": "Seine"},
        {"min_zoom": 7.0},
    ]
    assert layers["disputed"][0]["properties"] == {"min_zoom": 4.0, "featurecla": "Claim boundary", "name": "Claim"}
    assert [(f["properties"]["name"], f["tippecanoe"]["minzoom"]) for f in layers["places"]] == [
        ("Paris", 1),
        ("Berlin", 10),
    ]
    assert all("tippecanoe" not in f for f in layers["land"])
    report = json.loads((output / "5_region_map_report.json").read_text())
    assert report["tileset"]["bytes"] == tile_file.stat().st_size and report["tileset"]["seconds"] >= 0
    assert not (output / "5_region_map_tileset_work").exists()


def test_export_region_map_tileset_uses_full_resolution_geometry(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    geohistory, wikidata, match, output = _write(tmp_path)
    wiggly = shapely.Polygon([(20 + i / 100, 0 if i % 2 else 0.004) for i in range(1000)] + [(30, 10), (20, 10)])
    frame = pl.read_parquet(geohistory / "8_entity_geometry.parquet").filter(pl.col("entity_id") != "DE")
    pl.concat([frame, pl.DataFrame([_geometry_row("DE", wiggly)], schema=frame.schema)]).write_parquet(
        geohistory / "8_entity_geometry.parquet"
    )
    seen: list[int] = []
    inner = region_map_export._run_tippecanoe

    def capture(arguments: list[str]) -> None:
        layer = next(a for a in arguments if a.startswith("regions:"))
        seen.extend(
            len(f["geometry"]["coordinates"][0])
            for f in _layer(Path(layer.split(":", 1)[1]))
            if f["properties"]["id"] == "DE"
        )
        inner(arguments)

    monkeypatch.setattr(region_map_export, "_run_tippecanoe", capture)
    export_region_map(geohistory, wikidata, match, output)
    assert seen == [len(wiggly.exterior.coords)]


def test_export_region_map_raises_without_a_basemap_layer(tmp_path: Path) -> None:
    geohistory, wikidata, match, output = _write(tmp_path)
    _write_basemap(geohistory, [feature for feature in BASEMAP if feature[0] != "lakes"])
    with pytest.raises(ValueError, match=r"no feature for layer\(s\) \['lakes'\]"):
        export_region_map(geohistory, wikidata, match, output)


def test_export_region_map_fails_without_tippecanoe(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    geohistory, wikidata, match, output = _write(tmp_path)
    monkeypatch.setattr(region_map_export.shutil, "which", lambda _: None)
    with pytest.raises(RuntimeError, match="tippecanoe is not installed"):
        export_region_map(geohistory, wikidata, match, output)


@pytest.mark.skipif(shutil.which("tippecanoe") is None, reason="tippecanoe is not installed")
def test_real_tippecanoe_builds_a_tileset(tmp_path: Path) -> None:
    feature = {
        "type": "Feature",
        "properties": {"id": "A"},
        "geometry": mapping(box(0, 0, 10, 10)),
    }
    source = tmp_path / "a.geojsonl"
    source.write_text(json.dumps(feature) + "\n")
    REAL_RUN_TIPPECANOE(
        ["-o", str(tmp_path / "t.pmtiles"), "-Z0", "-z4", "--no-progress-indicator", "-L", f"regions:{source}"]
    )
    assert (tmp_path / "t.pmtiles").stat().st_size > 0
    with pytest.raises(RuntimeError, match="tippecanoe failed"):
        REAL_RUN_TIPPECANOE(["-o", str(tmp_path / "x.pmtiles"), "-L", f"regions:{tmp_path / 'missing'}"])


def test_run_tippecanoe_raises_on_dropped_features(monkeypatch: pytest.MonkeyPatch) -> None:
    class Result:
        returncode = 0
        stderr = "Dropped 3 features\n"

    monkeypatch.setattr(region_map_export.subprocess, "run", lambda *a, **k: Result())
    with pytest.raises(RuntimeError, match="dropped features"):
        REAL_RUN_TIPPECANOE(["-o", "x"])


def test_export_region_map_picks_the_overview_that_names_the_region(tmp_path: Path) -> None:
    geohistory, wikidata, match, output = _write(tmp_path)
    _extend(
        geohistory,
        wikidata,
        anchors=[("Q30", "FR"), ("Q31", "FR"), ("Q32", "BR")],
        overviews={"Q30": 1, "Q31": 0, "Q32": 0},
        regional=[("Q30", None), ("Q31", None), ("Q32", None)],
    )

    regions = _regions(export_region_map(geohistory, wikidata, match, output))

    assert regions["FR"]["overviewItemId"] == "Q31"
    assert regions["BR"]["overviewItemId"] == "Q32"
    assert regions["DE"]["overviewItemId"] is None


def test_export_region_map_ignores_unranked_overviews_and_traditions(tmp_path: Path) -> None:
    geohistory, wikidata, match, output = _write(tmp_path)
    _extend(
        geohistory,
        wikidata,
        anchors=[("Q30", "FR")],
        overviews={"Q30": None},
        regional=[("Q30", None)],
    )

    regions = _regions(export_region_map(geohistory, wikidata, match, output))

    assert regions["FR"]["overviewItemId"] is None
    assert regions["DE"]["overviewItemId"] is None


def test_export_region_map_lets_a_region_without_an_overview_inherit_its_nearest_ancestors(tmp_path: Path) -> None:
    geohistory, wikidata, match, output = _write(tmp_path)
    _extend(
        geohistory,
        wikidata,
        entities={"FI": ("admin_unit", box(5, 5, 6, 6), "x")},
        containment=[("NR", "FI")],
        anchors=[("Q30", "FR"), ("Q32", "BR"), ("Q33", "FI"), ("Q34", "CE")],
        overviews={"Q30": 0, "Q32": 0, "Q34": 0},
        regional=[("Q30", None), ("Q32", None), ("Q33", None), ("Q34", None)],
    )

    regions = _regions(export_region_map(geohistory, wikidata, match, output))

    assert (regions["BR"]["overviewItemId"], regions["BR"]["inheritedOverviewItemId"]) == ("Q32", None)
    assert (regions["NR"]["overviewItemId"], regions["NR"]["inheritedOverviewItemId"]) == (None, "Q30")
    assert regions["FI"]["parentId"] == "NR" and regions["FI"]["inheritedOverviewItemId"] == "Q30"
    assert regions["FR"]["inheritedOverviewItemId"] is None
    assert regions["DE"]["inheritedOverviewItemId"] is None
    assert (regions["CE"]["overviewItemId"], regions["CE"]["inheritedOverviewItemId"]) == ("Q34", None)


def test_export_region_map_inherits_the_overview_a_dissolved_mainland_hands_its_parent(tmp_path: Path) -> None:
    geohistory, wikidata, match, output = _write(tmp_path)
    _extend(
        geohistory,
        wikidata,
        entities={
            "BIG": ("country", box(100, 0, 120, 10), "natural_earth"),
            "MAIN": ("admin_unit", box(100, 0, 116, 10), "x"),
            "ISL": ("admin_unit", box(118, 0, 120, 2), "x"),
            "SUB": ("admin_unit", box(101, 1, 103, 3), "x"),
        },
        containment=[("BIG", "MAIN"), ("BIG", "ISL"), ("MAIN", "SUB")],
        anchors=[("Q20", "MAIN"), ("Q21", "SUB"), ("Q22", "ISL")],
        overviews={"Q20": 0},
        regional=[("Q20", None), ("Q21", None), ("Q22", None)],
    )

    regions = _regions(export_region_map(geohistory, wikidata, match, output))

    assert "MAIN" not in regions and regions["BIG"]["overviewItemId"] == "Q20"
    assert regions["SUB"]["inheritedOverviewItemId"] == "Q20"
    assert regions["ISL"]["inheritedOverviewItemId"] == "Q20"
    assert (regions["CE"]["overviewItemId"], regions["CE"]["inheritedOverviewItemId"]) == (None, None)


def test_export_region_map_makes_a_zone_drillable_through_its_member_regions(tmp_path: Path) -> None:
    geohistory, wikidata, match, output = _write(tmp_path)

    document = json.loads(export_region_map(geohistory, wikidata, match, output).read_text())
    regions = {r["id"]: r for r in document["regions"]}

    assert regions["CE"]["childIds"] == ["BR"] and regions["CE"]["drillable"] is True
    assert "CE" in document["worldZoneIds"]
    assert set(document["worldChildIds"]) == {"FR", "DE"}
    assert set(document) >= {"regions", "worldChildIds", "worldZoneIds"}


def test_export_region_map_draws_every_country_and_territory_but_no_genreless_subdivision(tmp_path: Path) -> None:
    geohistory, wikidata, match, output = _write(tmp_path)
    _extend(
        geohistory,
        wikidata,
        entities={
            "VU": ("country", box(40, 0, 41, 1), "natural_earth"),
            "JE": ("admin_unit", box(42, 0, 43, 1), "natural_earth"),
            "IN": ("country", box(1, 6, 3, 8), "natural_earth"),
        },
        containment=[("FR", "IN")],
    )

    document = json.loads(export_region_map(geohistory, wikidata, match, output).read_text())
    regions = {r["id"]: r for r in document["regions"]}

    assert "NR" not in regions
    assert (regions["VU"]["parentId"], regions["VU"]["genreCount"]) == ("world", 0)
    assert (regions["JE"]["parentId"], regions["JE"]["genreCount"]) == ("world", 0)
    assert regions["IN"]["parentId"] == "FR" and "IN" in regions["FR"]["childIds"]


def test_export_region_map_lists_a_zone_member_dissolved_through_a_chain(tmp_path: Path) -> None:
    geohistory, wikidata, match, output = _write(tmp_path)
    _extend(
        geohistory,
        wikidata,
        entities={
            "TOP": ("country", box(100, 0, 120, 10), "natural_earth"),
            "MID": ("admin_unit", box(100, 0, 116, 10), "x"),
            "LOW": ("admin_unit", box(100, 0, 112, 10), "x"),
            "ISL": ("admin_unit", box(118, 0, 120, 2), "x"),
            "ISL2": ("admin_unit", box(113, 0, 115, 2), "x"),
            "ZZ": ("cultural_region", box(100, 0, 112, 10), "wikidata"),
        },
        containment=[("TOP", "MID"), ("TOP", "ISL"), ("MID", "LOW"), ("MID", "ISL2")],
        anchors=[("Q20", "LOW"), ("Q21", "ISL"), ("Q22", "ISL2"), ("Q23", "ZZ")],
    )
    _append(geohistory / "8_entity_footprint.parquet", [("ZZ", "LOW", "cover")])

    regions = _regions(export_region_map(geohistory, wikidata, match, output))

    assert {"LOW", "MID"}.isdisjoint(regions)
    assert regions["ZZ"]["childIds"] == ["TOP"]


def _anchor_city(geohistory: Path, wikidata: Path, rows: list[tuple[str, str]]) -> None:
    _append(geohistory / "8_entity_footprint.parquet", [("RN", "BR", "point")])
    _append(
        geohistory / "5_music_anchor.parquet",
        [(item, anchor, "P740", False, None) for item, anchor in rows],
    )


def test_export_region_map_emits_a_city_only_when_a_genre_anchors_on_it(tmp_path: Path) -> None:
    geohistory, wikidata, match, output = _write(tmp_path)

    regions = _regions(export_region_map(geohistory, wikidata, match, output))

    assert "RN" not in regions
    assert all("cityIds" not in region for region in regions.values())


def test_export_region_map_lists_a_city_genre_on_the_city_and_its_admin_unit(tmp_path: Path) -> None:
    geohistory, wikidata, match, output = _write(tmp_path)
    _anchor_city(geohistory, wikidata, [("Q8", "RN")])
    _append(wikidata / "8_regional_hierarchy.parquet", [("Q8", None)])

    regions = _regions(export_region_map(geohistory, wikidata, match, output))

    city = regions["RN"]
    assert (city["kind"], city["parentId"], city["childIds"], city["drillable"]) == ("city", "BR", [], False)
    assert city["centroid"] == [-1.68, 48.11]
    assert city["bbox"] == city["mainBbox"] == [-1.68, 48.11, -1.68, 48.11]
    assert city["genres"] == [{"itemId": "Q8", "rule": "P740", "relation": "regional"}]
    assert regions["BR"]["cityIds"] == ["RN"]
    assert "RN" not in regions["BR"]["childIds"]
    assert {"itemId": "Q8", "rule": "city_parent", "relation": "regional"} in regions["BR"]["genres"]
    assert regions["FR"]["genreCount"] >= 1


def test_export_region_map_puts_a_city_layer_in_the_tileset(
    tmp_path: Path, fake_tippecanoe: list[list[str]], monkeypatch: pytest.MonkeyPatch
) -> None:
    geohistory, wikidata, match, output = _write(tmp_path)
    _anchor_city(geohistory, wikidata, [("Q8", "RN")])
    layers: dict[str, list[dict]] = {}
    inner = region_map_export._run_tippecanoe

    def capture(arguments: list[str]) -> None:
        for name, path in (a.split(":", 1) for a in arguments if a.count(":") == 1 and not a.startswith("-")):
            layers[name] = _layer(Path(path))
        inner(arguments)

    monkeypatch.setattr(region_map_export, "_run_tippecanoe", capture)

    export_region_map(geohistory, wikidata, match, output)

    (feature,) = layers["cities"]
    assert feature["properties"] == {"id": "RN", "parentId": "BR", "name": "Rennes", "min_zoom": 5}
    assert feature["geometry"] == {"type": "Point", "coordinates": [-1.68, 48.11]}
    assert "RN" not in {f["properties"]["id"] for f in layers["region_labels"]}


def test_mercator_extent_measures_the_larger_side_and_wraps_across_the_antimeridian():
    assert _mercator_extent([-180, -10, 180, 10]) == pytest.approx(1.0)
    assert _mercator_extent([0, 0, 36, 1]) == pytest.approx(0.1)
    assert _mercator_extent([170, 0, -170, 1]) == pytest.approx(20 / 360)
    assert _mercator_extent([0, -89, 1, 89]) == pytest.approx(_mercator_extent([0, -85.0511, 1, 85.0511]))


def test_zoom_spanning_is_the_zoom_at_which_a_box_covers_the_pixels():
    assert _zoom_spanning([-180, -10, 180, 10], TILE_PX) == pytest.approx(0.0)
    assert _zoom_spanning([0, 0, 36, 1], TILE_PX) == pytest.approx(math.log2(10))
    assert _zoom_spanning([1, 1, 1, 1], SPLIT_PX) == TILESET_MAX_ZOOM


def test_admin_bands_hand_each_split_down_to_the_subdivisions():
    children = {"world": ["RU", "LU"], "RU": ["MOW", "SIB"], "LU": ["LUX"], "SIB": ["NSK"]}
    bboxes = {"RU": [20, 41, 190, 82], "LU": [5.7, 49.4, 6.5, 50.2], "SIB": [60, 50, 110, 75]}
    bands, depths = _admin_bands(children, bboxes)

    assert bands["RU"][0] == bands["LU"][0] == 0.0
    assert bands["RU"][1] == pytest.approx(MIN_BAND)
    assert bands["LU"][1] == pytest.approx(_zoom_spanning(bboxes["LU"], SPLIT_PX))
    assert bands["LU"][1] > bands["SIB"][1] > bands["RU"][1]
    assert bands["MOW"] == (bands["RU"][1], None) and bands["LUX"] == (bands["LU"][1], None)
    assert bands["SIB"][0] == bands["RU"][1] and bands["NSK"][0] == bands["SIB"][1]
    assert depths == {"RU": 0, "LU": 0, "MOW": 1, "SIB": 1, "LUX": 1, "NSK": 2}


def test_admin_bands_keep_a_minimum_band_and_let_tiny_regions_split_past_the_tileset():
    tiny = [0, 0, 1e-4, 1e-4]
    world = [-180, -85, 180, 85]
    bands, _ = _admin_bands({"world": ["A"], "A": ["B"], "B": ["C"]}, {"A": world, "B": tiny})
    assert bands["A"] == (0.0, pytest.approx(MIN_BAND))
    assert bands["B"][1] == pytest.approx(_zoom_spanning(tiny, SPLIT_PX)) and bands["B"][1] > TILESET_MAX_ZOOM
    assert bands["C"] == (bands["B"][1], None)


def test_admin_bands_reject_a_region_reached_twice():
    with pytest.raises(ValueError, match="reached twice"):
        _admin_bands({"world": ["A", "B"], "A": ["C"], "B": ["C"]}, {"A": [0, 0, 10, 10], "B": [0, 0, 10, 10]})


def test_zone_band_shows_a_zone_between_its_min_and_max_spans():
    europe = [-10, 35, 40, 70]
    assert _zone_band(europe) == (
        pytest.approx(_zoom_spanning(europe, region_map_export.ZONE_MIN_PX)),
        pytest.approx(_zoom_spanning(europe, region_map_export.ZONE_MAX_PX)),
    )
    assert _zone_band([-180, -85, 180, 85])[0] == 0.0
    tiny_min, tiny_max = _zone_band([0, 0, 1e-5, 1e-5])
    assert tiny_min == TILESET_MAX_ZOOM and tiny_max >= tiny_min + MIN_BAND
