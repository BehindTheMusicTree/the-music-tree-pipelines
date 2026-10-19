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
