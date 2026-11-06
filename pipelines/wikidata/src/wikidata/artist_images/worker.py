"""Coordinate bounded refresh, durable progress and snapshot publication."""

import json
import logging
import os
import time
import uuid
from datetime import UTC, datetime, timedelta
from typing import Any

import httpx
import polars as pl
from common.artist_image_snapshot import resolve_artist_image_snapshot
from common.step_timer import step

from wikidata.artist_images.candidate import assemble_candidate
from wikidata.artist_images.commons import due_files, refresh_metadata_queue
from wikidata.artist_images.config import WorkerConfig
from wikidata.artist_images.discovery import acquire_discovery, commit_discovery
from wikidata.artist_images.locks import LockDeferred, exclusive_lock
from wikidata.artist_images.progress import quarantine_summary
from wikidata.artist_images.publish import _sync_directory, _write_json, publish_snapshot
from wikidata.artist_images.refresh import refresh_claim_queue
from wikidata.artist_images.state import compact_state, load_state
from wikidata.artist_images.transport import BudgetExhausted, RequestBudget

logger = logging.getLogger(__name__)


def _write_status(root, status):
    root.mkdir(parents=True, exist_ok=True)
    temporary = root / f".status-{uuid.uuid4().hex}.json"
    try:
        _write_json(temporary, status)
        os.replace(temporary, root / "status.json")
        _sync_directory(root)
    finally:
        temporary.unlink(missing_ok=True)


def _due(state, now, config):
    registry = state.tables["registry"]
    claims = registry.filter(pl.col("next_check_at").is_null() | (pl.col("next_check_at") <= now))
    return {
        "claims": set(claims["item_id"]),
        "metadata": set(due_files(state, now, config.metadata_days, config.missing_metadata_days)["image_file"]),
    }


def _coverage(budget, due):
    return {
        kind: {
            "attempted": len(work["attempted"]),
            "succeeded": len(work["succeeded"]),
            "failed": len(work["failed"]),
            "deferred": len(due[kind] - work["succeeded"] - work["failed"]),
        }
        for kind, work in budget.work.items()
    }


async def _run_locked(config: WorkerConfig, *, discover_only: bool) -> dict:
    started = datetime.now(UTC)
    previous_status_path = config.published_root / "status.json"
    previous_status = json.loads(previous_status_path.read_text()) if previous_status_path.exists() else {}
    if not isinstance(previous_status, dict):
        raise ValueError("Invalid previous image status")
    status: dict[str, Any] = {
        key: previous_status.get(key)
        for key in ("lastSuccessfulRunAt", "lastDueWorkProgressAt", "lastSuccessfulPublicationAt")
    }
    status.update(
        lastAttemptAt=started.isoformat(),
        outcome="healthy",
        reason=None,
        snapshotId=None,
        producerCommit=config.producer_commit,
        stages={},
    )
    budget = RequestBudget(
        {"wdqs": config.wdqs_attempts, "entities": config.entity_attempts, "commons": config.commons_attempts},
        config.run_seconds,
        catalogue_lock=config.catalogue_lock,
    )
    due: dict[str, set[str]] = {"claims": set(), "metadata": set()}
    stage = "preflight"
    try:
        with step(stage):
            snapshot = resolve_artist_image_snapshot(config.published_root)
            status["snapshotId"] = snapshot.snapshot_id
            status["lastSuccessfulPublicationAt"] = snapshot.manifest["publishedAt"]
            before = load_state(config.state_root)
            state = before
        async with httpx.AsyncClient() as client:
            discovery = state.provenance["discovery"]
            last_discovery = discovery["completedAt"]
            if (
                discover_only
                or last_discovery is None
                or (started - datetime.fromisoformat(last_discovery) >= timedelta(days=config.discovery_days))
            ):
                stage = "discovery"
                try:
                    with step(stage):
                        qids = await acquire_discovery(client, budget)
                        state = commit_discovery(config.state_root, state, qids, datetime.now(UTC))
                    status["stages"][stage] = "healthy"
                except (BudgetExhausted, LockDeferred) as exc:
                    status.update(outcome="deferred", reason=str(exc))
                    status["stages"][stage] = "deferred"
                except (httpx.HTTPError, RuntimeError, ValueError) as exc:
                    if discover_only:
                        raise
                    logger.warning("Discovery failed; retaining completed registry: %s", type(exc).__name__)
                    status.update(outcome="degraded", reason=f"Discovery: {type(exc).__name__}")
                    status["stages"][stage] = "degraded"
            else:
                status["stages"]["discovery"] = "fresh"
            due = _due(state, started, config)
            if not discover_only:
                for stage in ("claims", "metadata"):
                    try:
                        with step(stage):
                            if stage == "claims":
                                # Reserve half the remaining wall budget for Commons and publication.
                                deadline = budget.deadline
                                budget.deadline = min(
                                    deadline, time.monotonic() + max(0, deadline - time.monotonic()) / 2
                                )
                                try:
                                    state = await refresh_claim_queue(
                                        config.state_root,
                                        state,
                                        client,
                                        budget,
                                        datetime.now(UTC),
                                        positive_ttl_days=config.positive_days,
                                        negative_ttl_days=config.negative_days,
                                    )
                                finally:
                                    budget.deadline = deadline
                            else:
                                due["metadata"].update(_due(state, started, config)["metadata"])
                                state = await refresh_metadata_queue(
                                    config.state_root,
                                    state,
                                    client,
                                    budget,
                                    datetime.now(UTC),
                                    positive_days=config.metadata_days,
                                    negative_days=config.missing_metadata_days,
                                )
                        status["stages"][stage] = "healthy"
                    except (BudgetExhausted, LockDeferred) as exc:
                        if status["outcome"] != "degraded":
                            status.update(outcome="deferred", reason=str(exc))
                        status["stages"][stage] = "deferred"
                        state = load_state(config.state_root)
                    if stage == "claims":
                        status["quarantine"] = quarantine_summary(state)
                        if status["quarantine"]["count"]:
                            status.update(outcome="degraded", reason="Invalid claims quarantined; retries pending")
                            status["stages"]["claims"] = "degraded"
        stage = "publication"
        coverage = _coverage(budget, due)
        status["refreshCoverage"] = coverage
        progressed = any(work["succeeded"] for work in budget.work.values())
        if (
            progressed
            or state.generation != before.generation
            or state.sequence != before.sequence
            or snapshot.manifest["sourceStateGeneration"] != state.generation
        ):
            with step("compact_state"):
                state = compact_state(config.state_root, state)
            with step("candidate_validation"):
                images, lineage, provenance = assemble_candidate(
                    state,
                    datetime.now(UTC),
                    config.producer_commit,
                    coverage,
                    claims_days=config.positive_days,
                    metadata_days=config.metadata_days,
                    previous_snapshot=snapshot,
                )
            with step(stage):
                snapshot = publish_snapshot(config.published_root, images, lineage, provenance)
            status["snapshotId"] = snapshot.snapshot_id
            status["lastSuccessfulPublicationAt"] = snapshot.manifest["publishedAt"]
            status["stages"][stage] = "healthy"
        else:
            status["stages"][stage] = "unchanged"
        if progressed:
            status["lastDueWorkProgressAt"] = datetime.now(UTC).isoformat()
        if status["outcome"] == "healthy":
            status["lastSuccessfulRunAt"] = datetime.now(UTC).isoformat()
        status["registrySize"] = state.tables["registry"].height
        status["discovery"] = state.provenance["discovery"]
        from wikidata.artist_images.candidate import summarize_age

        status["ageSummary"] = summarize_age(
            pl.read_parquet(snapshot.lineage_path), datetime.now(UTC), config.positive_days, config.metadata_days
        )
        return status
    except Exception as exc:
        status.update(outcome="failed", reason=f"{stage}: {type(exc).__name__}")
        status["stages"][stage] = "failed"
        raise
    finally:
        status["httpAttempts"] = budget.attempts
        status["httpMetrics"] = budget.metrics
        status["refreshCoverage"] = _coverage(budget, due)
        status["completedAt"] = datetime.now(UTC).isoformat()
        try:
            _write_status(config.published_root, status)
        except OSError:
            logger.exception("Could not record image status; publication pointer is authoritative")
        logger.info(
            "artist image outcome=%s snapshot=%s reason=%s", status["outcome"], status["snapshotId"], status["reason"]
        )


async def run_refresh(
    config: WorkerConfig, *, discover_only: bool = False, writer_fd: int | None = None, background_fd: int | None = None
) -> dict:
    """Keep a single environment writer, including status changes on deferral."""
    if (writer_fd is None) != (background_fd is None):
        raise ValueError("Both wrapper lock descriptors are required")
    with exclusive_lock(config.writer_lock, inherited_fd=writer_fd):
        try:
            with exclusive_lock(config.background_lock, inherited_fd=background_fd):
                return await _run_locked(config, discover_only=discover_only)
        except LockDeferred as exc:
            path = config.published_root / "status.json"
            status = json.loads(path.read_text()) if path.exists() else {}
            status.update(lastAttemptAt=datetime.now(UTC).isoformat(), outcome="deferred", reason=str(exc))
            _write_status(config.published_root, status)
            return status
