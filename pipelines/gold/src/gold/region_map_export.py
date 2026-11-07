import gzip
import hashlib
import json
import logging
import math
import shutil
import subprocess
import time
from collections import defaultdict
from collections.abc import Callable
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
CITY_KIND = "city"
CITY_PARENT_RULE = "city_parent"
CITY_TILESET_MIN_ZOOM = 5
SIZE_WARNING_RATIO = 0.9
# The index is fetched whole on every map load, before any geometry.
INDEX_SIZE_LIMIT_BYTES = 2_000_000
INDEX_PATH = "index.json"
NESTED_COUNTRY_MIN_OVERLAP = 0.9
MAINLAND_MIN_SHARE = 0.5
# Australia, the smallest continent, is ~7.7M km2; Greenland, the largest island, ~2.1M km2
CONTINENT_MIN_AREA_KM2 = 5_000_000
KM_PER_DEGREE = 111.32
TILESET_MIN_ZOOM = 0
TILESET_MAX_ZOOM = 10
# In tile units: a 4096-unit tile draws on 512 px, so 8 drops detail under a pixel. Unsimplified 1:10m coasts
# otherwise keep thousands of sub-pixel zigzags at low zoom, which a stroke turns into a hairy outline.
TILESET_SIMPLIFICATION = 8
# Zoom bands, Google Maps style: a region splits into its subdivisions once its main part spans SPLIT_PX on screen.
# Kept under a phone's width so framing a region on any screen lands past its split and shows its subdivisions.
TILE_PX = 512
SPLIT_PX = 320
MIN_BAND = 1.0
# A parent's label outlives its fill a little, so the country name stays while its subdivisions appear.
LABEL_OVERLAP = 1.5
ZONE_MIN_PX = 120
ZONE_MAX_PX = 2000
MERCATOR_MAX_LATITUDE = 85.0511
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


def _mercator_extent(bbox: list[float]) -> float:
    """The larger side of a lon/lat box in Web Mercator world units, where the whole world spans 1."""
    west, south, east, north = bbox

    def y(latitude: float) -> float:
        latitude = math.radians(max(-MERCATOR_MAX_LATITUDE, min(MERCATOR_MAX_LATITUDE, latitude)))
        return (1 - math.log(math.tan(latitude) + 1 / math.cos(latitude)) / math.pi) / 2

    width = (east - west if east >= west else east + 360 - west) / 360
    return max(width, abs(y(south) - y(north)))


def _zoom_spanning(bbox: list[float], px: float) -> float:
    """The zoom from which the box spans `px` screen pixels."""
    extent = _mercator_extent(bbox)
    return TILESET_MAX_ZOOM if extent <= 0 else math.log2(px / (TILE_PX * extent))


Band = tuple[float, float | None]


def _admin_bands(
    children: dict[str, list[str]], main_bboxes: dict[str, list[float]]
) -> tuple[dict[str, Band], dict[str, int]]:
    """Each admin region's zoom band `[min_zoom, max_zoom)` and depth below the world. A region shows from the zoom
    its parent splits at, and splits itself, giving way to its subdivisions, once it spans SPLIT_PX; a region
    without subdivisions on the map never splits (`max_zoom` None).

    Splits are not capped at TILESET_MAX_ZOOM: the map overzooms the last tiles, and a region splitting past the
    map's own max zoom is one too small to subdivide on screen."""
    bands: dict[str, Band] = {}
    depths: dict[str, int] = {}
    pending = [(region, 0.0, 0) for region in children.get(WORLD_ID, [])]
    while pending:
        region, min_zoom, depth = pending.pop()
        if region in bands:
            raise ValueError(f"region {region} is reached twice in the admin tree")
        max_zoom = None
        if children.get(region):
            max_zoom = max(_zoom_spanning(main_bboxes[region], SPLIT_PX), min_zoom + MIN_BAND)
            pending.extend((child, max_zoom, depth + 1) for child in children[region])
        bands[region] = (min_zoom, max_zoom)
        depths[region] = depth
    return bands, depths


def _zone_band(main_bbox: list[float]) -> Band:
    """A zone shows while its main part spans between ZONE_MIN_PX and ZONE_MAX_PX: smaller it is clutter, larger it
    is no longer a place one looks at. A zone too small to span ZONE_MIN_PX still shows from the last tileset zoom,
    as its label is the only way to select it."""
    min_zoom = min(max(_zoom_spanning(main_bbox, ZONE_MIN_PX), 0.0), float(TILESET_MAX_ZOOM))
    return min_zoom, max(_zoom_spanning(main_bbox, ZONE_MAX_PX), min_zoom + MIN_BAND)


def _band_properties(band: Band) -> dict:
    """An open band leaves `max_zoom` out, so the style tests it with `has`."""
    min_zoom, max_zoom = band
    return {"min_zoom": min_zoom} if max_zoom is None else {"min_zoom": min_zoom, "max_zoom": max_zoom}


def _region_tileset(
    regions: list[tuple[str, str, str, str, int, Band, shapely.Geometry]],
    zones: list[tuple[str, str, Band, shapely.Geometry, tuple[float, float]]],
    labels: list[tuple[str, str, str, int, Band, tuple[float, float]]],
    cities: list[tuple[str, str, str, float, tuple[float, float]]],
    basemap: pl.DataFrame,
    work_dir: Path,
    geometry_dir: Path,
) -> tuple[str, int]:
    """The one vector tileset of the map, from full-resolution geometry. Layers `regions` (`id`, `parentId`, `kind`,
    `name`, same as the index, plus `depth` and the zoom band `min_zoom`/`max_zoom`), `region_labels` (`id`,
    `parentId`, `name`, `depth`, band, at the pole of inaccessibility), `zones` (`id`, `name`, band), `zone_labels`
    (same, at the pole of inaccessibility of the zone's main part), `cities` (`id`, `parentId`, `name`, `min_zoom`,
    one point per city region) and Natural Earth's `land`, `lakes`, `rivers`, `disputed` and `places`. Every feature
    with a `min_zoom` is left out of the tiles below it. Returns the index's sha256 of the file,
    `regions/<sha256>.pmtiles`, and its size in bytes."""
    if shutil.which("tippecanoe") is None:
        raise RuntimeError("tippecanoe is not installed, see pipelines/gold/README.md")
    work_dir.mkdir(parents=True, exist_ok=True)
    built = work_dir / "built.pmtiles"
    layers: dict[str, tuple[list[tuple[dict, shapely.Geometry]], bool]] = {
        "regions": (
            [
                ({"id": r, "parentId": p, "kind": k, "name": n, "depth": d, **_band_properties(b)}, shape)
                for r, n, p, k, d, b, shape in regions
            ],
            True,
        ),
        "region_labels": (
            [
                ({"id": r, "parentId": p, "name": n, "depth": d, **_band_properties(b)}, shapely.Point(point))
                for r, p, n, d, b, point in labels
            ],
            True,
        ),
        "zones": ([({"id": z, "name": n, **_band_properties(b)}, shape) for z, n, b, shape, _ in zones], True),
        "zone_labels": (
            [({"id": z, "name": n, **_band_properties(b)}, shapely.Point(point)) for z, n, b, _, point in zones],
            True,
        ),
        **{layer: (features, layer != "land") for layer, features in _basemap_layers(basemap).items()},
    }
    if cities:
        layers["cities"] = (
            [
                ({"id": c, "parentId": p, "name": n, "min_zoom": z}, shapely.Point(point))
                for c, p, n, z, point in cities
            ],
            True,
        )
    arguments = [
        "-o", str(built), "--force",
        f"-Z{TILESET_MIN_ZOOM}", f"-z{TILESET_MAX_ZOOM}",
        "--detect-shared-borders", f"--simplification={TILESET_SIMPLIFICATION}", "--visvalingam",
        "--simplify-only-low-zooms", "--no-progress-indicator",
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


def _approximate_area_km2(shape: shapely.Geometry) -> float:
    return shape.area * KM_PER_DEGREE**2 * math.cos(math.radians(shape.centroid.y))


def _islands(
    parents: dict[str, str], load: Callable[[str], shapely.Geometry], land: list[shapely.Geometry]
) -> set[str]:
    """The subdivisions on an island: off any continent and off their parent's main land mass (Corsica, Hokkaido,
    Northern Ireland; not Alaska or French Guiana, which sit on a continent). A subdivision on no mapped land at all
    is too small for the land layer, so it is an island too."""
    continents = {i for i, mass in enumerate(land) if _approximate_area_km2(mass) >= CONTINENT_MIN_AREA_KM2}
    tree = shapely.STRtree(land)

    def mass_of(shape: shapely.Geometry) -> int | None:
        touched = tree.query(shape, predicate="intersects")
        if len(touched) <= 1:
            return int(touched[0]) if len(touched) else None
        return int(max(touched, key=lambda i: land[i].intersection(shape).area))

    main_mass = {parent: mass_of(load(parent)) for parent in set(parents.values())}
    islands = set()
    for child, parent in parents.items():
        mass = mass_of(load(child))
        if mass is None or (mass not in continents and mass != main_mass[parent]):
            islands.add(child)
    return islands


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
        geohistory_silver_dir / "1_cultural_entity.parquet",
        columns=["entity_id", "kind", "label", "latitude", "longitude", "federated_state"],
    )
    geometry = pl.read_parquet(geohistory_silver_dir / "8_entity_geometry.parquet")
    basemap = pl.read_parquet(geohistory_silver_dir / "8_basemap_feature.parquet")
    land = [
        mass
        for wkb in basemap.filter(pl.col("layer") == "land")["geometry_wkb"]
        for mass in shapely.get_parts(shapely.from_wkb(wkb))
    ]
    if not land:
        raise ValueError("basemap has no land layer: islands cannot be told apart")
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
    federated_states = set(entities.filter(pl.col("federated_state"))["entity_id"])
    labels = dict(entities.select("entity_id", "label").rows())
    city_points = {
        entity_id: (longitude, latitude)
        for entity_id, kind, latitude, longitude in entities.select("entity_id", "kind", "latitude", "longitude").rows()
        if kind == CITY_KIND and latitude is not None and longitude is not None
    }
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
    genres_by_city: dict[str, list[dict]] = defaultdict(list)
    overview_candidates: dict[str, list[tuple[int, str]]] = defaultdict(list)
    placed: dict[str, list[str]] = defaultdict(list)
    unplaced = []
    for item_id, anchor_id, rule, overview_rank in anchors.select(
        "wikidata_id", "anchor_id", "rule", "overview_rank"
    ).rows():
        relation = "canonical" if item_id in canonical_ids else "regional" if item_id in regional_ids else "tradition"
        if anchor_id in city_points and footprint_admins.get(anchor_id):
            # a city genre is listed on the city and on the admin unit holding it, so the admin chain rolls it up
            admin_id = footprint_admins[anchor_id][0]
            genres_by_city[anchor_id].append({"itemId": item_id, "rule": rule, "relation": relation})
            if not any(g["itemId"] == item_id for g in genres_by_region[admin_id]):
                genres_by_region[admin_id].append({"itemId": item_id, "rule": CITY_PARENT_RULE, "relation": relation})
            placed[item_id].append(admin_id)
            if overview_rank is not None and relation != "tradition":
                overview_candidates[anchor_id].append((overview_rank, item_id))
        elif anchor_id in admin_ids or anchor_id in zone_candidates:
            genres_by_region[anchor_id] = [g for g in genres_by_region[anchor_id] if g["itemId"] != item_id]
            genres_by_region[anchor_id].append({"itemId": item_id, "rule": rule, "relation": relation})
            placed[item_id].append(anchor_id)
            if overview_rank is not None and relation != "tradition":
                overview_candidates[anchor_id].append((overview_rank, item_id))
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

    overview_by_region = {region: min(candidates)[1] for region, candidates in overview_candidates.items()}
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
        if mainland in overview_by_region:
            overview_by_region.setdefault(parent, overview_by_region.pop(mainland))
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
    # every country and every territory no country holds (Jersey, Antarctica) is drawn, genre-less or not, so no land
    # goes missing at world level; a genre-less subdivision is not, its country already covers its ground
    included.update(region for region in admin_ids if kinds[region] == "country" or region not in parents)
    # a state of a federation is drawn even without a genre, like a country; every other genre-less subdivision stays
    # out of the map (hidden) until a placement override gives it one
    included.update(region for region in admin_ids if region in federated_states)
    # an island is a strong cultural entity, drawn genre-less or not
    included.update(_islands(parents, load, land))
    for region in list(included):
        node = region
        while node in parents and parents[node] not in included:
            node = parents[node]
            included.add(node)
    admin_regions = sorted(r for r in included if r in admin_ids)
    zones = sorted(r for r in included if r in zone_candidates)

    hidden_regions = sorted(admin_ids - set(admin_regions))
    children: dict[str, list[str]] = defaultdict(list)
    for region in admin_regions:
        children[parents.get(region, WORLD_ID)].append(region)

    def admin_ancestors(admin_id: str) -> list[str]:
        chain = [admin_id]
        while chain[-1] in parents:
            chain.append(parents[chain[-1]])
        return chain

    def inherited_overview(region: str) -> str | None:
        """The overview of the nearest admin ancestor that has one, for an admin region without its own."""
        if region in overview_by_region:
            return None
        return next((overview_by_region[a] for a in admin_ancestors(region)[1:] if a in overview_by_region), None)

    def city_parent(city: str) -> str:
        admin_id = footprint_admins[city][0]
        while admin_id in dissolved:
            admin_id = dissolved[admin_id]
        return admin_id

    city_regions = sorted(c for c, genres in genres_by_city.items() if genres)
    city_parents = {city: city_parent(city) for city in city_regions}
    cities_by_parent: dict[str, list[str]] = defaultdict(list)
    for city, parent in city_parents.items():
        cities_by_parent[parent].append(city)

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
    zone_members: dict[str, set[str]] = defaultdict(set)
    zone_set = set(zones)
    for entity_id, admin_id in footprint.select("entity_id", "admin_id").rows():
        while admin_id in dissolved:
            admin_id = dissolved[admin_id]
        if entity_id in zone_set and admin_id in shapes:
            zone_members[entity_id].add(admin_id)
            for ancestor in admin_ancestors(admin_id):
                zone_ids_by_region[ancestor].add(entity_id)

    admin_region_set = set(admin_regions)
    for zone, members in zone_members.items():
        listed = members & admin_region_set
        children[zone] = sorted(m for m in listed if not any(a in listed for a in admin_ancestors(m)[1:]))
    zone_inside: dict[str, list[str]] = {
        zone: sorted(other for other in zones if zone_members[other] < zone_members[zone] and zone_members[other])
        for zone in zones
    }
    for zone, inside in zone_inside.items():
        zone_ids_by_region[zone].update(inside)
    nested_zones = {other for inside in zone_inside.values() for other in inside}

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
                "overviewItemId": overview_by_region.get(region),
                "inheritedOverviewItemId": None if region in zone_set else inherited_overview(region),
                "genres": genres_by_region.get(region, []),
                "songCount": sum(song_counts.get(item, 0) for item in relevant),
                "genreCount": len(relevant),
            }
        )
    for city in city_regions:
        parent, point = city_parents[city], list(city_points[city])
        own = {g["itemId"] for g in genres_by_city[city]}
        relevant = own.union(*(descendants.get(item, set()) for item in own))
        entries.append(
            {
                "id": city,
                "name": labels[city],
                "kind": CITY_KIND,
                "parentId": parent,
                "childIds": [],
                "zoneIds": [],
                "bbox": point + point,
                "mainBbox": point + point,
                "centroid": point,
                "drillable": False,
                "overviewItemId": overview_by_region.get(city),
                "inheritedOverviewItemId": None
                if city in overview_by_region
                else next((overview_by_region[a] for a in admin_ancestors(parent) if a in overview_by_region), None),
                "genres": genres_by_city[city],
                "songCount": sum(song_counts.get(item, 0) for item in relevant),
                "genreCount": len(relevant),
            }
        )
    for entry in entries:
        if entry["id"] in cities_by_parent:
            entry["cityIds"] = cities_by_parent[entry["id"]]
    document: dict = {
        "regions": entries,
        "worldChildIds": children.get(WORLD_ID, []),
        "worldZoneIds": [zone for zone in zones if zone not in nested_zones],
    }
    if hidden_regions:
        document["hiddenRegions"] = [
            {
                "id": region,
                "name": labels[region],
                "kind": kinds[region],
                "parentId": parents.get(region, WORLD_ID),
                "centroid": shapes[region]["centroid"],
            }
            for region in hidden_regions
        ]
    if not any(entry["genres"] for entry in entries):
        raise ValueError("region map has no region with a genre")

    geometry_dir = output_dir / "5_region_map"
    if geometry_dir.exists():
        shutil.rmtree(geometry_dir)
    geometry_dir.mkdir(parents=True)
    started = time.monotonic()
    bands, depths = _admin_bands(
        {region: children.get(region, []) for region in [WORLD_ID, *admin_regions]},
        {region: shapes[region]["main_bbox"] for region in admin_regions},
    )
    if unbanded := set(admin_regions) - set(bands):
        raise ValueError(f"admin regions outside the world's tree: {sorted(unbanded)}")

    def label_band(region: str) -> Band:
        min_zoom, max_zoom = bands[region]
        return min_zoom, None if max_zoom is None else max_zoom + LABEL_OVERLAP

    document["tileset"], tileset_bytes = _region_tileset(
        [(r, labels[r], parents.get(r, WORLD_ID), kinds[r], depths[r], bands[r], load(r)) for r in admin_regions],
        [(z, labels[z], _zone_band(shapes[z]["main_bbox"]), load(z), tuple(shapes[z]["centroid"])) for z in zones],
        [
            (
                entry["id"],
                entry["parentId"],
                entry["name"],
                depths[entry["id"]],
                label_band(entry["id"]),
                tuple(entry["centroid"]),
            )
            for entry in entries
            if entry["kind"] not in ("zone", CITY_KIND)
        ],  # fmt: skip
        [
            (c, p, labels[c], max(float(CITY_TILESET_MIN_ZOOM), bands[p][0]), city_points[c])
            for c, p in city_parents.items()
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
    without_overview = sum(1 for entry in entries if entry["overviewItemId"] is None)
    inheriting = sum(1 for entry in entries if entry["inheritedOverviewItemId"] is not None)
    logger.info(
        "%d of %d region(s) have no overview genre, %d of them inherit one from an ancestor region",
        without_overview,
        len(entries),
        inheriting,
    )
    report = {
        "unplacedGenreCount": unplaced_frame.height,
        "regionsWithoutOverview": without_overview,
        "hiddenRegionCount": len(hidden_regions),
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
