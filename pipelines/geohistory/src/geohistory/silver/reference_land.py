"""Natural Earth land independent of the place containment graph."""

from pathlib import Path

import polars as pl
import shapely


def reference_land(bronze_dir: Path) -> shapely.Geometry:
    countries = pl.read_parquet(bronze_dir / "natural_earth_admin.parquet", columns=["level", "geometry_wkb"]).filter(
        pl.col("level") == 0
    )
    if countries.is_empty():
        raise ValueError("natural_earth_admin.parquet has no admin-0 country, cannot build the land layer")
    return shapely.union_all(shapely.make_valid(shapely.from_wkb(countries["geometry_wkb"].to_list())))
