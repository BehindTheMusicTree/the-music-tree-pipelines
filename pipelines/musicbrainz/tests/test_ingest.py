from pathlib import Path
from unittest.mock import MagicMock

import polars as pl
import pyarrow as pa
import pytest

from musicbrainz import ingest as bm


def _conn_returning(batches: list[pa.RecordBatch] | object, schema: pa.Schema | None = None) -> MagicMock:
    conn = MagicMock()
    cur = conn.cursor.return_value.__enter__.return_value
    if isinstance(batches, list):
        cur.fetch_record_batch.return_value = pa.RecordBatchReader.from_batches(schema or batches[0].schema, batches)
    else:
        cur.fetch_record_batch.return_value = batches
    return conn


def _id_batch(*ids: int) -> pa.RecordBatch:
    return pa.record_batch({"id": pa.array(ids, pa.int64())})


def test_ingest_table_rejects_unknown_table(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="Unknown bronze table"):
        bm.ingest_table(MagicMock(), "not_a_real_table", "SELECT 1", tmp_path)


@pytest.mark.parametrize("table", bm.BRONZE_TABLES)
def test_ingest_table_writes_parquet_for_each_bronze_table(tmp_path: Path, table: str) -> None:
    conn = _conn_returning([_id_batch(1, 2), _id_batch(3)])

    output_dir = tmp_path / "bronze"
    result = bm.ingest_table(conn, table, "SELECT id FROM t", output_dir)

    cur = conn.cursor.return_value.__enter__.return_value
    cur.execute.assert_called_once_with("SELECT id FROM t")
    cur.adbc_statement.set_options.assert_called_once_with(
        **{"adbc.postgresql.batch_size_hint_bytes": str(bm.BATCH_SIZE_HINT_BYTES)}
    )
    assert result == output_dir / f"{table}.parquet"
    assert pl.read_parquet(result).equals(pl.DataFrame({"id": [1, 2, 3]}))
    assert not (output_dir / f"{table}.parquet.tmp").exists()


def test_ingest_table_keeps_previous_file_when_interrupted(tmp_path: Path) -> None:
    schema = pa.schema({"id": pa.int64()})

    def batches():
        yield _id_batch(1)
        raise KeyboardInterrupt

    conn = _conn_returning(pa.RecordBatchReader.from_batches(schema, batches()))
    previous = tmp_path / "recording.parquet"
    pl.DataFrame({"id": [42]}).write_parquet(previous)

    with pytest.raises(KeyboardInterrupt):
        bm.ingest_table(conn, "recording", "SELECT id FROM t", tmp_path)

    assert pl.read_parquet(previous).equals(pl.DataFrame({"id": [42]}))


def test_ingest_table_raises_on_empty_results(tmp_path: Path) -> None:
    conn = _conn_returning([], pa.schema({"id": pa.int64()}))

    with pytest.raises(ValueError, match="is empty"):
        bm.ingest_table(conn, "recording", "SELECT id FROM t", tmp_path)

    assert list(tmp_path.iterdir()) == []


def test_column_expression_keeps_passthrough_types() -> None:
    assert bm.column_expression("name", "text") == '"name"'


@pytest.mark.parametrize(("data_type", "cast"), [("smallint", "bigint"), ("integer", "bigint"), ("uuid", "text")])
def test_column_expression_casts_to_bronze_dtype(data_type: str, cast: str) -> None:
    assert bm.column_expression("gid", data_type) == f'"gid"::{cast} AS "gid"'


def test_column_expression_raises_on_unsupported_type() -> None:
    with pytest.raises(ValueError, match="unsupported Postgres type 'jsonb' for column 'data'"):
        bm.column_expression("data", "jsonb")


def test_table_query_selects_every_column() -> None:
    query = bm.table_query("artist", [("id", "integer"), ("name", "character varying")])

    assert query == 'SELECT "id"::bigint AS "id", "name" FROM musicbrainz.artist'


def test_table_query_keeps_only_projected_columns() -> None:
    query = bm.table_query("url", [("id", "integer"), ("gid", "uuid"), ("url", "text"), ("edits_pending", "integer")])

    assert query == 'SELECT "id"::bigint AS "id", "url" FROM musicbrainz.url'


def _cursor_rows(rows: list[tuple]) -> MagicMock:
    conn = MagicMock()
    conn.cursor.return_value.__enter__.return_value.fetchall.return_value = rows
    return conn


def test_read_column_types_groups_bronze_columns_by_table() -> None:
    rows = [(table, "id", "integer") for table in bm.BRONZE_TABLES] + [
        ("artist", "gid", "uuid"),
        ("editor", "id", "integer"),
    ]

    columns = bm.read_column_types(_cursor_rows(rows))

    assert set(columns) == set(bm.BRONZE_TABLES)
    assert columns["artist"] == [("id", "integer"), ("gid", "uuid")]


def test_read_column_types_raises_on_missing_table() -> None:
    rows = [(table, "id", "integer") for table in bm.BRONZE_TABLES if table != "genre"]

    with pytest.raises(ValueError, match="missing from the musicbrainz schema: genre"):
        bm.read_column_types(_cursor_rows(rows))


def _fake_ingest_table(conn: object, table: str, query: str, output_dir: Path) -> Path:
    path = output_dir / f"{table}.parquet"
    path.touch()
    return path


def _fake_column_types(data_type: str) -> dict[str, bm.Columns]:
    return {
        table: [(column, data_type) for column in bm.PROJECTED_COLUMNS.get(table, ("id",))]
        for table in bm.BRONZE_TABLES
    }


def _mock_ingestion(monkeypatch: pytest.MonkeyPatch, sequence: int | None) -> MagicMock:
    ingest_table = MagicMock(side_effect=_fake_ingest_table)
    monkeypatch.setattr(bm, "ingest_table", ingest_table)
    monkeypatch.setattr(bm, "read_replication_sequence", MagicMock(return_value=sequence))
    monkeypatch.setattr(bm, "read_column_types", MagicMock(return_value=_fake_column_types("integer")))
    return ingest_table


def test_run_bronze_ingestion_ingests_every_bronze_table(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    ingest_table = _mock_ingestion(monkeypatch, 187271)

    connect = MagicMock()
    result = bm.run_bronze_ingestion(connect, tmp_path)

    conn = connect.return_value.__enter__.return_value
    columns = _fake_column_types("integer")
    expected = [(conn, table, bm.table_query(table, columns[table]), tmp_path) for table in bm.BRONZE_TABLES]
    assert sorted(call.args for call in ingest_table.call_args_list) == sorted(expected)
    assert connect.call_count == 1 + len(bm.BRONZE_TABLES)
    assert result == [tmp_path / f"{table}.parquet" for table in bm.BRONZE_TABLES]


def test_run_bronze_ingestion_raises_when_a_table_fails(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    ingest_table = _mock_ingestion(monkeypatch, 187271)
    ingest_table.side_effect = ValueError("genre is empty")

    with pytest.raises(ValueError, match="genre is empty"):
        bm.run_bronze_ingestion(MagicMock(), tmp_path)

    assert not (tmp_path / bm.SNAPSHOT_FILENAME).exists()


def test_run_bronze_ingestion_skips_unchanged_mirror(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    ingest_table = _mock_ingestion(monkeypatch, 187271)
    bm.run_bronze_ingestion(MagicMock(), tmp_path)
    ingest_table.reset_mock()

    result = bm.run_bronze_ingestion(MagicMock(), tmp_path)

    ingest_table.assert_not_called()
    assert result == [tmp_path / f"{table}.parquet" for table in bm.BRONZE_TABLES]


def test_run_bronze_ingestion_reingests_after_replication(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    _mock_ingestion(monkeypatch, 187271)
    bm.run_bronze_ingestion(MagicMock(), tmp_path)
    ingest_table = _mock_ingestion(monkeypatch, 187272)

    bm.run_bronze_ingestion(MagicMock(), tmp_path)

    assert ingest_table.call_count == len(bm.BRONZE_TABLES)


def test_run_bronze_ingestion_reingests_when_a_table_is_missing(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    ingest_table = _mock_ingestion(monkeypatch, 187271)
    bm.run_bronze_ingestion(MagicMock(), tmp_path)
    ingest_table.reset_mock()
    (tmp_path / "recording.parquet").unlink()

    bm.run_bronze_ingestion(MagicMock(), tmp_path)

    assert ingest_table.call_count == len(bm.BRONZE_TABLES)


def test_run_bronze_ingestion_reingests_when_a_query_changes(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    ingest_table = _mock_ingestion(monkeypatch, 187271)
    bm.run_bronze_ingestion(MagicMock(), tmp_path)
    ingest_table.reset_mock()
    monkeypatch.setattr(bm, "read_column_types", MagicMock(return_value=_fake_column_types("bigint")))

    bm.run_bronze_ingestion(MagicMock(), tmp_path)

    assert ingest_table.call_count == len(bm.BRONZE_TABLES)


def test_run_bronze_ingestion_always_ingests_standalone_db(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    ingest_table = _mock_ingestion(monkeypatch, None)
    bm.run_bronze_ingestion(MagicMock(), tmp_path)

    bm.run_bronze_ingestion(MagicMock(), tmp_path)

    assert ingest_table.call_count == 2 * len(bm.BRONZE_TABLES)
    assert not (tmp_path / bm.SNAPSHOT_FILENAME).exists()


def test_run_bronze_ingestion_forgets_snapshot_when_interrupted(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    _mock_ingestion(monkeypatch, 187271)
    bm.run_bronze_ingestion(MagicMock(), tmp_path)
    ingest_table = _mock_ingestion(monkeypatch, 187272)
    ingest_table.side_effect = KeyboardInterrupt

    with pytest.raises(KeyboardInterrupt):
        bm.run_bronze_ingestion(MagicMock(), tmp_path)

    assert not (tmp_path / bm.SNAPSHOT_FILENAME).exists()


def test_read_replication_sequence_returns_current_sequence() -> None:
    conn = MagicMock()
    conn.cursor.return_value.__enter__.return_value.fetchall.return_value = [(187271,)]

    assert bm.read_replication_sequence(conn) == 187271


def test_read_replication_sequence_is_none_on_standalone_db() -> None:
    conn = MagicMock()
    conn.cursor.return_value.__enter__.return_value.fetchall.return_value = []

    assert bm.read_replication_sequence(conn) is None


def test_read_replication_sequence_raises_on_several_rows() -> None:
    conn = MagicMock()
    conn.cursor.return_value.__enter__.return_value.fetchall.return_value = [(1,), (2,)]

    with pytest.raises(ValueError, match="replication_control has 2 rows"):
        bm.read_replication_sequence(conn)
