import asyncio
from datetime import UTC, datetime
from pathlib import Path

import httpx
import polars as pl
import pytest

from wikidata.artist_images.bootstrap import seed_registry
from wikidata.artist_images.claims import ClaimBatch, ClaimObservation
from wikidata.artist_images.refresh import acquire_claim_batches, commit_claims
from wikidata.artist_images.state import ImageState, compact_state, load_state
from wikidata.artist_images.transport import RequestBudget


def test_negative_replaces_only_confirmed_qid_after_restart(tmp_path: Path) -> None:
    now = datetime(2026, 11, 5, tzinfo=UTC)
    claims = pl.DataFrame(
        {
            "item_id": ["Q1", "Q2", "Q3"],
            "musicbrainz_artist_id": ["a", "b", "c"],
            "image_file": ["a.jpg", "b.jpg", "c.jpg"],
            "claims_checked_at": [now, now, now],
        }
    )
    state = compact_state(
        tmp_path,
        ImageState(
            "",
            0,
            {
                "registry": seed_registry(claims),
                "claims": claims,
                "commons": pl.DataFrame({"image_file": ["a.jpg"]}),
            },
            {},
        ),
    )
    batch = ClaimBatch(
        (ClaimObservation("Q1", "no_image", ()), ClaimObservation("Q2", "needs_confirmation", ())), ("Q3",)
    )
    commit_claims(tmp_path, state, batch, now, positive_ttl_days=7, negative_ttl_days=30)
    recovered = load_state(tmp_path)
    assert recovered.tables["claims"]["item_id"].to_list() == ["Q2", "Q3"]
    registry = recovered.tables["registry"].sort("item_id")
    assert registry["claims_status"].to_list() == ["no_image", "unfetched", "unfetched"]
    assert registry["claims_checked_at"].to_list() == [now, None, None]


def test_successful_batch_is_yielded_before_another_request() -> None:
    limits = RequestBudget({"wdqs": 8, "entities": 8, "commons": 8}, 60)
    calls = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(request)
        return httpx.Response(
            200,
            json={
                "results": {
                    "bindings": [
                        {
                            "item": {"type": "uri", "value": "http://www.wikidata.org/entity/Q1"},
                        }
                    ]
                }
            },
        )

    async def run() -> None:
        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            batches = acquire_claim_batches(client, limits, ["Q1"])
            batch = await anext(batches)
            assert batch.observations[0].status == "needs_confirmation"
            assert len(calls) == 1
            with pytest.raises(StopAsyncIteration):
                await anext(batches)

    asyncio.run(run())


@pytest.mark.parametrize("invalid_target", [False, True])
def test_redirect_to_confirmed_imageless_or_quarantined_target(tmp_path, invalid_target):
    from unittest.mock import patch
    from wikidata.artist_images.refresh import refresh_claim_queue

    now = datetime.now(UTC)
    claims = pl.DataFrame(
        {"item_id": ["Q1"], "musicbrainz_artist_id": ["artist"], "image_file": ["a.jpg"], "claims_checked_at": [now]}
    )
    state = compact_state(
        tmp_path,
        ImageState(
            "",
            0,
            {
                "registry": seed_registry(claims),
                "claims": claims,
                "commons": pl.DataFrame({"image_file": ["a.jpg"]}),
            },
            {},
        ),
    )
    calls = []

    def respond(request):
        calls.append(request)
        if request.url.host == "query.wikidata.org":
            qid = "Q1" if len(calls) == 1 else "Q2"
            binding = {"item": {"type": "uri", "value": "http://www.wikidata.org/entity/" + qid}}
            if invalid_target and qid == "Q2":
                binding["mbid"] = {"type": "literal", "value": "malformed"}
            return httpx.Response(200, json={"results": {"bindings": [binding]}})
        if len(calls) == 2:
            return httpx.Response(
                200, json={"redirects": [{"from": "Q1", "to": "Q2"}], "entities": {"Q2": {"id": "Q2", "claims": {}}}}
            )
        return httpx.Response(200, json={"entities": {"Q2": {"id": "Q2", "claims": {}}}})

    async def run():
        async with httpx.AsyncClient(transport=httpx.MockTransport(respond)) as client:
            return await refresh_claim_queue(
                tmp_path,
                state,
                client,
                RequestBudget({"wdqs": 8, "entities": 8, "commons": 8}, 60),
                now,
                positive_ttl_days=7,
                negative_ttl_days=30,
            )

    with patch("wikidata.artist_images.transport.asyncio.sleep", return_value=None):
        asyncio.run(run())
    state = load_state(tmp_path)
    if invalid_target:
        assert len(calls) == 3
        assert state.tables["claims"].equals(claims)
        assert state.tables["registry"]["item_id"].to_list() == ["Q1"]
        assert state.tables["registry"]["failure_reason"].to_list() == [
            "invalid_claims: Redirect target Q2: Invalid MusicBrainz artist identifier"
        ]
        assert state.tables["registry"]["consecutive_failures"].to_list() == [1]
        return
    assert len(calls) == 4
    assert state.tables["claims"].is_empty()
    assert state.tables["registry"]["item_id"].to_list() == ["Q2"]
    assert state.tables["registry"]["claims_status"].to_list() == ["no_mbid"]
    assert state.provenance["redirects"] == {"Q1": "Q2"}
