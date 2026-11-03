import gzip
import hashlib
import json
import logging
import shutil
import subprocess
import time
from collections import defaultdict
from pathlib import Path

import polars as pl
import shapely
from jsonschema import ValidationError, validate
from shapely.geometry import mapping

REGION_MAP_SCHEMA_PATH = Path(__file__).parent / "schemas" / "region_map.schema.json"

WORLD_ID = "world"
ADMIN_KINDS = ("country", "admin_unit")
ZONE_KINDS = ("historical_country", "historical_region", "cultural_region", "macro_region")
UNFOOTPRINTED_KINDS = ("civilization", "curated")
SIZE_WARNING_RATIO = 0.9
# The index is fetched whole on every map load, before any geometry.
INDEX_SIZE_LIMIT_BYTES = 2_000_000
INDEX_PATH = "index.json"
NESTED_COUNTRY_MIN_OVERLAP = 0.9
MAINLAND_MIN_SHARE = 0.5
TILESET_MIN_ZOOM = 0
TILESET_MAX_ZOOM = 10
TILESET_DIR = "regions"
BASEMAP_LAYER_NAMES = ("land", "lakes", "rivers", "disputed", "places")
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


def _canonical_bytes(document: dict) -> bytes:
    """The serialization grow hashes and serves, so a file's sha256 here is its URL there."""
    return json.dumps(document, sort_keys=True, separators=(",", ":"), ensure_ascii=False, allow_nan=False).encode()


def _check_size(label: str, size: int, limit: int) -> None:
    if size > limit:
        raise ValueError(f"{label} is {size} bytes, above the {limit} cap")
    if size > limit * SIZE_WARNING_RATIO:
        logger.warning("%s is %d bytes, above %d%% of the %d cap", label, size, SIZE_WARNING_RATIO * 100, limit)


def _write_geojsonl(path: Path, features: list[tuple[dict, shapely.Geometry]], by_min_zoom: bool = False) -> None:
    """With `by_min_zoom`, a feature's `min_zoom` property also becomes its tippecanoe `minzoom`, so the low-zoom tiles
    do not carry thousands of features the style hides anyway."""
    with path.open("w", encoding="utf-8") as handle:
        for properties, geometry in features:
            feature = {"type": "Feature", "properties": properties, "geometry": mapping(geometry)}
            if by_min_zoom:
                feature["tippecanoe"] = {"minzoom": min(int(properties["min_zoom"]), TILESET_MAX_ZOOM)}
            handle.write(json.dumps(feature, separators=(",", ":"), ensure_ascii=False, allow_nan=False) + "\n")


def _run_tippecanoe(arguments: list[str]) -> None:
    result = subprocess.run(["tippecanoe", *arguments], capture_output=True, text=True, check=False)
    if result.returncode != 0:
        raise RuntimeError(f"tippecanoe failed with exit code {result.returncode}: {result.stderr[-2000:]}")
    # tippecanoe reports any feature it had to drop; none is acceptable, the map would silently lose a region
    dropped = [line for line in result.stderr.splitlines() if "drop" in line.lower()]
    if dropped:
        raise RuntimeError(f"tippecanoe dropped features: {' | '.join(dropped[:5])}")


def _basemap_layers(basemap: pl.DataFrame) -> dict[str, list[tuple[dict, shapely.Geometry]]]:
    layers: dict[str, list[tuple[dict, shapely.Geometry]]] = {}
    for layer, name, featurecla, min_zoom, wkb in basemap.select(
        "layer", "name", "featurecla", "min_zoom", "geometry_wkb"
    ).rows():
        properties = {"min_zoom": min_zoom}
        if layer == "land":
            properties = {}
        elif layer == "disputed":
            properties["featurecla"] = featurecla
        if name is not None:
            properties["name"] = name
        layers.setdefault(layer, []).append((properties, shapely.from_wkb(wkb)))
    missing = set(BASEMAP_LAYER_NAMES) - set(layers)
    if missing:
        raise ValueError(f"the basemap has no feature for layer(s) {sorted(missing)}")
    return layers


def _region_tileset(
    regions: list[tuple[str, str, str, str, shapely.Geometry]],
    zones: list[tuple[str, str, shapely.Geometry]],
    labels: list[tuple[str, str, str, tuple[float, float]]],
    basemap: pl.DataFrame,
    work_dir: Path,
    geometry_dir: Path,
) -> tuple[str, int]:
    """The one vector tileset of the map, from full-resolution geometry. Layers `regions` (`id`, `parentId`, `kind`,
    `name`, same as the index), `region_labels` (`id`, `parentId`, `name`, at the pole of inaccessibility), `zones`
    (`id`, `name`), and Natural Earth's `land`, `lakes`, `rivers`, `disputed` and `places`. Returns the index's
    sha256 of the file, `regions/<sha256>.pmtiles`, and its size in bytes."""
    if shutil.which("tippecanoe") is None:
        raise RuntimeError("tippecanoe is not installed, see pipelines/gold/README.md")
    work_dir.mkdir(parents=True, exist_ok=True)
    built = work_dir / "built.pmtiles"
    layers: dict[str, tuple[list[tuple[dict, shapely.Geometry]], bool]] = {
        "regions": (
            [({"id": r, "parentId": p, "kind": k, "name": n}, shape) for r, n, p, k, shape in regions],
            False,
        ),
        "region_labels": (
            [({"id": r, "parentId": p, "name": n}, shapely.Point(point)) for r, p, n, point in labels],
            False,
        ),
        "zones": ([({"id": z, "name": n}, shape) for z, n, shape in zones], False),
        **{layer: (features, layer != "land") for layer, features in _basemap_layers(basemap).items()},
    }
    arguments = [
        "-o", str(built), "--force",
        f"-Z{TILESET_MIN_ZOOM}", f"-z{TILESET_MAX_ZOOM}",
        "--detect-shared-borders", "--no-progress-indicator",
    ]  # fmt: skip
    for layer, (features, by_min_zoom) in layers.items():
        path = work_dir / f"{layer}.geojsonl"
        _write_geojsonl(path, features, by_min_zoom)
        arguments += ["-L", f"{layer}:{path}"]
    _run_tippecanoe(arguments)
    if not built.exists() or built.stat().st_size == 0:
        raise RuntimeError("tippecanoe wrote no tileset")
    content = built.read_bytes()
    sha256 = hashlib.sha256(content).hexdigest()
    (geometry_dir / TILESET_DIR).mkdir(parents=True, exist_ok=True)
    built.replace(geometry_dir / TILESET_DIR / f"{sha256}.pmtiles")
    return sha256, len(content)


def _write_part(index_line: dict, parts_dir: Path) -> None:
    """grow's import-run part: the index alone, as a JSON object (not a string) so grow can re-hash it. The geometry is
    the tileset, published to object storage by the runner, so no other file is uploaded."""
    parts_dir.mkdir()
    with gzip.open(parts_dir / "part-00000.ndjson.gz", "wt", encoding="utf-8") as part:
        part.write(json.dumps(index_line, separators=(",", ":"), ensure_ascii=False, allow_nan=False) + "\n")


def export_region_map(
    geohistory_silver_dir: Path, wikidata_silver_dir: Path, genre_match_path: Path, output_dir: Path
) -> Path:
    entities = pl.read_parquet(
        geohistory_silver_dir / "1_cultural_entity.parquet", columns=["entity_id", "kind", "label"]
    )
    geometry = pl.read_parquet(geohistory_silver_dir / "8_entity_geometry.parquet")
    basemap = pl.read_parquet(geohistory_silver_dir / "8_basemap_feature.parquet")
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
    geometry_dir.mkdir(parents=True)
    started = time.monotonic()
    document["tileset"], tileset_bytes = _region_tileset(
        [(r, labels[r], parents.get(r, WORLD_ID), kinds[r], load(r)) for r in admin_regions],
        [(z, labels[z], load(z)) for z in zones],
        [
            (entry["id"], entry["parentId"], entry["name"], tuple(entry["centroid"]))
            for entry in entries
            if entry["kind"] != "zone"
        ],
        basemap,
        output_dir / "5_region_map_tileset_work",
        geometry_dir,
    )
    tileset_seconds = round(time.monotonic() - started, 1)
    shutil.rmtree(output_dir / "5_region_map_tileset_work")
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
    _write_part(index_line, geometry_dir / "parts")

    unplaced_frame = pl.DataFrame(unplaced, schema=UNPLACED_SCHEMA, orient="row").sort("wikidata_id")
    unplaced_frame.write_csv(output_dir / "5_region_map_unplaced.csv")
    report = {
        "unplacedGenreCount": unplaced_frame.height,
        "tileset": {"bytes": tileset_bytes, "seconds": tileset_seconds},
    }
    (output_dir / "5_region_map_report.json").write_text(json.dumps(report, indent=2) + "\n")
    if unplaced_frame.height:
        logger.warning("%d genre(s) have no region on the map, see 5_region_map_unplaced.csv", unplaced_frame.height)
    logger.info(
        "wrote %d region(s) and %d zone(s) to %s",
        len(admin_regions),
        len(zones),
        geometry_dir,
    )
    return geometry_dir / INDEX_PATH
