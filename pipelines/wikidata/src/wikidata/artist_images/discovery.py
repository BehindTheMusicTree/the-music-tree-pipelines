"""P434-scope discovery transactions that only add candidates to the registry."""

from datetime import datetime
from pathlib import Path

import polars as pl

from wikidata.artist_images.claims import _QID
from wikidata.artist_images.state import ImageState, compact_state

DISCOVERY_QUERY = """PREFIX wdt: <http://www.wikidata.org/prop/direct/>
SELECT ?item WHERE { ?item wdt:P434 ?mbid . }
"""


def parse_discovery(payload: object) -> list[str]:
    if not isinstance(payload, dict) or not isinstance(payload.get("results"), dict):
        raise ValueError("Invalid discovery response")
    bindings = payload["results"].get("bindings")
    if not isinstance(bindings, list) or not bindings:
        raise ValueError("Discovery response must contain candidates")
    qids = set()
    prefix = "http://www.wikidata.org/entity/"
    for row in bindings:
        term = row.get("item") if isinstance(row, dict) else None
        if not isinstance(term, dict) or term.get("type") != "uri":
            raise ValueError("Invalid discovery RDF term")
        value = term.get("value")
        if not isinstance(value, str) or not value.startswith(prefix) or not _QID.fullmatch(value[len(prefix) :]):
            raise ValueError("Invalid discovery QID")
        qids.add(value[len(prefix) :])
    return sorted(qids)


def commit_discovery(root: Path, state: ImageState, qids: list[str], completed_at: datetime) -> ImageState:
    """Atomically record complete discovery without treating omission as removal."""
    if not qids or any(not _QID.fullmatch(qid) for qid in qids) or completed_at.tzinfo is None:
        raise ValueError("Discovery requires valid nonempty QIDs and an aware completion time")
    registry = state.tables["registry"]
    aliases = state.provenance.get("redirects", {})
    discovered = {aliases.get(qid, qid) for qid in qids}
    known = set(registry["item_id"])
    rows = []
    for qid in sorted(discovered - known):
        rows.append(
            {
                "item_id": qid,
                "first_seen_at": completed_at,
                "last_seen_in_discovery_at": completed_at,
                "claims_status": "unfetched",
                "claims_checked_at": None,
                "next_check_at": None,
                "last_attempt_at": None,
                "consecutive_failures": 0,
                "retry_after_at": None,
                "failure_reason": None,
                "first_failure_at": None,
            }
        )
    registry = registry.with_columns(
        pl.when(pl.col("item_id").is_in(sorted(discovered)))
        .then(pl.lit(completed_at))
        .otherwise(pl.col("last_seen_in_discovery_at"))
        .alias("last_seen_in_discovery_at")
    )
    if rows:
        registry = pl.concat([registry, pl.DataFrame(rows, schema=registry.schema)])
    tables = {**state.tables, "registry": registry.sort("item_id")}
    provenance = {**state.provenance, "discovery": {"coverage": "complete", "completedAt": completed_at.isoformat()}}
    return compact_state(root, ImageState(state.generation, state.sequence, tables, provenance))


async def acquire_discovery(client, budget) -> list[str]:
    import time

    from wikidata.artist_images.transport import request_json
    from wikidata.wikidata_client import SPARQL_ENDPOINT

    payload = await request_json(
        client,
        budget,
        "wdqs",
        "POST",
        SPARQL_ENDPOINT,
        family_deadline=min(budget.deadline, time.monotonic() + 90),
        data={"query": DISCOVERY_QUERY},
    )
    return parse_discovery(payload)
