import logging
from collections import Counter
from pathlib import Path

import polars as pl
import shapely

from geohistory.silver.curation import read_curation_list

logger = logging.getLogger(__name__)

RELIGION_KIND = "religion"
RELIGIONS_REGION = "religions"
REGIONS = (
    ("americas", "Americas"),
    ("europe", "Europe"),
    ("africa", "Africa"),
    ("asia", "Asia"),
    ("oceania", "Oceania"),
    (RELIGIONS_REGION, "Religions"),
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
# A coordinate that falls in no country (a strait, an archipelago's sea) takes the nearest country within this.
NEAREST_COUNTRY_TOLERANCE_DEGREES = 2.0
REGION_SCHEMA = {"column_id": pl.String, "region_id": pl.String, "region_label": pl.String, "region_rank": pl.Int32}


def _read_admin(bronze_dir: Path) -> pl.DataFrame:
    admin = pl.read_parquet(
        bronze_dir / "natural_earth_admin.parquet", columns=["ne_id", "level", "adm0_a3", "continent", "area_km2"]
    )
    return admin.with_columns(
        pl.col("adm0_a3")
        .replace_strict(dict(admin.filter(pl.col("level") == 0).select("adm0_a3", "continent").rows()), default=None)
        .alias("country_continent")
    ).with_columns(
        pl.col("country_continent")
        .replace_strict(CONTINENT_REGION, default=None, return_dtype=pl.String)
        .alias("region")
    )


def _entity_votes(admin: pl.DataFrame, silver_dir: Path) -> dict[str, Counter[str]]:
    """Area-weighted region votes per entity: its footprint's admin units, else the units it is mapped to."""
    placed = admin.drop_nulls("region")
    unit_area = {ne_id: (region, area) for ne_id, region, area in placed.select("ne_id", "region", "area_km2").rows()}

    own: dict[str, Counter[str]] = {}
    ne_map = pl.read_parquet(silver_dir / "1_entity_ne_admin.parquet").drop_nulls("entity_id")
    for ne_id, entity_id in ne_map.select("ne_id", "entity_id").rows():
        if ne_id in unit_area:
            region, area = unit_area[ne_id]
            own.setdefault(entity_id, Counter())[region] += area

    votes: dict[str, Counter[str]] = {}
    footprint = pl.read_parquet(silver_dir / "8_entity_footprint.parquet")
    for entity_id, admin_id in footprint.select("entity_id", "admin_id").rows():
        votes.setdefault(entity_id, Counter()).update(own.get(admin_id, ()))
    for entity_id, regions in own.items():
        if not votes.get(entity_id):
            votes[entity_id] = Counter(regions)
    return votes


def _coordinate_regions(bronze_dir: Path, admin: pl.DataFrame, points: pl.DataFrame) -> dict[str, str]:
    """Region of the country containing each entity's point, else of the nearest one within the tolerance."""
    if not points.height:
        return {}
    countries = (
        pl.read_parquet(bronze_dir / "natural_earth_admin.parquet", columns=["ne_id", "geometry_wkb"])
        .join(admin.filter(pl.col("level") == 0).drop_nulls("region").select("ne_id", "region"), on="ne_id")
        .sort("ne_id")
    )
    tree = shapely.STRtree(shapely.make_valid(shapely.from_wkb(countries["geometry_wkb"].to_list())))
    region_of = countries["region"].to_list()
    entity_ids = points["entity_id"].to_list()
    geometries = shapely.points(points["longitude"].to_numpy(), points["latitude"].to_numpy())

    found: dict[str, str] = {}
    inside_point, inside_country = tree.query(geometries, predicate="intersects")
    for point, country in sorted(zip(inside_point.tolist(), inside_country.tolist(), strict=True)):
        found.setdefault(entity_ids[point], region_of[country])
    outside = [index for index, entity_id in enumerate(entity_ids) if entity_id not in found]
    if outside:
        near_point, near_country = tree.query_nearest(
            geometries[outside], max_distance=NEAREST_COUNTRY_TOLERANCE_DEGREES, all_matches=False
        )
        for point, country in zip(near_point.tolist(), near_country.tolist(), strict=True):
            found[entity_ids[outside[point]]] = region_of[country]
    return found


def _region_overrides(curation_dir: Path, entities: pl.DataFrame) -> dict[str, str]:
    rules = read_curation_list(curation_dir, "region_overrides")
    unknown_regions = sorted(set(rules["region_id"]) - set(REGION_RANK))
    if unknown_regions:
        raise ValueError(
            f"region_overrides names unknown region(s) {unknown_regions}, expected one of {list(REGION_RANK)}"
        )
    if rules["entity_id"].n_unique() != rules.height:
        raise ValueError("region_overrides must list each entity_id once")
    unknown = sorted(set(rules["entity_id"]) - set(entities["entity_id"]))
    if unknown:
        logger.warning("%d region_overrides entity id(s) are not in scope: %s", len(unknown), unknown[:5])
    return dict(rules.select("entity_id", "region_id").rows())


def _plurality(tally: Counter[str]) -> str | None:
    return min(tally, key=lambda region: (-tally[region], REGION_RANK[region])) if tally else None


def column_region(bronze_dir: Path, silver_dir: Path, curation_dir: Path) -> Path:
    lanes = pl.read_parquet(silver_dir / "6_genealogy_lane.parquet")
    entities = pl.read_parquet(silver_dir / "1_cultural_entity.parquet")
    admin = _read_admin(bronze_dir)
    votes = _entity_votes(admin, silver_dir)
    overrides = _region_overrides(curation_dir, entities)
    forced = {
        **dict.fromkeys(entities.filter(pl.col("kind") == RELIGION_KIND)["entity_id"], RELIGIONS_REGION),
        **overrides,
    }

    columns = lanes.group_by("column_id").agg("entity_id").sort("column_id").rows()
    unvoted = {entity_id for _, members in columns for entity_id in members if not votes.get(entity_id)} - set(forced)
    fallback = _coordinate_regions(
        bronze_dir,
        admin,
        entities.filter(
            pl.col("entity_id").is_in(unvoted) & pl.col("latitude").is_not_null() & pl.col("longitude").is_not_null()
        ).sort("entity_id"),
    )

    rows: list[tuple[str, str, str, int]] = []
    unplaced: list[tuple[str, list[str]]] = []
    for column_id, members in columns:
        region = _plurality(Counter(forced[entity_id] for entity_id in members if entity_id in forced))
        if region is None:
            tally: Counter[str] = Counter()
            for entity_id in members:
                tally.update(votes.get(entity_id, ()))
            region = _plurality(tally) or _plurality(Counter(fallback[e] for e in members if e in fallback))
        if region is None:
            unplaced.append((column_id, members))
            continue
        rows.append((column_id, region, REGION_LABEL[region], REGION_RANK[region]))
    if unplaced:
        entity_ids = sorted({entity_id for _, members in unplaced for entity_id in members})
        raise ValueError(
            f"{len(unplaced)} genealogy column(s) have no region: add a region_overrides row for each entity: "
            f"{entity_ids[:10]}"
        )

    path = silver_dir / "6_column_region.parquet"
    pl.DataFrame(rows, schema=REGION_SCHEMA, orient="row").write_parquet(path)
    per_region = Counter(region for _, region, _, _ in rows)
    logger.info(
        "assigned %d columns to regions (%s) to %s",
        len(rows),
        ", ".join(f"{region}: {per_region[region]}" for region, _ in REGIONS),
        path,
    )
    return path
