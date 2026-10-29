import logging
from collections import defaultdict
from pathlib import Path

import polars as pl
import shapely

from geohistory.silver.music_anchor import PLACE_KINDS

logger = logging.getLogger(__name__)

FOOTPRINT_SCHEMA = {"entity_id": pl.String, "admin_id": pl.String, "rule": pl.String}


def _point_footprints(bronze_dir: Path, ne_map: pl.DataFrame, points: pl.DataFrame) -> list[tuple[str, str]]:
    """(entity_id, admin_id) for each point falling inside an admin-1 polygon mapped to an entity."""
    polygons = (
        pl.read_parquet(bronze_dir / "natural_earth_admin.parquet", columns=["ne_id", "level", "geometry_wkb"])
        .filter(pl.col("level") == 1)
        .join(ne_map.select("ne_id", "entity_id").drop_nulls(), on="ne_id")
        .sort("ne_id")
    )
    tree = shapely.STRtree(shapely.make_valid(shapely.from_wkb(polygons["geometry_wkb"].to_list())))
    admin_of = polygons["entity_id"].to_list()
    point_ids = points["entity_id"].to_list()
    hits_point, hits_polygon = tree.query(
        shapely.points(points["longitude"].to_numpy(), points["latitude"].to_numpy()), predicate="within"
    )
    found: dict[str, str] = {}
    for point, polygon in sorted(zip(hits_point.tolist(), hits_polygon.tolist(), strict=True)):
        found.setdefault(point_ids[point], admin_of[polygon])
    return sorted(found.items())


def entity_footprint(bronze_dir: Path, silver_dir: Path) -> Path:
    entities = pl.read_parquet(silver_dir / "1_cultural_entity.parquet")
    ne_map = pl.read_parquet(silver_dir / "1_entity_ne_admin.parquet")
    containment = pl.read_parquet(silver_dir / "2_place_containment.parquet").sort("parent_id", "child_id")
    succession = pl.read_parquet(silver_dir / "3_entity_succession.parquet").sort("predecessor_id", "successor_id")

    place_ids = set(entities.filter(pl.col("kind").is_in(PLACE_KINDS))["entity_id"])
    admin_ids = set(entities.filter(pl.col("kind") == "admin_unit")["entity_id"])
    ne_ids = set(ne_map["entity_id"].drop_nulls())
    nodes = ne_ids | admin_ids
    children: dict[str, list[str]] = defaultdict(list)
    for parent, child in containment.select("parent_id", "child_id").rows():
        children[parent].append(child)

    cache: dict[str, tuple[frozenset[str], bool]] = {}

    def cover(node: str) -> tuple[frozenset[str], bool]:
        """Admin ids tiling the node; complete when the node itself can stand for them."""
        if node not in cache:
            if node in ne_ids:
                cache[node] = (frozenset({node}), True)
            else:
                parts = [part for part in map(cover, children.get(node, ())) if part[0]]
                union = frozenset().union(*(ids for ids, _ in parts))
                complete = node in admin_ids and bool(parts) and all(done for _, done in parts)
                cache[node] = (frozenset({node}), True) if complete else (union, False)
        return cache[node]

    own: dict[str, tuple[frozenset[str], str]] = {}
    for entity_id in sorted(place_ids):
        if entity_id in nodes and (entity_id in ne_ids or cover(entity_id)[1]):
            own[entity_id] = (frozenset({entity_id}), "self")
            continue
        tiles = frozenset().union(*(cover(child)[0] for child in children.get(entity_id, ())))
        if tiles:
            own[entity_id] = (tiles, "cover")

    neighbours: dict[str, list[str]] = defaultdict(list)
    for predecessor, successor in succession.select("predecessor_id", "successor_id").rows():
        neighbours[predecessor].append(successor)
    reverse: dict[str, list[str]] = defaultdict(list)
    for predecessor, successor in succession.select("predecessor_id", "successor_id").rows():
        reverse[successor].append(predecessor)

    rows = [(entity_id, admin, rule) for entity_id, (ids, rule) in own.items() for admin in sorted(ids)]
    for entity_id in sorted(place_ids - set(own)):
        for related in (neighbours, reverse):
            ids = frozenset().union(*(own[other][0] for other in related.get(entity_id, ()) if other in own))
            if ids:
                rows.extend((entity_id, admin, "succession") for admin in sorted(ids))
                break

    covered = {entity_id for entity_id, _, _ in rows}
    points = entities.filter(
        pl.col("latitude").is_not_null() & pl.col("longitude").is_not_null() & ~pl.col("entity_id").is_in(covered)
    ).sort("entity_id")
    rows.extend((entity_id, admin, "point") for entity_id, admin in _point_footprints(bronze_dir, ne_map, points))

    frame = pl.DataFrame(rows, schema=FOOTPRINT_SCHEMA, orient="row").sort("entity_id", "admin_id")
    unplaced = len(place_ids - set(frame["entity_id"]))
    if unplaced:
        logger.info("%d place entit(ies) have no admin footprint", unplaced)
    output_path = silver_dir / "8_entity_footprint.parquet"
    frame.write_parquet(output_path)
    logger.info("wrote %d rows to %s", frame.height, output_path)
    return output_path
