"""Verify replay boundaries independently of the containment graph and physical label layer."""

import argparse
import json
from pathlib import Path

import polars as pl
import shapely
from geohistory.silver.reference_land import reference_land

PRECISION = 0.000001
ISLAND_WITNESSES = {
    "Q22890": (-7.5, 53.5),
    "Q23666": (-2, 54),
    "Q36117": (114, 0),
    "Q40285": (141, -5),
    "Q3757": (110, -7.5),
    "Q3492": (101, 0),
}
PAIRS = (
    ("Q145", "Q22890"),
    ("Q145", "Q23666"),
    ("Q252", "Q36117"),
    ("Q252", "Q40285"),
    ("Q252", "Q3757"),
    ("Q252", "Q3492"),
    ("Q29999", "Q664609"),
)


def validate(replay: Path) -> dict:
    parts = list(shapely.get_parts(reference_land(replay / "bronze")))
    index = shapely.STRtree(parts)
    shapes = {
        row["entity_id"]: shapely.from_wkb(row["geometry_wkb"])
        for row in pl.read_parquet(replay / "silver/8_entity_geometry.parquet").iter_rows(named=True)
    }
    coverage = pl.read_parquet(replay / "silver/8_entity_coverage.parquet")
    islands = []
    for entity, (longitude, latitude) in ISLAND_WITNESSES.items():
        hits = index.query(shapely.Point(longitude, latitude), predicate="intersects")
        if len(hits) != 1:
            raise ValueError(f"{entity} witness does not identify one independent land component")
        actual = shapes[entity]
        union = shapely.union_all(
            shapely.from_wkb(coverage.filter(pl.col("entity_id") == entity)["geometry_wkb"].to_list())
        )
        equal_land = bool(shapely.equals(actual, parts[hits[0]]))
        distance = float(shapely.hausdorff_distance(union, actual))
        difference = float(shapely.symmetric_difference(union, actual).area)
        # Clipping rounds both axes to the source's six-decimal coordinate grid.
        within_precision = distance <= 2**0.5 * PRECISION and difference <= actual.length * 2**0.5 * PRECISION
        if not equal_land or not within_precision:
            raise ValueError(f"{entity} does not match independent land/coverage: {distance=}, {difference=}")
        islands.append(
            {
                "entity": entity,
                "equalsIndependentLand": equal_land,
                "coverageUnionEqualsBoundary": bool(shapely.equals(union, actual)),
                "coverageWithinSourcePrecision": within_precision,
                "coverageHausdorffDegrees": distance,
                "coverageSymmetricDifferenceSquareDegrees": difference,
            }
        )
    relations = pl.read_parquet(replay / "silver/2_place_relation.parquet")
    cases = []
    for child, parent in PAIRS:
        rows = relations.filter((pl.col("child_id") == child) & (pl.col("parent_id") == parent))
        if rows.height != 1 or rows["extent"].item() != "partial":
            raise ValueError(f"expected one partial relation: {child} -> {parent}")
        cases.append({"child": child, "parent": parent, "extent": "partial"})
    return {"coordinatePrecisionDegrees": PRECISION, "islands": islands, "relations": cases}


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("replay", type=Path, help="Directory containing the replay's bronze/ and silver/ outputs")
    parser.add_argument("output", type=Path)
    args = parser.parse_args()
    args.output.write_text(json.dumps(validate(args.replay), indent=2) + "\n")
