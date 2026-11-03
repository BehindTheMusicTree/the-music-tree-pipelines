import gzip
import hashlib
import json
import shutil
from pathlib import Path

import polars as pl
import pytest
import shapely
from shapely.geometry import Point, box, mapping

from gold import region_map_export
from gold.region_map_export import export_region_map

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
    _write_basemap(geohistory)
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
    assert set(report) == {"unplacedGenreCount", "tileset"}


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
    pl.DataFrame(schema={"wikidata_id": pl.String, "anchor_id": pl.String, "rule": pl.String}).write_parquet(
        geohistory / "5_music_anchor.parquet"
    )

    with pytest.raises(ValueError, match="no region"):
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
    assert [a for a in fake_tippecanoe[0] if a.startswith(("-Z", "-z", "--detect"))] == [
        "-Z0",
        "-z10",
        "--detect-shared-borders",
    ]
    index_regions = {r["id"]: r for r in json.loads(index_path.read_text())["regions"]}
    assert {f["properties"]["id"] for f in layers["regions"]} == {"FR", "BR", "NR", "DE"}
    assert all(
        f["properties"] == {"id": f["properties"]["id"], "parentId": index_regions[f["properties"]["id"]]["parentId"],
                            "kind": index_regions[f["properties"]["id"]]["kind"],
                            "name": index_regions[f["properties"]["id"]]["name"]}
        for f in layers["regions"]
    )  # fmt: skip
    assert [f["properties"] for f in layers["zones"]] == [{"id": "CE", "name": "Celtic nations"}]
    assert list(layers) == ["regions", "region_labels", "zones", "land", "lakes", "rivers", "disputed", "places"]
    labelled = {f["properties"]["id"]: f for f in layers["region_labels"]}
    assert set(labelled) == {"FR", "BR", "NR", "DE"}
    assert labelled["BR"]["properties"] == {"id": "BR", "parentId": "FR", "name": "Bretagne"}
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
    assert all("tippecanoe" not in f for name in ("regions", "region_labels", "zones", "land") for f in layers[name])
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
