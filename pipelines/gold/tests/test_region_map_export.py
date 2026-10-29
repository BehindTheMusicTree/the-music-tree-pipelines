import json
from pathlib import Path

import polars as pl
import pytest
import shapely
from shapely.geometry import Point, box

from gold import region_map_export
from gold.region_map_export import export_region_map


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
    regional: list[tuple[str, str | None]] = [],
) -> None:
    _append(geohistory / "1_cultural_entity.parquet", [(e, kind, e) for e, (kind, _, _) in entities.items()])
    _append(
        geohistory / "8_entity_geometry.parquet",
        [tuple(_geometry_row(e, shape, source).values()) for e, (_, shape, source) in entities.items()],
    )
    _append(geohistory / "2_place_containment.parquet", [(parent, child, "p") for parent, child in containment])
    _append(geohistory / "5_music_anchor.parquet", [(item, anchor, "P495") for item, anchor in anchors])
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


def _write(tmp_path: Path) -> tuple[Path, Path, Path, Path]:
    geohistory, wikidata, output = tmp_path / "geohistory", tmp_path / "wikidata", tmp_path / "out"
    geohistory.mkdir()
    wikidata.mkdir()
    output.mkdir()
    pl.DataFrame(
        [
            ("FR", "country", "France"),
            ("BR", "admin_unit", "Bretagne"),
            ("NR", "admin_unit", "Normandie"),
            ("DE", "country", "Germany"),
            ("CE", "cultural_region", "Celtic nations"),
            ("LG", "language", "Breton"),
        ],
        schema=["entity_id", "kind", "label"],
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
    pl.DataFrame([("CE", "BR", "point")], schema=["entity_id", "admin_id", "rule"], orient="row").write_parquet(
        geohistory / "8_entity_footprint.parquet"
    )
    pl.DataFrame(
        [("FR", "BR", "p"), ("FR", "NR", "p")], schema=["parent_id", "child_id", "source"], orient="row"
    ).write_parquet(geohistory / "2_place_containment.parquet")
    pl.DataFrame(
        [
            ("Q1", "BR", "P495"),
            ("Q2", "CE", "P495"),
            ("Q3", "DE", "P495"),
            ("Q4", "LG", "P495"),
            ("Q5", "NOPE", "P495"),
        ],
        schema=["wikidata_id", "anchor_id", "rule"],
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
    assert set(regions) == {"FR", "BR", "NR", "DE", "CE"}
    assert regions["FR"]["childIds"] == ["BR", "NR"] and regions["FR"]["drillable"]
    assert regions["FR"]["parentId"] == "world" and regions["BR"]["parentId"] == "FR"
    assert regions["BR"]["genres"] == [{"itemId": "Q1", "rule": "P495", "relation": "canonical"}]
    assert (regions["BR"]["songCount"], regions["BR"]["genreCount"]) == (4, 3)
    assert (regions["FR"]["songCount"], regions["FR"]["genreCount"]) == (4, 3)
    assert (regions["NR"]["songCount"], regions["NR"]["genreCount"]) == (0, 0)
    assert regions["CE"]["kind"] == "zone" and regions["CE"]["genres"][0]["relation"] == "regional"
    assert regions["BR"]["zoneIds"] == ["CE"] and regions["FR"]["zoneIds"] == ["CE"]
    assert regions["NR"]["zoneIds"] == []
    geometry_dir = output / "5_region_map"
    world = json.loads((geometry_dir / "geometry" / "world.geojson").read_text())
    assert {f["id"] for f in world["features"]} == {"FR", "DE"}
    assert {f["id"] for f in json.loads((geometry_dir / "geometry" / "FR.geojson").read_text())["features"]} == {
        "BR",
        "NR",
    }
    assert not (geometry_dir / "geometry" / "BR.geojson").exists()
    assert (geometry_dir / "zones" / "CE.geojson").exists()
    assert all(p.stat().st_size <= region_map_export.SIZE_LIMIT_BYTES for p in geometry_dir.rglob("*.geojson"))


def test_export_region_map_writes_a_bundle_of_the_index_and_every_file(tmp_path: Path) -> None:
    geohistory, wikidata, match, output = _write(tmp_path)

    index_path = export_region_map(geohistory, wikidata, match, output)

    bundle = json.loads((output / "5_region_map.json").read_text())
    assert bundle["index"] == json.loads(index_path.read_text())
    assert set(bundle["files"]) == {"geometry/world.geojson", "geometry/FR.geojson", "zones/CE.geojson"}
    assert bundle["files"]["zones/CE.geojson"] == json.loads((output / "5_region_map/zones/CE.geojson").read_text())


def test_export_region_map_raises_on_oversized_bundle(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    geohistory, wikidata, match, output = _write(tmp_path)
    monkeypatch.setattr(region_map_export, "BUNDLE_LIMIT_BYTES", 10)

    with pytest.raises(ValueError, match="bundle"):
        export_region_map(geohistory, wikidata, match, output)


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

    regions = _regions(export_region_map(geohistory, wikidata, match, output))

    assert "MAIN" not in regions
    assert regions["BIG"]["childIds"] == ["ISL", "SUB"] and regions["SUB"]["parentId"] == "BIG"
    assert {g["itemId"] for g in regions["BIG"]["genres"]} == {"Q20"}
    assert (regions["ISL"]["songCount"], regions["ISL"]["genreCount"]) == (0, 0)
    assert regions["BIG"]["genreCount"] == 2


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
    assert {f["path"] for f in report["files"]} == {
        "geometry/world.geojson",
        "geometry/FR.geojson",
        "zones/CE.geojson",
    }
    assert all(f["bytes"] > 0 and f["tolerance"] == 0.0 for f in report["files"])


def test_export_region_map_places_footprinted_anchors_on_their_admin_regions(tmp_path: Path) -> None:
    geohistory, wikidata, match, output = _write(tmp_path)
    entities = pl.read_parquet(geohistory / "1_cultural_entity.parquet")
    pl.concat(
        [
            entities,
            pl.DataFrame(
                [("EG", "ethnic_group", "Javanese"), ("CV", "civilization", "Rome")],
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
        [anchors, pl.DataFrame([("Q6", "EG", "P495"), ("Q7", "CV", "P495")], schema=anchors.schema, orient="row")]
    ).write_parquet(geohistory / "5_music_anchor.parquet")

    index_path = export_region_map(geohistory, wikidata, match, output)

    regions = {r["id"]: r for r in json.loads(index_path.read_text())["regions"]}
    genres = regions["NR"]["genres"]
    assert genres == [{"itemId": "Q6", "rule": "P495", "relation": "tradition"}]
    unplaced = pl.read_csv(output / "5_region_map_unplaced.csv")
    assert ("Q7", "anchor is not a place") in unplaced.select("wikidata_id", "reason").rows()


def test_export_region_map_raises_on_oversized_geometry(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    geohistory, wikidata, match, output = _write(tmp_path)
    monkeypatch.setattr(region_map_export, "SIZE_LIMIT_BYTES", 10)

    with pytest.raises(ValueError, match="stays above"):
        export_region_map(geohistory, wikidata, match, output)


def test_export_region_map_raises_without_regions(tmp_path: Path) -> None:
    geohistory, wikidata, match, output = _write(tmp_path)
    pl.DataFrame(schema={"wikidata_id": pl.String, "anchor_id": pl.String, "rule": pl.String}).write_parquet(
        geohistory / "5_music_anchor.parquet"
    )

    with pytest.raises(ValueError, match="no region"):
        export_region_map(geohistory, wikidata, match, output)


def test_without_specks_keeps_the_largest_part_and_drops_islets() -> None:
    main, islet = box(0, 0, 10, 10), box(50, 50, 50.01, 50.01)

    assert region_map_export._without_specks(shapely.union_all([main, islet]), 0.1).equals(main)
    assert region_map_export._without_specks(islet, 1.0).equals(islet)


def test_feature_collection_raises_on_emptied_geometry() -> None:
    with pytest.raises(ValueError, match="emptied or degraded"):
        region_map_export._feature_collection([("P", "P", Point(1, 1).buffer(0))], "n")


def test_feature_collection_keeps_the_largest_part_of_a_speck_only_feature() -> None:
    encoded, tolerance = region_map_export._feature_collection(
        [("A", "A", shapely.union_all([box(0, 0, 0.001, 0.001), box(5, 5, 5.0005, 5.0005)]))], "n"
    )

    assert tolerance == 0.0 and json.loads(encoded)["features"][0]["geometry"]["coordinates"]


def test_polygons_flattens_nested_geometry_collections() -> None:
    inner = shapely.GeometryCollection([box(0, 0, 1, 1), Point(5, 5)])
    outer = shapely.GeometryCollection([inner, shapely.MultiPolygon([box(2, 2, 3, 3), box(4, 4, 5, 5)])])

    assert len(region_map_export._polygons(outer)) == 3


def test_rounded_keeps_a_valid_geometry_whose_every_part_collapses_and_warns(
    caplog: pytest.LogCaptureFixture,
) -> None:
    tiny = shapely.Polygon([(0, 0), (0.004, 0), (0.004, 0.004)])

    with caplog.at_level("WARNING"):
        result = region_map_export._rounded(tiny, "Q99")

    assert result.equals(tiny) and "Q99" in caplog.text


def test_rounded_drops_an_invalid_geometry_whose_every_part_collapses() -> None:
    bowtie = shapely.Polygon([(0, 0), (0.004, 0.004), (0.004, 0), (0, 0.004)])

    assert region_map_export._rounded(bowtie, "Q99").is_empty


def test_rounded_survives_parts_that_collapse_when_rounded() -> None:
    kept = box(1.13, 1.26, 1.43, 1.56)
    collapsed = shapely.Polygon([(0.77, 2.29), (0.77, 2.29), (0.77, 3.29), (0.77, 3.29)])
    speck = shapely.Polygon([(2.9901, 0.7301), (2.9902, 0.7301), (2.9902, 0.7302), (2.9901, 0.7302)])

    assert region_map_export._rounded(shapely.MultiPolygon([speck, collapsed, kept]), "R").equals(kept)


def test_export_region_map_warns_when_the_bundle_nears_its_cap(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    geohistory, wikidata, match, output = _write(tmp_path)
    export_region_map(geohistory, wikidata, match, output)
    size = (output / "5_region_map.json").stat().st_size
    monkeypatch.setattr(region_map_export, "BUNDLE_LIMIT_BYTES", int(size / 0.95))

    with caplog.at_level("WARNING"):
        export_region_map(geohistory, wikidata, match, output)

    assert "5_region_map.json is" in caplog.text


def test_export_region_map_does_not_warn_well_under_the_bundle_cap(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    geohistory, wikidata, match, output = _write(tmp_path)

    with caplog.at_level("WARNING"):
        export_region_map(geohistory, wikidata, match, output)

    assert "5_region_map.json is" not in caplog.text
