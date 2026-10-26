import logging
from pathlib import Path

import polars as pl
from common.quality_checks import check_non_empty, check_unique_key

from geohistory.silver.curation import read_curation_list

logger = logging.getLogger(__name__)

EXCLUDED_CATEGORIES = ("Bookkeeping", "Sign_Language", "Artificial_Language")
WIKIDATA_ENTITY_KINDS = (
    "archaeological_culture",
    "civilization",
    "historical_country",
    "country",
    "historical_region",
    "ethnic_group",
    "cultural_region",
    "macro_region",
)
_NUMBER = r"-?[\d.]+(?:[eE][-+]?\d+)?"
POINT_PATTERN = rf"^Point\(({_NUMBER}) ({_NUMBER})\)$"


def wikidata_points(claims: pl.DataFrame) -> pl.DataFrame:
    return (
        claims.filter(pl.col("property") == "P625")
        .select(
            pl.col("item_id").alias("wikidata_id"),
            pl.col("value").str.extract(POINT_PATTERN, 1).cast(pl.Float64, strict=False).alias("longitude"),
            pl.col("value").str.extract(POINT_PATTERN, 2).cast(pl.Float64, strict=False).alias("latitude"),
        )
        .drop_nulls()
        .sort("wikidata_id", "longitude", "latitude")
        .unique("wikidata_id", keep="first", maintain_order=True)
    )


def _glottolog_entities(languoids: pl.DataFrame) -> pl.DataFrame:
    excluded = languoids.filter(pl.col("category").is_in(EXCLUDED_CATEGORIES))["glottocode"].to_list()
    has_excluded_ancestor = (
        pl.col("classification").str.split("/").list.eval(pl.element().is_in(excluded)).list.any().fill_null(False)
    )
    return languoids.filter(~pl.col("category").is_in(EXCLUDED_CATEGORIES) & ~has_excluded_ancestor).select(
        pl.col("glottocode").alias("entity_id"),
        pl.col("level").alias("kind"),
        pl.col("name").alias("label"),
        pl.col("latitude").cast(pl.Float64, strict=False),
        pl.col("longitude").cast(pl.Float64, strict=False),
        pl.col("macroarea"),
    )


def cultural_entity(
    bronze_dir: Path, wikidata_bronze_dir: Path, output_dir: Path, curation_dir: Path
) -> tuple[Path, Path]:
    languoids = pl.read_parquet(bronze_dir / "glottolog_languoid.parquet")
    wikidata = pl.read_parquet(wikidata_bronze_dir / "wikidata_cultural_entity.parquet")
    claims = pl.read_parquet(wikidata_bronze_dir / "wikidata_cultural_entity_claim.parquet")

    glottolog = _glottolog_entities(languoids)
    item_glottocodes = wikidata.filter(pl.col("glottocode").is_not_null()).select("item_id", "glottocode").unique()
    unknown = item_glottocodes.join(languoids.select("glottocode"), on="glottocode", how="anti")
    if unknown.height:
        logger.warning("%d Wikidata Glottocode(s) are absent from Glottolog and left out", unknown.height)
    linked = item_glottocodes.join(glottolog.select(pl.col("entity_id").alias("glottocode")), on="glottocode")

    wikidata_entities = (
        wikidata.filter(pl.col("kind").is_in(WIKIDATA_ENTITY_KINDS))
        .join(linked.select("item_id").unique(), on="item_id", how="anti")
        .sort(
            "item_id",
            pl.col("kind").replace_strict({kind: rank for rank, kind in enumerate(WIKIDATA_ENTITY_KINDS)}),
            "item_label",
        )
        .unique("item_id", keep="first", maintain_order=True)
        .select(
            pl.col("item_id").alias("entity_id"),
            pl.col("kind"),
            pl.col("item_label").alias("label"),
        )
    )

    crosswalk = pl.concat(
        [
            linked.select(pl.col("glottocode").alias("entity_id"), pl.col("item_id").alias("wikidata_id")),
            wikidata_entities.select("entity_id", pl.col("entity_id").alias("wikidata_id")),
        ]
    ).sort("entity_id", "wikidata_id")

    entity_points = (
        crosswalk.join(wikidata_points(claims), on="wikidata_id")
        .sort("entity_id", "wikidata_id")
        .unique("entity_id", keep="first", maintain_order=True)
        .select("entity_id", pl.col("latitude").alias("wd_latitude"), pl.col("longitude").alias("wd_longitude"))
    )
    entities = (
        pl.concat(
            [
                glottolog,
                wikidata_entities.with_columns(
                    pl.lit(None, pl.Float64).alias(c) for c in ("latitude", "longitude")
                ).with_columns(pl.lit(None, pl.String).alias("macroarea")),
            ]
        )
        .join(entity_points, on="entity_id", how="left")
        .with_columns(
            pl.when(pl.col("latitude").is_not_null() & pl.col("longitude").is_not_null())
            .then(pl.lit("glottolog"))
            .when(pl.col("wd_latitude").is_not_null())
            .then(pl.lit("wikidata"))
            .alias("coordinate_source"),
        )
        .with_columns(
            pl.when(pl.col("coordinate_source") == "glottolog")
            .then(pl.col("latitude"))
            .otherwise(pl.col("wd_latitude"))
            .alias("latitude"),
            pl.when(pl.col("coordinate_source") == "glottolog")
            .then(pl.col("longitude"))
            .otherwise(pl.col("wd_longitude"))
            .alias("longitude"),
        )
        .select("entity_id", "kind", "label", "latitude", "longitude", "coordinate_source", "macroarea")
        .sort("entity_id")
    )
    excluded = read_curation_list(curation_dir, "entity_exclusions")["entity_id"].to_list()
    unknown_exclusions = sorted(set(excluded) - set(entities["entity_id"].to_list()))
    if unknown_exclusions:
        logger.warning("%d excluded entity id(s) are not in scope: %s", len(unknown_exclusions), unknown_exclusions[:5])
    entities = entities.filter(~pl.col("entity_id").is_in(excluded))
    crosswalk = crosswalk.filter(~pl.col("entity_id").is_in(excluded))
    check_non_empty(entities, "1_cultural_entity")
    check_unique_key(entities, "entity_id", "1_cultural_entity")

    output_dir.mkdir(parents=True, exist_ok=True)
    entity_path = output_dir / "1_cultural_entity.parquet"
    crosswalk_path = output_dir / "1_entity_wikidata_item.parquet"
    entities.write_parquet(entity_path)
    crosswalk.write_parquet(crosswalk_path)
    logger.info("wrote %d rows to %s and %d rows to %s", entities.height, entity_path, crosswalk.height, crosswalk_path)
    return entity_path, crosswalk_path
