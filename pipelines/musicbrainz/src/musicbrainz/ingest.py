import json
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
    "l_recording_work",
    "link",
    "link_type",
    "artist_credit",
    "artist_credit_name",
    "artist",
    "artist_type",
    "area",
    "iso_3166_1",
    "l_artist_artist",
    "link_attribute",
    "link_attribute_type",
    "l_artist_url",
    "release_group",
    "release_group_primary_type",
    "release_group_secondary_type",
    "release_group_secondary_type_join",
    "release_group_meta",
    "recording_first_release_date",
)

# Rows pulled per batch via a server-side cursor, rather than loading a whole table into
# memory at once — the `recording` table alone is multiple million rows.
BATCH_SIZE = 100_000

# The two largest tables: Silver reads only these columns, and `SELECT *` would stream (and compress) the rest.
PROJECTED_COLUMNS = {
    "recording": ("id", "gid", "name", "artist_credit"),
    "url": ("id", "url"),
}

# Records which mirror state and queries produced the Parquet files, so a run against an unchanged mirror skips
# re-extracting them.
SNAPSHOT_FILENAME = "_snapshot.json"


def table_query(table: str) -> str:
    return f"SELECT {', '.join(PROJECTED_COLUMNS.get(table, ('*',)))} from musicbrainz.{table}"


def read_replication_sequence(conn: psycopg.Connection) -> int | None:
    """The mirror's replication sequence; None on a standalone database (no or a NULL row), which never replicates."""
    with conn.cursor() as cur:
        cur.execute("SELECT current_replication_sequence FROM musicbrainz.replication_control")
        rows = cur.fetchall()
    if len(rows) > 1:
        raise ValueError(f"replication_control has {len(rows)} rows, expected at most 1")
    return rows[0][0] if rows else None


def ingest_table(conn: psycopg.Connection, table: str, output_dir: Path) -> Path:
    if table not in BRONZE_TABLES:
        raise ValueError(f"Unknown bronze table: {table!r}. Expected one of: {', '.join(BRONZE_TABLES)}")

    logger.info("ingesting table %s", table)
    output_dir.mkdir(parents=True, exist_ok=True)
    output_path = output_dir / f"{table}.parquet"
    # Written beside the target then renamed, so a run killed mid-write keeps the previous complete file.
    tmp_path = output_dir / f"{table}.parquet.tmp"

    total_rows = 0
    writer: pq.ParquetWriter | None = None
    with conn.cursor(name=f"bronze_{table}") as cur:
        batches = pl.read_database(
            table_query(table),
            cur,
            iter_batches=True,
            batch_size=BATCH_SIZE,
            infer_schema_length=BATCH_SIZE,
        )
        for batch in batches:
            arrow_batch = batch.to_arrow()
            if writer is None:
                writer = pq.ParquetWriter(tmp_path, arrow_batch.schema)
            writer.write_table(arrow_batch)
            total_rows += batch.height
    if writer is not None:
        writer.close()

    if total_rows == 0:
        raise ValueError(f"{table} is empty")
    tmp_path.replace(output_path)

    logger.info("wrote %d rows to %s", total_rows, output_path)
    return output_path


def run_bronze_ingestion(conn: psycopg.Connection, output_dir: Path) -> list[Path]:
    sequence = read_replication_sequence(conn)
    snapshot = {"replication_sequence": sequence, "queries": {table: table_query(table) for table in BRONZE_TABLES}}
    snapshot_path = output_dir / SNAPSHOT_FILENAME
    paths = [output_dir / f"{table}.parquet" for table in BRONZE_TABLES]
    if (
        sequence is not None
        and snapshot_path.exists()
        and json.loads(snapshot_path.read_text()) == snapshot
        and all(path.exists() for path in paths)
    ):
        logger.info("mirror unchanged since replication sequence %d, keeping existing bronze tables", sequence)
        return paths

    snapshot_path.unlink(missing_ok=True)
    paths = [ingest_table(conn, table, output_dir) for table in BRONZE_TABLES]
    if sequence is not None:
        snapshot_path.write_text(json.dumps(snapshot))
    return paths


if __name__ == "__main__":
    from musicbrainz import db

    logging.basicConfig(level=logging.INFO)
    load_pipeline_env(__file__)
    output_dir = resolve_pipeline_path(__file__, require_env("BRONZE_OUTPUT_DIR"))
    with db.connect() as conn:
        run_bronze_ingestion(conn, output_dir)
