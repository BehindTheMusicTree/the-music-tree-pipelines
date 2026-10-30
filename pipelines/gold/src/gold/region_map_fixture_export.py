"""On-demand cut of the region map export into a small, self-contained fixture.

Not run as part of `gold.__main__`. Reads a `5_region_map/` directory and writes the same layout holding
only the demo path: the world level, the requested regions with their ancestors and children, and the
requested zones. Only the requested regions and their ancestors stay drillable, so every kept
`geometry/<id>.geojson` is copied whole; every other kept region becomes a leaf. `zoneIds` are pruned to
the kept zones, so referential integrity holds.

Usage:
    uv run --package gold python -m gold.region_map_fixture_export <5_region_map dir> <out dir> [--region Q142 --region Q12130 --zone Q236626]
"""

import argparse
import json
import logging
import shutil
from pathlib import Path

from jsonschema import ValidationError, validate

from gold.region_map_export import REGION_MAP_SCHEMA_PATH, WORLD_ID

DEFAULT_REGIONS = ("Q142", "Q12130")
DEFAULT_ZONES = ("Q236626",)

logger = logging.getLogger(__name__)


def _check_integrity(regions: dict[str, dict], root: Path) -> None:
    for region in regions.values():
        references = [*region["childIds"], *region["zoneIds"], *([region["parentId"]] if region["parentId"] else [])]
        dangling = [r for r in references if r not in regions and r != WORLD_ID]
        if dangling:
            raise ValueError(f"{region['id']} references regions missing from the fixture: {dangling[:5]}")
        if region["drillable"] and not (root / "geometry" / f"{region['id']}.geojson").exists():
            raise ValueError(f"{region['id']} is drillable but its geometry file is missing")


def export_region_map_fixture(
    region_map_dir: Path, output_dir: Path, region_ids: tuple[str, ...], zone_ids: tuple[str, ...]
) -> Path:
    logger.info("cutting region map fixture from %s", region_map_dir)
    index = json.loads((region_map_dir / "index.json").read_text())
    if "files" not in index:
        raise ValueError(f"{region_map_dir / 'index.json'} predates content-addressed files; re-run gold")
    regions = {r["id"]: r for r in index["regions"]}

    unknown = [r for r in region_ids if r not in regions or regions[r]["kind"] == "zone"]
    if unknown:
        raise ValueError(f"unknown region(s) {unknown}")
    unknown_zones = [z for z in zone_ids if z not in regions or regions[z]["kind"] != "zone"]
    if unknown_zones:
        raise ValueError(f"unknown zone(s) {unknown_zones}")

    drillable = set(region_ids)
    for region_id in region_ids:
        node = regions[region_id]["parentId"]
        while node and node != WORLD_ID:
            drillable.add(node)
            node = regions[node]["parentId"]
    drillable = {r for r in drillable if regions[r]["drillable"]}

    kept = {r["id"] for r in regions.values() if r["parentId"] == WORLD_ID} | set(region_ids)
    for region_id in list(kept):
        node = regions[region_id]["parentId"]
        while node and node != WORLD_ID:
            kept.add(node)
            node = regions[node]["parentId"]
    for region_id in drillable:
        kept.update(regions[region_id]["childIds"])
    kept_zones = set(zone_ids)

    cut = []
    for region_id in sorted(kept):
        region = dict(regions[region_id])
        region["childIds"] = region["childIds"] if region_id in drillable else []
        region["drillable"] = region_id in drillable
        region["zoneIds"] = [z for z in region["zoneIds"] if z in kept_zones]
        cut.append(region)
    cut.extend(regions[z] for z in sorted(kept_zones))

    wanted = [
        "geometry/world.geojson",
        *(f"geometry/{r}.geojson" for r in sorted(drillable)),
        *(f"zones/{z}.geojson" for z in sorted(kept_zones)),
    ]
    document = {"regions": cut, "files": {relative: index["files"][relative] for relative in wanted}}
    try:
        validate(document, json.loads(REGION_MAP_SCHEMA_PATH.read_text()))
    except ValidationError as e:
        raise ValueError(f"region map fixture failed schema validation at {e.json_path}: {e.message}") from e

    (output_dir / "geometry").mkdir(parents=True, exist_ok=True)
    (output_dir / "zones").mkdir(exist_ok=True)
    for relative in wanted:
        shutil.copyfile(region_map_dir / relative, output_dir / relative)
    (output_dir / "index.json").write_text(json.dumps(document, indent=2, ensure_ascii=False))
    _check_integrity({r["id"]: r for r in cut}, output_dir)
    logger.info("wrote %d region(s) and %d file(s) to %s", len(cut), len(wanted), output_dir)
    return output_dir / "index.json"


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)

    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("region_map_dir", type=Path)
    parser.add_argument("output_dir", type=Path)
    parser.add_argument("--region", action="append", dest="regions")
    parser.add_argument("--zone", action="append", dest="zones")
    args = parser.parse_args()

    export_region_map_fixture(
        args.region_map_dir,
        args.output_dir,
        tuple(args.regions or DEFAULT_REGIONS),
        tuple(args.zones or DEFAULT_ZONES),
    )
