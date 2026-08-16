import logging
from pathlib import Path

import psycopg
import polars as pl
from common.env import load_pipeline_env, require_env

logger = logging.getLogger(__name__)

BRONZE_TABLES = ("recording", "tag", "recording_tag", "genre")


def ingest_table(conn: psycopg.Connection, table: str, output_dir: Path) -> Path:
    if table not in BRONZE_TABLES:
        raise ValueError(f"Unknown bronze table: {table!r}. Expected one of: {', '.join(BRONZE_TABLES)}")

    logger.info("ingesting table %s", table)
    df = pl.read_database(f"SELECT * from musicbrainz.{table}", conn)

    output_dir.mkdir(parents=True, exist_ok=True)
    output_path = output_dir / f"{table}.parquet"
    df.write_parquet(output_path)
    logger.info("wrote %d rows to %s", df.height, output_path)

    return output_path


def run_bronze_ingestion(conn: psycopg.Connection, output_dir: Path) -> list[Path]:
    return [ingest_table(conn, table, output_dir) for table in BRONZE_TABLES]


if __name__ == "__main__":
    from musicbrainz import db

    logging.basicConfig(level=logging.INFO)
    load_pipeline_env(__file__)
    output_dir = Path(require_env("BRONZE_OUTPUT_DIR"))
    with db.connect() as conn:
        run_bronze_ingestion(conn, output_dir)
