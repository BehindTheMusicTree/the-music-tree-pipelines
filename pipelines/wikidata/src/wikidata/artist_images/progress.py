"""Record per-identity work and retry delays without changing successful observations."""

from datetime import datetime, timedelta
from pathlib import Path

import polars as pl

from wikidata.artist_images.state import ImageState, checkpoint_state


def defer_failed(
    root: Path,
    state: ImageState,
    table: str,
    identities: set[str],
    now: datetime,
    *,
    reasons: dict[str, str] | None = None,
) -> ImageState:
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
        if table == "registry":
            row["failure_reason"] = (
                reasons[identity] if reasons is not None else (row["failure_reason"] or "request_failed")
            )
            row["first_failure_at"] = row["first_failure_at"] or now
        rows.append(row)
    return checkpoint_state(root, state, {table: pl.DataFrame(rows, schema=cached.schema)}, {table: sorted(identities)})


def quarantine_summary(state: ImageState) -> dict:
    """Expose all outstanding invalid claims, including retries deferred this run."""
    failed = state.tables["registry"].filter(pl.col("failure_reason").str.starts_with("invalid_claims:"))
    oldest = failed["first_failure_at"].min()
    if oldest is not None and not isinstance(oldest, datetime):
        raise ValueError("Invalid quarantine failure time")
    return {
        "count": failed.height,
        "oldestFailureAt": oldest.isoformat() if oldest is not None else None,
        "sample": [
            {
                "itemId": row["item_id"],
                "reason": row["failure_reason"],
                "firstFailureAt": row["first_failure_at"].isoformat(),
                "lastAttemptAt": row["last_attempt_at"].isoformat(),
                "retryAfterAt": row["retry_after_at"].isoformat(),
                "consecutiveFailures": row["consecutive_failures"],
            }
            for row in failed.sort("first_failure_at", "item_id").head(20).iter_rows(named=True)
        ],
    }
