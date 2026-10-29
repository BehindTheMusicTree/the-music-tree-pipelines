import json
import logging
from pathlib import Path

import polars as pl
from jsonschema import ValidationError, validate

GENEALOGY_SCHEMA_PATH = Path(__file__).parent / "schemas" / "genealogy.schema.json"

logger = logging.getLogger(__name__)


def export_genealogy(geohistory_silver_dir: Path, wikidata_silver_dir: Path, output_dir: Path) -> Path:
    traditions = pl.read_parquet(geohistory_silver_dir / "5_tradition.parquet")
    links = pl.read_parquet(geohistory_silver_dir / "5_tradition_link.parquet")
    lanes = pl.read_parquet(geohistory_silver_dir / "6_genealogy_lane.parquet")
    tradition_lane = dict(pl.read_parquet(geohistory_silver_dir / "6_genealogy_tradition_lane.parquet").rows())
    bands = pl.read_parquet(geohistory_silver_dir / "7_band_fragment.parquet")
    order = pl.read_parquet(geohistory_silver_dir / "7_lane_order.parquet")
    succession = pl.read_parquet(geohistory_silver_dir / "3_entity_succession.parquet")
    canonical_ids = set(pl.read_parquet(wikidata_silver_dir / "7_canonical_hierarchy.parquet").get_column("item_id"))

    columns = [
        {
            "id": column_id,
            "position": position,
            "lanes": [
                {
                    "entityId": lane["entity_id"],
                    "label": lane["label"],
                    "startYear": lane["start_year"],
                    "endYear": lane["end_year"],
                }
                for lane in lanes.filter(pl.col("column_id") == column_id)
                .sort("start_year", "entity_id", nulls_last=True)
                .to_dicts()
            ],
        }
        for column_id, position in order.sort("position").rows()
    ]
    shown_ids = set(lanes.get_column("entity_id")) | set(bands.get_column("entity_id"))
    document = {
        "columns": columns,
        "bands": [
            {
                "entityId": band["entity_id"],
                "label": band["label"],
                "fragment": band["fragment"],
                "startPosition": band["start_position"],
                "endPosition": band["end_position"],
                "startYear": band["start_year"],
                "endYear": band["end_year"],
            }
            for band in bands.sort("entity_id", "fragment").to_dicts()
        ],
        "successions": [
            {"predecessorId": predecessor, "successorId": successor}
            for predecessor, successor in succession.select("predecessor_id", "successor_id").rows()
            if predecessor in shown_ids and successor in shown_ids
        ],
        "traditions": [
            {
                "wikidataId": row["wikidata_id"],
                "label": row["label"],
                "start": row["start_year"],
                "end": row["end_year"],
                "anchorId": tradition_lane.get(row["wikidata_id"]),
                "inCanonicalTree": row["wikidata_id"] in canonical_ids,
            }
            for row in traditions.sort("wikidata_id").to_dicts()
        ],
        "traditionLinks": [
            {"sourceId": source, "targetId": target, "kind": kind}
            for source, target, kind in links.select("from_wikidata_id", "to_wikidata_id", "kind").rows()
        ],
    }

    if not document["traditions"] or not columns:
        raise ValueError(f"genealogy has {len(document['traditions'])} tradition(s) and {len(columns)} column(s)")
    empty = [column["id"] for column in columns if not column["lanes"]]
    if empty:
        raise ValueError(f"genealogy columns without lanes (order and lane files disagree): {empty[:5]}")

    unshown = sorted({t["anchorId"] for t in document["traditions"] if t["anchorId"] not in (None, *shown_ids)})
    if unshown:
        raise ValueError(f"genealogy tradition anchors outside the lanes and bands: {unshown[:5]}")

    try:
        validate(document, json.loads(GENEALOGY_SCHEMA_PATH.read_text()))
    except ValidationError as e:
        raise ValueError(f"genealogy failed schema validation ({GENEALOGY_SCHEMA_PATH}): {e.message}")

    output_dir.mkdir(parents=True, exist_ok=True)
    output_path = output_dir / "4_genealogy.json"
    output_path.write_text(json.dumps(document, indent=2, ensure_ascii=False))
    logger.info("wrote %d column(s) and %d tradition(s) to %s", len(columns), len(document["traditions"]), output_path)
    return output_path
