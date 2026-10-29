import logging
from collections import defaultdict
from pathlib import Path

import polars as pl
from common.quality_checks import check_unique_key

from geohistory.silver.curation import read_curation_list
from geohistory.silver.entity_membership import GLOTTOLOG_KINDS, _assert_acyclic
from geohistory.silver.music_anchor import PLACE_KINDS

logger = logging.getLogger(__name__)

SOURCE_PRIORITY = ("p131", "p361", "p30", "p706", "natural_earth")
PAIR_SCHEMA = {"parent_id": pl.String, "child_id": pl.String}
EDGE_SCHEMA = PAIR_SCHEMA | {"source": pl.String}
CLAIM_SOURCES = {"P30": "p30", "P706": "p706"}


def _ancestors(entity: str, parents: dict[str, set[str]]) -> set[str]:
    seen: set[str] = set()
    pending = list(parents.get(entity, ()))
    while pending:
        node = pending.pop()
        if node not in seen:
            seen.add(node)
            pending.extend(parents.get(node, ()))
    return seen


def _p131_edges(hierarchy: pl.DataFrame, item_entity: dict[str, str], place_ids: set[str]) -> list[tuple[str, str]]:
    direct: dict[str, list[str]] = defaultdict(list)
    for item, value in (
        hierarchy.filter(pl.col("property") == "P131").select("item_id", "value").sort("item_id", "value").rows()
    ):
        direct[item].append(value)

    def first_places(item: str, seen: set[str]) -> set[str]:
        found: set[str] = set()
        for value in direct.get(item, ()):
            entity = item_entity.get(value)
            if entity in place_ids:
                found.add(entity)
            elif value not in seen:
                found |= first_places(value, seen | {value})
        return found

    edges: list[tuple[str, str]] = []
    for item in sorted(direct):
        child = item_entity.get(item)
        if child in place_ids:
            edges.extend((parent, child) for parent in sorted(first_places(item, {item})))
    return edges


def _item_edges(pairs: pl.DataFrame, item_entity: dict[str, str], place_ids: set[str]) -> list[tuple[str, str]]:
    edges: list[tuple[str, str]] = []
    for item, value in pairs.select("item_id", "value").sort("item_id", "value").rows():
        child, parent = item_entity.get(item), item_entity.get(value)
        if child in place_ids and parent in place_ids:
            edges.append((parent, child))
    return edges


def place_containment(wikidata_bronze_dir: Path, silver_dir: Path, curation_dir: Path) -> Path:
    claims = pl.read_parquet(wikidata_bronze_dir / "wikidata_cultural_entity_claim.parquet")
    hierarchy = pl.read_parquet(wikidata_bronze_dir / "wikidata_admin_hierarchy.parquet")
    entities = pl.read_parquet(silver_dir / "1_cultural_entity.parquet")
    crosswalk = pl.read_parquet(silver_dir / "1_entity_wikidata_item.parquet").sort("entity_id", "wikidata_id")
    ne_map = pl.read_parquet(silver_dir / "1_entity_ne_admin.parquet")
    membership = pl.read_parquet(silver_dir / "2_entity_membership.parquet")

    place_ids = set(entities.filter(pl.col("kind").is_in(PLACE_KINDS))["entity_id"])
    item_entity: dict[str, str] = {}
    for entity_id, wikidata_id in crosswalk.select("entity_id", "wikidata_id").rows():
        item_entity.setdefault(wikidata_id, entity_id)

    removals = (
        read_curation_list(curation_dir, "entity_membership_overrides")
        .filter(pl.col("action") == "remove")
        .select(pl.col("parent_entity_id").alias("parent_id"), pl.col("child_entity_id").alias("child_id"))
    )
    p361_hierarchy = pl.DataFrame(
        _item_edges(hierarchy.filter(pl.col("property") == "P361"), item_entity, place_ids),
        schema=PAIR_SCHEMA,
        orient="row",
    ).join(removals, on=["parent_id", "child_id"], how="anti")
    p361_membership = membership.filter(
        (pl.col("source") != "glottolog")
        & pl.col("child_id").is_in(place_ids)
        & pl.col("parent_id").is_in(place_ids)
        & ~pl.col("child_id").is_in(entities.filter(pl.col("kind").is_in(GLOTTOLOG_KINDS))["entity_id"].to_list())
    ).select("parent_id", "child_id")

    def frame(pairs: list[tuple[str, str]], source: str) -> pl.DataFrame:
        return pl.DataFrame(pairs, schema=PAIR_SCHEMA, orient="row").with_columns(pl.lit(source).alias("source"))

    sourced = [
        frame(_p131_edges(hierarchy, item_entity, place_ids), "p131"),
        pl.concat([p361_hierarchy, p361_membership]).with_columns(pl.lit("p361").alias("source")),
        *(
            frame(
                _item_edges(
                    claims.filter((pl.col("property") == prop) & pl.col("value").is_not_null()), item_entity, place_ids
                ),
                source,
            )
            for prop, source in CLAIM_SOURCES.items()
        ),
    ]

    ranked = (
        pl.concat(sourced)
        .filter(pl.col("parent_id") != pl.col("child_id"))
        .with_columns(
            pl.col("source")
            .replace_strict({source: rank for rank, source in enumerate(SOURCE_PRIORITY)}, return_dtype=pl.Int8)
            .alias("rank")
        )
        .sort("rank", "parent_id", "child_id")
        .unique(["parent_id", "child_id"], keep="first", maintain_order=True)
        .join(removals, on=["parent_id", "child_id"], how="anti")
    )
    reverse = ranked.select(
        pl.col("parent_id").alias("child_id"), pl.col("child_id").alias("parent_id"), pl.col("rank").alias("other_rank")
    )
    conflicts = ranked.join(reverse, on=["parent_id", "child_id"]).filter(pl.col("rank") >= pl.col("other_rank"))
    if conflicts.height:
        logger.warning(
            "%d mutual containment claim(s) dropped, the lower-priority source loses and ties drop both: %s",
            conflicts.height,
            conflicts.head(5).select("parent_id", "child_id", "source").rows(),
        )
    edges = ranked.join(conflicts.select("parent_id", "child_id"), on=["parent_id", "child_id"], how="anti").drop(
        "rank"
    )

    parents: dict[str, set[str]] = defaultdict(set)
    for parent, child in edges.select("parent_id", "child_id").rows():
        parents[child].add(parent)
    fallback = []
    for entity_id, country_id in (
        ne_map.filter(
            (pl.col("level") == 1) & pl.col("entity_id").is_not_null() & pl.col("country_entity_id").is_not_null()
        )
        .select("entity_id", "country_entity_id")
        .unique()
        .sort("entity_id", "country_entity_id")
        .rows()
    ):
        if entity_id == country_id or entity_id not in place_ids or country_id not in place_ids:
            continue
        if country_id in _ancestors(entity_id, parents) or entity_id in _ancestors(country_id, parents):
            continue
        fallback.append((country_id, entity_id, "natural_earth"))
        parents[entity_id].add(country_id)

    edges = pl.concat([edges, pl.DataFrame(fallback, schema=EDGE_SCHEMA, orient="row")]).sort("parent_id", "child_id")
    _assert_acyclic(edges, "2_place_containment")
    check_unique_key(
        edges.select(pl.concat_str("parent_id", "child_id", separator="|").alias("key")), "key", "2_place_containment"
    )

    output_path = silver_dir / "2_place_containment.parquet"
    edges.write_parquet(output_path)
    logger.info("wrote %d rows to %s", edges.height, output_path)
    return output_path
