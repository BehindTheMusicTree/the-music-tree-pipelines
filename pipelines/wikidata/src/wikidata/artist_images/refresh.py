"""Bounded QID acquisition and durable complete-observation replacement."""

import time
from collections.abc import AsyncIterator
from datetime import datetime, timedelta
from pathlib import Path

import httpx
import polars as pl

from wikidata.artist_images.claims import ClaimBatch, claims_query, parse_claims
from wikidata.artist_images.state import ImageState, checkpoint_state, load_state
from wikidata.artist_images.transport import BudgetExhausted, QueryTimeout, RequestBudget, request_json
from wikidata.artist_images.locks import LockDeferred
from wikidata.wikidata_client import SPARQL_ENDPOINT


async def acquire_claim_batches(
    client: httpx.AsyncClient,
    budget: RequestBudget,
    qids: list[str],
    *,
    family_deadline: float | None = None,
) -> AsyncIterator[ClaimBatch]:
    """One retry per query then deterministic splits sharing a 90-second family."""
    query = claims_query(qids)
    if family_deadline is None:
        family_deadline = min(budget.deadline, time.monotonic() + 90)
    try:
        payload = await request_json(
            client,
            budget,
            "wdqs",
            "POST",
            SPARQL_ENDPOINT,
            family_deadline=family_deadline,
            data={"query": query},
        )
        yield parse_claims(qids, payload)
    except QueryTimeout:
        if len(qids) < 100:
            raise
        middle = len(qids) // 2
        for child in (qids[:middle], qids[middle:]):
            async for batch in acquire_claim_batches(client, budget, child, family_deadline=family_deadline):
                yield batch


def commit_claims(
    root: Path,
    state: ImageState,
    batch: ClaimBatch,
    now: datetime,
    *,
    positive_ttl_days: int,
    negative_ttl_days: int,
) -> ImageState:
    """Persist only complete usable observations; unconfirmed identities stay due."""
    if now.tzinfo is None or any(type(ttl) is not int or ttl <= 0 for ttl in (positive_ttl_days, negative_ttl_days)):
        raise ValueError("Claim commits require timezone-aware time and positive TTLs")
    accepted = [observation for observation in batch.observations if observation.status != "needs_confirmation"]
    accepted_ids = [observation.item_id for observation in accepted]
    if len(set(accepted_ids)) != len(accepted_ids) or set(accepted_ids) & set(batch.rejected_qids):
        raise ValueError("Conflicting claim batch coverage")
    registry = state.tables["registry"]
    if not set(accepted_ids) <= set(registry["item_id"]):
        raise ValueError("Claim batch contains unregistered QIDs")
    if not accepted:
        return state
    updates = []
    claims = []
    for observation in accepted:
        if observation.status not in ("has_image", "no_image", "no_mbid", "missing"):
            raise ValueError("Invalid claim observation status")
        if (observation.status == "has_image") != bool(observation.associations):
            raise ValueError("Inconsistent claim observation associations")
        row = registry.filter(pl.col("item_id") == observation.item_id).row(0, named=True)
        row.update(
            {
                "claims_checked_at": now,
                "claims_status": observation.status,
                "next_check_at": now
                + timedelta(days=positive_ttl_days if observation.status == "has_image" else negative_ttl_days),
                "last_attempt_at": now,
                "consecutive_failures": 0,
                "retry_after_at": None,
            }
        )
        updates.append(row)
        for mbid, image in observation.associations:
            claims.append(
                {
                    "item_id": observation.item_id,
                    "musicbrainz_artist_id": mbid,
                    "image_file": image,
                    "claims_checked_at": now,
                }
            )
    return checkpoint_state(
        root,
        state,
        {
            "registry": pl.DataFrame(updates, schema=registry.schema),
            "claims": pl.DataFrame(claims, schema=state.tables["claims"].schema),
        },
        {"registry": accepted_ids, "claims": accepted_ids},
    )


async def refresh_claim_queue(
    root: Path,
    state: ImageState,
    client: httpx.AsyncClient,
    budget: RequestBudget,
    now: datetime,
    *,
    positive_ttl_days: int,
    negative_ttl_days: int,
) -> ImageState:
    """Drain due QIDs once per run; commit each accepted batch before further I/O."""
    from wikidata.artist_images.entities import confirm_entities, merge_redirect
    from wikidata.artist_images.scheduling import due_qids

    from wikidata.artist_images.progress import defer_failed

    attempted: set[str] = set()
    while True:
        registry = state.tables["registry"].filter(~pl.col("item_id").is_in(list(attempted)))
        qids = due_qids(registry, now, 1000)
        if not qids:
            return state
        attempted.update(qids)
        budget.work["claims"]["attempted"].update(qids)
        try:
            async for batch in acquire_claim_batches(client, budget, qids):
                state = commit_claims(
                    root,
                    state,
                    batch,
                    now,
                    positive_ttl_days=positive_ttl_days,
                    negative_ttl_days=negative_ttl_days,
                )
                budget.work["claims"]["succeeded"].update(
                    observation.item_id
                    for observation in batch.observations
                    if observation.status != "needs_confirmation"
                )
                unresolved = [
                    observation.item_id
                    for observation in batch.observations
                    if observation.status == "needs_confirmation"
                ]
                for start in range(0, len(unresolved), 50):
                    confirmed = await confirm_entities(client, budget, unresolved[start : start + 50])
                    state = commit_claims(
                        root,
                        state,
                        ClaimBatch(confirmed.observations, ()),
                        now,
                        positive_ttl_days=positive_ttl_days,
                        negative_ttl_days=negative_ttl_days,
                    )
                    budget.work["claims"]["succeeded"].update(
                        observation.item_id for observation in confirmed.observations
                    )
                    for source, target in confirmed.redirects:
                        async for target_batch in acquire_claim_batches(client, budget, [target]):
                            if target_batch.rejected_qids or len(target_batch.observations) != 1:
                                raise ValueError("Invalid redirect target claims")
                            observation = target_batch.observations[0]
                            if observation.status == "needs_confirmation":
                                target_confirmation = await confirm_entities(client, budget, [target])
                                if (
                                    target_confirmation.redirects
                                    or target_confirmation.requires_refresh
                                    or len(target_confirmation.observations) != 1
                                ):
                                    raise ValueError("Redirect target changed during confirmation; retry next run")
                                observation = target_confirmation.observations[0]
                            state = merge_redirect(
                                root,
                                state,
                                source,
                                observation,
                                now,
                                positive_ttl_days=positive_ttl_days,
                                negative_ttl_days=negative_ttl_days,
                            )
                        budget.work["claims"]["succeeded"].add(source)
                    redirect_targets = {target for _, target in confirmed.redirects}
                    if set(confirmed.requires_refresh) - redirect_targets:
                        raise ValueError("Entity claims changed during observation; retry next run")
                if batch.rejected_qids:
                    raise ValueError(
                        f"Claims validation rejected {len(batch.rejected_qids)} QIDs; publication suppressed"
                    )
        except (BudgetExhausted, LockDeferred):
            raise
        except Exception:
            failed = set(qids) - budget.work["claims"]["succeeded"]
            budget.work["claims"]["failed"].update(failed)
            defer_failed(root, load_state(root), "registry", failed, now)
            raise
