import shutil
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import polars as pl


def write_ndjson_parts(frame: pl.DataFrame, parts_dir: Path, rows_per_part: int, rows: pl.Series | None = None) -> int:
    """Writes `frame` (or only the row positions in `rows`) as gzip NDJSON parts, gathering one part at a time."""
    # Wiped first so a smaller run never leaves a previous run's trailing parts behind.
    shutil.rmtree(parts_dir, ignore_errors=True)
    parts_dir.mkdir(parents=True)
    total = frame.height if rows is None else rows.len()
    part_count = -(-total // rows_per_part)

    def write(index: int) -> None:
        start = index * rows_per_part
        part = frame.slice(start, rows_per_part) if rows is None else frame[rows.slice(start, rows_per_part)]
        part.write_ndjson(parts_dir / f"part-{index:05d}.ndjson.gz", compression="gzip")

    with ThreadPoolExecutor() as pool:
        list(pool.map(write, range(part_count)))
    return part_count
