import json
import logging
import shutil
from collections import defaultdict
from pathlib import Path

import numpy as np
import polars as pl
import shapely
from jsonschema import ValidationError, validate
from shapely.errors import GEOSException
from shapely.geometry import mapping

REGION_MAP_SCHEMA_PATH = Path(__file__).parent / "schemas" / "region_map.schema.json"

WORLD_ID = "world"
ADMIN_KINDS = ("country", "admin_unit")
ZONE_KINDS = ("historical_country", "historical_region", "cultural_region", "macro_region")
UNFOOTPRINTED_KINDS = ("civilization", "curated")
SIZE_LIMIT_BYTES = 300_000
SIMPLIFY_TOLERANCES = (0.0, 0.01, 0.02, 0.05, 0.1, 0.2, 0.5, 1.0)
COORDINATE_DECIMALS = 2
UNPLACED_SCHEMA = {"wikidata_id": pl.String, "anchor_id": pl.String, "reason": pl.String}

logger = logging.getLogger(__name__)


def _canonical_descendants(hierarchy: pl.DataFrame) -> dict[str, set[str]]:
    children: dict[str, list[str]] = defaultdict(list)
    for item, parent in hierarchy.select("item_id", "parent_id").rows():
        if parent is not None:
            children[parent].append(item)
    descendants: dict[str, set[str]] = {}
    for root in children:
        seen: set[str] = set()
        pending = list(children[root])
        while pending:
            node = pending.pop()
            if node not in seen:
                seen.add(node)
                pending.extend(children.get(node, ()))
        descendants[root] = seen
    return descendants


def _simplified(geometries: list, tolerance: float) -> list:
    if tolerance == 0.0:
        return geometries
    try:
        simplified = list(shapely.coverage_simplify(np.array(geometries, dtype=object), tolerance))
    except GEOSException:
        logger.warning(
            "coverage_simplify rejected the inputs at tolerance %s, simplifying each geometry alone", tolerance
        )
        simplified = [shapely.simplify(geometry, tolerance, preserve_topology=True) for geometry in geometries]
    return simplified


def _without_specks(geometry, tolerance: float):
    parts = shapely.get_parts(geometry)
    kept = [part for part in parts if part.area >= tolerance**2]
    if len(kept) == len(parts):
        return geometry
    return shapely.union_all(kept or [max(parts, key=lambda part: part.area)])


def _rounded(geometry):
    rounded = shapely.transform(geometry, lambda coordinates: np.round(coordinates, COORDINATE_DECIMALS))
    polygons = [p for p in shapely.get_parts(shapely.make_valid(rounded)) if p.geom_type == "Polygon"]
    return shapely.union_all(polygons) if polygons else geometry


def _feature_collection(features: list[tuple[str, str, object]], name: str) -> bytes:
    geometries = [geometry for _, _, geometry in features]
    for tolerance in SIMPLIFY_TOLERANCES:
        rounded = [_rounded(_without_specks(geometry, tolerance)) for geometry in _simplified(geometries, tolerance)]
        degenerate = [
            features[i][0]
            for i, geometry in enumerate(rounded)
            if geometry.is_empty or geometry.geom_type not in ("Polygon", "MultiPolygon")
        ]
        if degenerate:
            raise ValueError(f"{name}: simplification emptied or degraded the geometry of {degenerate[:5]}")
        document = {
            "type": "FeatureCollection",
            "features": [
                {
                    "type": "Feature",
                    "id": feature_id,
                    "properties": {"id": feature_id, "name": label},
                    "geometry": mapping(g),
                }
                for (feature_id, label, _), g in zip(features, rounded, strict=True)
            ],
        }
        encoded = json.dumps(document, separators=(",", ":"), ensure_ascii=False).encode()
        if len(encoded) <= SIZE_LIMIT_BYTES:
            return encoded
    raise ValueError(f"{name} stays above {SIZE_LIMIT_BYTES} bytes at tolerance {SIMPLIFY_TOLERANCES[-1]}")


def export_region_map(
    geohistory_silver_dir: Path, wikidata_silver_dir: Path, genre_match_path: Path, output_dir: Path
) -> Path:
    entities = pl.read_parquet(
        geohistory_silver_dir / "1_cultural_entity.parquet", columns=["entity_id", "kind", "label"]
    )
    geometry = pl.read_parquet(geohistory_silver_dir / "8_entity_geometry.parquet")
    footprint = pl.read_parquet(geohistory_silver_dir / "8_entity_footprint.parquet")
    containment = pl.read_parquet(geohistory_silver_dir / "2_place_containment.parquet").sort("parent_id", "child_id")
    anchors = pl.read_parquet(geohistory_silver_dir / "5_music_anchor.parquet").sort("wikidata_id")
    canonical = pl.read_parquet(wikidata_silver_dir / "7_canonical_hierarchy.parquet")
    regional_ids = set(pl.read_parquet(wikidata_silver_dir / "8_regional_hierarchy.parquet")["item_id"])
    canonical_ids = set(canonical["item_id"])
    descendants = _canonical_descendants(canonical)

    kinds = dict(entities.select("entity_id", "kind").rows())
    labels = dict(entities.select("entity_id", "label").rows())
    shapes = {row["entity_id"]: row for row in geometry.to_dicts()}
    admin_ids = {
        entity_id
        for entity_id, kind in kinds.items()
        if entity_id in shapes
        and (kind == "admin_unit" or (kind == "country" and shapes[entity_id]["source"] == "natural_earth"))
    }
    zone_candidates = {
        entity_id
        for entity_id, kind in kinds.items()
        if entity_id in shapes and entity_id not in admin_ids and kind in ZONE_KINDS + ("country",)
    }

    footprint_admins: dict[str, list[str]] = defaultdict(list)
    for entity_id, admin_id in footprint.select("entity_id", "admin_id").rows():
        if admin_id in admin_ids:
            footprint_admins[entity_id].append(admin_id)

    genres_by_region: dict[str, list[dict]] = defaultdict(list)
    unplaced = []
    for item_id, anchor_id, rule in anchors.select("wikidata_id", "anchor_id", "rule").rows():
        relation = "canonical" if item_id in canonical_ids else "regional" if item_id in regional_ids else "tradition"
        if anchor_id in admin_ids or anchor_id in zone_candidates:
            genres_by_region[anchor_id].append({"itemId": item_id, "rule": rule, "relation": relation})
        elif kinds.get(anchor_id) in UNFOOTPRINTED_KINDS:
            unplaced.append((item_id, anchor_id, "anchor is not a place"))
        elif footprint_admins.get(anchor_id):
            for admin_id in footprint_admins[anchor_id]:
                genres_by_region[admin_id].append({"itemId": item_id, "rule": rule, "relation": "tradition"})
        else:
            reason = "anchor has no geometry" if anchor_id in shapes else "anchor has no footprint"
            unplaced.append((item_id, anchor_id, reason))

    song_counts = dict(
        pl.read_parquet(genre_match_path, columns=["wikidata_genre_item_id"])
        .drop_nulls()
        .group_by("wikidata_genre_item_id")
        .len()
        .rows()
    )

    parents: dict[str, str] = {}
    for parent, child in containment.select("parent_id", "child_id").rows():
        if parent in admin_ids and child in admin_ids:
            parents.setdefault(child, parent)

    included = set(genres_by_region)
    for region in list(included):
        node = region
        while node in parents and parents[node] not in included:
            node = parents[node]
            included.add(node)
    admin_regions = sorted(r for r in included if r in admin_ids)
    zones = sorted(r for r in included if r in zone_candidates)

    children: dict[str, list[str]] = defaultdict(list)
    for region in admin_regions:
        children[parents.get(region, WORLD_ID)].append(region)

    def admin_ancestors(admin_id: str) -> list[str]:
        chain = [admin_id]
        while chain[-1] in parents:
            chain.append(parents[chain[-1]])
        return chain

    zone_ids_by_region: dict[str, set[str]] = defaultdict(set)
    zone_set = set(zones)
    for entity_id, admin_id in footprint.select("entity_id", "admin_id").rows():
        if entity_id in zone_set and admin_id in shapes:
            for ancestor in admin_ancestors(admin_id):
                zone_ids_by_region[ancestor].add(entity_id)

    entries = []
    for region in [*admin_regions, *zones]:
        own = {g["itemId"] for g in genres_by_region.get(region, ())}
        relevant = own.union(*(descendants.get(item, set()) for item in own))
        shape = shapes[region]
        entries.append(
            {
                "id": region,
                "name": labels[region],
                "kind": "zone" if region in zone_set else kinds[region],
                "parentId": None if region in zone_set else parents.get(region, WORLD_ID),
                "childIds": children.get(region, []),
                "zoneIds": sorted(zone_ids_by_region.get(region, ())),
                "bbox": shape["bbox"],
                "mainBbox": shape["main_bbox"],
                "centroid": shape["centroid"],
                "drillable": bool(children.get(region)),
                "genres": genres_by_region.get(region, []),
                "songCount": sum(song_counts.get(item, 0) for item in relevant),
                "genreCount": len(relevant),
            }
        )
    document = {"regions": entries}

    if not entries:
        raise ValueError("region map has no region")
    try:
        validate(document, json.loads(REGION_MAP_SCHEMA_PATH.read_text()))
    except ValidationError as e:
        raise ValueError(f"region map failed schema validation ({REGION_MAP_SCHEMA_PATH}): {e.message}")

    def load(region: str):
        return shapely.from_wkb(shapes[region]["geometry_wkb"])

    geometry_dir = output_dir / "5_region_map"
    if geometry_dir.exists():
        shutil.rmtree(geometry_dir)
    (geometry_dir / "geometry").mkdir(parents=True)
    (geometry_dir / "zones").mkdir()

    countries = children.get(WORLD_ID, [])
    (geometry_dir / "geometry" / "world.geojson").write_bytes(
        _feature_collection([(r, labels[r], load(r)) for r in countries], "world")
    )
    for region in admin_regions:
        if children.get(region):
            (geometry_dir / "geometry" / f"{region}.geojson").write_bytes(
                _feature_collection([(r, labels[r], load(r)) for r in children[region]], region)
            )
    for zone in zones:
        (geometry_dir / "zones" / f"{zone}.geojson").write_bytes(
            _feature_collection([(zone, labels[zone], load(zone))], zone)
        )
    (geometry_dir / "index.json").write_text(json.dumps(document, indent=2, ensure_ascii=False))

    unplaced_frame = pl.DataFrame(unplaced, schema=UNPLACED_SCHEMA, orient="row").sort("wikidata_id")
    unplaced_frame.write_csv(output_dir / "5_region_map_unplaced.csv")
    (output_dir / "5_region_map_report.json").write_text(f'{{"unplacedGenreCount": {unplaced_frame.height}}}\n')
    if unplaced_frame.height:
        logger.warning("%d genre(s) have no region on the map, see 5_region_map_unplaced.csv", unplaced_frame.height)
    logger.info("wrote %d region(s) and %d zone(s) to %s", len(admin_regions), len(zones), geometry_dir)
    return geometry_dir / "index.json"
