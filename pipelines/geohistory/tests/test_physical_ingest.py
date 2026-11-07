import polars as pl
import pytest
import shapely
from shapely.geometry import mapping

from geohistory import natural_earth_client
from geohistory.ingest import ingest_natural_earth_physical


def feature(geometry, *, name="island", kind="Island"):
    return {
        "properties": {"NE_ID": 1, "WIKIDATAID": "Q1", "NAME": name, "FEATURECLA": kind},
        "geometry": mapping(geometry) if geometry is not None else None,
    }


def test_physical_ingestion_unions_feature_shards_and_preserves_source_identity(tmp_path, monkeypatch):
    first, second = shapely.box(0, 0, 1, 1), shapely.box(1, 0, 2, 1)
    monkeypatch.setattr(
        natural_earth_client,
        "download_layer",
        lambda *_: {"features": [feature(first), feature(second), feature(None, kind="Range/mtn")]},
    )
    frame = pl.read_parquet(ingest_natural_earth_physical("v5.1.2", tmp_path))
    assert frame.select("ne_id", "wikidata_id", "version").rows() == [("1", "Q1", "v5.1.2")]
    assert shapely.equals(shapely.from_wkb(frame["geometry_wkb"][0]), shapely.union_all([first, second]))


def test_physical_ingestion_rejects_missing_geometry_and_conflicting_identity(tmp_path, monkeypatch):
    monkeypatch.setattr(natural_earth_client, "download_layer", lambda *_: {"features": [feature(None)]})
    with pytest.raises(ValueError, match="no geometry"):
        ingest_natural_earth_physical("v5.1.2", tmp_path)
    monkeypatch.setattr(
        natural_earth_client,
        "download_layer",
        lambda *_: {"features": [feature(shapely.box(0, 0, 1, 1)), feature(shapely.box(1, 0, 2, 1), name="different")]},
    )
    with pytest.raises(ValueError, match="conflicting metadata"):
        ingest_natural_earth_physical("v5.1.2", tmp_path)
