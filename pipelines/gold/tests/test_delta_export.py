import gzip
import json
from pathlib import Path

import polars as pl

from gold.delta_export import DELETED_NAME, SYNCED_DIRNAME, write_delta


def _read(path: Path) -> list[dict]:
    return [json.loads(line) for line in gzip.decompress(path.read_bytes()).decode().splitlines()]


def _frame(rows: dict[str, str]) -> pl.DataFrame:
    return pl.DataFrame({"id": list(rows), "title": list(rows.values())})


def _promote(output_dir: Path) -> None:
    (output_dir / SYNCED_DIRNAME).mkdir(exist_ok=True)
    (output_dir / "songs_hashes.parquet").replace(output_dir / SYNCED_DIRNAME / "songs_hashes.parquet")


def test_write_delta_without_baseline_writes_hashes_only(tmp_path: Path) -> None:
    write_delta(_frame({"a": "x"}), "id", "songs", tmp_path, 10)

    assert (tmp_path / "songs_hashes.parquet").exists()
    assert not (tmp_path / "songs_delta").exists()


def test_write_delta_emits_changed_added_and_deleted_rows(tmp_path: Path) -> None:
    write_delta(_frame({"a": "x", "b": "y", "c": "z"}), "id", "songs", tmp_path, 10)
    _promote(tmp_path)

    write_delta(_frame({"a": "x", "b": "changed", "d": "new"}), "id", "songs", tmp_path, 10)

    delta_dir = tmp_path / "songs_delta"
    upserts = _read(delta_dir / "part-00000.ndjson.gz")
    assert sorted(row["id"] for row in upserts) == ["b", "d"]
    assert _read(delta_dir / DELETED_NAME) == [{"id": "c"}]


def test_write_delta_is_empty_when_nothing_changed_and_clears_stale_delta(tmp_path: Path) -> None:
    frame = _frame({"a": "x"})
    write_delta(frame, "id", "songs", tmp_path, 10)
    _promote(tmp_path)
    (tmp_path / "songs_delta").mkdir()
    (tmp_path / "songs_delta" / "part-00007.ndjson.gz").write_bytes(b"stale")

    write_delta(frame, "id", "songs", tmp_path, 10)

    assert sorted(path.name for path in (tmp_path / "songs_delta").iterdir()) == [DELETED_NAME]
    assert _read(tmp_path / "songs_delta" / DELETED_NAME) == []
