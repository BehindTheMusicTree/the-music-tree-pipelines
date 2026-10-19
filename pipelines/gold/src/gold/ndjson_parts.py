import shutil
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import polars as pl


def write_ndjson_parts(frame: pl.DataFrame, parts_dir: Path, rows_per_part: int) -> int:
    # Wiped first so a smaller run never leaves a previous run's trailing parts behind.
    shutil.rmtree(parts_dir, ignore_errors=True)
    parts_dir.mkdir(parents=True)

    def write(indexed_part: tuple[int, pl.DataFrame]) -> None:
        index, part = indexed_part
        part.write_ndjson(parts_dir / f"part-{index:05d}.ndjson.gz", compression="gzip")

    with ThreadPoolExecutor() as pool:
        list(pool.map(write, enumerate(frame.iter_slices(rows_per_part))))
    return -(-frame.height // rows_per_part)
