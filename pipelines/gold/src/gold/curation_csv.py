from pathlib import Path

import polars as pl

ITEM_ID_PATTERN = r"^(Q[1-9][0-9]*|LOCAL:.+)$"
MBID_PATTERN = r"^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$"


def read_curation_csv(
    path: Path, required: tuple[str, ...], key: tuple[str, ...], patterns: dict[str, str] | None = None
) -> pl.DataFrame:
    """A `manual_<list>.csv`, every column read as a string, raising on a null/blank `required` value,
    a non-null value not matching its `patterns` regex, or a duplicate `key`."""
    df = pl.read_csv(path, infer_schema=False)
    for column in required:
        if not df.filter(pl.col(column).is_null() | (pl.col(column).str.strip_chars() == "")).is_empty():
            raise ValueError(f"{path.name} has row(s) with a null/blank '{column}'")
    for column, pattern in (patterns or {}).items():
        malformed = df.filter(~pl.col(column).str.contains(pattern)).get_column(column).to_list()
        if malformed:
            raise ValueError(f"{path.name} has malformed '{column}' value(s): {malformed[:3]}")
    if df.select(key).is_duplicated().any():
        raise ValueError(f"{path.name} has duplicate {key} row(s)")
    return df
