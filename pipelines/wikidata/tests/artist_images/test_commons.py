import asyncio

import httpx
import pytest

from wikidata.artist_images.commons import acquire_metadata
from wikidata.artist_images.transport import RequestBudget


def acquire(responses: list[dict]) -> tuple[list[dict], RequestBudget]:
    budget = RequestBudget({"wdqs": 8, "entities": 8, "commons": 8}, 60)

    async def run() -> list[dict]:
        async with httpx.AsyncClient(
            transport=httpx.MockTransport(lambda _: httpx.Response(200, json=responses.pop(0)))
        ) as client:
            return await acquire_metadata(client, budget, ["a.jpg"])

    return asyncio.run(run()), budget


def test_confirmed_missing_is_a_cacheable_observation() -> None:
    rows, budget = acquire([{"query": {"pages": [{"title": "File:a.jpg", "missing": True}]}}])
    assert rows == [{"image_file": "a.jpg", "result_status": "missing", "canonical_title": "File:a.jpg"}]
    assert budget.attempts["commons"] == 1


def test_omitted_page_is_not_a_missing_file() -> None:
    with pytest.raises(ValueError, match="omitted"):
        acquire([{"query": {"pages": []}}])


def test_all_continuation_pages_are_required_before_accepting_missing_result() -> None:
    with pytest.raises(ValueError, match="Incomplete"):
        acquire(
            [
                {
                    "query": {"pages": [{"title": "File:a.jpg", "missing": True}]},
                    "continue": {"iicontinue": "next", "continue": "-||"},
                },
                {"error": {"code": "badcontinue"}},
            ]
        )


def test_redirect_mapping_is_preserved() -> None:
    rows, _ = acquire(
        [
            {
                "query": {
                    "normalized": [{"from": "File:a.jpg", "to": "File:A.jpg"}],
                    "redirects": [{"from": "File:A.jpg", "to": "File:B.jpg"}],
                    "pages": [{"title": "File:B.jpg", "missing": True}],
                }
            }
        ]
    )
    assert rows[0]["canonical_title"] == "File:B.jpg"
    assert rows[0]["image_file"] == "a.jpg"


def test_repeated_continuation_is_rejected() -> None:
    response = {"query": {"pages": []}, "continue": {"iicontinue": "next"}}
    with pytest.raises(ValueError, match="Repeated"):
        acquire([response, response])


def test_refresh_commits_confirmed_missing_and_retains_unreferenced_cache(tmp_path) -> None:
    from datetime import UTC, datetime, timedelta

    import polars as pl

    from wikidata.artist_images.commons import refresh_metadata_queue
    from wikidata.artist_images.state import ImageState, compact_state, load_state
    from wikidata.silver.commons_image_metadata import _SCHEMA

    now = datetime(2026, 11, 5, tzinfo=UTC)
    schema = {
        **_SCHEMA,
        "result_status": pl.String,
        "canonical_title": pl.String,
        "last_attempt_at": pl.Datetime("us", "UTC"),
        "consecutive_failures": pl.UInt32,
        "retry_after_at": pl.Datetime("us", "UTC"),
    }
    metadata = pl.DataFrame(
        [
            {"image_file": "a.jpg", "checked_at": now - timedelta(days=40), "result_status": "observed"},
            {"image_file": "orphan.jpg", "checked_at": now, "result_status": "observed"},
        ],
        schema=schema,
    )
    state = compact_state(
        tmp_path,
        ImageState(
            "",
            0,
            {
                "registry": pl.DataFrame({"item_id": ["Q1"]}),
                "claims": pl.DataFrame({"item_id": ["Q1", "Q1"], "image_file": ["a.jpg", "a.jpg"]}),
                "commons": metadata,
            },
            {},
        ),
    )
    budget = RequestBudget({"wdqs": 8, "entities": 8, "commons": 8}, 60)

    async def run() -> None:
        body = {"query": {"pages": [{"title": "File:a.jpg", "missing": True}]}}
        async with httpx.AsyncClient(transport=httpx.MockTransport(lambda _: httpx.Response(200, json=body))) as client:
            await refresh_metadata_queue(tmp_path, state, client, budget, now)

    asyncio.run(run())
    recovered = load_state(tmp_path).tables["commons"].sort("image_file")
    assert recovered["image_file"].to_list() == ["a.jpg", "orphan.jpg"]
    assert recovered["result_status"].to_list() == ["missing", "observed"]
    assert recovered["checked_at"].to_list() == [now, now]
    assert budget.attempts["commons"] == 1
