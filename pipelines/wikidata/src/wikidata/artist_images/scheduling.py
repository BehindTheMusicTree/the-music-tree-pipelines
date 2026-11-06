"""Stable bounded queues that reserve capacity for overdue existing work."""

from datetime import datetime

import polars as pl


def due_qids(registry: pl.DataFrame, now: datetime, capacity: int) -> list[str]:
    if type(capacity) is not int or capacity < 1 or capacity > 1000:
        raise ValueError("QID capacity must be between 1 and 1000")
    if now.tzinfo is None:
        raise ValueError("Queue time must be timezone aware")
    due = registry.filter(
        (pl.col("next_check_at").is_null() | (pl.col("next_check_at") <= now))
        & (pl.col("retry_after_at").is_null() | (pl.col("retry_after_at") <= now))
    )
    retrying = due.filter(pl.col("consecutive_failures") > 0).sort("retry_after_at", "item_id")
    due = due.filter(pl.col("consecutive_failures") == 0)
    retry_ids = retrying["item_id"].to_list()
    retry_capacity = min(len(retry_ids), max(1, capacity // 4)) if capacity > 1 else 0
    regular_capacity = capacity - retry_capacity
    existing = due.filter(pl.col("claims_checked_at").is_not_null()).sort("claims_checked_at", "item_id")
    new = due.filter(pl.col("claims_checked_at").is_null()).sort("item_id")
    existing_ids = existing["item_id"].to_list()
    new_ids = new["item_id"].to_list()
    reserved = (regular_capacity + 1) // 2
    selected = existing_ids[:reserved]
    selected.extend(new_ids[: regular_capacity - len(selected)])
    selected.extend(existing_ids[reserved : reserved + regular_capacity - len(selected)])
    selected.extend(retry_ids[: capacity - len(selected)])
    return selected
