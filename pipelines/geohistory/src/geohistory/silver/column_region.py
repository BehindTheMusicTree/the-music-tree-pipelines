import logging
from collections import Counter
from pathlib import Path

import polars as pl

logger = logging.getLogger(__name__)

OTHER_REGION = "other"
REGIONS = (
    ("americas", "Americas"),
    ("europe", "Europe"),
    ("africa", "Africa"),
    ("asia", "Asia"),
    ("oceania", "Oceania"),
    (OTHER_REGION, "Other"),
)
REGION_RANK = {region_id: rank for rank, (region_id, _) in enumerate(REGIONS)}
REGION_LABEL = dict(REGIONS)
CONTINENT_REGION = {
    "North America": "americas",
    "South America": "americas",
    "Europe": "europe",
    "Africa": "africa",
    "Asia": "asia",
    "Oceania": "oceania",
}
REGION_SCHEMA = {"column_id": pl.String, "region_id": pl.String, "region_label": pl.String, "region_rank": pl.Int32}


def _entity_regions(bronze_dir: Path, silver_dir: Path) -> dict[str, Counter[str]]:
    """Region votes per entity: the continent of each admin unit it covers, or of the country it is."""
    admin = pl.read_parquet(
        bronze_dir / "natural_earth_admin.parquet", columns=["ne_id", "level", "adm0_a3", "continent"]
    )
    continent_of_country = dict(admin.filter(pl.col("level") == 0).select("adm0_a3", "continent").rows())
    country_of_unit = dict(admin.select("ne_id", "adm0_a3").rows())
    region_of_unit = {
        ne_id: CONTINENT_REGION.get(continent_of_country[adm0_a3] or "", OTHER_REGION)
        for ne_id, adm0_a3 in country_of_unit.items()
    }

    ne_map = pl.read_parquet(silver_dir / "1_entity_ne_admin.parquet").drop_nulls("entity_id")
    unit_regions: dict[str, set[str]] = {}
    for ne_id, entity_id in ne_map.select("ne_id", "entity_id").rows():
        unit_regions.setdefault(entity_id, set()).add(region_of_unit[ne_id])

    votes: dict[str, Counter[str]] = {}
    footprint = pl.read_parquet(silver_dir / "8_entity_footprint.parquet")
    for entity_id, admin_id in footprint.select("entity_id", "admin_id").rows():
        votes.setdefault(entity_id, Counter()).update(unit_regions.get(admin_id, ()))
    for entity_id, regions in unit_regions.items():
        if not votes.get(entity_id):
            votes.setdefault(entity_id, Counter()).update(regions)
    return votes


def column_region(bronze_dir: Path, silver_dir: Path) -> Path:
    lanes = pl.read_parquet(silver_dir / "6_genealogy_lane.parquet")
    votes = _entity_regions(bronze_dir, silver_dir)

    rows: list[tuple[str, str, str, int]] = []
    for column_id, members in lanes.group_by("column_id").agg("entity_id").sort("column_id").rows():
        tally: Counter[str] = Counter()
        for entity_id in members:
            tally.update(votes.get(entity_id, Counter()))
        placed = {region: count for region, count in tally.items() if region != OTHER_REGION} or dict(tally)
        region = min(placed, key=lambda r: (-placed[r], REGION_RANK[r])) if placed else OTHER_REGION
        rows.append((column_id, region, REGION_LABEL[region], REGION_RANK[region]))

    path = silver_dir / "6_column_region.parquet"
    pl.DataFrame(rows, schema=REGION_SCHEMA, orient="row").write_parquet(path)
    unclassified = sum(1 for _, region, _, _ in rows if region == OTHER_REGION)
    logger.info("assigned %d columns to regions (%d in other) to %s", len(rows), unclassified, path)
    return path
