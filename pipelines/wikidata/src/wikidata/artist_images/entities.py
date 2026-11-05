"""Confirm absent P434 identities without interpreting transport errors as deletion."""

import time
from dataclasses import dataclass

import httpx

from wikidata.artist_images.claims import ClaimObservation, _QID
from wikidata.artist_images.transport import RequestBudget, request_json
from wikidata.wikidata_client import API_ENDPOINT


@dataclass(frozen=True)
class EntityConfirmation:
    observations: tuple[ClaimObservation, ...]
    redirects: tuple[tuple[str, str], ...]
    requires_refresh: tuple[str, ...]


def parse_confirmations(qids: list[str], payload: dict) -> EntityConfirmation:
    if not qids or len(qids) > 50 or len(set(qids)) != len(qids) or any(not _QID.fullmatch(qid) for qid in qids):
        raise ValueError("Entity confirmation requires 1–50 distinct QIDs")
    if "error" in payload or not isinstance(payload.get("entities"), dict):
        raise ValueError("Invalid entity confirmation response")
    redirects = payload.get("redirects", [])
    if not isinstance(redirects, list):
        raise ValueError("Invalid entity redirects")
    mapping = {}
    for redirect in redirects:
        if not isinstance(redirect, dict):
            raise ValueError("Invalid entity redirect")
        source, target = redirect.get("from"), redirect.get("to")
        if source not in qids or not isinstance(target, str) or not _QID.fullmatch(target) or source in mapping:
            raise ValueError("Invalid or conflicting entity redirect")
        mapping[source] = target
    observations, requires_refresh = [], []
    for qid in qids:
        target = mapping.get(qid, qid)
        entity = payload["entities"].get(target)
        if not isinstance(entity, dict) or entity.get("id") != target:
            raise ValueError("Incomplete entity confirmation coverage")
        if qid in mapping:
            # Redirect targets need a complete claims observation and registry merge;
            # the source association set remains untouched until that transaction.
            requires_refresh.append(target)
            continue
        if "missing" in entity:
            observations.append(ClaimObservation(qid, "missing", ()))
            continue
        claims = entity.get("claims")
        if not isinstance(claims, dict):
            raise ValueError("Entity claims are absent or malformed")
        statements = claims.get("P434", [])
        if not isinstance(statements, list):
            raise ValueError("Malformed P434 statements")
        active = []
        for statement in statements:
            if not isinstance(statement, dict) or statement.get("rank") not in ("normal", "preferred", "deprecated"):
                raise ValueError("Malformed entity statement")
            if statement["rank"] != "deprecated":
                active.append(statement)
        rank = "preferred" if any(statement["rank"] == "preferred" for statement in active) else "normal"
        has_value = False
        for statement in active:
            if statement["rank"] != rank:
                continue
            snak = statement.get("mainsnak")
            if not isinstance(snak, dict) or snak.get("snaktype") not in ("value", "somevalue", "novalue"):
                raise ValueError("Malformed P434 snak")
            if snak["snaktype"] == "value":
                value = snak.get("datavalue")
                if not isinstance(value, dict) or not isinstance(value.get("value"), str):
                    raise ValueError("Malformed P434 value")
                has_value = True
        if has_value:
            requires_refresh.append(qid)
        else:
            observations.append(ClaimObservation(qid, "no_mbid", ()))
    return EntityConfirmation(tuple(observations), tuple(sorted(mapping.items())), tuple(sorted(set(requires_refresh))))


async def confirm_entities(client: httpx.AsyncClient, budget: RequestBudget, qids: list[str]) -> EntityConfirmation:
    if not qids or len(qids) > 50 or any(not _QID.fullmatch(qid) for qid in qids):
        raise ValueError("Entity confirmation requires 1–50 QIDs")
    payload = await request_json(
        client,
        budget,
        "entities",
        "GET",
        API_ENDPOINT,
        family_deadline=min(budget.deadline, time.monotonic() + 90),
        params={
            "action": "wbgetentities",
            "ids": "|".join(qids),
            "props": "claims",
            "redirects": "yes",
            "format": "json",
        },
    )
    return parse_confirmations(qids, payload)


def merge_redirect(
    root, state, source: str, observation: ClaimObservation, now, *, positive_ttl_days: int, negative_ttl_days: int
):
    """Merge a redirect only with a complete fresh target observation in one base transaction."""
    from datetime import timedelta

    import polars as pl

    from wikidata.artist_images.state import ImageState, compact_state

    target = observation.item_id
    if source == target or observation.status not in ("has_image", "no_image", "no_mbid", "missing"):
        raise ValueError("Redirect merge requires a distinct target with complete P434 coverage")
    if (observation.status == "has_image") != bool(observation.associations):
        raise ValueError("Inconsistent redirect target associations")
    registry = state.tables["registry"]
    source_rows = registry.filter(pl.col("item_id") == source)
    if source_rows.height != 1:
        raise ValueError("Redirect source is not registered")
    target_rows = registry.filter(pl.col("item_id") == target)
    row = (target_rows if target_rows.height else source_rows).row(0, named=True)
    history = registry.filter(pl.col("item_id").is_in([source, target]))
    first_seen = history["first_seen_at"]
    row.update(
        {
            "item_id": target,
            "first_seen_at": None if first_seen.null_count() else first_seen.min(),
            "last_seen_in_discovery_at": history["last_seen_in_discovery_at"].max(),
            "claims_checked_at": now,
            "claims_status": observation.status,
            "next_check_at": now
            + timedelta(days=positive_ttl_days if observation.status == "has_image" else negative_ttl_days),
            "last_attempt_at": now,
            "consecutive_failures": 0,
            "retry_after_at": None,
        }
    )
    registry = pl.concat(
        [registry.filter(~pl.col("item_id").is_in([source, target])), pl.DataFrame([row], schema=registry.schema)]
    )
    claims = state.tables["claims"]
    replacements = pl.DataFrame(
        [
            {
                "item_id": target,
                "musicbrainz_artist_id": mbid,
                "image_file": image,
                "claims_checked_at": now,
            }
            for mbid, image in observation.associations
        ],
        schema=claims.schema,
    )
    claims = pl.concat([claims.filter(~pl.col("item_id").is_in([source, target])), replacements])
    aliases = dict(state.provenance.get("redirects", {}))
    # Retarget historical aliases so redirect chains stay one hop and cycles cannot survive.
    for alias, canonical in list(aliases.items()):
        if canonical == source:
            aliases[alias] = target
    if target in aliases:
        raise ValueError("Redirect merge would create an alias cycle")
    aliases[source] = target
    return compact_state(
        root,
        ImageState(
            state.generation,
            state.sequence,
            {**state.tables, "registry": registry, "claims": claims},
            {**state.provenance, "redirects": aliases},
        ),
    )
