from pathlib import Path
from unittest.mock import MagicMock

import polars as pl
import pytest

from musicbrainz_to_the_music_tree_api import bronze_musicbrainz as bm


def test_ingest_table_rejects_unknown_table(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="Unknown bronze table"):
        bm.ingest_table(MagicMock(), "not_a_real_table", tmp_path)


@pytest.mark.parametrize("table", bm.BRONZE_TABLES)
def test_ingest_table_writes_parquet_for_each_bronze_table(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, table: str
) -> None:
    df = pl.DataFrame({"id": [1, 2]})
    read_database = MagicMock(return_value=df)
    monkeypatch.setattr(bm.pl, "read_database", read_database)

    output_dir = tmp_path / "bronze"
    result = bm.ingest_table(MagicMock(), table, output_dir)

    read_database.assert_called_once()
    query, conn = read_database.call_args.args
    assert query == f"SELECT * from musicbrainz.{table}"
    assert result == output_dir / f"{table}.parquet"
    assert result.exists()
    assert pl.read_parquet(result).equals(df)


def test_ingest_table_creates_output_dir(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setattr(bm.pl, "read_database", MagicMock(return_value=pl.DataFrame({"id": [1]})))

    output_dir = tmp_path / "does" / "not" / "exist"
    bm.ingest_table(MagicMock(), "recording", output_dir)

    assert output_dir.is_dir()


def test_run_bronze_ingestion_ingests_every_bronze_table(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    ingest_table = MagicMock(side_effect=lambda conn, table, output_dir: output_dir / f"{table}.parquet")
    monkeypatch.setattr(bm, "ingest_table", ingest_table)

    conn = MagicMock()
    result = bm.run_bronze_ingestion(conn, tmp_path)

    assert ingest_table.call_args_list == [((conn, table, tmp_path), {}) for table in bm.BRONZE_TABLES]
    assert result == [tmp_path / f"{table}.parquet" for table in bm.BRONZE_TABLES]
