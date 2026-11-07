"""Select Wikidata's best non-deprecated rank before resolving spatial claims."""

import polars as pl

STATEMENT_COLUMNS = {"statement_id", "subject_item_id", "property", "object_item_id", "rank", "snak_type"}
QUALIFIER_COLUMNS = {"statement_id", "qualifier_property", "value", "value_type", "time_precision"}
RANKS = {"preferred", "normal", "deprecated"}
SNAK_TYPES = {"value", "somevalue", "novalue"}


def best_place_statements(statements: pl.DataFrame, qualifiers: pl.DataFrame) -> tuple[pl.DataFrame, pl.DataFrame]:
    """Keep best-rank statements and their own qualifiers, including unknown and absent values."""
    for frame, required, name in (
        (statements, STATEMENT_COLUMNS, "statements"),
        (qualifiers, QUALIFIER_COLUMNS, "qualifiers"),
    ):
        missing = required - set(frame.columns)
        if missing:
            raise ValueError(f"place {name} missing columns: {sorted(missing)}")
    if statements.select(pl.col("statement_id").is_null().any()).item():
        raise ValueError("place statement_id must not be null")
    if statements["statement_id"].n_unique() != statements.height:
        raise ValueError("place statement_id must be unique")
    for column in ("subject_item_id", "property", "rank", "snak_type"):
        if statements[column].null_count():
            raise ValueError(f"place statement {column} must not be null")
    if set(statements["rank"]) - RANKS or set(statements["snak_type"]) - SNAK_TYPES:
        raise ValueError("invalid place statement rank or snak type")
    if statements.filter(
        ((pl.col("snak_type") == "value") & pl.col("object_item_id").is_null())
        | ((pl.col("snak_type") != "value") & pl.col("object_item_id").is_not_null())
    ).height:
        raise ValueError("place statement object does not match snak type")
    if qualifiers.filter(pl.col("statement_id").is_null()).height:
        raise ValueError("place qualifier statement_id must not be null")
    if qualifiers.join(statements.select("statement_id"), on="statement_id", how="anti").height:
        raise ValueError("place qualifier references an unknown statement")

    eligible = statements.filter(pl.col("rank") != "deprecated")
    selected = eligible.filter(
        (pl.col("rank") == "preferred") | ~((pl.col("rank") == "preferred").any().over("subject_item_id", "property"))
    ).sort("subject_item_id", "property", "statement_id")
    selected_qualifiers = qualifiers.join(selected.select("statement_id"), on="statement_id", how="semi").sort(
        "statement_id", "qualifier_property", "value"
    )
    return selected, selected_qualifiers
