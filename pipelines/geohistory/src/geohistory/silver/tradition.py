import logging
from collections import defaultdict
from pathlib import Path

import polars as pl

from geohistory.silver.curation import MEMBERSHIP_ACTIONS, TRADITION_LINK_KINDS, read_curation_list
from geohistory.silver.entity_time_span import dated_claims

logger = logging.getLogger(__name__)

CUTOFF_YEAR = 1950
OVERVIEW_PREFIX = "music of "
TRADITION_SCHEMA = {
    "wikidata_id": pl.String,
    "label": pl.String,
    "start_year": pl.Int32,
    "end_year": pl.Int32,
    "anchor_id": pl.String,
    "anchor_rule": pl.String,
}
LINK_SCHEMA = {"from_wikidata_id": pl.String, "to_wikidata_id": pl.String, "kind": pl.String}


def _claim_years(dated: pl.DataFrame, side: str, *, latest: bool) -> dict[str, int]:
    best = (
        dated.filter(pl.col("side") == side)
        .sort("item_id", "priority", "year", descending=[False, False, latest])
        .unique("item_id", keep="first", maintain_order=True)
    )
    return dict(zip(best["item_id"], best["year"], strict=True))


def _lift_to_date(entity: str, year: int, spans: dict[str, tuple[int | None, int | None]], successors: dict) -> str:
    seen = {entity}
    while True:
        end = spans.get(entity, (None, None))[1]
        if end is None or end >= year:
            return entity
        candidates = [s for s in successors.get(entity, ()) if s not in seen]
        if not candidates:
            return entity
        started = [s for s in candidates if (spans.get(s, (None, None))[0] or year) <= year]
        entity = (started or candidates)[0]
        seen.add(entity)


def _placement_overrides(overrides: pl.DataFrame, entity_ids: set[str], item_entity: dict[str, str]) -> dict:
    resolved = {}
    for qid, entity_id, start, end in overrides.select("wikidata_id", "entity_id", "start_year", "end_year").rows():
        if not (entity_id or start or end):
            raise ValueError(f"tradition_placement_overrides row for {qid} sets neither entity_id nor a year")
        entity = None
        if entity_id:
            entity = entity_id if entity_id in entity_ids else item_entity.get(entity_id)
            if entity is None:
                raise ValueError(f"tradition_placement_overrides row for {qid} targets unknown entity {entity_id}")
        resolved[qid] = (entity, int(start) if start else None, int(end) if end else None)
    return resolved


def _assert_acyclic(links: set[tuple[str, str]]) -> None:
    children = defaultdict(list)
    indegree: dict[str, int] = defaultdict(int)
    for source, target in links:
        children[source].append(target)
        indegree[target] += 1
        indegree.setdefault(source, indegree.get(source, 0))
    ready = [node for node, degree in indegree.items() if degree == 0]
    visited = 0
    while ready:
        node = ready.pop()
        visited += 1
        for child in children[node]:
            indegree[child] -= 1
            if indegree[child] == 0:
                ready.append(child)
    if visited != len(indegree):
        cyclic = sorted(node for node, degree in indegree.items() if degree > 0)
        raise ValueError(
            f"tradition lineage has a cycle, break it with a tradition_link_overrides remove: {cyclic[:10]}"
        )


def _apply_link_overrides(links: dict[str, set[tuple[str, str]]], overrides: pl.DataFrame, known: set[str]) -> None:
    for source, target, kind, action in overrides.select("from_wikidata_id", "to_wikidata_id", "kind", "action").rows():
        if kind not in TRADITION_LINK_KINDS:
            raise ValueError(f"tradition_link_overrides kind {kind!r} not in {TRADITION_LINK_KINDS}")
        if action not in MEMBERSHIP_ACTIONS:
            raise ValueError(f"tradition_link_overrides action {action!r} not in {MEMBERSHIP_ACTIONS}")
        if source == target:
            raise ValueError(f"tradition_link_overrides row links {source} to itself")
        if action == "add":
            unknown = {source, target} - known
            if unknown:
                raise ValueError(f"tradition_link_overrides add targets non-tradition item(s) {sorted(unknown)}")
            links[kind].add((source, target))
        elif (source, target) in links[kind]:
            links[kind].remove((source, target))
        else:
            logger.warning(
                "tradition_link_overrides remove %s -> %s (%s) matches no derived link", source, target, kind
            )


def tradition(wikidata_bronze_dir: Path, silver_dir: Path, curation_dir: Path) -> Path:
    claims = pl.read_parquet(wikidata_bronze_dir / "wikidata_music_tradition_claims.parquet")
    tree = pl.read_parquet(wikidata_bronze_dir / "wikidata_genre_tree.parquet")
    entity_ids = set(pl.read_parquet(silver_dir / "1_cultural_entity.parquet")["entity_id"])
    crosswalk = pl.read_parquet(silver_dir / "1_entity_wikidata_item.parquet").sort("entity_id")
    music_anchor = pl.read_parquet(silver_dir / "5_music_anchor.parquet")
    time_span = pl.read_parquet(silver_dir / "4_entity_time_span.parquet")

    additions = set(read_curation_list(curation_dir, "tradition_additions")["wikidata_id"])
    exclusions = set(read_curation_list(curation_dir, "tradition_exclusions")["wikidata_id"])
    if additions & exclusions:
        raise ValueError(f"tradition additions and exclusions share {sorted(additions & exclusions)}")

    item_entity: dict[str, str] = {}
    for entity_id, wikidata_id in crosswalk.select("entity_id", "wikidata_id").rows():
        item_entity.setdefault(wikidata_id, entity_id)
    spans = {e: (s, t) for e, s, t in time_span.select("entity_id", "start_year", "end_year").rows()}
    anchored = {q: (a, r) for q, a, r in music_anchor.select("wikidata_id", "anchor_id", "rule").rows()}
    values: dict[tuple[str, str], list[str]] = defaultdict(list)
    for item, prop, value in claims.filter(pl.col("value").is_not_null()).select("item_id", "property", "value").rows():
        values[(item, prop)].append(value)

    dated = dated_claims(claims)
    claim_start, claim_end = _claim_years(dated, "start", latest=False), _claim_years(dated, "end", latest=True)
    labels = {
        item: label
        for item, label in claims.filter(pl.col("property") == "label")
        .sort("item_id", "value")
        .unique("item_id", keep="first", maintain_order=True)
        .select("item_id", "value")
        .rows()
    }
    genres = set(tree["item_id"])
    placements = _placement_overrides(
        read_curation_list(curation_dir, "tradition_placement_overrides"), entity_ids, item_entity
    )

    rows = []
    for qid, label in sorted(labels.items()):
        if qid in exclusions:
            continue
        _, override_start, override_end = placements.get(qid, (None, None, None))
        known_start = override_start if override_start is not None else claim_start.get(qid)

        anchor, rule = anchored.get(qid, (None, None))

        start = known_start if known_start is not None else spans.get(anchor, (None, None))[0]
        end = override_end if override_end is not None else claim_end.get(qid)
        is_overview = label.startswith(OVERVIEW_PREFIX)
        if qid not in additions:
            if qid not in genres and not is_overview:
                continue
            if start is None or start > CUTOFF_YEAR or (is_overview and anchor is None):
                continue
        rows.append((qid, label, start, min(end, CUTOFF_YEAR) if end is not None else CUTOFF_YEAR, anchor, rule))

    traditions = pl.DataFrame(rows, schema=TRADITION_SCHEMA, orient="row").sort("wikidata_id")
    known = set(traditions["wikidata_id"])
    unknown_overrides = set(placements) - known
    if unknown_overrides:
        logger.warning(
            "%d placement override(s) target non-tradition items: %s",
            len(unknown_overrides),
            sorted(unknown_overrides)[:5],
        )

    links: dict[str, set[tuple[str, str]]] = {"lineage": set(), "association": set()}
    for child, parent in tree.select("item_id", "parent_id").rows():
        links["lineage"].add((parent, child))
    for (item, prop), linked in values.items():
        for other in linked:
            if prop == "P155":
                links["lineage"].add((other, item))
            elif prop == "P156":
                links["lineage"].add((item, other))
            elif prop == "P737":
                links["association"].add((other, item))
    for kind in links:
        links[kind] = {(a, b) for a, b in links[kind] if a != b and a in known and b in known}
    _apply_link_overrides(links, read_curation_list(curation_dir, "tradition_link_overrides"), known)
    _assert_acyclic(links["lineage"])
    link_frame = pl.DataFrame(
        [(a, b, kind) for kind, pairs in links.items() for a, b in pairs], schema=LINK_SCHEMA, orient="row"
    ).sort("kind", "from_wikidata_id", "to_wikidata_id")

    unplaced = traditions.filter(pl.col("anchor_id").is_null()).select("wikidata_id", "label", "start_year")
    if unplaced.height:
        logger.warning("%d tradition(s) have no anchor entity, see 5_tradition_unplaced.csv", unplaced.height)

    traditions.write_parquet(silver_dir / "5_tradition.parquet")
    link_frame.write_parquet(silver_dir / "5_tradition_link.parquet")
    unplaced.write_csv(silver_dir / "5_tradition_unplaced.csv")
    (silver_dir / "5_tradition_report.json").write_text(f'{{"unplacedTraditionCount": {unplaced.height}}}\n')
    logger.info("wrote %d traditions and %d links to %s", traditions.height, link_frame.height, silver_dir)
    return silver_dir / "5_tradition.parquet"
