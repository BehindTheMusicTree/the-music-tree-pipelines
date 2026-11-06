"""Natural Earth land independent of the place containment graph."""

from collections import defaultdict
from collections.abc import Callable
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


class IslandLandIndex:
    """Index independent land components for reuse across island candidates."""

    def __init__(self, land: shapely.Geometry) -> None:
        self.parts = (
            list(shapely.get_parts(land))
            if land.geom_type in {"Polygon", "MultiPolygon"} and not land.is_empty and land.is_valid
            else []
        )
        self.tree = shapely.STRtree(self.parts)

    def select(
        self,
        point: shapely.Geometry,
        *,
        is_simple_island: bool,
        verify_identity: Callable[[shapely.Geometry], bool],
    ) -> shapely.Geometry | None:
        """Select a unique interior hit only after independent identity verification."""
        if not is_simple_island or point.geom_type != "Point" or point.is_empty:
            return None
        matches = self.tree.query(point, predicate="intersects")
        if len(matches) != 1:
            return None
        part = self.parts[matches[0]]
        if not shapely.contains(part, point) or not verify_identity(part):
            return None
        return part
