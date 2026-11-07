import json

import pytest
import shapely

from geohistory.composed_boundaries import build_composed_boundaries
from geohistory import ingest


def feature(name, subregion, sovereign, geometry):
    return {
        "properties": {
            "NE_ID": name,
            "NAME": name,
            "SUBREGION": subregion,
            "SOV_A3": sovereign,
            "CONTINENT": "Europe",
            "ADM0_A3": "NLD",
        },
        "geometry": shapely.geometry.mapping(geometry) if geometry is not None else None,
    }


def test_composition_keeps_caribbean_members_separate_from_mainland(tmp_path, monkeypatch):
    island = shapely.box(0, 0, 1, 1)
    foreign = shapely.box(2, 0, 3, 1)
    mainland = shapely.box(20, 0, 21, 1)
    collection = {
        "features": [
            feature("island", "Caribbean", "NL1", island),
            feature("foreign", "Caribbean", "FR1", foreign),
            feature("mainland", "Western Europe", "NL1", mainland),
        ]
    }
    frame = build_composed_boundaries(collection, "pinned-version", selected_ids={"Q664609", "Q29999"})
    caribbean = frame.filter(frame["wikidata_id"] == "Q664609").row(0, named=True)
    kingdom = frame.filter(frame["wikidata_id"] == "Q29999").row(0, named=True)
    assert shapely.equals(shapely.from_wkb(caribbean["geometry_wkb"]), shapely.union_all([island, foreign]))
    assert shapely.equals(shapely.from_wkb(kingdom["geometry_wkb"]), shapely.union_all([island, mainland]))
    assert {m["name"] for m in json.loads(caribbean["members_json"])} == {"island", "foreign"}
    assert frame["complete"].to_list() == [True, True]
    monkeypatch.setattr(ingest.natural_earth_client, "download_layer", lambda version, layer: collection)
    monkeypatch.setattr("geohistory.composed_boundaries.build_composed_boundaries", lambda *args: frame)
    assert ingest.ingest_composed_boundaries("pinned-version", tmp_path).is_file()
    assert frame.equals(
        build_composed_boundaries(
            {"features": list(reversed(collection["features"]))}, "pinned-version", selected_ids={"Q664609", "Q29999"}
        )
    )


def test_composition_fails_when_members_or_geometry_are_missing():
    with pytest.raises(ValueError, match="no members"):
        build_composed_boundaries({"features": []}, "v", selected_ids={"Q664609"})
    with pytest.raises(ValueError, match="without geometry"):
        build_composed_boundaries(
            {"features": [feature("missing", "Caribbean", "NL1", None)]}, "v", selected_ids={"Q664609"}
        )


def test_explicit_country_composition_requires_every_member():
    members = [feature(code, "x", code, shapely.box(i, 0, i + 1, 1)) for i, code in enumerate(("KOR", "PRK"))]
    for member in members:
        member["properties"]["ADM0_A3"] = member["properties"]["NAME"]
    complete = build_composed_boundaries({"features": members}, "v", selected_ids={"Q18097"})
    assert complete["complete"].item()
    with pytest.raises(ValueError, match="missing members"):
        build_composed_boundaries({"features": members[:1]}, "v", selected_ids={"Q18097"})
    with pytest.raises(ValueError, match="unknown composed"):
        build_composed_boundaries({"features": members}, "v", selected_ids={"unknown"})


def test_display_compositions_require_components_and_never_supply_containment():
    from geohistory.composed_boundaries import display_compositions

    units = ("IRL", "NIR", "SCT", "WLS", "IMN")
    countries = ("ALB", "BIH", "BGR", "HRV", "GRC", "KOS", "MNE", "MKD", "ROU", "SRB", "SVN")
    collection = {
        "features": [
            {
                "properties": {"GU_A3": code, "ADM0_A3": code},
                "geometry": shapely.geometry.mapping(shapely.box(i, 0, i + 1, 1)),
            }
            for i, code in enumerate((*units, *countries))
        ]
    }
    subdivisions = {
        "features": [
            {"properties": {"iso_3166_2": code}, "geometry": shapely.geometry.mapping(shapely.box(i, 2, i + 1, 3))}
            for i, code in enumerate(("GB-CON", "FR-22", "FR-29", "FR-35", "FR-44", "FR-56"))
        ]
    }
    rows = display_compositions(collection, subdivisions, "pinned")
    assert {row[0]: (row[-2], row[-1]) for row in rows} == {"Q236626": (True, "display"), "Q23522": (False, "display")}
    celtic = shapely.from_wkb(rows[0][1])
    assert celtic.covers(shapely.box(1, 0, 2, 1))  # Northern Ireland
    assert not celtic.intersects(shapely.box(15, 0, 16, 1))
    with pytest.raises(ValueError, match="missing members"):
        display_compositions(collection, {"features": []}, "pinned")
    subdivisions["features"][0]["geometry"] = None
    with pytest.raises(ValueError, match="without geometry"):
        display_compositions(collection, subdivisions, "pinned")


def test_current_administrative_composition_requires_exactly_both_provinces():
    from geohistory.composed_boundaries import administrative_compositions

    subdivisions = {
        "features": [
            {"properties": {"iso_3166_2": code}, "geometry": shapely.geometry.mapping(shapely.box(i, 0, i + 1, 1))}
            for i, code in enumerate(("ES-BA", "ES-CC", "ES-CO"))
        ]
    }
    rows = administrative_compositions(subdivisions, "pinned")
    assert len(rows) == 1
    qid, wkb, source, version, definition, members, complete, usage = rows[0]
    assert qid == "Q5777"
    assert shapely.equals(shapely.from_wkb(wkb), shapely.box(0, 0, 2, 1))
    assert source == "natural_earth_admin_unit_union"
    assert version == "pinned" and "boe.es" in definition
    assert {m["value"] for m in json.loads(members)} == {"ES-BA", "ES-CC"}
    assert complete and usage == "containment"
    for features in (subdivisions["features"][:1], subdivisions["features"] + subdivisions["features"][:1]):
        with pytest.raises(ValueError, match="exactly one member"):
            administrative_compositions({"features": features}, "pinned")
    subdivisions["features"][0]["geometry"] = None
    with pytest.raises(ValueError, match="without geometry"):
        administrative_compositions(subdivisions, "pinned")


@pytest.fixture
def polynesia_sources():
    units = ("PCN", "PYF", "COK", "TON", "WLF", "WSM", "TUV", "NIU", "ASM", "TKL")
    collection = {
        "features": [
            {
                "properties": {"GU_A3": code, "NE_ID": code},
                "geometry": shapely.geometry.mapping(shapely.box(-170 + i, -15, -169.9 + i, -14.9)),
            }
            for i, code in enumerate(units)
        ]
    }
    for code, parts in (
        (
            "NZL",
            [shapely.box(174, -40, 175, -39), shapely.box(-177, -44, -176, -43.5), shapely.box(169, -53, 170, -52)],
        ),
        ("CHL", [shapely.box(-109.4, -27.2, -109.3, -27.1), shapely.box(-72, -35, -71, -34)]),
        ("USA", [shapely.box(-120, 35, -119, 36)]),
    ):
        collection["features"].append(
            {
                "properties": {"GU_A3": code, "NE_ID": code},
                "geometry": shapely.geometry.mapping(shapely.MultiPolygon(parts)),
            }
        )
    subdivisions = {
        "features": [
            {
                "properties": {"iso_3166_2": "US-HI", "ne_id": "hawaii"},
                "geometry": shapely.geometry.mapping(shapely.box(-156, 20, -155, 21)),
            }
        ]
    }
    return collection, subdivisions


def test_polynesia_selects_islands_without_inheriting_sovereign_mainlands(polynesia_sources):
    collection, subdivisions = polynesia_sources
    frame = build_composed_boundaries(collection, "pinned", subdivisions, selected_ids={"Q35942"})
    row = frame.row(0, named=True)
    geometry = shapely.from_wkb(row["geometry_wkb"])
    for point in [(-155.5, 20.5), (-109.35, -27.15), (174.5, -39.5), (-176.5, -43.75), (-169.95, -14.95)]:
        assert geometry.contains(shapely.Point(*point))
    for point in [(-119.5, 35.5), (-71.5, -34.5), (169.5, -52.5), (-130, 0)]:
        assert not geometry.intersects(shapely.Point(*point))
    assert not row["complete"] and row["usage"] == "containment"
    assert "teara.govt.nz" in row["definition"]
    collection["features"].reverse()
    assert frame.equals(build_composed_boundaries(collection, "pinned", subdivisions, selected_ids={"Q35942"}))


@pytest.mark.parametrize(
    "defect", ["unit", "duplicate_unit", "hawaii", "duplicate", "geometry", "cut", "component", "subdivisions"]
)
def test_polynesia_fails_on_missing_or_ambiguous_components(polynesia_sources, defect):
    collection, subdivisions = polynesia_sources
    if defect == "unit":
        collection["features"].pop(0)
    elif defect == "duplicate_unit":
        collection["features"].append(collection["features"][0])
    elif defect == "hawaii":
        subdivisions["features"] = []
    elif defect == "duplicate":
        subdivisions["features"] *= 2
    elif defect == "geometry":
        subdivisions["features"][0]["geometry"] = None
    elif defect in {"cut", "component"}:
        chile = next(f for f in collection["features"] if f["properties"]["GU_A3"] == "CHL")
        chile["geometry"] = shapely.geometry.mapping(
            shapely.box(-109.5, -27.2, -108.5, -27.1) if defect == "cut" else shapely.box(-72, -35, -71, -34)
        )
    elif defect == "subdivisions":
        subdivisions = None
    with pytest.raises(ValueError, match="Polynesia"):
        build_composed_boundaries(collection, "pinned", subdivisions, selected_ids={"Q35942"})
