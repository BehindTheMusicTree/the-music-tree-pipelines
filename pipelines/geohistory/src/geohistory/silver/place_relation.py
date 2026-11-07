"""Resolve geographic candidates before projecting full containment."""

import json
from collections import Counter
from pathlib import Path

import polars as pl

from geohistory.silver.curation import read_place_relation_overrides
from geohistory.silver.place_relation_geometry import ReferenceGeometry
from geohistory.silver.place_relation_resolution import (
    PlaceStatement,
    SpatialEvidence,
    resolve_relations,
    resolve_statement_evidence,
)
from geohistory.silver.place_statements import best_place_statements
from geohistory.silver.reference_land import (
    composed_references,
    reference_admin_geometries,
    reference_island_geometries,
)

# Natural Earth source coordinates are rounded to six decimal places.
REFERENCE_PRECISION = 0.000001


def resolve_place_candidates(
    bronze_dir: Path,
    wikidata_dir: Path,
    silver_dir: Path,
    curation_dir: Path,
    candidates: pl.DataFrame,
    entities: pl.DataFrame,
    crosswalk: pl.DataFrame,
    ne_map: pl.DataFrame,
) -> pl.DataFrame:
    for name in ("wikidata_place_statement", "wikidata_place_qualifier", "wikidata_place_metadata"):
        if not (wikidata_dir / f"{name}.parquet").exists():
            raise FileNotFoundError(f"{name}.parquet is missing: rerun Wikidata Bronze before geohistory Silver")
    statements, qualifiers = best_place_statements(
        pl.read_parquet(wikidata_dir / "wikidata_place_statement.parquet"),
        pl.read_parquet(wikidata_dir / "wikidata_place_qualifier.parquet"),
    )
    overrides = read_place_relation_overrides(curation_dir)
    entity_ids = set(entities["entity_id"])
    if any(child not in entity_ids or parent not in entity_ids for child, parent in overrides):
        raise ValueError("place_relation_overrides references an unknown entity")
    kinds = dict(entities.select("entity_id", "kind").rows())
    geometries = reference_admin_geometries(bronze_dir, ne_map)
    islands = reference_island_geometries(bronze_dir, crosswalk, entities)
    geometries.update(islands)
    references = {
        entity: ReferenceGeometry(
            geometry, True, "natural_earth_island" if entity in islands else "natural_earth_admin", "independent"
        )
        for entity, geometry in geometries.items()
        if kinds.get(entity) not in {"historical_country", "historical_region"}
    }
    references = composed_references(
        references,
        {(child, parent) for (child, parent), (extent, _) in overrides.items() if extent == "full"},
        complete_entities=set(),
    )
    pl.DataFrame(
        [
            (entity, reference.geometry.wkb, reference.source, reference.complete)
            for entity, reference in sorted(references.items())
        ],
        schema={"entity_id": pl.String, "geometry_wkb": pl.Binary, "source": pl.String, "complete": pl.Boolean},
        orient="row",
    ).write_parquet(silver_dir / "2_place_reference_geometry.parquet")
    item_entity: dict[str, str] = {}
    for entity, item in crosswalk.select("entity_id", "wikidata_id").sort("entity_id", "wikidata_id").rows():
        item_entity.setdefault(item, entity)
    by_pair: dict[tuple[str, str, str], list[PlaceStatement]] = {}
    qualifier_rows: dict[str, list[dict]] = {}
    dated_ids: set[str] = set()
    for row in qualifiers.iter_rows(named=True):
        qualifier_rows.setdefault(row["statement_id"], []).append(row)
    for row in statements.filter(pl.col("snak_type") == "value").iter_rows(named=True):
        child, parent = item_entity.get(row["subject_item_id"]), item_entity.get(row["object_item_id"])
        if child is None or parent is None or child == parent:
            continue
        attached = qualifier_rows.get(row["statement_id"], [])
        scoped = any(q["qualifier_property"] == "P518" for q in attached)
        dated = any(q["qualifier_property"] in {"P580", "P582", "P585"} for q in attached)
        # Unsupported time precisions remain unknown rather than certifying historical boundaries with today's map.
        statement = PlaceStatement(child, parent, row["property"].lower(), row["statement_id"], scoped)
        by_pair.setdefault((child, parent, statement.source), []).append(statement)
        if dated:
            dated_ids.add(statement.statement_id)
    evidence = []
    for parent, child, source in candidates.unique().sort("parent_id", "child_id", "source").iter_rows():
        attached_statements = by_pair.get((child, parent, source), [])
        if attached_statements:
            evidence.extend(resolve_statement_evidence(attached_statements, references, precision=REFERENCE_PRECISION))
        else:
            evidence.extend(
                resolve_statement_evidence(
                    [PlaceStatement(child, parent, source, f"{source}:{child}:{parent}", False)],
                    references,
                    precision=REFERENCE_PRECISION,
                )
            )
        if source == "natural_earth":
            evidence.append(SpatialEvidence(child, parent, source, f"ne-admin:{child}:{parent}", "full"))
    evidence = [
        SpatialEvidence(
            e.child_id,
            e.parent_id,
            e.source,
            e.statement_id,
            "unknown" if e.statement_id in dated_ids else e.extent,
            e.scoped,
        )
        for e in evidence
    ]
    decisions = resolve_relations(evidence, overrides)
    rows = [
        (
            d.child_id,
            d.parent_id,
            d.extent,
            d.conflict,
            d.override_reason,
            json.dumps([e.statement_id for e in d.evidence]),
            json.dumps(
                [
                    {
                        "statement_id": e.statement_id,
                        "source": e.source,
                        "extent": e.extent,
                        "scoped": e.scoped,
                        "qualifiers": qualifier_rows.get(e.statement_id, []),
                    }
                    for e in d.evidence
                ],
                sort_keys=True,
            ),
            references[d.child_id].source if d.child_id in references else None,
            references[d.parent_id].source if d.parent_id in references else None,
        )
        for d in decisions
    ]
    frame = pl.DataFrame(
        rows,
        schema={
            "child_id": pl.String,
            "parent_id": pl.String,
            "extent": pl.String,
            "conflict": pl.Boolean,
            "override_reason": pl.String,
            "statement_ids": pl.String,
            "evidence_json": pl.String,
            "child_boundary_source": pl.String,
            "parent_boundary_source": pl.String,
        },
        orient="row",
    )
    frame.filter(pl.col("extent") != "reject").write_parquet(silver_dir / "2_place_relation.parquet")
    frame.filter(pl.col("extent").is_in(["unknown", "reject"]) | pl.col("conflict")).write_csv(
        silver_dir / "2_place_relation_review.csv"
    )
    report = {
        "decisions": dict(Counter(d.extent for d in decisions)),
        "candidateRows": candidates.height,
        "candidatePairs": candidates.select("child_id", "parent_id").unique().height,
        "decisionPairs": len(decisions),
        "candidateSources": dict(Counter(candidates["source"])),
        "missingChildBoundaries": sorted({d.child_id for d in decisions if d.child_id not in references}),
        "missingParentBoundaries": sorted({d.parent_id for d in decisions if d.parent_id not in references}),
        "conflicts": sum(d.conflict for d in decisions),
        "statements": statements.height,
        "qualifiers": qualifiers.height,
        "scopedStatements": qualifiers.filter(pl.col("qualifier_property") == "P518")["statement_id"].n_unique(),
        "referenceBoundaries": len(references),
        "islandBoundaries": len(islands),
        "incompleteBoundaries": sorted(entity for entity, reference in references.items() if not reference.complete),
    }
    (silver_dir / "2_place_relation_report.json").write_text(json.dumps(report, sort_keys=True, indent=2) + "\n")
    full = frame.filter(pl.col("extent") == "full").select("parent_id", "child_id")
    result = candidates.join(full, on=["parent_id", "child_id"], how="semi")
    curated = [
        (parent, child, "curation")
        for child, parent, extent in frame.select("child_id", "parent_id", "extent").rows()
        if extent == "full" and (child, parent) in overrides
    ]
    return pl.concat([result, pl.DataFrame(curated, schema=candidates.schema, orient="row")]).unique()
