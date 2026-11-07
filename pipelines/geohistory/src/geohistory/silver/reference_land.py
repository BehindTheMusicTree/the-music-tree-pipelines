"""Natural Earth land independent of the place containment graph."""

from collections import defaultdict
from collections.abc import Callable
from pathlib import Path

from geohistory.silver.place_relation_geometry import ReferenceGeometry

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
        # A planar component touching the seam may be only half of a connected island.
        west, _, east, _ = part.bounds
        if west <= -180 + 0.000001 or east >= 180 - 0.000001:
            return None
        if not shapely.contains(part, point) or not verify_identity(part):
            return None
        return part


def reference_island_geometries(
    bronze_dir: Path, crosswalk: pl.DataFrame, entities: pl.DataFrame
) -> dict[str, shapely.Geometry]:
    """Identify simple islands against QID-linked physical labels before selecting land."""
    physical = pl.read_parquet(bronze_dir / "natural_earth_physical.parquet").filter(pl.col("kind") == "Island")
    physical = (
        physical.join(crosswalk, left_on="wikidata_id", right_on="wikidata_id")
        .join(entities.select("entity_id", "latitude", "longitude"), on="entity_id")
        .drop_nulls(["latitude", "longitude"])
    )
    index = IslandLandIndex(reference_land(bronze_dir))
    result: dict[str, shapely.Geometry] = {}
    for row in physical.iter_rows(named=True):
        label_polygon = shapely.make_valid(shapely.from_wkb(row["geometry_wkb"]))
        identity_point = label_polygon.representative_point()
        selected = index.select(
            shapely.Point(row["longitude"], row["latitude"]),
            is_simple_island=True,
            verify_identity=lambda part: shapely.contains(part, identity_point),
        )
        if selected is not None:
            if row["entity_id"] in result and not shapely.equals(result[row["entity_id"]], selected):
                raise ValueError(f"conflicting physical island identity for {row['entity_id']}")
            result[row["entity_id"]] = selected
    return result


def composed_references(
    references: dict[str, ReferenceGeometry],
    full_members: set[tuple[str, str]],
    *,
    complete_entities: set[str],
) -> dict[str, ReferenceGeometry]:
    """Build explicit member unions, retaining dependencies to prevent circular proof."""
    if any(child == parent for child, parent in full_members):
        raise ValueError("composed boundary cannot contain itself")
    members: dict[str, set[str]] = defaultdict(set)
    for child, parent in full_members:
        members[parent].add(child)
    result = dict(references)
    pending = set(members) - set(result)
    while pending:
        ready = sorted(parent for parent in pending if members[parent] <= result.keys())
        if not ready:
            break
        for parent in ready:
            children = [result[child] for child in sorted(members[parent])]
            if any(child.boundary_kind != "independent" for child in children):
                pending.remove(parent)
                continue
            periods = {(child.start_year, child.end_year) for child in children}
            if len(periods) != 1:
                pending.remove(parent)
                continue
            start_year, end_year = next(iter(periods))
            dependencies = frozenset((child, parent) for child in members[parent]).union(
                *(child.derived_from for child in children)
            )
            result[parent] = ReferenceGeometry(
                shapely.union_all([child.geometry for child in children]),
                parent in complete_entities and all(child.complete for child in children),
                "explicit_member_union",
                "independent",
                dependencies,
                start_year,
                end_year,
            )
            pending.remove(parent)
    return result
