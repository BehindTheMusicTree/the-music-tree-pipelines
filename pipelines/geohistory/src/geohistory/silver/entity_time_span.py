import logging
from pathlib import Path

import polars as pl

logger = logging.getLogger(__name__)

DATE_PROPERTIES = pl.DataFrame(
    {
        "property": ["P571", "P580", "P576", "P582"],
        "side": ["start", "start", "end", "end"],
        "priority": [1, 2, 1, 2],
    },
    schema_overrides={"priority": pl.Int8},
)
PERIOD_PRIORITY = 3
GLOTTOLOG_PRIORITY = 4
YEAR_PATTERN = r"^\+?(-?\d+)-"


def _dated_claims(claims: pl.DataFrame) -> pl.DataFrame:
    return (
        claims.join(DATE_PROPERTIES, on="property")
        .with_columns(pl.col("value").str.extract(YEAR_PATTERN, 1).cast(pl.Int32).alias("year"))
        .drop_nulls("year")
        .select("item_id", "property", "side", "priority", "year", pl.col("time_precision").alias("precision"))
    )


def _best(candidates: pl.DataFrame, side: str, *, latest: bool) -> pl.DataFrame:
    return (
        candidates.filter(pl.col("side") == side)
        .sort(
            "entity_id",
            "priority",
            "year",
            "precision",
            descending=[False, False, latest, True],
            nulls_last=True,
        )
        .unique("entity_id", keep="first", maintain_order=True)
        .select(
            "entity_id",
            pl.col("year").alias(f"{side}_year"),
            pl.col("precision").alias(f"{side}_precision"),
            pl.col("source").alias(f"{side}_source"),
        )
    )


def entity_time_span(bronze_dir: Path, wikidata_bronze_dir: Path, silver_dir: Path) -> Path:
    languoids = pl.read_parquet(bronze_dir / "glottolog_languoid.parquet")
    claims = pl.read_parquet(wikidata_bronze_dir / "wikidata_cultural_entity_claim.parquet")
    entities = pl.read_parquet(silver_dir / "1_cultural_entity.parquet").select("entity_id")
    crosswalk = pl.read_parquet(silver_dir / "1_entity_wikidata_item.parquet")

    dated = _dated_claims(claims)
    own = dated.join(crosswalk, left_on="item_id", right_on="wikidata_id").select(
        "entity_id", "side", "priority", "year", "precision", pl.col("property").alias("source")
    )
    periods = (
        claims.filter(pl.col("property") == "P2348")
        .join(crosswalk, left_on="item_id", right_on="wikidata_id")
        .select("entity_id", pl.col("value").alias("period_id"))
        .join(dated, left_on="period_id", right_on="item_id")
        .select(
            "entity_id",
            "side",
            pl.lit(PERIOD_PRIORITY, pl.Int8).alias("priority"),
            "year",
            "precision",
            pl.lit("period").alias("source"),
        )
    )
    documentation = pl.concat(
        [
            languoids.select(
                pl.col("glottocode").alias("entity_id"),
                pl.lit(side).alias("side"),
                pl.lit(GLOTTOLOG_PRIORITY, pl.Int8).alias("priority"),
                pl.col(column).cast(pl.Int32, strict=False).alias("year"),
                pl.lit(None, pl.Int8).alias("precision"),
                pl.lit("glottolog").alias("source"),
            ).drop_nulls("year")
            for side, column in (("start", "first_year_of_documentation"), ("end", "last_year_of_documentation"))
        ]
    )
    candidates = pl.concat([own, periods, documentation])

    time_span = (
        entities.join(_best(candidates, "start", latest=False), on="entity_id", how="left")
        .join(_best(candidates, "end", latest=True), on="entity_id", how="left")
        .sort("entity_id")
    )
    inverted = time_span.filter(pl.col("start_year") > pl.col("end_year")).height
    if inverted:
        logger.warning("%d entities start after they end", inverted)

    output_path = silver_dir / "4_entity_time_span.parquet"
    time_span.write_parquet(output_path)
    logger.info("wrote %d rows to %s", time_span.height, output_path)
    return output_path
