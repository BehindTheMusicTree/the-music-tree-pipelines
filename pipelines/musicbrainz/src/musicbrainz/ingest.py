import json
import logging
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

import adbc_driver_postgresql.dbapi as adbc
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

# Arrow batch size the driver aims for, rather than loading a whole table into memory at once — the `recording`
# table alone is tens of millions of rows.
BATCH_SIZE_HINT_BYTES = 64 << 20

# Tables extracted at once, each on its own connection.
INGEST_WORKERS = 4

# The two largest tables: Silver reads only these columns, and `SELECT *` would stream (and compress) the rest.
PROJECTED_COLUMNS = {
    "recording": ("id", "gid", "name", "artist_credit"),
    "url": ("id", "url"),
}

# Records which mirror state and queries produced the Parquet files, so a run against an unchanged mirror skips
# re-extracting them.
SNAPSHOT_FILENAME = "_snapshot.json"


# Postgres types ingested as is, and those cast so Bronze keeps one dtype per kind: integers as int64, uuids as
# strings (Arrow has no native uuid type).
PASSTHROUGH_TYPES = {"bigint", "boolean", "character", "character varying", "text", "timestamp with time zone"}
CASTS = {"smallint": "bigint", "integer": "bigint", "uuid": "text"}

Columns = list[tuple[str, str]]


def read_column_types(conn: adbc.Connection) -> dict[str, Columns]:
    with conn.cursor() as cur:
        cur.execute(
            "SELECT table_name, column_name, data_type FROM information_schema.columns"
            " WHERE table_schema = 'musicbrainz' ORDER BY table_name, ordinal_position"
        )
        rows = cur.fetchall()
    columns: dict[str, Columns] = {table: [] for table in BRONZE_TABLES}
    for table, column, data_type in rows:
        if table in columns:
            columns[table].append((column, data_type))
    missing = [table for table, table_columns in columns.items() if not table_columns]
    if missing:
        raise ValueError(f"bronze tables missing from the musicbrainz schema: {', '.join(missing)}")
    return columns


def column_expression(column: str, data_type: str) -> str:
    if data_type in PASSTHROUGH_TYPES:
        return f'"{column}"'
    if data_type in CASTS:
        return f'"{column}"::{CASTS[data_type]} AS "{column}"'
    raise ValueError(f"unsupported Postgres type {data_type!r} for column {column!r}")


def table_query(table: str, columns: Columns) -> str:
    data_types = dict(columns)
    names = PROJECTED_COLUMNS.get(table, tuple(data_types))
    return f"SELECT {', '.join(column_expression(name, data_types[name]) for name in names)} FROM musicbrainz.{table}"


def read_replication_sequence(conn: adbc.Connection) -> int | None:
    """The mirror's replication sequence; None on a standalone database (no or a NULL row), which never replicates."""
    with conn.cursor() as cur:
        cur.execute("SELECT current_replication_sequence FROM musicbrainz.replication_control")
        rows = cur.fetchall()
    if len(rows) > 1:
        raise ValueError(f"replication_control has {len(rows)} rows, expected at most 1")
    return rows[0][0] if rows else None


def ingest_table(conn: adbc.Connection, table: str, query: str, output_dir: Path) -> Path:
    if table not in BRONZE_TABLES:
        raise ValueError(f"Unknown bronze table: {table!r}. Expected one of: {', '.join(BRONZE_TABLES)}")

    logger.info("ingesting table %s", table)
    output_dir.mkdir(parents=True, exist_ok=True)
    output_path = output_dir / f"{table}.parquet"
    # Written beside the target then renamed, so a run killed mid-write keeps the previous complete file.
    tmp_path = output_dir / f"{table}.parquet.tmp"

    total_rows = 0
    with conn.cursor() as cur:
        cur.adbc_statement.set_options(**{"adbc.postgresql.batch_size_hint_bytes": str(BATCH_SIZE_HINT_BYTES)})
        cur.execute(query)
        reader = cur.fetch_record_batch()
        with pq.ParquetWriter(tmp_path, reader.schema) as writer:
            for batch in reader:
                writer.write_batch(batch)
                total_rows += batch.num_rows

    if total_rows == 0:
        tmp_path.unlink()
        raise ValueError(f"{table} is empty")
    tmp_path.replace(output_path)

    logger.info("wrote %d rows to %s", total_rows, output_path)
    return output_path


def _ingest_on_own_connection(connect: Callable[[], adbc.Connection], table: str, query: str, output_dir: Path) -> Path:
    with connect() as conn:
        return ingest_table(conn, table, query, output_dir)


def run_bronze_ingestion(connect: Callable[[], adbc.Connection], output_dir: Path) -> list[Path]:
    with connect() as conn:
        sequence = read_replication_sequence(conn)
        columns = read_column_types(conn)
    queries = {table: table_query(table, columns[table]) for table in BRONZE_TABLES}
    snapshot = {"replication_sequence": sequence, "queries": queries}
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
    with ThreadPoolExecutor(max_workers=INGEST_WORKERS) as pool:
        futures = [
            pool.submit(_ingest_on_own_connection, connect, table, queries[table], output_dir)
            for table in BRONZE_TABLES
        ]
        try:
            for future in as_completed(futures):
                future.result()
        except BaseException:
            pool.shutdown(cancel_futures=True)
            raise
    paths = [future.result() for future in futures]
    if sequence is not None:
        snapshot_path.write_text(json.dumps(snapshot))
    return paths


if __name__ == "__main__":
    from musicbrainz import db

    logging.basicConfig(level=logging.INFO)
    load_pipeline_env(__file__)
    output_dir = resolve_pipeline_path(__file__, require_env("BRONZE_OUTPUT_DIR"))
    run_bronze_ingestion(db.connect, output_dir)
