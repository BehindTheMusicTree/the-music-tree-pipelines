from datetime import UTC, datetime, timedelta

import polars as pl

from wikidata.artist_images.scheduling import due_qids


def test_old_work_is_reserved_and_retry_deferrals_are_respected() -> None:
    now = datetime(2026, 11, 5, tzinfo=UTC)
    registry = pl.DataFrame(
        {
            "item_id": ["Q1", "Q2", "Q3", "Q4", "Q5"],
            "claims_checked_at": [None, None, now - timedelta(days=10), now - timedelta(days=8), now],
            "next_check_at": [None, None, now, now, now + timedelta(days=1)],
            "retry_after_at": [None, now + timedelta(hours=1), None, None, None],
            "consecutive_failures": [0, 1, 0, 0, 0],
        },
        schema_overrides={
            "claims_checked_at": pl.Datetime("us", "UTC"),
            "next_check_at": pl.Datetime("us", "UTC"),
            "retry_after_at": pl.Datetime("us", "UTC"),
        },
    )
    assert due_qids(registry, now, 2) == ["Q3", "Q1"]
    assert due_qids(registry, now, 4) == ["Q3", "Q4", "Q1"]
    assert due_qids(registry.filter(pl.col("item_id") != "Q1"), now, 2) == ["Q3", "Q4"]


def test_retries_reserve_capacity_without_starving_regular_work():
    now = datetime(2026, 11, 5, tzinfo=UTC)
    registry = pl.DataFrame(
        {
            "item_id": [f"Q{i}" for i in range(1, 13)],
            "claims_checked_at": [None] * 12,
            "next_check_at": [None] * 12,
            "retry_after_at": [now - timedelta(hours=1)] * 8 + [None] * 4,
            "consecutive_failures": [1] * 8 + [0] * 4,
        },
        schema_overrides={
            "claims_checked_at": pl.Datetime("us", "UTC"),
            "next_check_at": pl.Datetime("us", "UTC"),
            "retry_after_at": pl.Datetime("us", "UTC"),
        },
    )
    selected = due_qids(registry, now, 4)
    assert len(set(selected) & {f"Q{i}" for i in range(1, 9)}) == 1
    assert len(set(selected) & {"Q9", "Q10", "Q11", "Q12"}) == 3
    assert len(due_qids(registry.head(8), now, 4)) == 4
    assert due_qids(registry, now, 1) == ["Q10"]
    assert due_qids(registry.head(1), now, 1) == ["Q1"]
