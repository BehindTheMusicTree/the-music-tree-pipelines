import re

import polars as pl

REGION_POLICY_DTYPE = pl.Struct({"mode": pl.String, "countries": pl.List(pl.String)})


def region_policy(item: dict | None) -> dict | None:
    if item is None:
        return None
    restriction = item["contentDetails"].get("regionRestriction")
    if restriction is None:
        return {"mode": "unrestricted", "countries": []}
    if not isinstance(restriction, dict) or len(restriction) != 1:
        raise ValueError("invalid YouTube regionRestriction")
    mode = next(iter(restriction))
    countries = restriction[mode]
    if mode not in ("allowed", "blocked") or not isinstance(countries, list):
        raise ValueError("invalid YouTube regionRestriction")
    if any(not isinstance(code, str) or not re.fullmatch(r"[A-Z]{2}", code) or code == "XX" for code in countries):
        raise ValueError("invalid YouTube region country")
    return {"mode": mode, "countries": sorted(set(countries))}


def with_region_policy(frame: pl.LazyFrame) -> pl.LazyFrame:
    """A legacy cache/export has unknown regional metadata until enriched."""
    if "youtube_region_restriction" not in frame.collect_schema():
        return frame.with_columns(youtube_region_restriction=pl.lit(None, dtype=REGION_POLICY_DTYPE))
    return frame


def check_region_policies(songs: pl.DataFrame) -> None:
    policy = pl.col("youtube_region_restriction")
    mode, countries = policy.struct.field("mode"), policy.struct.field("countries")
    invalid = policy.is_not_null() & (
        pl.col("youtube_video_id").is_null()
        | mode.is_null()
        | ~mode.is_in(["allowed", "blocked", "unrestricted"])
        | countries.is_null()
        | ((mode == "unrestricted") & (countries.list.len() != 0))
        | countries.list.eval(
            pl.element().is_null() | ~pl.element().str.contains(r"^[A-Z]{2}$") | (pl.element() == "XX")
        ).list.any()
        | (countries != countries.list.unique().list.sort())
    )
    if songs.filter(invalid).height:
        raise ValueError("invalid youtube_region_restriction")
