from pathlib import Path

import polars as pl

from musicbrainz.silver import recording_genre as sl

RECORDING_TAG_ROWS = [
    {"recording": 1000, "tag": 1, "count": 2, "last_updated": None},
    {"recording": 1000, "tag": 2, "count": 1, "last_updated": None},
    {"recording": 1001, "tag": 1, "count": -1, "last_updated": None},
    {"recording": 1001, "tag": 3, "count": 1, "last_updated": None},
]

TAG_ROWS = [
    {"id": 1, "name": "Rock", "ref_count": 10},
    {"id": 2, "name": "not-a-genre", "ref_count": 5},
    {"id": 3, "name": "jazz", "ref_count": 3},
]

GENRE_ROWS = [
    {"id": 100, "gid": "a", "name": "rock", "comment": "", "edits_pending": 0, "last_updated": None},
    {"id": 101, "gid": "b", "name": "jazz", "comment": "", "edits_pending": 0, "last_updated": None},
]


def _write_bronze(tmp_path: Path) -> Path:
    bronze_dir = tmp_path / "bronze"
    bronze_dir.mkdir()
    pl.DataFrame(RECORDING_TAG_ROWS).write_parquet(bronze_dir / "recording_tag.parquet")
    pl.DataFrame(TAG_ROWS).write_parquet(bronze_dir / "tag.parquet")
    pl.DataFrame(GENRE_ROWS).write_parquet(bronze_dir / "genre.parquet")
    return bronze_dir


def test_recording_genre_matches_tag_to_genre_case_insensitively(tmp_path: Path) -> None:
    bronze_dir = _write_bronze(tmp_path)
    output_dir = tmp_path / "silver"

    result = sl.recording_genre(bronze_dir, output_dir)

    assert result == output_dir / "2_recording_genre.parquet"
    rows = pl.read_parquet(result).sort(["recording_id", "genre_id"]).to_dicts()
    assert rows == [
        {"recording_id": 1000, "genre_id": 100, "weight": 2},
        {"recording_id": 1001, "genre_id": 101, "weight": 1},
    ]


def test_recording_genre_creates_output_dir(tmp_path: Path) -> None:
    bronze_dir = _write_bronze(tmp_path)
    output_dir = tmp_path / "does" / "not" / "exist"

    sl.recording_genre(bronze_dir, output_dir)

    assert output_dir.is_dir()
