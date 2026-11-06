import asyncio
import json
from datetime import UTC, datetime, timedelta
from pathlib import Path
from unittest.mock import AsyncMock, patch

import polars as pl
import pytest
from common.artist_image_snapshot import resolve_artist_image_snapshot

from wikidata.artist_images.bootstrap import bootstrap_snapshot
from wikidata.artist_images.config import WorkerConfig
from wikidata.artist_images.state import ImageState, compact_state, load_state
from wikidata.artist_images.transport import ProviderUnavailable
from wikidata.artist_images.worker import run_refresh
from wikidata.silver.artist_images import select_artist_images
from wikidata.silver.commons_image_metadata import _SCHEMA


@pytest.fixture
def settings(tmp_path: Path):
    now = datetime.now(UTC)
    bronze, silver = tmp_path / "bronze", tmp_path / "silver"
    bronze.mkdir()
    silver.mkdir()
    claims = pl.DataFrame(
        {
            "item_id": ["Q1"],
            "musicbrainz_artist_id": ["00000000-0000-0000-0000-000000000001"],
            "image_file": ["a.jpg"],
            "fetched_at": [now],
        }
    )
    metadata = pl.DataFrame(
        [
            {
                "image_file": "a.jpg",
                "thumb_url": "https://upload.wikimedia.org/a.jpg",
                "page_url": "https://commons.wikimedia.org/a.jpg",
                "license": "CC0",
                "non_free": False,
                "attribution_required": False,
                "checked_at": now,
            }
        ],
        schema=_SCHEMA,
    )
    claims.write_parquet(bronze / "wikidata_artist_images.parquet")
    metadata.write_parquet(silver / "10_commons_image_metadata.parquet")
    select_artist_images(claims, metadata).write_parquet(silver / "11_artist_images.parquet")
    catalogue = tmp_path / "catalogue.lock"
    catalogue.touch()
    config = WorkerConfig(
        state_root=tmp_path / "state",
        published_root=tmp_path / "published",
        writer_lock=tmp_path / "writer.lock",
        background_lock=tmp_path / "background.lock",
        catalogue_lock=catalogue,
        producer_commit="commit",
        wdqs_attempts=8,
        entity_attempts=8,
        commons_attempts=8,
        run_seconds=60,
        discovery_days=7,
        positive_days=7,
        negative_days=30,
    )
    bootstrap_snapshot(bronze, silver, config.state_root, config.published_root, catalogue, "commit")
    state = load_state(config.state_root)
    compact_state(
        config.state_root,
        ImageState(
            state.generation,
            state.sequence,
            state.tables,
            {
                **state.provenance,
                "discovery": {"coverage": "complete", "completedAt": now.isoformat()},
            },
        ),
    )
    # First refresh publishes the updated discovery provenance.
    return config


def test_provider_failure_never_promotes_partial_progress(settings) -> None:
    old = resolve_artist_image_snapshot(settings.published_root)
    with patch(
        "wikidata.artist_images.worker.refresh_claim_queue", new=AsyncMock(side_effect=ProviderUnavailable("outage"))
    ):
        with pytest.raises(ProviderUnavailable, match="outage"):
            asyncio.run(run_refresh(settings))
    status = json.loads((settings.published_root / "status.json").read_text())
    assert status["outcome"] == "failed"
    assert status["snapshotId"] == old.snapshot_id
    assert status["stages"]["claims"] == "failed"
    assert resolve_artist_image_snapshot(settings.published_root).snapshot_id == old.snapshot_id


def test_healthy_noop_retains_snapshot_and_records_success(settings) -> None:
    with (
        patch(
            "wikidata.artist_images.worker.refresh_claim_queue",
            new=AsyncMock(side_effect=lambda *args, **kwargs: load_state(settings.state_root)),
        ),
        patch(
            "wikidata.artist_images.worker.refresh_metadata_queue",
            new=AsyncMock(side_effect=lambda *args, **kwargs: load_state(settings.state_root)),
        ),
    ):
        first = asyncio.run(run_refresh(settings))
        second = asyncio.run(run_refresh(settings))
    assert second["outcome"] == "healthy"
    assert second["snapshotId"] == first["snapshotId"]
    assert second["lastSuccessfulRunAt"] is not None


def test_failure_keeps_historical_success(settings) -> None:
    path = settings.published_root / "status.json"
    earlier = (datetime.now(UTC) - timedelta(days=2)).isoformat()
    path.write_text(json.dumps({"lastSuccessfulRunAt": earlier}))
    with patch(
        "wikidata.artist_images.worker.refresh_claim_queue", new=AsyncMock(side_effect=ProviderUnavailable("outage"))
    ):
        with pytest.raises(ProviderUnavailable):
            asyncio.run(run_refresh(settings))
    assert json.loads(path.read_text())["lastSuccessfulRunAt"] == earlier


def test_status_write_failure_does_not_mask_provider_error(settings) -> None:
    with (
        patch(
            "wikidata.artist_images.worker.refresh_claim_queue",
            new=AsyncMock(side_effect=ProviderUnavailable("outage")),
        ),
        patch("wikidata.artist_images.worker._write_status", side_effect=OSError("disk full")),
    ):
        with pytest.raises(ProviderUnavailable, match="outage"):
            asyncio.run(run_refresh(settings))


def test_complete_refresh_commits_claims_and_publishes_same_selected_image(settings):
    import httpx

    old = resolve_artist_image_snapshot(settings.published_root)
    payload = {
        "results": {
            "bindings": [
                {
                    "item": {"type": "uri", "value": "http://www.wikidata.org/entity/Q1"},
                    "mbid": {"type": "literal", "value": "00000000-0000-0000-0000-000000000001"},
                    "image": {"type": "uri", "value": "http://commons.wikimedia.org/wiki/Special:FilePath/a.jpg"},
                }
            ]
        }
    }
    requests = []

    def respond(request):
        requests.append(request)
        return httpx.Response(200, json=payload)

    client = httpx.AsyncClient(transport=httpx.MockTransport(respond))
    with patch("wikidata.artist_images.worker.httpx.AsyncClient", return_value=client):
        status = asyncio.run(run_refresh(settings))
    current = resolve_artist_image_snapshot(settings.published_root)
    assert status["outcome"] == "healthy"
    assert current.snapshot_id != old.snapshot_id
    assert len(requests) == 1
    assert status["refreshCoverage"]["claims"] == {"attempted": 1, "succeeded": 1, "failed": 0, "deferred": 0}
    assert load_state(settings.state_root).tables["registry"]["claims_status"].to_list() == ["has_image"]
    assert pl.read_parquet(current.lineage_path)["claims_checked_at"][0] is not None


def test_quarantine_publishes_other_artists_survives_restart_and_recovers(settings):
    import httpx

    from wikidata.artist_images.discovery import commit_discovery

    now = datetime.now(UTC)
    commit_discovery(settings.state_root, load_state(settings.state_root), ["Q1", "Q2"], now)
    old = resolve_artist_image_snapshot(settings.published_root)
    old_claims = load_state(settings.state_root).tables["claims"].filter(pl.col("item_id") == "Q1")
    requests = []
    corrected = False

    def binding(qid, mbid, image):
        return {
            "item": {"type": "uri", "value": f"http://www.wikidata.org/entity/{qid}"},
            "mbid": {"type": "literal", "value": mbid},
            "image": {"type": "uri", "value": f"http://commons.wikimedia.org/wiki/Special:FilePath/{image}"},
        }

    def respond(request):
        requests.append(request)
        if request.url.host == "query.wikidata.org":
            rows = [binding("Q1", "00000000-0000-0000-0000-000000000001", "a.jpg")]
            if not corrected:
                rows += [
                    binding("Q1", "malformed", "wrong.jpg"),
                    binding("Q2", "00000000-0000-0000-0000-000000000002", "b.jpg"),
                ]
            return httpx.Response(200, json={"results": {"bindings": rows}})
        return httpx.Response(
            200,
            json={
                "query": {
                    "pages": [
                        {
                            "title": "File:b.jpg",
                            "imageinfo": [
                                {
                                    "thumburl": "https://upload.wikimedia.org/b.jpg",
                                    "descriptionurl": "https://commons.wikimedia.org/b.jpg",
                                    "extmetadata": {
                                        "LicenseShortName": {"value": "CC0"},
                                        "AttributionRequired": {"value": "false"},
                                    },
                                }
                            ],
                        }
                    ]
                }
            },
        )

    def run(at=None):
        client = httpx.AsyncClient(transport=httpx.MockTransport(respond))
        with patch("wikidata.artist_images.worker.httpx.AsyncClient", return_value=client):
            if at is None:
                return asyncio.run(run_refresh(settings))
            with patch("wikidata.artist_images.worker.datetime", wraps=datetime) as clock:
                clock.now.return_value = at
                return asyncio.run(run_refresh(settings))

    first = run()
    snapshot = resolve_artist_image_snapshot(settings.published_root)
    state = load_state(settings.state_root)
    quarantined = state.tables["registry"].filter(pl.col("item_id") == "Q1").row(0, named=True)
    assert first["outcome"] == "degraded"
    assert first["stages"]["claims"] == "degraded"
    assert first["stages"]["metadata"] == "healthy"
    assert first["refreshCoverage"]["claims"] == {"attempted": 2, "succeeded": 1, "failed": 1, "deferred": 0}
    assert snapshot.snapshot_id != old.snapshot_id
    assert pl.read_parquet(snapshot.images_path).height == 2
    assert snapshot.manifest["quarantine"]["count"] == 1
    assert first["quarantine"]["sample"][0]["reason"] == "invalid_claims: Invalid MusicBrainz artist identifier"
    assert state.tables["claims"].filter(pl.col("item_id") == "Q1").equals(old_claims)
    assert quarantined["claims_checked_at"] is None
    assert quarantined["consecutive_failures"] == 1
    assert quarantined["retry_after_at"] == quarantined["last_attempt_at"] + timedelta(minutes=15)
    assert len(requests) == 2

    second = run()
    assert second["outcome"] == "degraded"
    assert second["quarantine"] == first["quarantine"]
    assert second["snapshotId"] == first["snapshotId"]
    assert second["lastSuccessfulRunAt"] is None
    assert len(requests) == 2

    corrected = True
    third = run(now + timedelta(hours=1))
    recovered = load_state(settings.state_root).tables["registry"].filter(pl.col("item_id") == "Q1").row(0, named=True)
    assert third["outcome"] == "healthy"
    assert third["quarantine"]["count"] == 0
    assert third["lastSuccessfulRunAt"] is not None
    assert third["snapshotId"] != first["snapshotId"]
    assert recovered["claims_checked_at"] is not None
    assert recovered["consecutive_failures"] == 0
    assert recovered["failure_reason"] is None
    assert recovered["first_failure_at"] is None
    assert recovered["retry_after_at"] is None
    assert len(requests) == 3


def test_incomplete_batch_still_blocks_publication(settings):
    import httpx

    from wikidata.artist_images.discovery import commit_discovery

    commit_discovery(settings.state_root, load_state(settings.state_root), ["Q1", "Q2"], datetime.now(UTC))
    old = resolve_artist_image_snapshot(settings.published_root)
    payload = {"results": {"bindings": [{"item": {"type": "uri", "value": "http://www.wikidata.org/entity/Q1"}}]}}
    client = httpx.AsyncClient(transport=httpx.MockTransport(lambda request: httpx.Response(200, json=payload)))
    with patch("wikidata.artist_images.worker.httpx.AsyncClient", return_value=client):
        with pytest.raises(ValueError, match="Incomplete SPARQL QID coverage"):
            asyncio.run(run_refresh(settings))
    assert resolve_artist_image_snapshot(settings.published_root).snapshot_id == old.snapshot_id
    assert json.loads((settings.published_root / "status.json").read_text())["outcome"] == "failed"
    registry = load_state(settings.state_root).tables["registry"]
    assert registry["claims_checked_at"].null_count() == 2
    assert registry["consecutive_failures"].to_list() == [1, 1]
