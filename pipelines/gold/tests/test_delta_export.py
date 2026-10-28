import gzip
import json
import logging
from pathlib import Path

import polars as pl
import pytest

from gold.delta_export import DELETED_NAME, SYNCED_DIRNAME, write_delta


def _read(path: Path) -> list[dict]:
    return [json.loads(line) for line in gzip.decompress(path.read_bytes()).decode().splitlines()]


def _frame(rows: dict[str, str]) -> pl.DataFrame:
    return pl.DataFrame({"id": list(rows), "title": list(rows.values())})


def _promote(output_dir: Path) -> None:
    (output_dir / SYNCED_DIRNAME).mkdir(exist_ok=True)
    (output_dir / "songs_snapshot.parquet").replace(output_dir / SYNCED_DIRNAME / "songs_snapshot.parquet")


def test_write_delta_without_baseline_writes_snapshot_only(tmp_path: Path) -> None:
    write_delta(_frame({"a": "x"}), "id", "songs", tmp_path, 10, ["title"])

    assert (tmp_path / "songs_snapshot.parquet").exists()
    assert not (tmp_path / "songs_delta").exists()


def test_write_delta_emits_changed_added_and_deleted_rows(tmp_path: Path) -> None:
    write_delta(_frame({"a": "x", "b": "y", "c": "z"}), "id", "songs", tmp_path, 10, ["title"])
    _promote(tmp_path)

    write_delta(_frame({"a": "x", "b": "changed", "d": "new"}), "id", "songs", tmp_path, 10, ["title"])

    delta_dir = tmp_path / "songs_delta"
    upserts = _read(delta_dir / "part-00000.ndjson.gz")
    assert sorted(row["id"] for row in upserts) == ["b", "d"]
    assert _read(delta_dir / DELETED_NAME) == [{"id": "c"}]


def test_write_delta_is_empty_when_nothing_changed_and_clears_stale_delta(tmp_path: Path) -> None:
    frame = _frame({"a": "x"})
    write_delta(frame, "id", "songs", tmp_path, 10, ["title"])
    _promote(tmp_path)
    (tmp_path / "songs_delta").mkdir()
    (tmp_path / "songs_delta" / "part-00007.ndjson.gz").write_bytes(b"stale")

    write_delta(frame, "id", "songs", tmp_path, 10, ["title"])

    assert sorted(path.name for path in (tmp_path / "songs_delta").iterdir()) == [DELETED_NAME]
    assert _read(tmp_path / "songs_delta" / DELETED_NAME) == []


def test_write_delta_needs_a_full_sync_when_a_column_is_added(tmp_path: Path) -> None:
    rows = {str(n): f"t{n}" for n in range(5)}
    write_delta(_frame(rows), "id", "songs", tmp_path, 2, ["title"])
    _promote(tmp_path)

    write_delta(_frame(rows).with_columns(genres=pl.lit(["rock"])), "id", "songs", tmp_path, 2, ["title", "genres"])

    assert not (tmp_path / "songs_delta").exists()


def test_write_delta_ignores_dtype_and_column_order(tmp_path: Path) -> None:
    write_delta(
        pl.DataFrame({"id": ["a"], "n": pl.Series([1], dtype=pl.Int32), "t": ["x"]}),
        "id",
        "songs",
        tmp_path,
        10,
        ["n", "t"],
    )
    _promote(tmp_path)

    write_delta(
        pl.DataFrame({"t": ["x"], "n": pl.Series([1], dtype=pl.Int64), "id": ["a"]}),
        "id",
        "songs",
        tmp_path,
        10,
        ["n", "t"],
    )

    assert _read(tmp_path / "songs_delta" / DELETED_NAME) == []
    assert [path.name for path in (tmp_path / "songs_delta").iterdir()] == [DELETED_NAME]


def test_write_delta_treats_null_to_value_and_value_to_null_as_changes(tmp_path: Path) -> None:
    write_delta(pl.DataFrame({"id": ["a", "b", "c"], "v": [None, "x", None]}), "id", "songs", tmp_path, 10, ["v"])
    _promote(tmp_path)

    write_delta(pl.DataFrame({"id": ["a", "b", "c"], "v": ["set", None, None]}), "id", "songs", tmp_path, 10, ["v"])

    upserts = _read(tmp_path / "songs_delta" / "part-00000.ndjson.gz")
    assert sorted(row["id"] for row in upserts) == ["a", "b"]


def test_write_delta_logs_the_changed_columns(tmp_path: Path, caplog: pytest.LogCaptureFixture) -> None:
    write_delta(pl.DataFrame({"id": ["a", "b"], "t": ["x", "y"], "v": [1, 2]}), "id", "songs", tmp_path, 10, ["t", "v"])
    _promote(tmp_path)

    with caplog.at_level(logging.INFO, logger="gold.delta_export"):
        write_delta(
            pl.DataFrame({"id": ["a", "b", "c"], "t": ["z", "y", "n"], "v": [1, 2, 3]}),
            "id",
            "songs",
            tmp_path,
            10,
            ["t", "v"],
        )

    assert "songs delta: 2 upserted (new 1, changed: t 1), 0 deleted" in caplog.text
