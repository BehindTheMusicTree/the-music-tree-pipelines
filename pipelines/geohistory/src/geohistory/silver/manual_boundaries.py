"""Load reviewed WGS84 polygons with an explicit display or containment purpose."""

import json
import re
from dataclasses import dataclass
from pathlib import Path

from shapely.geometry import shape

from geohistory.silver.place_relation_geometry import ReferenceGeometry

CATALOG = Path(__file__).with_name("manual_boundaries.geojson")


@dataclass(frozen=True)
class ManualBoundary:
    reference: ReferenceGeometry
    provenance: dict


def read_manual_boundaries(path: Path = CATALOG) -> dict[str, ManualBoundary]:
    collection = json.loads(path.read_text())
    if collection.get("type") != "FeatureCollection" or not isinstance(collection.get("features"), list):
        raise ValueError("manual boundaries must be a GeoJSON FeatureCollection")
    result = {}
    required = {
        "wikidata_id",
        "source_url",
        "license",
        "version",
        "definition",
        "components",
        "complete",
        "usage",
        "start_year",
        "end_year",
    }
    for feature in collection["features"]:
        properties = feature.get("properties", {})
        if feature.get("type") != "Feature" or set(properties) != required:
            raise ValueError("manual boundary has invalid provenance fields")
        qid = properties["wikidata_id"]
        if not isinstance(qid, str) or not re.fullmatch(r"Q[1-9][0-9]*", qid) or qid in result:
            raise ValueError("manual boundary QID must be valid and unique")
        for key in ("source_url", "license", "version", "definition"):
            if not isinstance(properties[key], str) or not properties[key].strip():
                raise ValueError(f"{qid}: {key} must be non-empty")
        if not properties["source_url"].startswith("https://"):
            raise ValueError(f"{qid}: source_url must use HTTPS")
        components = properties["components"]
        if not isinstance(components, list) or any(not isinstance(c, str) or not c.strip() for c in components):
            raise ValueError(f"{qid}: components must list source component identifiers")
        complete, usage = properties["complete"], properties["usage"]
        if type(complete) is not bool or usage not in {"display", "containment"}:
            raise ValueError(f"{qid}: invalid completeness or usage")
        if usage == "containment" and not complete:
            raise ValueError(f"{qid}: containment requires a complete boundary")
        start, end = properties["start_year"], properties["end_year"]
        if any(year is not None and type(year) is not int for year in (start, end)):
            raise ValueError(f"{qid}: boundary years must be integers or null")
        if start is not None and end is not None and start > end:
            raise ValueError(f"{qid}: boundary period is reversed")
        geometry = shape(feature["geometry"])
        if geometry.geom_type not in {"Polygon", "MultiPolygon"} or geometry.is_empty or not geometry.is_valid:
            raise ValueError(f"{qid}: boundary must be a valid non-empty polygon")
        west, south, east, north = geometry.bounds
        if not (-180 <= west <= east <= 180 and -90 <= south <= north <= 90):
            raise ValueError(f"{qid}: boundary coordinates must be WGS84 longitude/latitude")
        result[qid] = ManualBoundary(
            ReferenceGeometry(
                geometry,
                complete,
                "manual_boundary",
                "independent" if usage == "containment" else "display",
                start_year=start,
                end_year=end,
            ),
            properties,
        )
    return result
