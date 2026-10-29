import logging

import polars as pl

logger = logging.getLogger(__name__)

# grow-api accepts 0..32767; MusicBrainz dates are real years, so anything outside is corrupt data (the mirror has 20..3036).
FIRST_RELEASE_YEAR_RANGE = (1000, 2100)
# grow-api rejects a longer value with a 400, failing the whole import after hours of posting.
ARTIST_NAME_LEN_MAX = 2048
RELEASE_GROUP_TITLE_LEN_MAX = 2048
SONG_TITLE_LEN_MAX = 2048
MBID_PATTERN = r"^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$"


def check_non_empty(df: pl.DataFrame, name: str) -> None:
    if df.is_empty():
        raise ValueError(f"{name} is empty")


def check_null_rate(df: pl.DataFrame, column: str, name: str, max_null_ratio: float = 0.0) -> None:
    if df.is_empty():
        return
    null_ratio = df.select(pl.col(column).is_null().sum()).item() / df.height
    if null_ratio > max_null_ratio:
        raise ValueError(f"{name}: '{column}' null rate {null_ratio:.1%} exceeds {max_null_ratio:.1%} threshold")


def check_max_length(df: pl.DataFrame, column: str, max_length: int, name: str) -> None:
    too_long = df.filter(pl.col(column).str.len_chars() > max_length).height
    if too_long:
        raise ValueError(f"{name}: {too_long} row(s) with '{column}' longer than {max_length} characters")


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


def check_reject_rate(rejected: int, total: int, name: str, max_ratio: float) -> None:
    """External data carries the odd bad row: drop it, but fail when so many are bad that the source itself broke."""
    if not rejected:
        return
    ratio = rejected / total
    logger.warning("%s: rejected %d of %d row(s) (%.2f%%)", name, rejected, total, ratio * 100)
    if ratio > max_ratio:
        raise ValueError(
            f"{name}: rejected {rejected} of {total} row(s) ({ratio:.1%} exceeds {max_ratio:.1%} threshold)"
        )
