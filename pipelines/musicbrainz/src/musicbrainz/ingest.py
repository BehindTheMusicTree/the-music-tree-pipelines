import logging
from pathlib import Path

import psycopg
import polars as pl
import pyarrow.parquet as pq
from common.env import load_pipeline_env, require_env, resolve_pipeline_path

logger = logging.getLogger(__name__)

BRONZE_TABLES = (
    "recording",
    "tag",
    "recording_tag",
    "genre",
    "url",
    "l_recording_url",
    "link",
    "link_type",
    "artist_credit",
    "artist_credit_name",
    "artist",
)

# Rows pulled per batch via a server-side cursor, rather than loading a whole table into
# memory at once — the `recording` table alone is multiple million rows.
BATCH_SIZE = 100_000


def ingest_table(conn: psycopg.Connection, table: str, output_dir: Path) -> Path:
    if table not in BRONZE_TABLES:
        raise ValueError(f"Unknown bronze table: {table!r}. Expected one of: {', '.join(BRONZE_TABLES)}")

    logger.info("ingesting table %s", table)
    output_dir.mkdir(parents=True, exist_ok=True)
    output_path = output_dir / f"{table}.parquet"

    total_rows = 0
    writer: pq.ParquetWriter | None = None
    with conn.cursor(name=f"bronze_{table}") as cur:
        batches = pl.read_database(
            f"SELECT * from musicbrainz.{table}",
            cur,
            iter_batches=True,
            batch_size=BATCH_SIZE,
            infer_schema_length=BATCH_SIZE,
        )
        for batch in batches:
            arrow_batch = batch.to_arrow()
            if writer is None:
                writer = pq.ParquetWriter(output_path, arrow_batch.schema)
            writer.write_table(arrow_batch)
            total_rows += batch.height
    if writer is not None:
        writer.close()

    logger.info("wrote %d rows to %s", total_rows, output_path)
    return output_path


def run_bronze_ingestion(conn: psycopg.Connection, output_dir: Path) -> list[Path]:
    return [ingest_table(conn, table, output_dir) for table in BRONZE_TABLES]


if __name__ == "__main__":
    from musicbrainz import db

    logging.basicConfig(level=logging.INFO)
    load_pipeline_env(__file__)
    output_dir = resolve_pipeline_path(__file__, require_env("BRONZE_OUTPUT_DIR"))
    with db.connect() as conn:
        run_bronze_ingestion(conn, output_dir)
