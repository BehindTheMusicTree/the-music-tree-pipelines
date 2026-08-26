from pathlib import Path

import polars as pl

from musicbrainz.silver import recording_link as sl

URL_ROWS = [
    {"id": 1, "gid": "a", "url": "https://www.youtube.com/watch?v=abc"},
    {"id": 2, "gid": "b", "url": "https://open.spotify.com/track/def"},
    {"id": 3, "gid": "c", "url": "https://example.com/official"},
]

L_RECORDING_URL_ROWS = [
    {"id": 10, "link": 100, "entity0": 1000, "entity1": 1},
    {"id": 11, "link": 101, "entity0": 1001, "entity1": 2},
    {"id": 12, "link": 102, "entity0": 1002, "entity1": 3},
]

LINK_ROWS = [
    {"id": 100, "link_type": 200},
    {"id": 101, "link_type": 201},
    {"id": 102, "link_type": 202},
]

LINK_TYPE_ROWS = [
    {"id": 200, "name": "free streaming"},
    {"id": 201, "name": "streaming"},
    {"id": 202, "name": "other databases"},
]


def _write_bronze(tmp_path: Path) -> Path:
    bronze_dir = tmp_path / "bronze"
    bronze_dir.mkdir()
    pl.DataFrame(URL_ROWS).write_parquet(bronze_dir / "url.parquet")
    pl.DataFrame(L_RECORDING_URL_ROWS).write_parquet(bronze_dir / "l_recording_url.parquet")
    pl.DataFrame(LINK_ROWS).write_parquet(bronze_dir / "link.parquet")
    pl.DataFrame(LINK_TYPE_ROWS).write_parquet(bronze_dir / "link_type.parquet")
    return bronze_dir


def test_recording_link_joins_link_type_to_every_recording_url(tmp_path: Path) -> None:
    bronze_dir = _write_bronze(tmp_path)
    output_dir = tmp_path / "silver"

    result = sl.recording_link(bronze_dir, output_dir)

    assert result == output_dir / "1_recording_link.parquet"
    rows = pl.read_parquet(result).sort("recording_id").to_dicts()
    assert rows == [
        {"recording_id": 1000, "url": "https://www.youtube.com/watch?v=abc", "link_type": "free streaming"},
        {"recording_id": 1001, "url": "https://open.spotify.com/track/def", "link_type": "streaming"},
        {"recording_id": 1002, "url": "https://example.com/official", "link_type": "other databases"},
    ]


def test_recording_link_creates_output_dir(tmp_path: Path) -> None:
    bronze_dir = _write_bronze(tmp_path)
    output_dir = tmp_path / "does" / "not" / "exist"

    sl.recording_link(bronze_dir, output_dir)

    assert output_dir.is_dir()
