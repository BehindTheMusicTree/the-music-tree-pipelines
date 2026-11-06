"""Natural Earth land independent of the place containment graph."""

from collections import defaultdict
from pathlib import Path

import polars as pl
import shapely


def reference_land(bronze_dir: Path) -> shapely.Geometry:
    countries = pl.read_parquet(bronze_dir / "natural_earth_admin.parquet", columns=["level", "geometry_wkb"]).filter(
        pl.col("level") == 0
    )
    if countries.is_empty():
        raise ValueError("natural_earth_admin.parquet has no admin-0 country, cannot build the land layer")
    return shapely.union_all(shapely.make_valid(shapely.from_wkb(countries["geometry_wkb"].to_list())))


def reference_admin_geometries(bronze_dir: Path, ne_map: pl.DataFrame) -> dict[str, shapely.Geometry]:
    """Union Natural Earth polygons mapped to each entity, independent of containment."""
    polygons = (
        pl.read_parquet(bronze_dir / "natural_earth_admin.parquet", columns=["ne_id", "geometry_wkb"])
        .join(ne_map.select("ne_id", "entity_id").drop_nulls(), on="ne_id")
        .sort("ne_id")
    )
    grouped: dict[str, list[shapely.Geometry]] = defaultdict(list)
    for entity_id, wkb in polygons.select("entity_id", "geometry_wkb").rows():
        grouped[entity_id].append(shapely.make_valid(shapely.from_wkb(wkb)))
    return {entity_id: shapely.union_all(parts) for entity_id, parts in grouped.items()}
