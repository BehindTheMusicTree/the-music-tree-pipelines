import polars as pl
import pytest

from gold.quality_checks import check_non_empty, check_null_rate, check_row_count_delta, check_unique_key


def test_check_non_empty_passes_on_non_empty_df() -> None:
    check_non_empty(pl.DataFrame({"a": [1]}), "df")


def test_check_non_empty_raises_on_empty_df() -> None:
    with pytest.raises(ValueError, match="is empty"):
        check_non_empty(pl.DataFrame({"a": []}), "df")


def test_check_null_rate_passes_within_threshold() -> None:
    check_null_rate(pl.DataFrame({"a": [1, None, 3, 4]}), "a", "df", max_null_ratio=0.5)


def test_check_null_rate_raises_above_threshold() -> None:
    with pytest.raises(ValueError, match="null rate"):
        check_null_rate(pl.DataFrame({"a": [1, None]}), "a", "df", max_null_ratio=0.0)


def test_check_null_rate_skips_empty_df() -> None:
    check_null_rate(pl.DataFrame({"a": []}, schema={"a": pl.Int64}), "a", "df")


def test_check_unique_key_passes_on_unique_values() -> None:
    check_unique_key(pl.DataFrame({"a": [1, 2, 3]}), "a", "df")


def test_check_unique_key_ignores_nulls() -> None:
    check_unique_key(pl.DataFrame({"a": [1, None, None]}), "a", "df")


def test_check_unique_key_raises_on_duplicate() -> None:
    with pytest.raises(ValueError, match="not unique"):
        check_unique_key(pl.DataFrame({"a": [1, 1, 2]}), "a", "df")


def test_check_row_count_delta_passes_on_equal_counts() -> None:
    check_row_count_delta(10, 10, "step")


def test_check_row_count_delta_passes_within_ratio() -> None:
    check_row_count_delta(10, 9, "step", max_delta_ratio=0.2)


def test_check_row_count_delta_raises_above_ratio() -> None:
    with pytest.raises(ValueError, match="row count changed"):
        check_row_count_delta(10, 5, "step", max_delta_ratio=0.2)


def test_check_row_count_delta_raises_on_rows_from_empty_input() -> None:
    with pytest.raises(ValueError, match="0-row input"):
        check_row_count_delta(0, 3, "step")


def test_check_row_count_delta_passes_on_zero_to_zero() -> None:
    check_row_count_delta(0, 0, "step")
