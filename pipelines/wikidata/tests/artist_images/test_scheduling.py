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
