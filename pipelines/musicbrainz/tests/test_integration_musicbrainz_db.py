from pathlib import Path

import adbc_driver_postgresql.dbapi as adbc
import polars as pl
import pytest

from musicbrainz.ingest import BRONZE_TABLES, ingest_table, read_column_types, read_replication_sequence, table_query


@pytest.mark.integration
def test_artist_table_has_sample_rows(mb_conn: adbc.Connection) -> None:
    with mb_conn.cursor() as cur:
        cur.execute("select 1 from musicbrainz.artist limit 1")
        assert cur.fetchone() is not None


@pytest.mark.integration
def test_artist_credit_table_has_sample_rows(mb_conn: adbc.Connection) -> None:
    with mb_conn.cursor() as cur:
        cur.execute("select 1 from musicbrainz.artist_credit limit 1")
        assert cur.fetchone() is not None


@pytest.mark.integration
def test_artist_credit_name_table_has_sample_rows(mb_conn: adbc.Connection) -> None:
    with mb_conn.cursor() as cur:
        cur.execute("select 1 from musicbrainz.artist_credit_name limit 1")
        assert cur.fetchone() is not None


@pytest.mark.integration
def test_read_replication_sequence_reads_replication_control(mb_conn: adbc.Connection) -> None:
    sequence = read_replication_sequence(mb_conn)

    assert sequence is None or isinstance(sequence, int)


@pytest.mark.integration
def test_every_bronze_table_has_supported_column_types(mb_conn: adbc.Connection) -> None:
    columns = read_column_types(mb_conn)

    for table in BRONZE_TABLES:
        table_query(table, columns[table])


@pytest.mark.integration
def test_ingest_table_writes_bronze_dtypes(mb_conn: adbc.Connection, tmp_path: Path) -> None:
    columns = read_column_types(mb_conn)

    path = ingest_table(mb_conn, "artist", table_query("artist", columns["artist"]), tmp_path)

    schema = pl.read_parquet_schema(path)
    assert schema["id"] == pl.Int64
    assert schema["type"] == pl.Int64
    assert schema["gid"] == pl.String
    assert schema["last_updated"] == pl.Datetime("us", "UTC")
