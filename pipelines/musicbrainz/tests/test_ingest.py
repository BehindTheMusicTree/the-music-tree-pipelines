from pathlib import Path
from unittest.mock import MagicMock

import polars as pl
import pytest

from musicbrainz import ingest as bm


def test_ingest_table_rejects_unknown_table(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="Unknown bronze table"):
        bm.ingest_table(MagicMock(), "not_a_real_table", tmp_path)


@pytest.mark.parametrize("table", bm.BRONZE_TABLES)
def test_ingest_table_writes_parquet_for_each_bronze_table(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, table: str
) -> None:
    batches = [pl.DataFrame({"id": [1, 2]}), pl.DataFrame({"id": [3]})]
    read_database = MagicMock(return_value=iter(batches))
    monkeypatch.setattr(bm.pl, "read_database", read_database)

    output_dir = tmp_path / "bronze"
    result = bm.ingest_table(MagicMock(), table, output_dir)

    read_database.assert_called_once()
    query = read_database.call_args.args[0]
    columns = ", ".join(bm.PROJECTED_COLUMNS.get(table, ("*",)))
    assert query == f"SELECT {columns} from musicbrainz.{table}"
    assert read_database.call_args.kwargs == {
        "iter_batches": True,
        "batch_size": bm.BATCH_SIZE,
        "infer_schema_length": bm.BATCH_SIZE,
    }
    assert result == output_dir / f"{table}.parquet"
    assert result.exists()
    assert pl.read_parquet(result).equals(pl.concat(batches))


def test_ingest_table_creates_output_dir(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setattr(bm.pl, "read_database", MagicMock(return_value=iter([pl.DataFrame({"id": [1]})])))

    output_dir = tmp_path / "does" / "not" / "exist"
    bm.ingest_table(MagicMock(), "recording", output_dir)

    assert output_dir.is_dir()


def test_ingest_table_keeps_previous_file_when_interrupted(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    def batches():
        yield pl.DataFrame({"id": [1]})
        raise KeyboardInterrupt

    monkeypatch.setattr(bm.pl, "read_database", MagicMock(return_value=batches()))
    previous = tmp_path / "recording.parquet"
    pl.DataFrame({"id": [42]}).write_parquet(previous)

    with pytest.raises(KeyboardInterrupt):
        bm.ingest_table(MagicMock(), "recording", tmp_path)

    assert pl.read_parquet(previous).equals(pl.DataFrame({"id": [42]}))


def test_ingest_table_raises_on_empty_results(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setattr(bm.pl, "read_database", MagicMock(return_value=iter([])))

    with pytest.raises(ValueError, match="is empty"):
        bm.ingest_table(MagicMock(), "recording", tmp_path)


def _fake_ingest_table(conn: object, table: str, output_dir: Path) -> Path:
    path = output_dir / f"{table}.parquet"
    path.touch()
    return path


def _mock_ingestion(monkeypatch: pytest.MonkeyPatch, sequence: int | None) -> MagicMock:
    ingest_table = MagicMock(side_effect=_fake_ingest_table)
    monkeypatch.setattr(bm, "ingest_table", ingest_table)
    monkeypatch.setattr(bm, "read_replication_sequence", MagicMock(return_value=sequence))
    return ingest_table


def test_run_bronze_ingestion_ingests_every_bronze_table(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    ingest_table = _mock_ingestion(monkeypatch, 187271)

    conn = MagicMock()
    result = bm.run_bronze_ingestion(conn, tmp_path)

    assert ingest_table.call_args_list == [((conn, table, tmp_path), {}) for table in bm.BRONZE_TABLES]
    assert result == [tmp_path / f"{table}.parquet" for table in bm.BRONZE_TABLES]


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
    monkeypatch.setitem(bm.PROJECTED_COLUMNS, "recording", ("id",))

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


def test_read_replication_sequence_raises_without_exactly_one_row() -> None:
    conn = MagicMock()
    conn.cursor.return_value.__enter__.return_value.fetchall.return_value = []

    with pytest.raises(ValueError, match="replication_control has 0 rows"):
        bm.read_replication_sequence(conn)
