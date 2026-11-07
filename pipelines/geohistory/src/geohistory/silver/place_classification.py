"""Distinguish a current administrative entity from its former classifications."""

import json
from datetime import date

import polars as pl

ADMINISTRATIVE_ENTITY = "Q56061"
COUNTRY = "Q6256"
HISTORICAL_CLASSES = {"Q1620908", "Q3024240"}
HISTORICAL_KINDS = {"historical_region", "historical_country"}


def _best(statements: list[dict]) -> list[dict]:
    rank = "preferred" if any(s.get("rank") == "preferred" for s in statements) else "normal"
    return [s for s in statements if s.get("rank", "normal") == rank]


def _target(statement: dict) -> str | None:
    snak = statement.get("mainsnak", {})
    if snak.get("snaktype") != "value":
        return None
    return snak.get("datavalue", {}).get("value", {}).get("id")


def _current(statement: dict, year: int) -> bool:
    for prop, snaks in statement.get("qualifiers", {}).items():
        if prop in {"P518", "P585"}:
            return False
        if prop not in {"P580", "P582"}:
            continue
        for snak in snaks:
            value = snak.get("datavalue", {}).get("value", {})
            if (
                snak.get("snaktype") != "value"
                or value.get("precision", 0) < 9
                or value.get("calendarmodel") != "http://www.wikidata.org/entity/Q1985727"
                or value.get("before", 0)
                or value.get("after", 0)
            ):
                return False
            raw = value.get("time", "")
            try:
                bound = int(raw.split("-", 1)[0].lstrip("+"))
            except ValueError:
                return False
            if (prop == "P580" and bound >= year) or (prop == "P582" and bound <= year):
                return False
    return True


def current_administrative_kinds(
    entities: pl.DataFrame, metadata: pl.DataFrame, *, as_of_year: int | None = None
) -> pl.DataFrame:
    year = date.today().year if as_of_year is None else as_of_year
    claims = {
        item: json.loads(row["claims_json"])
        for row in metadata.iter_rows(named=True)
        if row["status"] != "missing"
        for item in {row["resolved_id"], row.get("requested_id", row["resolved_id"])}
    }

    def ancestors(item: str) -> set[str]:
        pending = [item]
        seen: set[str] = set()
        while pending:
            node = pending.pop()
            if node in seen:
                continue
            seen.add(node)
            if node not in claims:
                raise ValueError(f"place class {node} is missing: rerun Wikidata Bronze with class hierarchy discovery")
            pending.extend(
                target
                for statement in _best(claims[node].get("P279", []))
                if _current(statement, year) and (target := _target(statement))
            )
        return seen

    replacements: dict[str, str] = {}
    for entity, kind in entities.select("entity_id", "kind").rows():
        if kind not in HISTORICAL_KINDS:
            continue
        for statement in _best(claims.get(entity, {}).get("P31", [])):
            if not _current(statement, year) or not (target := _target(statement)):
                continue
            classes = ancestors(target)
            if not classes & HISTORICAL_CLASSES and ADMINISTRATIVE_ENTITY in classes:
                replacement = "country" if COUNTRY in classes else "admin_unit"
                if replacements.get(entity) != "country":
                    replacements[entity] = replacement
    return entities.with_columns(pl.col("entity_id").replace_strict(replacements, default=pl.col("kind")).alias("kind"))
