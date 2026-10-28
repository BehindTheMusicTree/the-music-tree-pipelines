import gzip
from pathlib import Path

import polars as pl

from gold.ndjson_parts import write_ndjson_parts


def test_write_ndjson_parts_splits_in_order_and_clears_stale_parts(tmp_path: Path) -> None:
    parts_dir = tmp_path / "parts"
    parts_dir.mkdir()
    (parts_dir / "part-00009.ndjson.gz").write_bytes(b"stale")

    count = write_ndjson_parts(pl.DataFrame({"id": [1, 2, 3, 4, 5]}), parts_dir, 2)

    assert count == 3
    names = sorted(path.name for path in parts_dir.iterdir())
    assert names == ["part-00000.ndjson.gz", "part-00001.ndjson.gz", "part-00002.ndjson.gz"]
    last = gzip.decompress((parts_dir / "part-00002.ndjson.gz").read_bytes()).decode()
    assert last == '{"id":5}\n'


def test_write_ndjson_parts_writes_only_the_given_rows(tmp_path: Path) -> None:
    frame = pl.DataFrame({"id": [10, 11, 12, 13, 14]})

    count = write_ndjson_parts(frame, tmp_path / "parts", 2, pl.Series([0, 2, 4], dtype=pl.UInt32))

    assert count == 2
    first = gzip.decompress((tmp_path / "parts" / "part-00000.ndjson.gz").read_bytes()).decode()
    last = gzip.decompress((tmp_path / "parts" / "part-00001.ndjson.gz").read_bytes()).decode()
    assert first == '{"id":10}\n{"id":12}\n'
    assert last == '{"id":14}\n'
