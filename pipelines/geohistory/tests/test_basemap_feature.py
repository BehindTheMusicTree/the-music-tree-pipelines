from pathlib import Path

import polars as pl
import pytest
import shapely
from geohistory.silver.basemap_feature import basemap_feature

BOWTIE = shapely.Polygon([(10, 0), (12, 2), (12, 0), (10, 2)])


def _write(bronze: Path, countries: list[tuple[int, shapely.Geometry]], features: list[tuple]) -> None:
    bronze.mkdir()
    pl.DataFrame(
        [(level, geometry.wkb) for level, geometry in countries],
        schema={"level": pl.Int8, "geometry_wkb": pl.Binary},
        orient="row",
    ).write_parquet(bronze / "natural_earth_admin.parquet")
    pl.DataFrame(
        [(layer, name, None, 1.0, geometry.wkb) for layer, name, geometry in features],
        schema={
            "layer": pl.String,
            "name": pl.String,
            "featurecla": pl.String,
            "min_zoom": pl.Float64,
            "geometry_wkb": pl.Binary,
        },
        orient="row",
    ).write_parquet(bronze / "natural_earth_basemap.parquet")


def test_basemap_feature_adds_land_as_the_union_of_countries_and_repairs_geometry(tmp_path: Path) -> None:
    _write(
        tmp_path / "bronze",
        [(0, shapely.box(0, 0, 1, 1)), (0, shapely.box(1, 0, 2, 1)), (1, shapely.box(50, 50, 51, 51))],
        [("lakes", "Bowtie", BOWTIE), ("places", "Paris", shapely.Point(2, 48))],
    )

    path = basemap_feature(tmp_path / "bronze", tmp_path / "silver")

    assert path == tmp_path / "silver" / "8_basemap_feature.parquet"
    frame = pl.read_parquet(path)
    assert frame["layer"].to_list() == ["land", "lakes", "places"]
    assert frame["min_zoom"][0] == 0.0
    geometries = shapely.from_wkb(frame["geometry_wkb"].to_list())
    assert geometries[0].equals(shapely.box(0, 0, 2, 1))
    assert all(shapely.is_valid(geometry) for geometry in geometries)
    assert geometries[1].area == pytest.approx(2.0)


def test_basemap_feature_raises_without_admin_0_country(tmp_path: Path) -> None:
    _write(tmp_path / "bronze", [(1, shapely.box(0, 0, 1, 1))], [("places", "Paris", shapely.Point(2, 48))])

    with pytest.raises(ValueError, match="no admin-0 country"):
        basemap_feature(tmp_path / "bronze", tmp_path / "silver")
