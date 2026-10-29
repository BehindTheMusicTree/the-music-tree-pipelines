import json
from pathlib import Path

import pytest

from gold.region_map_fixture_export import export_region_map_fixture


def _region(region_id: str, kind: str, parent: str | None, children=(), zones=()) -> dict:
    return {
        "id": region_id,
        "name": region_id,
        "kind": kind,
        "parentId": parent,
        "childIds": list(children),
        "zoneIds": list(zones),
        "bbox": [0, 0, 1, 1],
        "mainBbox": [0, 0, 1, 1],
        "centroid": [0.5, 0.5],
        "drillable": bool(children),
        "genres": [],
        "songCount": 0,
        "genreCount": 0,
    }


def _export(tmp_path: Path) -> Path:
    regions = [
        _region("FR", "country", "world", ["BR", "NR"], ["CE"]),
        _region("BR", "admin_unit", "FR", [], ["CE"]),
        _region("NR", "admin_unit", "FR", ["CA"], ["CE", "OT"]),
        _region("CA", "admin_unit", "NR"),
        _region("DE", "country", "world"),
        _region("SP", "country", "world", ["AN"]),
        _region("AN", "admin_unit", "SP"),
        _region("CE", "zone", None),
        _region("OT", "zone", None),
    ]
    source = tmp_path / "5_region_map"
    for directory in ("geometry", "zones"):
        (source / directory).mkdir(parents=True)
    (source / "index.json").write_text(json.dumps({"regions": regions}))
    for relative in ("world", "FR", "NR", "SP"):
        (source / "geometry" / f"{relative}.geojson").write_text("{}")
    for zone in ("CE", "OT"):
        (source / "zones" / f"{zone}.geojson").write_text("{}")
    return source


def test_fixture_is_a_referentially_intact_subset(tmp_path: Path) -> None:
    source = _export(tmp_path)
    out = tmp_path / "fixture"

    export_region_map_fixture(source, out, ("BR",), ("CE",))

    regions = {r["id"]: r for r in json.loads((out / "index.json").read_text())["regions"]}
    assert set(regions) == {"FR", "BR", "NR", "DE", "SP", "CE"}
    assert regions["FR"]["drillable"] and regions["FR"]["childIds"] == ["BR", "NR"]
    assert not regions["SP"]["drillable"] and regions["SP"]["childIds"] == []
    assert not regions["NR"]["drillable"] and "CA" not in regions and "AN" not in regions
    assert regions["NR"]["zoneIds"] == ["CE"]
    for region in regions.values():
        assert all(r in regions for r in [*region["childIds"], *region["zoneIds"]])
        if region["drillable"]:
            assert (out / "geometry" / f"{region['id']}.geojson").exists()
    assert (out / "geometry" / "world.geojson").exists() and (out / "zones" / "CE.geojson").exists()
    assert not (out / "geometry" / "SP.geojson").exists()


def test_fixture_prunes_zones_that_are_not_requested(tmp_path: Path) -> None:
    source = _export(tmp_path)
    out = tmp_path / "fixture"

    export_region_map_fixture(source, out, ("BR",), ())

    regions = {r["id"]: r for r in json.loads((out / "index.json").read_text())["regions"]}
    assert "CE" not in regions and regions["BR"]["zoneIds"] == []


@pytest.mark.parametrize(("regions", "zones"), [(("NOPE",), ()), (("CE",), ()), ((), ("BR",)), ((), ("NOPE",))])
def test_fixture_raises_on_an_unknown_region_or_zone(tmp_path: Path, regions: tuple, zones: tuple) -> None:
    source = _export(tmp_path)

    with pytest.raises(ValueError, match="unknown"):
        export_region_map_fixture(source, tmp_path / "fixture", regions, zones)
