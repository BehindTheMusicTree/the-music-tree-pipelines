import polars as pl


def check_non_empty(df: pl.DataFrame, name: str) -> None:
    if df.is_empty():
        raise ValueError(f"{name} is empty")


def check_null_rate(df: pl.DataFrame, column: str, name: str, max_null_ratio: float = 0.0) -> None:
    if df.is_empty():
        return
    null_ratio = df.select(pl.col(column).is_null().sum()).item() / df.height
    if null_ratio > max_null_ratio:
        raise ValueError(f"{name}: '{column}' null rate {null_ratio:.1%} exceeds {max_null_ratio:.1%} threshold")


def check_unique_key(df: pl.DataFrame, column: str, name: str) -> None:
    non_null = df.select(column).drop_nulls()
    distinct = non_null.n_unique()
    if distinct != non_null.height:
        raise ValueError(f"{name}: '{column}' is not unique ({non_null.height - distinct} duplicate value(s))")


def check_row_count_delta(before_count: int, after_count: int, name: str, max_delta_ratio: float = 0.0) -> None:
    if before_count == 0:
        if after_count != 0:
            raise ValueError(f"{name}: {after_count} row(s) produced from a 0-row input")
        return
    delta_ratio = abs(after_count - before_count) / before_count
    if delta_ratio > max_delta_ratio:
        raise ValueError(
            f"{name}: row count changed from {before_count} to {after_count} "
            f"({delta_ratio:.1%} exceeds {max_delta_ratio:.1%} threshold)"
        )
