"""Intersect resolved reference boundaries with administrative polygons for map coverage."""

from math import isfinite
from pathlib import Path

import polars as pl
import shapely

from geohistory.silver.place_relation_geometry import ReferenceGeometry

COVERAGE_SCHEMA = {
    "entity_id": pl.String,
    "admin_id": pl.String,
    "extent": pl.String,
    "geometry_source": pl.String,
    "geometry_wkb": pl.Binary,
    "complete": pl.Boolean,
}


def reference_coverage(
    references: dict[str, ReferenceGeometry], admins: dict[str, shapely.Geometry], *, precision: float
) -> pl.DataFrame:
    """Keep clipped polygons; coverage is never an input to the containment graph."""
    if not isfinite(precision) or precision <= 0:
        raise ValueError("precision must be finite and positive")
    admin_ids = sorted(admins)
    polygons = [shapely.set_precision(shapely.make_valid(admins[item]), precision) for item in admin_ids]
    index = shapely.STRtree(polygons)
    rows = []
    for entity_id, reference in sorted(references.items()):
        if reference.boundary_kind != "independent":
            continue
        geometry = shapely.set_precision(shapely.make_valid(reference.geometry), precision)
        if geometry.is_empty or geometry.area == 0:
            continue
        for position in sorted(index.query(geometry, predicate="intersects")):
            admin = polygons[position]
            clipped = shapely.intersection(geometry, admin)
            if clipped.is_empty or clipped.area == 0:
                continue
            extent = "full" if shapely.covers(geometry, admin) else "partial"
            rows.append((entity_id, admin_ids[position], extent, reference.source, clipped.wkb, reference.complete))
    return pl.DataFrame(rows, schema=COVERAGE_SCHEMA, orient="row").sort("entity_id", "admin_id")


def entity_coverage(bronze_dir: Path, silver_dir: Path) -> Path:
    from geohistory.silver.reference_land import reference_admin_geometries

    reference_rows = pl.read_parquet(silver_dir / "2_place_reference_geometry.parquet")
    geometry_rows = pl.read_parquet(silver_dir / "8_entity_geometry.parquet")
    completeness = dict(reference_rows.select("entity_id", "complete").rows())
    references = {
        row["entity_id"]: ReferenceGeometry(
            shapely.from_wkb(row["geometry_wkb"]),
            completeness.get(row["entity_id"], False),
            row["source"],
            "independent",
        )
        for row in geometry_rows.iter_rows(named=True)
    }
    admins = reference_admin_geometries(bronze_dir, pl.read_parquet(silver_dir / "1_entity_ne_admin.parquet"))
    frame = reference_coverage(references, admins, precision=0.000001)
    path = silver_dir / "8_entity_coverage.parquet"
    frame.write_parquet(path)
    return path
