import json
from pathlib import Path

import polars as pl
import pytest
import shapely
from shapely.geometry import Point, box

from gold import region_map_export
from gold.region_map_export import export_region_map


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
    pl.DataFrame({"item_id": ["Q2"]}).write_parquet(wikidata / "8_regional_hierarchy.parquet")
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
    assert regions["CE"]["kind"] == "zone" and regions["CE"]["genres"][0]["relation"] == "regional"
    assert regions["BR"]["zoneIds"] == ["CE"] and regions["FR"]["zoneIds"] == ["CE"]
    geometry_dir = output / "5_region_map"
    world = json.loads((geometry_dir / "geometry" / "world.geojson").read_text())
    assert {f["id"] for f in world["features"]} == {"FR", "DE"}
    assert (geometry_dir / "geometry" / "FR.geojson").exists()
    assert not (geometry_dir / "geometry" / "BR.geojson").exists()
    assert (geometry_dir / "zones" / "CE.geojson").exists()
    assert all(p.stat().st_size <= region_map_export.SIZE_LIMIT_BYTES for p in geometry_dir.rglob("*.geojson"))


def test_export_region_map_reports_unplaced_genres(tmp_path: Path) -> None:
    geohistory, wikidata, match, output = _write(tmp_path)

    export_region_map(geohistory, wikidata, match, output)

    unplaced = pl.read_csv(output / "5_region_map_unplaced.csv").sort("wikidata_id")
    assert unplaced.select("wikidata_id", "reason").rows() == [
        ("Q4", "anchor is not a place"),
        ("Q5", "anchor is not a place"),
    ]
    assert json.loads((output / "5_region_map_report.json").read_text()) == {"unplacedGenreCount": 2}


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
    with pytest.raises(ValueError, match="emptied"):
        region_map_export._feature_collection([("P", "P", Point(1, 1).buffer(0))], "n")
