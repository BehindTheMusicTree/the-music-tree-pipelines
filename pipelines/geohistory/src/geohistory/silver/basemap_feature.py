import logging
from pathlib import Path

import polars as pl
import shapely

logger = logging.getLogger(__name__)

BASEMAP_FEATURE_SCHEMA = pl.Schema(
    {
        "layer": pl.String(),
        "name": pl.String(),
        "featurecla": pl.String(),
        "min_zoom": pl.Float64(),
        "geometry_wkb": pl.Binary(),
    }
)


def basemap_feature(bronze_dir: Path, silver_dir: Path) -> Path:
    """Background features of the map: Natural Earth's disputed lines, places, rivers and lakes with valid geometry,
    plus the `land` layer, the union of the admin-0 countries so the coast coincides with the regions drawn on it."""
    countries = pl.read_parquet(bronze_dir / "natural_earth_admin.parquet", columns=["level", "geometry_wkb"]).filter(
        pl.col("level") == 0
    )
    if countries.is_empty():
        raise ValueError("natural_earth_admin.parquet has no admin-0 country, cannot build the land layer")
    land = shapely.union_all(shapely.make_valid(shapely.from_wkb(countries["geometry_wkb"].to_list())))

    features = pl.read_parquet(bronze_dir / "natural_earth_basemap.parquet")
    valid = shapely.make_valid(shapely.from_wkb(features["geometry_wkb"].to_list()))
    frame = pl.concat(
        [
            pl.DataFrame([("land", None, None, 0.0, land.wkb)], schema=BASEMAP_FEATURE_SCHEMA, orient="row"),
            features.with_columns(
                pl.Series("geometry_wkb", [geometry.wkb for geometry in valid], dtype=pl.Binary)
            ).select(BASEMAP_FEATURE_SCHEMA.names()),
        ]
    )
    silver_dir.mkdir(parents=True, exist_ok=True)
    output_path = silver_dir / "8_basemap_feature.parquet"
    frame.write_parquet(output_path)
    logger.info("wrote %d basemap feature(s) to %s", frame.height, output_path)
    return output_path
