import logging
from collections import defaultdict
from pathlib import Path

import polars as pl

from geohistory.silver.cultural_entity import ADMIN_KIND
from geohistory.silver.tradition import CUTOFF_YEAR

logger = logging.getLogger(__name__)

Span = tuple[int | None, int | None]
LANE_SCHEMA = {
    "entity_id": pl.String,
    "label": pl.String,
    "column_id": pl.String,
    "start_year": pl.Int32,
    "end_year": pl.Int32,
}
BAND_SCHEMA = {
    "entity_id": pl.String,
    "label": pl.String,
    "column_id": pl.String,
    "start_year": pl.Int32,
    "end_year": pl.Int32,
}


def _ancestors(parents: dict[str, list[str]], entity: str) -> set[str]:
    found: set[str] = set()
    pending = list(parents.get(entity, ()))
    while pending:
        parent = pending.pop()
        if parent not in found:
            found.add(parent)
            pending.extend(parents.get(parent, ()))
    return found


def _envelope(spans: list[Span]) -> Span:
    starts = [s for s, _ in spans if s is not None]
    ends = [e for _, e in spans if e is not None]
    return (min(starts) if starts else None, max(ends) if ends else None)


def _clip(span: Span) -> Span:
    start, end = span
    return (
        min(start, CUTOFF_YEAR) if start is not None else None,
        min(end, CUTOFF_YEAR) if end is not None else CUTOFF_YEAR,
    )


def _overlap(a: Span, b: Span) -> bool:
    (a_start, a_end), (b_start, b_end) = a, b
    if a_start is None or a_end is None or b_start is None or b_end is None:
        return True
    return max(a_start, b_start) < min(a_end, b_end)


def _columns(lanes: list[str], spans: dict[str, Span], successions: list[tuple[str, str]]) -> dict[str, str]:
    members = {lane: [lane] for lane in lanes}
    owner = {lane: lane for lane in lanes}
    lane_set = set(lanes)
    for predecessor, successor in sorted(successions):
        if predecessor not in lane_set or successor not in lane_set:
            continue
        left, right = owner[predecessor], owner[successor]
        if left == right:
            continue
        if any(_overlap(spans[a], spans[b]) for a in members[left] for b in members[right]):
            continue
        keep, drop = sorted((left, right))
        members[keep].extend(members.pop(drop))
        for lane in members[keep]:
            owner[lane] = keep
    return {lane: min(members[root]) for lane, root in owner.items()}


def _lift_admin_anchors(traditions: pl.DataFrame, entities: pl.DataFrame, containment: pl.DataFrame) -> pl.DataFrame:
    """Lanes stay at country level: an admin-unit anchor stands in as its nearest non-admin containing place."""
    kinds = dict(entities.select("entity_id", "kind").rows())
    parents: dict[str, list[str]] = defaultdict(list)
    for child, parent in containment.sort("child_id", "parent_id").select("child_id", "parent_id").rows():
        parents[child].append(parent)

    def lifted(anchor: str) -> str:
        level, seen = [anchor], {anchor}
        while level:
            found = sorted(node for node in level if kinds.get(node) != ADMIN_KIND)
            if found:
                return found[0]
            upper = sorted({p for node in level for p in parents.get(node, ())} - seen)
            seen.update(upper)
            level = upper
        raise ValueError(f"admin unit {anchor} has no non-admin containing place to lift to")

    return traditions.with_columns(pl.col("anchor_id").map_elements(lifted, return_dtype=pl.String))


def genealogy_lane(silver_dir: Path) -> tuple[Path, Path]:
    entities = pl.read_parquet(silver_dir / "1_cultural_entity.parquet")
    traditions = _lift_admin_anchors(
        pl.read_parquet(silver_dir / "5_tradition.parquet").drop_nulls("anchor_id"),
        entities,
        pl.read_parquet(silver_dir / "2_place_containment.parquet"),
    )
    membership = pl.read_parquet(silver_dir / "2_entity_membership.parquet")
    succession = pl.read_parquet(silver_dir / "3_entity_succession.parquet")
    time_span = pl.read_parquet(silver_dir / "4_entity_time_span.parquet")
    labels = dict(entities.select("entity_id", "label").rows())

    anchored = set(traditions["anchor_id"])
    parents: dict[str, list[str]] = defaultdict(list)
    for child, parent in membership.select("child_id", "parent_id").rows():
        parents[child].append(parent)
    ancestors = {entity: _ancestors(parents, entity) for entity in anchored}
    has_descendant = {a for entity in anchored for a in ancestors[entity] if a in anchored}
    lanes = sorted(anchored - has_descendant)

    lane_ancestors = {lane: ancestors[lane] for lane in lanes}
    covered: dict[str, set[str]] = defaultdict(set)
    for lane, above in lane_ancestors.items():
        for entity in above:
            covered[entity].add(lane)
    candidates = {e: lane_set for e, lane_set in covered.items() if len(lane_set) >= 2 or e in anchored}
    candidates.update({e: covered[e] for e in has_descendant})
    redundant = {
        e
        for e, lane_set in candidates.items()
        if e not in anchored
        and any(c != e and e in _ancestors(parents, c) and candidates[c] == lane_set for c in candidates)
    }
    bands = {e: lane_set for e, lane_set in candidates.items() if e not in redundant}

    own = {e: (s, t) for e, s, t in time_span.select("entity_id", "start_year", "end_year").rows()}
    anchored_spans = {
        entity: _envelope(list(zip(group["start_year"], group["end_year"], strict=True)))
        for (entity,), group in traditions.group_by("anchor_id")
    }

    def span_of(entity: str) -> Span:
        start, end = own.get(entity, (None, None))
        fallback = anchored_spans.get(entity, (None, None))
        return (start if start is not None else fallback[0], end if end is not None else fallback[1])

    spans = {lane: _clip(span_of(lane)) for lane in lanes}
    column_of = _columns(lanes, spans, list(succession.select("predecessor_id", "successor_id").rows()))

    lane_rows = [(lane, labels[lane], column_of[lane], *spans[lane]) for lane in lanes]
    band_rows: list[tuple[str, str, str, int | None, int | None]] = []
    for band, lane_set in sorted(bands.items()):
        start, end = span_of(band)
        if start is None:
            start, end = _envelope([spans[lane] for lane in lane_set])
        span = _clip((start, end))
        band_rows.extend(
            (band, labels[band], column, *span) for column in sorted({column_of[lane] for lane in lane_set})
        )

    lane_path, band_path = silver_dir / "6_genealogy_lane.parquet", silver_dir / "6_genealogy_band.parquet"
    traditions.select("wikidata_id", pl.col("anchor_id").alias("lane_id")).sort("wikidata_id").write_parquet(
        silver_dir / "6_genealogy_tradition_lane.parquet"
    )
    pl.DataFrame(lane_rows, schema=LANE_SCHEMA, orient="row").write_parquet(lane_path)
    pl.DataFrame(band_rows, schema=BAND_SCHEMA, orient="row").write_parquet(band_path)
    logger.info("wrote %d lanes and %d bands to %s", len(lanes), len(bands), silver_dir)
    return lane_path, band_path
