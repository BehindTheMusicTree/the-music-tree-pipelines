"""Normalize geographic statement results without collapsing statement provenance."""

import json

import polars as pl
from collections.abc import Callable

from wikidata.wikidata_client import PLACE_STATEMENT_PIDS, QID_PATTERN
from wikidata.wikidata_client import EntityInfo

ENTITY_PREFIX = "http://www.wikidata.org/entity/"
RANKS = {
    "http://wikiba.se/ontology#NormalRank": "normal",
    "http://wikiba.se/ontology#PreferredRank": "preferred",
    "http://wikiba.se/ontology#DeprecatedRank": "deprecated",
}
SCHEMA = {
    "statement_id": pl.String,
    "subject_item_id": pl.String,
    "property": pl.String,
    "object_item_id": pl.String,
    "rank": pl.String,
    "snak_type": pl.String,
}
QUALIFIER_SCHEMA = {
    "statement_id": pl.String,
    "qualifier_property": pl.String,
    "value": pl.String,
    "value_type": pl.String,
    "snak_type": pl.String,
    "time_precision": pl.Int8,
}
VALUE_TYPES = {
    "WikibaseItem": "wikibase-item",
    "WikibaseProperty": "wikibase-property",
    "Time": "time",
    "String": "string",
    "ExternalId": "external-id",
    "Url": "url",
    "CommonsMedia": "commonsMedia",
    "GlobeCoordinate": "globe-coordinate",
    "Quantity": "quantity",
    "Monolingualtext": "monolingualtext",
    "Math": "math",
    "MusicalNotation": "musical-notation",
    "GeoShape": "geo-shape",
    "TabularData": "tabular-data",
}

PART_SCHEMA: dict[str, pl.DataType | type[pl.DataType]] = {
    "requested_id": pl.String,
    "resolved_id": pl.String,
    "label": pl.String,
    "instance_of": pl.List(pl.String),
    "subclass_of": pl.List(pl.String),
    "modified": pl.String,
    "status": pl.String,
}


def discover_place_parts(
    seeds: list[str],
    load_qualifiers: Callable[[list[str]], pl.DataFrame],
    fetch_entities: Callable[[list[str]], dict[str, EntityInfo | None]],
    *,
    max_parts: int,
    max_depth: int,
) -> pl.DataFrame:
    """Follow only explicit P518 item references, with finite count and depth."""
    if max_parts <= 0 or max_depth < 0:
        raise ValueError("place part discovery count must be positive and depth non-negative")
    visited = set(seeds)
    requested: set[str] = set()
    pending = sorted(visited)
    rows: list[tuple[str, str | None, str | None, list[str], list[str], str | None, str]] = []
    depth = 0
    while pending:
        qualifiers = load_qualifiers(pending)
        parts = (
            set(
                qualifiers.filter(
                    (pl.col("qualifier_property") == "P518")
                    & (pl.col("value_type") == "wikibase-item")
                    & (pl.col("snak_type") == "value")
                )["value"].drop_nulls()
            )
            - requested
        )
        if not parts:
            break
        if parts - visited and depth >= max_depth:
            raise ValueError(f"place part discovery exceeded depth limit {max_depth}")
        requested.update(parts)
        if len(requested) > max_parts:
            raise ValueError(f"place part discovery exceeded count limit {max_parts}")
        fetched = fetch_entities(sorted(parts))
        if set(fetched) != parts:
            raise ValueError("place part discovery did not receive every requested entity")
        next_items = set()
        for qid in sorted(parts):
            entity = fetched[qid]
            if entity is None:
                rows.append((qid, None, None, [], [], None, "missing"))
                continue
            status = "redirect" if entity.id != qid else "found"
            rows.append((qid, entity.id, entity.label, entity.instance_of, entity.subclass_of, entity.modified, status))
            if entity.id not in visited:
                next_items.add(entity.id)
        visited.update(parts | next_items)
        pending = sorted(next_items)
        depth += 1
    return pl.DataFrame(rows, schema=PART_SCHEMA, orient="row").sort("requested_id")


def _entity_id(value: str | None) -> str | None:
    if value is None or not value.startswith(ENTITY_PREFIX):
        return None
    result = value.removeprefix(ENTITY_PREFIX)
    return result if QID_PATTERN.fullmatch(result) else None


def normalize_statements(rows: list[dict[str, str | None]]) -> pl.DataFrame:
    normalized = []
    for row in rows:
        subject = _entity_id(row.get("item"))
        statement = row.get("statement")
        prop = row.get("property")
        rank = RANKS.get(row.get("rank") or "")
        snak = row.get("snakType")
        value = _entity_id(row.get("value")) if snak == "value" else None
        if (
            subject is None
            or not statement
            or prop not in PLACE_STATEMENT_PIDS
            or rank is None
            or snak not in {"value", "somevalue", "novalue"}
            or (snak == "value" and value is None)
        ):
            raise ValueError(f"invalid geographic statement result: {row}")
        normalized.append((statement, subject, prop, value, rank, snak))
    result = (
        pl.DataFrame(normalized, schema=SCHEMA, orient="row")
        .unique()
        .sort("subject_item_id", "property", "statement_id")
    )
    if result["statement_id"].n_unique() != result.height:
        raise ValueError("conflicting geographic statement id")
    return result


def disambiguate_no_values(
    rows: list[dict[str, str | None]],
    statements: pl.DataFrame,
    fetch: Callable[[list[str]], dict[str, dict]],
) -> tuple[list[dict[str, str | None]], set[tuple[str, str]]]:
    """Resolve RDF's shared no-value marker against the original Wikidata statements."""
    info = {
        statement: (subject, prop, snak)
        for statement, subject, prop, snak in statements.select(
            "statement_id", "subject_item_id", "property", "snak_type"
        ).iter_rows()
    }
    ambiguous: dict[tuple[str, str], dict[str, str | None]] = {}
    retained = []
    for row in rows:
        statement = row.get("statement") or ""
        uri = row.get("qualifierProperty") or ""
        prop = uri.removeprefix(ENTITY_PREFIX) if uri.startswith(ENTITY_PREFIX) else ""
        if statement in info and info[statement][1:] == (prop, "novalue") and row.get("snakType") == "novalue":
            ambiguous[(statement, prop)] = row
        else:
            retained.append(row)
    if not ambiguous:
        return retained, set()
    entities = fetch(sorted({info[statement][0] for statement, _ in ambiguous}))
    confirmed = set()
    for (statement, prop), row in sorted(ambiguous.items()):
        subject = info[statement][0]
        target = statement.rsplit("/", 1)[-1].replace("$", "-").lower()
        entity = entities.get(subject)
        if entity is None or "missing" in entity:
            raise ValueError(f"cannot disambiguate no-value qualifier for missing item {subject}")
        matches = [
            claim
            for claim in entity.get("claims", {}).get(prop, [])
            if claim.get("id", "").replace("$", "-").lower() == target
        ]
        if len(matches) != 1:
            raise ValueError(f"cannot find unique Wikidata statement {statement}")
        count = sum(snak.get("snaktype") == "novalue" for snak in matches[0].get("qualifiers", {}).get(prop, []))
        if count:
            retained.extend([row] * count)
            confirmed.add((statement, prop))
    return retained, confirmed


def normalize_qualifiers(
    rows: list[dict[str, str | None]],
    statements: pl.DataFrame,
    confirmed_no_values: set[tuple[str, str]] | None = None,
) -> pl.DataFrame:
    statement_info = {
        statement: (prop, snak)
        for statement, prop, snak in statements.select("statement_id", "property", "snak_type").iter_rows()
    }
    normalized = []
    for row in rows:
        statement = row.get("statement")
        uri = row.get("qualifierProperty") or ""
        prop = uri.removeprefix(ENTITY_PREFIX) if uri.startswith(ENTITY_PREFIX) else ""
        value_type = VALUE_TYPES.get((row.get("valueType") or "").removeprefix("http://wikiba.se/ontology#"))
        snak = row.get("snakType")
        if statement not in statement_info or not prop.startswith("P") or not prop[1:].isdigit():
            raise ValueError(f"invalid geographic qualifier result: {row}")
        if value_type is None or snak not in {"value", "somevalue", "novalue"}:
            raise ValueError(f"invalid geographic qualifier result: {row}")
        if (
            snak == "novalue"
            and statement_info[statement] == (prop, "novalue")
            and (statement, prop) not in (confirmed_no_values or set())
        ):
            raise ValueError(f"ambiguous no-value statement and qualifier: {statement} {prop}")
        value = row.get("value") if snak == "value" else None
        if snak == "value" and value is None:
            raise ValueError(f"invalid geographic qualifier result: {row}")
        if value_type == "wikibase-item" and value is not None:
            value = _entity_id(value)
            if value is None:
                raise ValueError(f"invalid geographic qualifier item: {row}")
        precision = row.get("precision")
        if value_type == "time" and snak == "value" and precision is None:
            raise ValueError(f"geographic time qualifier is missing precision: {row}")
        if value_type != "time" and precision is not None:
            raise ValueError(f"unexpected geographic qualifier precision: {row}")
        normalized.append((statement, prop, value, value_type, snak, int(precision) if precision is not None else None))
    return pl.DataFrame(normalized, schema=QUALIFIER_SCHEMA, orient="row").sort(
        "statement_id", "qualifier_property", "value"
    )


METADATA_SCHEMA = {
    "requested_id": pl.String,
    "resolved_id": pl.String,
    "status": pl.String,
    "revision_id": pl.Int64,
    "modified": pl.String,
    "claims_json": pl.String,
    "label": pl.String,
}


def place_metadata(
    ids: list[str],
    fetch: Callable[[list[str]], dict[str, dict]],
    *,
    include_class_hierarchy: bool = False,
    max_classes: int = 10000,
    max_depth: int = 32,
) -> pl.DataFrame:
    """Preserve raw classification and coordinate statements with rank and provenance."""
    if include_class_hierarchy and (max_classes <= 0 or max_depth <= 0):
        raise ValueError("place class discovery limits must be positive")
    requested = sorted(set(ids))
    entities = fetch(requested)
    if set(entities) != set(requested):
        raise ValueError("place metadata did not receive every requested entity")
    rows = []
    for qid in requested:
        entity = entities[qid]
        missing = "missing" in entity
        resolved = None if missing else entity.get("redirects", {}).get("to", entity.get("id", qid))
        claims = {pid: values for pid, values in entity.get("claims", {}).items() if pid in {"P31", "P279", "P625"}}
        rows.append(
            (
                qid,
                resolved,
                "missing" if missing else "redirect" if resolved != qid else "found",
                entity.get("lastrevid"),
                entity.get("modified"),
                json.dumps(claims, sort_keys=True),
                entity.get("labels", {}).get("en", {}).get("value"),
            )
        )
    result = pl.DataFrame(rows, schema=METADATA_SCHEMA, orient="row")
    if not include_class_hierarchy:
        return result

    def targets(frame: pl.DataFrame, prop: str) -> set[str]:
        return {
            value
            for raw in frame["claims_json"]
            for claim in json.loads(raw).get(prop, [])
            if (value := claim.get("mainsnak", {}).get("datavalue", {}).get("value", {}).get("id"))
            and QID_PATTERN.fullmatch(value)
        }

    pending = targets(result, "P31")
    visited: set[str] = set()
    depth = 0
    while pending:
        if depth >= max_depth or len(visited | pending) > max_classes:
            raise ValueError("place class discovery exceeded its depth or count limit")
        missing_classes = pending - set(result["requested_id"])
        if missing_classes:
            result = pl.concat([result, place_metadata(sorted(missing_classes), fetch)])
        classes = result.filter(pl.col("requested_id").is_in(sorted(pending)))
        visited.update(pending)
        pending = targets(classes, "P279") - visited
        depth += 1
    return result.sort("requested_id")
