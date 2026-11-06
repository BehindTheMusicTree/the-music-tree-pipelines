"""Normalize geographic statement results without collapsing statement provenance."""

import polars as pl

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
