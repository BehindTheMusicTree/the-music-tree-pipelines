"""Record per-identity work and retry delays without changing successful observations."""

from datetime import datetime, timedelta
from pathlib import Path

import polars as pl

from wikidata.artist_images.state import ImageState, checkpoint_state


def defer_failed(root: Path, state: ImageState, table: str, identities: set[str], now: datetime) -> ImageState:
    key = "item_id" if table == "registry" else "image_file"
    if not identities:
        return state
    cached = state.tables[table]
    rows = []
    existing = {row[key]: row for row in cached.filter(pl.col(key).is_in(sorted(identities))).to_dicts()}
    for identity in sorted(identities):
        row = existing.get(identity, dict.fromkeys(cached.columns))
        row[key] = identity
        failures = (row["consecutive_failures"] or 0) + 1
        row.update(
            last_attempt_at=now,
            consecutive_failures=failures,
            retry_after_at=now + timedelta(minutes=min(24 * 60, 15 * 2 ** min(failures - 1, 7))),
        )
        rows.append(row)
    return checkpoint_state(root, state, {table: pl.DataFrame(rows, schema=cached.schema)}, {table: sorted(identities)})
