import logging
import shutil
from collections.abc import Sequence
from pathlib import Path

import polars as pl

from gold.ndjson_parts import write_ndjson_parts

logger = logging.getLogger(__name__)

SYNCED_DIRNAME = "_synced"
DELETED_NAME = "deleted.ndjson.gz"

_NEW = "_is_new"


def write_delta(
    frame: pl.DataFrame, key: str, name: str, output_dir: Path, rows_per_part: int, compared: Sequence[str]
) -> None:
    """Writes `<name>_snapshot.parquet` and, when a synced baseline exists, `<name>_delta/` against it.

    Only the `compared` columns (the ones grow stores) are snapshotted and compared, by value and column by column, so
    the delta never depends on a hash's stability and a column grow ignores never triggers a sync. The baseline is
    promoted by the caller that syncs to grow, only after grow merged successfully, so a failed sync makes the next
    run re-send its changes.
    """
    snapshot_path = output_dir / f"{name}_snapshot.parquet"
    tmp_path = snapshot_path.with_suffix(".parquet.tmp")
    snapshot = frame.select(key, *compared)
    snapshot.write_parquet(tmp_path)
    tmp_path.replace(snapshot_path)

    delta_dir = output_dir / f"{name}_delta"
    shutil.rmtree(delta_dir, ignore_errors=True)
    baseline_path = output_dir / SYNCED_DIRNAME / snapshot_path.name
    if not baseline_path.exists():
        logger.info("no synced baseline at %s, %s needs a full sync", baseline_path, name)
        return

    baseline = pl.scan_parquet(baseline_path)
    baseline_columns = set(baseline.collect_schema().names())
    if baseline_columns != set(snapshot.columns):
        logger.info("synced baseline of %s has different columns, it needs a full sync", name)
        return

    values = list(compared)
    current = snapshot.lazy().with_row_index()
    previous = baseline.rename({column: f"{column}_synced" for column in values}).with_columns(
        pl.lit(False).alias(_NEW)
    )
    changes = (
        current.join(previous, on=key, how="left", maintain_order="left")
        .select(
            "index",
            new=pl.col(_NEW).is_null(),
            **{column: pl.col(column).ne_missing(pl.col(f"{column}_synced")) for column in values},
        )
        .filter(pl.any_horizontal(pl.col("new"), *(pl.col(column) for column in values)))
        .collect(engine="streaming")
    )
    deleted = baseline.join(current.select(key), on=key, how="anti").select(key).collect(engine="streaming")

    changed_rows = changes.get_column("index").sort()
    write_ndjson_parts(frame, delta_dir, rows_per_part, changed_rows)
    deleted.write_ndjson(delta_dir / DELETED_NAME, compression="gzip")
    new_count = changes.get_column("new").sum()
    by_column = ", ".join(
        f"{column} {count}" for column in values if (count := changes.filter(~pl.col("new")).get_column(column).sum())
    )
    logger.info(
        "%s delta: %d upserted (new %d%s), %d deleted",
        name,
        changed_rows.len(),
        new_count,
        f", changed: {by_column}" if by_column else "",
        deleted.height,
    )
