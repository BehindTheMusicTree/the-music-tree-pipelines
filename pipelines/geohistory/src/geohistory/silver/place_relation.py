"""Resolve geographic candidates before projecting full containment."""

import json
from datetime import date
from collections import Counter
from pathlib import Path

import polars as pl
import shapely

from geohistory.silver.curation import read_curation_list, read_place_relation_overrides
from geohistory.silver.manual_boundaries import read_manual_boundaries
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


def _current_administrative_statement(qualifiers: list[dict], year: int) -> bool:
    for qualifier in qualifiers:
        prop = qualifier["qualifier_property"]
        if prop == "P518":
            return False
        if prop not in {"P580", "P582", "P585"}:
            continue
        value = qualifier["value"]
        precision = qualifier["time_precision"]
        if qualifier["value_type"] != "time" or not value or precision is None or precision < 9:
            return False
        try:
            statement_year = int(value.split("-", 1)[0].lstrip("+"))
        except ValueError:
            return False
        # Coarse year precision cannot establish whether a same-year transition has happened.
        if prop == "P585" or (prop == "P580" and statement_year >= year) or (prop == "P582" and statement_year <= year):
            return False
    return True


def _boundary_candidates(references: dict[str, ReferenceGeometry], kinds: dict[str, str]) -> list[tuple[str, str, str]]:
    children = sorted(
        entity
        for entity, reference in references.items()
        if kinds.get(entity) == "country" and reference.boundary_kind == "independent" and reference.complete
    )
    index = shapely.STRtree([references[entity].geometry for entity in children])
    rows = []
    for parent, reference in sorted(references.items()):
        if reference.source != "natural_earth_map_unit_union" or reference.boundary_kind != "independent":
            continue
        for position in index.query(reference.geometry, predicate="intersects"):
            child = children[position]
            if child != parent:
                rows.append((parent, child, "reference_geometry"))
    return rows


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
    metadata = pl.read_parquet(wikidata_dir / "wikidata_place_metadata.parquet")
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
    composed = pl.read_parquet(bronze_dir / "natural_earth_composed_boundary.parquet")
    for row in composed.iter_rows(named=True):
        owners = crosswalk.filter(pl.col("wikidata_id") == row["wikidata_id"])["entity_id"]
        for owner in owners:
            if row["usage"] == "display" or kinds.get(owner) not in {"historical_country", "historical_region"}:
                references[owner] = ReferenceGeometry(
                    shapely.from_wkb(row["geometry_wkb"]),
                    row["complete"],
                    row["source"],
                    "display" if row["usage"] == "display" else "independent",
                )
    manual_provenance = []
    for qid, boundary in read_manual_boundaries().items():
        owners = crosswalk.filter(pl.col("wikidata_id") == qid)["entity_id"]
        for owner in owners:
            if owner in references:
                raise ValueError(f"manual boundary {qid} conflicts with an existing reference for {owner}")
            references[owner] = boundary.reference
            manual_provenance.append({"entity_id": owner, **boundary.provenance})
    (silver_dir / "2_manual_boundary_provenance.json").write_text(
        json.dumps(manual_provenance, indent=2, sort_keys=True) + "\n"
    )
    references = composed_references(
        references,
        {(child, parent) for (child, parent), (extent, _) in overrides.items() if extent == "full"},
        complete_entities=set(),
    )
    pl.DataFrame(
        [
            (
                entity,
                reference.geometry.wkb,
                reference.source,
                reference.complete,
                reference.boundary_kind,
                reference.start_year,
                reference.end_year,
            )
            for entity, reference in sorted(references.items())
        ],
        schema={
            "entity_id": pl.String,
            "geometry_wkb": pl.Binary,
            "source": pl.String,
            "complete": pl.Boolean,
            "boundary_kind": pl.String,
            "start_year": pl.Int64,
            "end_year": pl.Int64,
        },
        orient="row",
    ).write_parquet(silver_dir / "2_place_reference_geometry.parquet")
    removals = read_curation_list(curation_dir, "entity_membership_overrides").filter(pl.col("action") == "remove")
    removed = set(removals.select("child_entity_id", "parent_entity_id").rows())
    discovered = [row for row in _boundary_candidates(references, kinds) if (row[1], row[0]) not in removed]
    candidates = pl.concat([candidates, pl.DataFrame(discovered, schema=candidates.schema, orient="row")]).unique()
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
    as_of_year = date.today().year
    entity_items: dict[str, set[str]] = {}
    for item, entity in item_entity.items():
        entity_items.setdefault(entity, set()).add(item)
    administrative_graph: dict[str, list[tuple[str, str]]] = {}
    for row in statements.filter((pl.col("property") == "P131") & (pl.col("snak_type") == "value")).iter_rows(
        named=True
    ):
        historical_leg = any(
            kinds.get(item_entity.get(row[column], "")) in {"historical_country", "historical_region"}
            for column in ("subject_item_id", "object_item_id")
        )
        if not historical_leg and _current_administrative_statement(
            qualifier_rows.get(row["statement_id"], []), as_of_year
        ):
            administrative_graph.setdefault(row["subject_item_id"], []).append(
                (row["object_item_id"], row["statement_id"])
            )

    def administrative_path(child: str, parent: str) -> list[str]:
        targets = entity_items.get(parent, set())
        pending: list[tuple[str, list[str]]] = [(item, []) for item in sorted(entity_items.get(child, set()))]
        visited: set[str] = set()
        while pending:
            item, path = pending.pop()
            if item in targets:
                return path
            if item in visited:
                continue
            visited.add(item)
            pending.extend(
                (target, [*path, statement_id]) for target, statement_id in sorted(administrative_graph.get(item, ()))
            )
        return []

    evidence = []
    for parent, child, source in candidates.unique().sort("parent_id", "child_id", "source").iter_rows():
        attached_statements = by_pair.get((child, parent, source), [])
        if source == "reference_geometry":
            attached_statements = [PlaceStatement(child, parent, source, f"boundary:{child}:{parent}", False)]
        if attached_statements:
            evidence.extend(resolve_statement_evidence(attached_statements, references, precision=REFERENCE_PRECISION))
        else:
            # Flattened hierarchy paths do not preserve the scope or dates of their intermediate statements.
            evidence.append(SpatialEvidence(child, parent, source, f"unresolved:{source}:{child}:{parent}", "unknown"))
        if source == "natural_earth":
            evidence.append(SpatialEvidence(child, parent, source, f"ne-admin:{child}:{parent}", "full"))
        if source == "city_point" and kinds.get(child) == "city" and kinds.get(parent) == "admin_unit":
            evidence.append(SpatialEvidence(child, parent, source, f"city-point:{child}:{parent}", "full"))
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
    candidate_pairs = set(candidates.select("child_id", "parent_id").rows())
    administrative_statements: set[str] = set()
    for child, parent in sorted(candidate_pairs):
        if kinds.get(child) not in {"country", "admin_unit", "city"} or kinds.get(parent) not in {
            "country",
            "admin_unit",
        }:
            continue
        path = administrative_path(child, parent)
        administrative_statements.update(path)
        evidence.extend(
            SpatialEvidence(child, parent, "administrative_p131", statement_id, "full") for statement_id in path
        )
        if path:
            evidence.extend(
                resolve_statement_evidence(
                    [PlaceStatement(child, parent, "administrative_geometry", path[0], False)],
                    references,
                    precision=REFERENCE_PRECISION,
                )
            )
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
    referenced_parts = set(
        qualifiers.filter((pl.col("qualifier_property") == "P518") & (pl.col("value_type") == "wikibase-item"))[
            "value"
        ].drop_nulls()
    )
    known_parts = referenced_parts & set(crosswalk["wikidata_id"])
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
        "metadataItems": metadata.height,
        "administrativeAsOfYear": as_of_year,
        "referencedParts": len(referenced_parts),
        "knownEntityParts": len(known_parts),
        "partsOutsideEntityCatalog": sorted(referenced_parts - known_parts),
        "partsMissingMetadata": sorted(referenced_parts - set(metadata["requested_id"])),
        "scopedStatements": qualifiers.filter(pl.col("qualifier_property") == "P518")["statement_id"].n_unique(),
        "administrativeStatements": len(administrative_statements),
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
