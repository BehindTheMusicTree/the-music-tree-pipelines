import logging
from pathlib import Path

import polars as pl
from common.env import load_pipeline_env, require_env, resolve_pipeline_path
from common.quality_checks import check_non_empty, check_unique_key

from geohistory import glottolog_client

logger = logging.getLogger(__name__)

VALUE_PARAMETERS = ("classification", "category")


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


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    load_pipeline_env(__file__)
    ingest_glottolog_languoids(
        require_env("GLOTTOLOG_CLDF_VERSION"), resolve_pipeline_path(__file__, require_env("BRONZE_OUTPUT_DIR"))
    )
