import logging
from pathlib import Path

import polars as pl
import shapely.geometry
from common.env import load_pipeline_env, require_env, resolve_pipeline_path
from common.quality_checks import check_non_empty, check_unique_key

from geohistory import glottolog_client, natural_earth_client

logger = logging.getLogger(__name__)

VALUE_PARAMETERS = ("classification", "category")
ADMIN_SCHEMA = {
    "ne_id": pl.String,
    "level": pl.Int8,
    "wikidata_id": pl.String,
    "adm0_a3": pl.String,
    "name": pl.String,
    "iso_code": pl.String,
    "ne_type": pl.String,
    "geometry_wkb": pl.Binary,
}
ADMIN_FIELDS = {
    0: ("ADM0_A3", "WIKIDATAID", "ADM0_A3", "NAME", "ISO_A2_EH", "TYPE"),
    1: ("adm1_code", "wikidataid", "adm0_a3", "name", "iso_3166_2", "type_en"),
}


def build_languoids(languages: pl.DataFrame, values: pl.DataFrame, version: str) -> pl.DataFrame:
    parameters = (
        values.filter(pl.col("Parameter_ID").is_in(VALUE_PARAMETERS))
        .select("Language_ID", "Parameter_ID", "Value")
        .pivot(on="Parameter_ID", index="Language_ID", values="Value", aggregate_function="first")
    )
    missing = [parameter for parameter in VALUE_PARAMETERS if parameter not in parameters.columns]
    if missing:
        raise ValueError(f"Glottolog values.csv has no row for parameter(s) {missing}")

    return (
        languages.join(parameters, left_on="ID", right_on="Language_ID", how="left")
        .select(
            pl.col("Glottocode").alias("glottocode"),
            pl.col("Name").alias("name"),
            pl.col("Level").alias("level"),
            pl.col("category"),
            pl.col("classification"),
            pl.col("Latitude").cast(pl.Float64).alias("latitude"),
            pl.col("Longitude").cast(pl.Float64).alias("longitude"),
            pl.col("Macroarea").alias("macroarea"),
            pl.col("ISO639P3code").alias("iso639_3"),
            pl.col("First_Year_Of_Documentation").cast(pl.Int32).alias("first_year_of_documentation"),
            pl.col("Last_Year_Of_Documentation").cast(pl.Int32).alias("last_year_of_documentation"),
            pl.lit(version).alias("glottolog_version"),
        )
        .sort("glottocode")
    )


def ingest_glottolog_languoids(version: str, output_dir: Path) -> Path:
    logger.info("downloading Glottolog CLDF %s", version)
    archive = glottolog_client.download_cldf_archive(version)
    df = build_languoids(
        glottolog_client.read_cldf_table(archive, "languages.csv"),
        glottolog_client.read_cldf_table(archive, "values.csv"),
        version,
    )
    check_non_empty(df, "glottolog_languoid")
    check_unique_key(df, "glottocode", "glottolog_languoid")

    output_dir.mkdir(parents=True, exist_ok=True)
    output_path = output_dir / "glottolog_languoid.parquet"
    df.write_parquet(output_path)
    logger.info("wrote %d rows to %s", df.height, output_path)
    return output_path


def build_natural_earth_admin(admin_0: dict, admin_1: dict) -> pl.DataFrame:
    rows = []
    for level, layer in ((0, admin_0), (1, admin_1)):
        fields = ADMIN_FIELDS[level]
        for feature in layer["features"]:
            properties = feature["properties"]
            ne_id, wikidata_id, adm0_a3, name, iso_code, ne_type = (properties[field] or None for field in fields)
            if feature["geometry"] is None:
                raise ValueError(f"Natural Earth admin-{level} unit {ne_id} has no geometry")
            wkb = shapely.geometry.shape(feature["geometry"]).wkb
            rows.append((ne_id, level, wikidata_id, adm0_a3, name, iso_code, ne_type, wkb))
    df = pl.DataFrame(rows, schema=ADMIN_SCHEMA, orient="row").sort("level", "ne_id")
    countries = set(df.filter(pl.col("level") == 0)["adm0_a3"])
    orphans = df.filter((pl.col("level") == 1) & ~pl.col("adm0_a3").is_in(countries))
    if orphans.height:
        raise ValueError(f"Natural Earth admin-1 unit(s) with no admin-0 country: {orphans['ne_id'].to_list()[:10]}")
    return df


def ingest_natural_earth_admin(version: str, output_dir: Path) -> Path:
    logger.info("downloading Natural Earth %s", version)
    df = build_natural_earth_admin(
        natural_earth_client.download_layer(version, natural_earth_client.ADMIN_0_LAYER),
        natural_earth_client.download_layer(version, natural_earth_client.ADMIN_1_LAYER),
    )
    check_non_empty(df, "natural_earth_admin")
    check_unique_key(df, "ne_id", "natural_earth_admin")

    output_dir.mkdir(parents=True, exist_ok=True)
    output_path = output_dir / "natural_earth_admin.parquet"
    df.write_parquet(output_path)
    logger.info("wrote %d rows to %s", df.height, output_path)
    return output_path


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    load_pipeline_env(__file__)
    bronze_dir = resolve_pipeline_path(__file__, require_env("BRONZE_OUTPUT_DIR"))
    ingest_glottolog_languoids(require_env("GLOTTOLOG_CLDF_VERSION"), bronze_dir)
    ingest_natural_earth_admin(require_env("NATURAL_EARTH_VERSION"), bronze_dir)
