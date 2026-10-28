import logging
import shutil
from pathlib import Path

import polars as pl

from gold.ndjson_parts import write_ndjson_parts

logger = logging.getLogger(__name__)

SYNCED_DIRNAME = "_synced"
DELETED_NAME = "deleted.ndjson.gz"


def write_delta(frame: pl.DataFrame, key: str, name: str, output_dir: Path, rows_per_part: int) -> None:
    """Writes `<name>_hashes.parquet` and, when a synced baseline exists, `<name>_delta/` against it.

    The baseline is promoted by the caller that syncs to grow, only after grow merged successfully, so a failed sync
    makes the next run re-send its changes.
    """
    hashes = frame.select(key, row_hash=pl.struct(pl.all().exclude(key)).hash())
    hashes_path = output_dir / f"{name}_hashes.parquet"
    tmp_path = hashes_path.with_suffix(".parquet.tmp")
    hashes.write_parquet(tmp_path)
    tmp_path.replace(hashes_path)

    delta_dir = output_dir / f"{name}_delta"
    shutil.rmtree(delta_dir, ignore_errors=True)
    baseline_path = output_dir / SYNCED_DIRNAME / hashes_path.name
    if not baseline_path.exists():
        logger.info("no synced baseline at %s, %s needs a full sync", baseline_path, name)
        return

    baseline = pl.read_parquet(baseline_path)
    changed = hashes.with_row_index().join(baseline, on=[key, "row_hash"], how="anti")
    changed_rows = changed.get_column("index").sort()
    deleted = baseline.join(hashes, on=key, how="anti").select(key)
    del baseline, changed
    write_ndjson_parts(frame, delta_dir, rows_per_part, changed_rows)
    deleted.write_ndjson(delta_dir / DELETED_NAME, compression="gzip")
    logger.info("%s delta: %d upserted, %d deleted", name, changed_rows.len(), deleted.height)
