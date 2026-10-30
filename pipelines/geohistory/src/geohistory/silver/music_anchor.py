import logging
from collections import defaultdict
from pathlib import Path

import polars as pl

from geohistory.silver.curation import read_curation_list
from geohistory.silver.entity_time_span import dated_claims
from geohistory.silver.tradition import _claim_years, _lift_to_date, _placement_overrides

logger = logging.getLogger(__name__)

OVERVIEW_PREFIX = "music of "
CULTURE_PROPERTIES = ("P2596", "P1269")
COUNTRY_OF_ORIGIN = "P495"
PLACE_PROPERTIES = ("P17", "P276", "P921", "P361")
PLACE_KINDS = (
    "country",
    "historical_country",
    "historical_region",
    "cultural_region",
    "macro_region",
    "admin_unit",
)
RULE_PRIORITY = ("override", *CULTURE_PROPERTIES, COUNTRY_OF_ORIGIN, *PLACE_PROPERTIES, "label")
ANCHOR_SCHEMA = {"wikidata_id": pl.String, "anchor_id": pl.String, "rule": pl.String, "is_overview": pl.Boolean}
UNPLACED_SCHEMA = {"wikidata_id": pl.String, "label": pl.String, "candidate_ids": pl.String}


def _ancestors(entity: str, parents: dict[str, list[str]]) -> set[str]:
    seen: set[str] = set()
    pending = list(parents.get(entity, ()))
    while pending:
        node = pending.pop()
        if node not in seen:
            seen.add(node)
            pending.extend(parents.get(node, ()))
    return seen


def _pick(candidates: dict[str, str], parents: dict[str, list[str]]) -> tuple[str, str] | None:
    """Keep the most specific candidates (no candidate is an ancestor of another), then the best rule."""
    dominated = set().union(*(_ancestors(entity, parents) for entity in candidates)) if candidates else set()
    remaining = [(entity, rule) for entity, rule in candidates.items() if entity not in dominated]
    if not remaining:
        return None
    return min(remaining, key=lambda pair: (RULE_PRIORITY.index(pair[1]), pair[0]))


def _label_index(entities: pl.DataFrame, crosswalk: pl.DataFrame, aliases: pl.DataFrame) -> dict[str, set[str]]:
    index: dict[str, set[str]] = defaultdict(set)
    for entity_id, label in entities.filter(pl.col("kind").is_in(PLACE_KINDS)).select("entity_id", "label").rows():
        index[label.casefold()].add(entity_id)
    item_entities: dict[str, list[str]] = defaultdict(list)
    for entity_id, wikidata_id in crosswalk.rows():
        item_entities[wikidata_id].append(entity_id)
    place_ids = set(entities.filter(pl.col("kind").is_in(PLACE_KINDS))["entity_id"])
    for item, alias in aliases.select("item_id", "alias").rows():
        index[alias.casefold()].update(e for e in item_entities.get(item, ()) if e in place_ids)
    return index


def _collapse_equivalent(matches: set[str], kinds: dict[str, str], footprint_admins: dict[str, set[str]]) -> set[str]:
    """One admin_unit plus zones whose footprint is exactly that admin are the same place: keep the admin.

    A non-admin match with no footprint has no geometry to draw, so it cannot make the label ambiguous:
    it is dropped as long as a placeable match remains.
    """
    placeable = {e for e in matches if kinds.get(e) == "admin_unit" or footprint_admins.get(e)}
    if placeable:
        matches = placeable
    admins = [entity for entity in matches if kinds.get(entity) == "admin_unit"]
    if len(matches) < 2 or len(admins) != 1:
        return matches
    admin = admins[0]
    if all(footprint_admins.get(entity) == {admin} for entity in matches if entity != admin):
        return {admin}
    return matches


def music_anchor(wikidata_bronze_dir: Path, silver_dir: Path, curation_dir: Path) -> Path:
    claims = pl.read_parquet(wikidata_bronze_dir / "wikidata_music_tradition_claims.parquet")
    tree = pl.read_parquet(wikidata_bronze_dir / "wikidata_genre_tree.parquet")
    aliases = pl.read_parquet(wikidata_bronze_dir / "wikidata_cultural_entity_alias.parquet")
    entities = pl.read_parquet(silver_dir / "1_cultural_entity.parquet")
    crosswalk = pl.read_parquet(silver_dir / "1_entity_wikidata_item.parquet").sort("entity_id")
    succession = pl.read_parquet(silver_dir / "3_entity_succession.parquet").sort("predecessor_id", "successor_id")
    time_span = pl.read_parquet(silver_dir / "4_entity_time_span.parquet")
    membership = pl.read_parquet(silver_dir / "2_entity_membership.parquet")
    containment = pl.read_parquet(silver_dir / "2_place_containment.parquet")
    footprint = pl.read_parquet(silver_dir / "8_entity_footprint.parquet")

    entity_ids = set(entities["entity_id"])
    place_ids = set(entities.filter(pl.col("kind").is_in(PLACE_KINDS))["entity_id"])
    item_entity: dict[str, str] = {}
    for entity_id, wikidata_id in crosswalk.select("entity_id", "wikidata_id").rows():
        item_entity.setdefault(wikidata_id, entity_id)
    spans = {e: (s, t) for e, s, t in time_span.select("entity_id", "start_year", "end_year").rows()}
    successors: dict[str, list[str]] = defaultdict(list)
    for predecessor, successor in succession.select("predecessor_id", "successor_id").rows():
        successors[predecessor].append(successor)
    parents: dict[str, list[str]] = defaultdict(list)
    for child, parent in membership.select("child_id", "parent_id").rows():
        parents[child].append(parent)
    for child, parent in containment.select("child_id", "parent_id").rows():
        parents[child].append(parent)
    values: dict[tuple[str, str], list[str]] = defaultdict(list)
    for item, prop, value in claims.filter(pl.col("value").is_not_null()).select("item_id", "property", "value").rows():
        values[(item, prop)].append(value)
    claim_start = _claim_years(dated_claims(claims), "start", latest=False)
    labels = dict(
        claims.filter(pl.col("property") == "label")
        .sort("item_id", "value")
        .unique("item_id", keep="first", maintain_order=True)
        .select("item_id", "value")
        .rows()
    )
    genres = set(tree["item_id"])
    additions = set(read_curation_list(curation_dir, "tradition_additions")["wikidata_id"])
    exclusions = set(read_curation_list(curation_dir, "tradition_exclusions")["wikidata_id"])
    placements = _placement_overrides(
        read_curation_list(curation_dir, "tradition_placement_overrides"), entity_ids, item_entity
    )
    kinds = dict(entities.select("entity_id", "kind").rows())
    footprint_admins: dict[str, set[str]] = defaultdict(set)
    for entity_id, admin_id in footprint.select("entity_id", "admin_id").rows():
        footprint_admins[entity_id].add(admin_id)
    label_index = _label_index(entities, crosswalk, aliases)

    anchors, unplaced = [], []
    for qid, label in sorted(labels.items()):
        is_overview = label.startswith(OVERVIEW_PREFIX)
        if qid in exclusions or (qid not in genres and not is_overview and qid not in additions):
            continue
        override_entity, override_start, _ = placements.get(qid, (None, None, None))
        known_start = override_start if override_start is not None else claim_start.get(qid)

        candidates: dict[str, str] = {}

        def add(entity: str | None, rule: str) -> None:
            if entity is not None:
                candidates.setdefault(entity, rule)

        add(override_entity, "override")
        for prop in CULTURE_PROPERTIES:
            for value in values[(qid, prop)]:
                add(item_entity.get(value), prop)
        for value in values[(qid, COUNTRY_OF_ORIGIN)]:
            origin = item_entity.get(value)
            if origin is not None:
                add(
                    _lift_to_date(origin, known_start, spans, successors) if known_start is not None else origin,
                    COUNTRY_OF_ORIGIN,
                )
        for prop in PLACE_PROPERTIES:
            for value in values[(qid, prop)]:
                entity = item_entity.get(value)
                if entity in place_ids:
                    add(entity, prop)
        label_matches: set[str] = set()
        if is_overview:
            label_matches = _collapse_equivalent(
                label_index.get(label.removeprefix(OVERVIEW_PREFIX).removeprefix("the ").casefold(), set()),
                kinds,
                footprint_admins,
            )
            if len(label_matches) == 1:
                add(next(iter(label_matches)), "label")

        picked = (override_entity, "override") if override_entity else _pick(candidates, parents)
        if picked is None:
            if is_overview:
                unplaced.append((qid, label, "|".join(sorted(set(candidates) | label_matches))))
            continue
        anchors.append((qid, picked[0], picked[1], is_overview))

    frame = pl.DataFrame(anchors, schema=ANCHOR_SCHEMA, orient="row").sort("wikidata_id")
    unplaced_frame = pl.DataFrame(unplaced, schema=UNPLACED_SCHEMA, orient="row").sort("wikidata_id")
    if unplaced_frame.height:
        logger.warning("%d overview(s) have no music anchor, see 5_music_anchor_unplaced.csv", unplaced_frame.height)
    frame.write_parquet(silver_dir / "5_music_anchor.parquet")
    unplaced_frame.write_csv(silver_dir / "5_music_anchor_unplaced.csv")
    logger.info("wrote %d music anchors to %s", frame.height, silver_dir)
    return silver_dir / "5_music_anchor.parquet"
