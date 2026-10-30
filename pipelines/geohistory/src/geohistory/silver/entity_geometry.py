import logging
from collections import defaultdict
from pathlib import Path

import polars as pl
import shapely

DRAWN_KINDS = ("country", "admin_unit", "historical_country", "historical_region", "cultural_region", "macro_region")

logger = logging.getLogger(__name__)

GEOMETRY_SCHEMA = pl.Schema(
    {
        "entity_id": pl.String(),
        "geometry_wkb": pl.Binary(),
        "source": pl.String(),
        "bbox": pl.List(pl.Float64()),
        "main_bbox": pl.List(pl.Float64()),
        "centroid": pl.List(pl.Float64()),
    }
)


def _polygons(geometry: shapely.Geometry) -> list[shapely.Geometry]:
    return list(shapely.get_parts(geometry))


def _ne_geometries(bronze_dir: Path, ne_map: pl.DataFrame) -> dict[str, shapely.Geometry]:
    polygons = (
        pl.read_parquet(bronze_dir / "natural_earth_admin.parquet", columns=["ne_id", "geometry_wkb"])
        .join(ne_map.select("ne_id", "entity_id").drop_nulls(), on="ne_id")
        .sort("ne_id")
    )
    grouped: dict[str, list[shapely.Geometry]] = defaultdict(list)
    for entity_id, wkb in polygons.select("entity_id", "geometry_wkb").rows():
        grouped[entity_id].append(shapely.make_valid(shapely.from_wkb(wkb)))
    return {entity_id: shapely.union_all(parts) for entity_id, parts in grouped.items()}


def _descendants(node: str, children: dict[str, list[str]]) -> set[str]:
    seen: set[str] = set()
    pending = list(children.get(node, ()))
    while pending:
        current = pending.pop()
        if current not in seen:
            seen.add(current)
            pending.extend(children.get(current, ()))
    return seen


def entity_geometry(bronze_dir: Path, silver_dir: Path) -> Path:
    ne_map = pl.read_parquet(silver_dir / "1_entity_ne_admin.parquet")
    containment = pl.read_parquet(silver_dir / "2_place_containment.parquet").sort("parent_id", "child_id")
    drawn = set(
        pl.read_parquet(silver_dir / "1_cultural_entity.parquet", columns=["entity_id", "kind"]).filter(
            pl.col("kind").is_in(DRAWN_KINDS)
        )["entity_id"]
    )
    footprint = pl.read_parquet(silver_dir / "8_entity_footprint.parquet").filter(pl.col("entity_id").is_in(drawn))

    children: dict[str, list[str]] = defaultdict(list)
    for parent, child in containment.select("parent_id", "child_id").rows():
        children[parent].append(child)
    ne_geometry = _ne_geometries(bronze_dir, ne_map)

    tile_cache: dict[str, shapely.Geometry | None] = {}

    def tile(admin_id: str) -> shapely.Geometry | None:
        """An admin unit's own polygon, else the tiling of the polygons it contains."""
        if admin_id not in tile_cache:
            if admin_id in ne_geometry:
                tile_cache[admin_id] = ne_geometry[admin_id]
            else:
                parts = [part for child in children.get(admin_id, ()) if (part := tile(child)) is not None]
                tile_cache[admin_id] = shapely.union_all(parts) if parts else None
        return tile_cache[admin_id]

    admins_by_entity: dict[str, set[str]] = defaultdict(set)
    for entity_id, admin_id in footprint.select("entity_id", "admin_id").rows():
        admins_by_entity[entity_id].add(admin_id)

    rows = []
    missing = 0
    for entity_id in sorted(admins_by_entity):
        admins = admins_by_entity[entity_id]
        nested = set().union(*(_descendants(admin, children) for admin in admins)) & admins
        tiles = [geometry for admin in sorted(admins - nested) if (geometry := tile(admin)) is not None]
        missing += len(admins - nested) - len(tiles)
        if not tiles:
            raise ValueError(f"{entity_id} has a footprint but none of its admin units has a polygon")
        geometry = tiles[0] if len(tiles) == 1 else shapely.union_all(tiles)
        if not shapely.is_valid(geometry) or shapely.is_empty(geometry):
            raise ValueError(f"{entity_id} footprint does not tile into a valid geometry")
        main = max(_polygons(geometry), key=shapely.area)
        centroid = shapely.centroid(main)
        source = "natural_earth" if admins == {entity_id} and entity_id in ne_geometry else "footprint"
        rows.append(
            (
                entity_id,
                shapely.to_wkb(geometry),
                source,
                list(shapely.bounds(geometry)),
                list(shapely.bounds(main)),
                [centroid.x, centroid.y],
            )
        )

    if missing:
        logger.warning("%d footprint admin unit(s) have no polygon and are left out of their geometry", missing)
    frame = pl.DataFrame(rows, schema=GEOMETRY_SCHEMA, orient="row").sort("entity_id")
    output_path = silver_dir / "8_entity_geometry.parquet"
    frame.write_parquet(output_path)
    logger.info("wrote %d rows to %s", frame.height, output_path)
    return output_path
