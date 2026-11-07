"""Cultural roles and memberships, independent of spatial containment."""

import json
import logging
from collections import defaultdict, deque
from pathlib import Path

import polars as pl

from geohistory.silver.curation import read_curation_list
from geohistory.silver.entity_membership import _assert_acyclic

logger = logging.getLogger(__name__)
ROLE_SCHEMA = {
    "entity_id": pl.String,
    "is_cultural": pl.Boolean,
    "source": pl.String,
    "reason": pl.String,
}
MEMBERSHIP_SCHEMA = {
    "child_id": pl.String,
    "parent_id": pl.String,
    "source": pl.String,
    "reason": pl.String,
}


def _boolean(value: str | None) -> bool:
    # Curation exports false as an empty CSV cell.
    if value is None or value == "" or value == "false":
        return False
    if value == "true":
        return True
    raise ValueError(f"invalid cultural role boolean: {value!r}")


def build_cultural_roles(entities: pl.DataFrame, rules: pl.DataFrame) -> pl.DataFrame:
    roles = {
        entity: (
            True if kind == "cultural_region" else None,
            "entity_kind" if kind == "cultural_region" else None,
            None,
        )
        for entity, kind in entities.select("entity_id", "kind").iter_rows()
    }
    seen = set()
    for entity, cultural, reason in rules.iter_rows():
        if entity not in roles or entity in seen or not reason or not reason.strip():
            raise ValueError(f"invalid or duplicate entity_cultural_roles: {entity}")
        seen.add(entity)
        roles[entity] = (_boolean(cultural), "curation", reason)
    return pl.DataFrame(
        [(entity, *values) for entity, values in sorted(roles.items())], schema=ROLE_SCHEMA, orient="row"
    )


def build_cultural_memberships(
    roles: pl.DataFrame, administrative_edges: pl.DataFrame, rules: pl.DataFrame
) -> pl.DataFrame:
    ids = set(roles["entity_id"])
    _assert_acyclic(administrative_edges, "administrative cultural inheritance")
    memberships = {}
    for child, parent in administrative_edges.select("child_id", "parent_id").iter_rows():
        if child not in ids or parent not in ids:
            raise ValueError("administrative cultural inheritance references an unknown entity")
        memberships[(child, parent)] = ("administrative_inheritance", "current_administration")
    excluded = set()
    seen_pairs = set()
    for child, parent, action, reason in rules.iter_rows():
        pair = (child, parent)
        if (
            child not in ids
            or parent not in ids
            or child == parent
            or pair in seen_pairs
            or action not in {"add", "remove"}
            or not reason
            or not reason.strip()
        ):
            raise ValueError(f"invalid or duplicate cultural_membership_overrides: {pair}")
        seen_pairs.add(pair)
        if action == "add":
            memberships[pair] = ("curation", reason)
        else:
            excluded.add(pair)
            memberships.pop(pair, None)
    direct = pl.DataFrame(list(memberships), schema={"child_id": pl.String, "parent_id": pl.String}, orient="row")
    _assert_acyclic(direct, "cultural membership")
    parents: dict[str, set[str]] = defaultdict(set)
    for child, parent in memberships:
        parents[child].add(parent)
    children: dict[str, set[str]] = defaultdict(set)
    remaining = {entity: len(parents[entity]) for entity in ids}
    for child, parent in memberships:
        children[parent].add(child)
    ready = deque(sorted(entity for entity in ids if not remaining[entity]))
    paths: dict[str, dict[str, list[str]]] = {}
    while ready:
        child = ready.popleft()
        ancestors: dict[str, list[str]] = {}
        for parent in sorted(parents[child]):
            candidates = {parent: [child, parent]}
            candidates.update({ancestor: [child, *path] for ancestor, path in paths[parent].items()})
            for ancestor, path in candidates.items():
                if (child, ancestor) in excluded:
                    continue
                previous = ancestors.get(ancestor)
                if previous is None or (len(path), path) < (len(previous), previous):
                    ancestors[ancestor] = path
        paths[child] = ancestors
        for ancestor, path in ancestors.items():
            memberships.setdefault((child, ancestor), ("transitive", json.dumps(path)))
        for descendant in sorted(children[child]):
            remaining[descendant] -= 1
            if not remaining[descendant]:
                ready.append(descendant)
    return pl.DataFrame(
        [(child, parent, *values) for (child, parent), values in sorted(memberships.items())],
        schema=MEMBERSHIP_SCHEMA,
        orient="row",
    )


def cultural_membership(silver_dir: Path, curation_dir: Path) -> tuple[Path, Path]:
    roles = build_cultural_roles(
        pl.read_parquet(silver_dir / "1_cultural_entity.parquet"),
        read_curation_list(curation_dir, "entity_cultural_roles"),
    )
    # A P131 candidate can be accepted on geometry alone; require evidence of current administration.
    relations = pl.read_parquet(silver_dir / "2_place_relation.parquet")
    administrative = (
        pl.DataFrame(
            [
                (child, parent)
                for child, parent, extent, conflict, evidence in relations.select(
                    "child_id", "parent_id", "extent", "conflict", "evidence_json"
                ).iter_rows()
                if extent == "full"
                and not conflict
                and any(
                    e["source"] in {"administrative_p131", "natural_earth"} and e["extent"] == "full"
                    for e in json.loads(evidence)
                )
            ],
            schema={"child_id": pl.String, "parent_id": pl.String},
            orient="row",
        )
        .join(
            pl.read_parquet(silver_dir / "2_place_containment.parquet").select("child_id", "parent_id"),
            on=["child_id", "parent_id"],
            how="semi",
        )
        .unique()
    )
    memberships = build_cultural_memberships(
        roles, administrative, read_curation_list(curation_dir, "cultural_membership_overrides")
    )
    role_path = silver_dir / "2_entity_cultural_role.parquet"
    membership_path = silver_dir / "2_cultural_membership.parquet"
    roles.write_parquet(role_path)
    memberships.write_parquet(membership_path)
    logger.info("wrote %d cultural roles and %d memberships", roles.height, memberships.height)
    return role_path, membership_path
