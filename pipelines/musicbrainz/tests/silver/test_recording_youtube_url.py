from pathlib import Path

import polars as pl

from musicbrainz.silver import recording_youtube_url as sl

URL_ROWS = [
    {"id": 1, "gid": "a", "url": "https://www.youtube.com/watch?v=abc"},
    {"id": 2, "gid": "b", "url": "https://youtu.be/def"},
    {"id": 3, "gid": "c", "url": "https://example.com/official"},
]

L_RECORDING_URL_ROWS = [
    {"id": 10, "link": 100, "entity0": 1000, "entity1": 1},
    {"id": 11, "link": 101, "entity0": 1001, "entity1": 2},
    {"id": 12, "link": 102, "entity0": 1002, "entity1": 3},
]


def _write_bronze(tmp_path: Path) -> Path:
    bronze_dir = tmp_path / "bronze"
    bronze_dir.mkdir()
    pl.DataFrame(URL_ROWS).write_parquet(bronze_dir / "url.parquet")
    pl.DataFrame(L_RECORDING_URL_ROWS).write_parquet(bronze_dir / "l_recording_url.parquet")
    return bronze_dir


def test_recording_youtube_url_filters_to_youtube_domains(tmp_path: Path) -> None:
    bronze_dir = _write_bronze(tmp_path)
    output_dir = tmp_path / "silver"

    result = sl.recording_youtube_url(bronze_dir, output_dir)

    assert result == output_dir / "1_recording_youtube_url.parquet"
    rows = pl.read_parquet(result).sort("recording_id").to_dicts()
    assert rows == [
        {"recording_id": 1000, "youtube_url": "https://www.youtube.com/watch?v=abc"},
        {"recording_id": 1001, "youtube_url": "https://youtu.be/def"},
    ]


def test_recording_youtube_url_creates_output_dir(tmp_path: Path) -> None:
    bronze_dir = _write_bronze(tmp_path)
    output_dir = tmp_path / "does" / "not" / "exist"

    sl.recording_youtube_url(bronze_dir, output_dir)

    assert output_dir.is_dir()
