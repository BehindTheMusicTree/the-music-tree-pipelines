import gzip
import hashlib
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
SIZE_WARNING_RATIO = 0.9
# The index is fetched whole on every map load, before any geometry.
INDEX_SIZE_LIMIT_BYTES = 2_000_000
FILES_PER_PART = 25
INDEX_PATH = "index.json"
NESTED_COUNTRY_MIN_OVERLAP = 0.9
MAINLAND_MIN_SHARE = 0.5
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


def _share_within(child: shapely.Geometry, parent: shapely.Geometry) -> float:
    child, parent = shapely.make_valid(child), shapely.make_valid(parent)
    return child.intersection(parent).area / child.area if child.area else 0.0


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


def _polygons(geometry: shapely.Geometry) -> list[shapely.Geometry]:
    if geometry.geom_type == "Polygon":
        return [geometry]
    if geometry.geom_type in ("MultiPolygon", "GeometryCollection"):
        return [polygon for part in shapely.get_parts(geometry) for polygon in _polygons(part)]
    return []


def _rounded(geometry: shapely.Geometry, region_id: str) -> shapely.Geometry:
    rounded = shapely.transform(geometry, lambda coordinates: np.round(coordinates, COORDINATE_DECIMALS))
    polygons = [polygon for part in shapely.get_parts(rounded) for polygon in _polygons(shapely.make_valid(part))]
    if polygons:
        return shapely.union_all(polygons)
    if not geometry.is_valid:
        return shapely.Polygon()
    logger.warning("rounding collapsed every part of %s, keeping its unrounded geometry", region_id)
    return geometry


def _canonical_bytes(document: dict) -> bytes:
    """The serialization grow hashes and serves, so a file's sha256 here is its URL there."""
    return json.dumps(document, sort_keys=True, separators=(",", ":"), ensure_ascii=False, allow_nan=False).encode()


def _check_size(label: str, size: int, limit: int) -> None:
    if size > limit:
        raise ValueError(f"{label} is {size} bytes, above the {limit} cap")
    if size > limit * SIZE_WARNING_RATIO:
        logger.warning("%s is %d bytes, above %d%% of the %d cap", label, size, SIZE_WARNING_RATIO * 100, limit)


def _feature_collection(features: list[tuple[str, str, object]], name: str) -> tuple[dict, bytes, float]:
    geometries = [geometry for _, _, geometry in features]
    for tolerance in SIMPLIFY_TOLERANCES:
        rounded = [
            _rounded(_without_specks(geometry, tolerance), feature_id)
            for (feature_id, _, _), geometry in zip(features, _simplified(geometries, tolerance), strict=True)
        ]
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
        encoded = _canonical_bytes(document)
        if len(encoded) <= SIZE_LIMIT_BYTES:
            return document, encoded, tolerance
    raise ValueError(f"{name} stays above {SIZE_LIMIT_BYTES} bytes at tolerance {SIMPLIFY_TOLERANCES[-1]}")


def _write_parts(lines: list[dict], index_line: dict, parts_dir: Path) -> int:
    """grow's import-run parts, one file per line as a JSON object (not a string) so grow can re-hash it. The index,
    up to `INDEX_SIZE_LIMIT_BYTES`, gets a part of its own so no part outgrows `FILES_PER_PART` geometry files."""
    parts_dir.mkdir()
    chunks = [lines[start : start + FILES_PER_PART] for start in range(0, len(lines), FILES_PER_PART)] + [[index_line]]
    for index, chunk in enumerate(chunks):
        with gzip.open(parts_dir / f"part-{index:05d}.ndjson.gz", "wt", encoding="utf-8") as part:
            part.writelines(
                json.dumps(line, separators=(",", ":"), ensure_ascii=False, allow_nan=False) + "\n" for line in chunk
            )
    return len(chunks)


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
    regional = pl.read_parquet(wikidata_silver_dir / "8_regional_hierarchy.parquet")
    regional_ids = set(regional["item_id"])
    regional_parent = dict(regional.select("item_id", "parent_id").rows())
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
    placed: dict[str, list[str]] = defaultdict(list)
    unplaced = []
    for item_id, anchor_id, rule in anchors.select("wikidata_id", "anchor_id", "rule").rows():
        relation = "canonical" if item_id in canonical_ids else "regional" if item_id in regional_ids else "tradition"
        if anchor_id in admin_ids or anchor_id in zone_candidates:
            genres_by_region[anchor_id].append({"itemId": item_id, "rule": rule, "relation": relation})
            placed[item_id].append(anchor_id)
        elif kinds.get(anchor_id) in UNFOOTPRINTED_KINDS:
            unplaced.append((item_id, anchor_id, "anchor is not a place"))
        elif footprint_admins.get(anchor_id):
            for admin_id in dict.fromkeys(footprint_admins[anchor_id]):
                if any(g["itemId"] == item_id for g in genres_by_region[admin_id]):
                    continue
                genres_by_region[admin_id].append({"itemId": item_id, "rule": rule, "relation": "tradition"})
                placed[item_id].append(admin_id)
        else:
            reason = "anchor has no geometry" if anchor_id in shapes else "anchor has no footprint"
            unplaced.append((item_id, anchor_id, reason))

    inherited = set()
    for item_id in sorted((canonical_ids | regional_ids) - set(placed)):
        ancestor, seen = regional_parent.get(item_id), {item_id}
        while ancestor is not None and ancestor not in placed and ancestor not in seen:
            seen.add(ancestor)
            ancestor = regional_parent.get(ancestor)
        if ancestor in placed:
            relation = "canonical" if item_id in canonical_ids else "regional"
            for region in placed[ancestor]:
                genres_by_region[region].append({"itemId": item_id, "rule": "regional_parent", "relation": relation})
            inherited.add(item_id)
    unplaced = [row for row in unplaced if row[0] not in inherited]

    song_counts = dict(
        pl.read_parquet(genre_match_path, columns=["wikidata_genre_item_id"])
        .drop_nulls()
        .group_by("wikidata_genre_item_id")
        .len()
        .rows()
    )

    containment_parents: dict[str, list[str]] = defaultdict(list)
    for parent, child in containment.select("parent_id", "child_id").rows():
        containment_parents[child].append(parent)

    def nearest_admin_parent(child: str) -> str | None:
        seen = {child}
        level = list(dict.fromkeys(containment_parents.get(child, ())))
        while level:
            admins = [node for node in level if node in admin_ids]
            if admins:
                return admins[0]
            seen.update(level)
            level = list(dict.fromkeys(g for node in level for g in containment_parents.get(node, ()) if g not in seen))
        return None

    def load(region: str):
        return shapely.from_wkb(shapes[region]["geometry_wkb"])

    parents: dict[str, str] = {}
    for child in sorted(admin_ids):
        parent = nearest_admin_parent(child)
        if parent is None:
            continue
        if kinds[child] == "country" and _share_within(load(child), load(parent)) < NESTED_COUNTRY_MIN_OVERLAP:
            continue
        parents[child] = parent

    areas: dict[str, float] = {}

    def area(region: str) -> float:
        if region not in areas:
            areas[region] = load(region).area
        return areas[region]

    def depth(region: str) -> int:
        level = 0
        while region in parents:
            region = parents[region]
            level += 1
        return level

    def dissolvable(child: str, parent: str) -> bool:
        return kinds[child] == "admin_unit" or kinds[parent] == "country"

    def next_dissolution() -> tuple[str, str] | None:
        siblings: dict[str, list[str]] = defaultdict(list)
        for child, parent in parents.items():
            siblings[parent].append(child)
        candidates = (p for p in siblings if len(siblings[p]) >= 2 and p not in dissolved.values())
        for parent in sorted(candidates, key=lambda p: (-depth(p), p)):
            claimants = [
                c for c in siblings[parent] if area(c) >= MAINLAND_MIN_SHARE * area(parent) and dissolvable(c, parent)
            ]
            if claimants:
                return min(claimants, key=lambda c: (-area(c), c)), parent
        return None

    dissolved: dict[str, str] = {}
    while (dissolution := next_dissolution()) is not None:
        mainland, parent = dissolution
        for grandchild in [g for g, p in parents.items() if p == mainland]:
            parents[grandchild] = parent
        del parents[mainland]
        dissolved[mainland] = parent
        owned = genres_by_region.pop(mainland, [])
        known = {g["itemId"] for g in genres_by_region.get(parent, ())}
        genres_by_region[parent].extend(g for g in owned if g["itemId"] not in known)
        admin_ids.discard(mainland)
    if dissolved:
        logger.info(
            "dissolved %d mainland region(s) into their parent: %s",
            len(dissolved),
            ", ".join(f"{labels[c]} ({c}) -> {p}" for c, p in dissolved.items()),
        )
    admin_children: dict[str, list[str]] = defaultdict(list)
    for child, parent in parents.items():
        admin_children[parent].append(child)

    included = {region for region, genres in genres_by_region.items() if genres}
    for region in list(included):
        node = region
        while node in parents and parents[node] not in included:
            node = parents[node]
            included.add(node)
    for region in list(included):
        included.update(admin_children.get(region, ()))
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

    def own_items(region: str) -> set[str]:
        return {g["itemId"] for g in genres_by_region.get(region, ())}

    def rolled_up_items(region: str) -> set[str]:
        items, pending = set(), [region]
        while pending:
            node = pending.pop()
            items |= own_items(node)
            pending.extend(admin_children.get(node, ()))
        return items

    zone_ids_by_region: dict[str, set[str]] = defaultdict(set)
    zone_set = set(zones)
    for entity_id, admin_id in footprint.select("entity_id", "admin_id").rows():
        admin_id = dissolved.get(admin_id, admin_id)
        if entity_id in zone_set and admin_id in shapes:
            for ancestor in admin_ancestors(admin_id):
                zone_ids_by_region[ancestor].add(entity_id)

    entries = []
    for region in [*admin_regions, *zones]:
        own = own_items(region) if region in zone_set else rolled_up_items(region)
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
    document: dict = {"regions": entries}
    if not entries:
        raise ValueError("region map has no region")

    geometry_dir = output_dir / "5_region_map"
    if geometry_dir.exists():
        shutil.rmtree(geometry_dir)
    (geometry_dir / "geometry").mkdir(parents=True)
    (geometry_dir / "zones").mkdir()

    files: list[tuple[str, int, float]] = []
    lines: list[dict] = []

    def write_collection(path: Path, features: list[tuple[str, str, object]], name: str) -> None:
        collection, encoded, tolerance = _feature_collection(features, name)
        path.write_bytes(encoded)
        relative = str(path.relative_to(geometry_dir))
        files.append((relative, len(encoded), tolerance))
        lines.append({"path": relative, "sha256": hashlib.sha256(encoded).hexdigest(), "document": collection})
        _check_size(path.name, len(encoded), SIZE_LIMIT_BYTES)

    write_collection(
        geometry_dir / "geometry" / "world.geojson",
        [(r, labels[r], load(r)) for r in children.get(WORLD_ID, [])],
        "world",
    )
    for region in admin_regions:
        if children.get(region):
            write_collection(
                geometry_dir / "geometry" / f"{region}.geojson",
                [(r, labels[r], load(r)) for r in children[region]],
                region,
            )
    for zone in zones:
        write_collection(geometry_dir / "zones" / f"{zone}.geojson", [(zone, labels[zone], load(zone))], zone)

    document["files"] = {line["path"]: line["sha256"] for line in lines}
    try:
        validate(document, json.loads(REGION_MAP_SCHEMA_PATH.read_text()))
    except ValidationError as e:
        raise ValueError(
            f"region map failed schema validation ({REGION_MAP_SCHEMA_PATH}) at {e.json_path}: {e.message}"
        ) from e
    encoded_index = _canonical_bytes(document)
    _check_size(INDEX_PATH, len(encoded_index), INDEX_SIZE_LIMIT_BYTES)
    (geometry_dir / INDEX_PATH).write_bytes(encoded_index)
    index_line = {"path": INDEX_PATH, "sha256": hashlib.sha256(encoded_index).hexdigest(), "document": document}
    part_count = _write_parts(lines, index_line, geometry_dir / "parts")

    unplaced_frame = pl.DataFrame(unplaced, schema=UNPLACED_SCHEMA, orient="row").sort("wikidata_id")
    unplaced_frame.write_csv(output_dir / "5_region_map_unplaced.csv")
    report = {
        "unplacedGenreCount": unplaced_frame.height,
        "files": [{"path": p, "bytes": b, "tolerance": t} for p, b, t in sorted(files)],
    }
    (output_dir / "5_region_map_report.json").write_text(json.dumps(report, indent=2) + "\n")
    if unplaced_frame.height:
        logger.warning("%d genre(s) have no region on the map, see 5_region_map_unplaced.csv", unplaced_frame.height)
    logger.info(
        "wrote %d region(s) and %d zone(s) to %s, in %d import part(s)",
        len(admin_regions),
        len(zones),
        geometry_dir,
        part_count,
    )
    return geometry_dir / INDEX_PATH
