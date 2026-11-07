import json

import pytest
import shapely
from shapely.geometry import mapping

from geohistory.silver.manual_boundaries import read_manual_boundaries
from geohistory.silver.place_relation_geometry import ReferenceGeometry, resolve_geometry
from geohistory.silver.reference_land import composed_references


def catalog(tmp_path, **changes):
    properties = {
        "wikidata_id": "Q1",
        "source_url": "https://example.org/boundary",
        "license": "CC0-1.0",
        "version": "2026-01",
        "definition": "Test source boundary",
        "components": [],
        "complete": True,
        "usage": "display",
        "start_year": None,
        "end_year": None,
    }
    properties.update(changes)
    path = tmp_path / "boundaries.geojson"
    path.write_text(
        json.dumps(
            {
                "type": "FeatureCollection",
                "features": [
                    {"type": "Feature", "properties": properties, "geometry": mapping(shapely.box(0, 0, 2, 2))}
                ],
            }
        )
    )
    return path


def test_display_boundary_never_proves_containment_or_composes(tmp_path):
    boundary = read_manual_boundaries(catalog(tmp_path))["Q1"]
    child = ReferenceGeometry(shapely.box(0.5, 0.5, 1, 1), True, "test", "independent")
    assert resolve_geometry(child, boundary.reference, ("Q2", "Q1"), precision=1e-6) == "unknown"
    assert "Q3" not in composed_references({"Q1": boundary.reference}, {("Q1", "Q3")}, complete_entities={"Q3"})
    assert boundary.provenance["license"] == "CC0-1.0"


def test_containment_boundary_respects_period(tmp_path):
    boundary = read_manual_boundaries(catalog(tmp_path, usage="containment", start_year=1800, end_year=1900))["Q1"]
    child = ReferenceGeometry(shapely.box(0.5, 0.5, 1, 1), True, "test", "independent", start_year=1800, end_year=1900)
    assert resolve_geometry(child, boundary.reference, ("Q2", "Q1"), precision=1e-6) == "unknown"
    assert (
        resolve_geometry(child, boundary.reference, ("Q2", "Q1"), precision=1e-6, start_year=1850, end_year=1860)
        == "full"
    )


@pytest.mark.parametrize(
    "changes",
    [
        {"usage": "containment", "complete": False},
        {"license": ""},
        {"version": ""},
        {"start_year": 1900, "end_year": 1800},
        {"start_year": True},
        {"complete": "true"},
        {"wikidata_id": "Q0"},
        {"usage": "approximate"},
        {"components": [""]},
    ],
)
def test_invalid_provenance_fails(tmp_path, changes):
    with pytest.raises(ValueError):
        read_manual_boundaries(catalog(tmp_path, **changes))
