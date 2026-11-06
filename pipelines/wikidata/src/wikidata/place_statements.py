"""Normalize geographic statement results without collapsing statement provenance."""

import polars as pl
from collections.abc import Callable

from wikidata.wikidata_client import PLACE_STATEMENT_PIDS, QID_PATTERN

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
    result = pl.DataFrame(normalized, schema=SCHEMA, orient="row").sort("subject_item_id", "property", "statement_id")
    if result["statement_id"].n_unique() != result.height:
        raise ValueError("duplicate geographic statement id")
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
