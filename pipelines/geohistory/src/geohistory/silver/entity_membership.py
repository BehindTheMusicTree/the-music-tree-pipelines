import logging
from collections import defaultdict
from pathlib import Path

import polars as pl
from common.quality_checks import check_unique_key

from geohistory.silver.curation import MEMBERSHIP_ACTIONS, read_curation_list

logger = logging.getLogger(__name__)

GLOTTOLOG_KINDS = ("family", "language", "dialect")


def _assert_acyclic(edges: pl.DataFrame, name: str = "2_entity_membership") -> None:
    children = defaultdict(list)
    pending_parents: dict[str, int] = defaultdict(int)
    for child_id, parent_id in edges.select("child_id", "parent_id").iter_rows():
        children[parent_id].append(child_id)
        pending_parents[child_id] += 1
        pending_parents.setdefault(parent_id, 0)
    ready = [node for node, count in pending_parents.items() if count == 0]
    while ready:
        node = ready.pop()
        del pending_parents[node]
        for child in children[node]:
            pending_parents[child] -= 1
            if pending_parents[child] == 0:
                ready.append(child)
    if pending_parents:
        cyclic = edges.filter(pl.col("child_id").is_in(pending_parents) & pl.col("parent_id").is_in(pending_parents))
        raise ValueError(
            f"{name} has a cycle through {sorted(pending_parents)[:5]}, edges (child, parent): {cyclic.head(5).rows()}"
        )


def _apply_overrides(edges: pl.DataFrame, entity_ids: list[str], overrides: pl.DataFrame) -> pl.DataFrame:
    invalid = overrides.filter(~pl.col("action").is_in(MEMBERSHIP_ACTIONS))
    if invalid.height:
        raise ValueError(f"entity_membership_overrides has invalid action(s): {invalid['action'].unique().to_list()}")
    additions = overrides.filter(pl.col("action") == "add").select(
        pl.col("child_entity_id").alias("child_id"), pl.col("parent_entity_id").alias("parent_id")
    )
    unknown = additions.filter(~pl.col("child_id").is_in(entity_ids) | ~pl.col("parent_id").is_in(entity_ids))
    if unknown.height:
        raise ValueError(f"entity_membership_overrides adds edge(s) on unknown entities: {unknown.head(5).rows()}")
    removals = overrides.filter(pl.col("action") == "remove").select(
        pl.col("child_entity_id").alias("child_id"), pl.col("parent_entity_id").alias("parent_id")
    )
    missing = removals.join(edges.select("child_id", "parent_id"), on=["child_id", "parent_id"], how="anti")
    if missing.height:
        logger.warning("%d removed edge(s) do not exist: %s", missing.height, missing.head(5).rows())
    return pl.concat(
        [
            edges.join(removals, on=["child_id", "parent_id"], how="anti"),
            additions.with_columns(pl.lit("curation").alias("source")),
        ]
    )


def entity_membership(bronze_dir: Path, wikidata_bronze_dir: Path, silver_dir: Path, curation_dir: Path) -> Path:
    languoids = pl.read_parquet(bronze_dir / "glottolog_languoid.parquet")
    claims = pl.read_parquet(wikidata_bronze_dir / "wikidata_cultural_entity_claim.parquet")
    entities = pl.read_parquet(silver_dir / "1_cultural_entity.parquet")
    crosswalk = pl.read_parquet(silver_dir / "1_entity_wikidata_item.parquet")

    entity_ids = entities.select("entity_id")
    glottolog_ids = entities.filter(pl.col("kind").is_in(GLOTTOLOG_KINDS))["entity_id"].to_list()

    glottolog_edges = (
        languoids.select(
            pl.col("glottocode").alias("child_id"),
            pl.col("classification").str.split("/").list.last().alias("parent_id"),
        )
        .filter(pl.col("parent_id").is_not_null() & (pl.col("parent_id") != ""))
        .join(entity_ids, left_on="child_id", right_on="entity_id", how="semi")
        .join(entity_ids, left_on="parent_id", right_on="entity_id", how="semi")
        .with_columns(pl.lit("glottolog").alias("source"))
    )

    wikidata_edges = (
        claims.filter(pl.col("property") == "P361")
        .select(pl.col("item_id").alias("child_item"), pl.col("value").alias("parent_item"))
        .join(
            crosswalk.rename({"entity_id": "child_id", "wikidata_id": "child_item"}),
            on="child_item",
        )
        .join(
            crosswalk.rename({"entity_id": "parent_id", "wikidata_id": "parent_item"}),
            on="parent_item",
        )
        .filter(
            (pl.col("child_id") != pl.col("parent_id"))
            & ~(pl.col("child_id").is_in(glottolog_ids) & pl.col("parent_id").is_in(glottolog_ids))
        )
        .select("child_id", "parent_id")
        .with_columns(pl.lit("wikidata_p361").alias("source"))
    )

    edges = (
        _apply_overrides(
            pl.concat([glottolog_edges, wikidata_edges]),
            entities["entity_id"].to_list(),
            read_curation_list(curation_dir, "entity_membership_overrides"),
        )
        .unique(["child_id", "parent_id"], keep="first", maintain_order=True)
        .sort("child_id", "parent_id")
    )
    _assert_acyclic(edges)
    check_unique_key(
        edges.select(pl.concat_str("child_id", "parent_id", separator="|").alias("key")), "key", "2_entity_membership"
    )

    output_path = silver_dir / "2_entity_membership.parquet"
    edges.write_parquet(output_path)
    logger.info("wrote %d rows to %s", edges.height, output_path)
    return output_path
