from pathlib import Path

import polars as pl

from musicbrainz.silver import recording_genre_youtube as sl

RECORDING_GENRE_ROWS = [
    {"recording_id": 1000, "genre_id": 100, "weight": 2},
    {"recording_id": 1000, "genre_id": 101, "weight": 1},
    {"recording_id": 1001, "genre_id": 102, "weight": 3},
    {"recording_id": 1002, "genre_id": 100, "weight": 1},
]

RECORDING_LINK_ROWS = [
    {"recording_id": 1000, "url": "https://www.youtube.com/watch?v=abc", "link_type": "streaming"},
    {"recording_id": 1000, "url": "https://youtu.be/def", "link_type": "free streaming"},
    {"recording_id": 1001, "url": "https://example.com/official", "link_type": "official homepage"},
    {"recording_id": 1002, "url": "https://open.spotify.com/track/xyz", "link_type": "streaming"},
]


def _write_silver(tmp_path: Path) -> Path:
    silver_dir = tmp_path / "silver"
    silver_dir.mkdir()
    pl.DataFrame(RECORDING_GENRE_ROWS).write_parquet(silver_dir / "2_recording_genre.parquet")
    pl.DataFrame(RECORDING_LINK_ROWS).write_parquet(silver_dir / "1_recording_link.parquet")
    return silver_dir


def test_recording_genre_youtube_joins_genre_and_youtube_links(tmp_path: Path) -> None:
    silver_dir = _write_silver(tmp_path)
    output_dir = tmp_path / "output"

    result = sl.recording_genre_youtube(silver_dir, output_dir)

    assert result == output_dir / "3_recording_genre_youtube.parquet"
    rows = pl.read_parquet(result).sort(["recording_id", "genre_id", "youtube_url"]).to_dicts()
    assert rows == [
        {
            "recording_id": 1000,
            "genre_id": 100,
            "weight": 2,
            "youtube_url": "https://www.youtube.com/watch?v=abc",
        },
        {
            "recording_id": 1000,
            "genre_id": 100,
            "weight": 2,
            "youtube_url": "https://youtu.be/def",
        },
        {
            "recording_id": 1000,
            "genre_id": 101,
            "weight": 1,
            "youtube_url": "https://www.youtube.com/watch?v=abc",
        },
        {
            "recording_id": 1000,
            "genre_id": 101,
            "weight": 1,
            "youtube_url": "https://youtu.be/def",
        },
    ]


def test_recording_genre_youtube_creates_output_dir(tmp_path: Path) -> None:
    silver_dir = _write_silver(tmp_path)
    output_dir = tmp_path / "does" / "not" / "exist"

    sl.recording_genre_youtube(silver_dir, output_dir)

    assert output_dir.is_dir()
