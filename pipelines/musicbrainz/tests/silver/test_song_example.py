from pathlib import Path

import polars as pl

from musicbrainz.silver import song_example as sl

RECORDING_LINK_ROWS = [
    # Two YouTube URL shapes for the same recording — the lexicographically first `url` wins.
    {"recording_id": 1000, "url": "https://www.youtube.com/watch?v=aaaaaaaaaaa", "link_type": "free streaming"},
    {"recording_id": 1000, "url": "https://youtu.be/zzzzzzzzzzz", "link_type": "streaming"},
    {"recording_id": 1001, "url": "https://youtu.be/bbbbbbbbbbb", "link_type": "streaming"},
    {"recording_id": 1002, "url": "https://www.youtube.com/embed/ccccccccccc", "link_type": "free streaming"},
    # A recording with only a non-YouTube link is never a song-example candidate.
    {"recording_id": 1003, "url": "https://open.spotify.com/track/def", "link_type": "streaming"},
    # A bare playlist URL carries no video id and must be dropped, not crash extraction.
    {"recording_id": 1004, "url": "https://www.youtube.com/playlist?list=PLxyz", "link_type": "streaming"},
    {"recording_id": 1004, "url": "https://youtu.be/ddddddddddd", "link_type": "streaming"},
]

RECORDING_GENRE_ROWS = [
    {"recording_id": 1000, "genre_id": 100, "weight": 5},
    {"recording_id": 1000, "genre_id": 101, "weight": 9},  # higher weight -> primary genre
    {"recording_id": 1001, "genre_id": 100, "weight": 3},
    {"recording_id": 1002, "genre_id": 100, "weight": 7},
    {"recording_id": 1004, "genre_id": 100, "weight": 1},
]

GENRE_ROWS = [
    {"id": 100, "name": "rock"},
    {"id": 101, "name": "jazz"},
]

RECORDING_ROWS = [
    {"id": 1000, "name": "Song A", "artist_credit": 10},
    {"id": 1001, "name": "Song B", "artist_credit": 11},
    {"id": 1002, "name": "Song C", "artist_credit": 10},
    {"id": 1004, "name": "Song D", "artist_credit": 11},
]

ARTIST_CREDIT_NAME_ROWS = [
    {"artist_credit": 10, "position": 0, "artist": 500, "name": "Artist X", "join_phrase": ""},
    {"artist_credit": 11, "position": 0, "artist": 501, "name": "Artist Y", "join_phrase": " feat. "},
    {"artist_credit": 11, "position": 1, "artist": 502, "name": "Artist Z", "join_phrase": ""},
]

ARTIST_ROWS = [
    {"id": 500, "name": "Artist X"},
    {"id": 501, "name": "Artist Y"},
    {"id": 502, "name": "Artist Z"},
]


def _write_inputs(tmp_path: Path) -> tuple[Path, Path]:
    bronze_dir = tmp_path / "bronze"
    silver_dir = tmp_path / "silver"
    bronze_dir.mkdir()
    silver_dir.mkdir()
    pl.DataFrame(RECORDING_LINK_ROWS).write_parquet(silver_dir / "1_recording_link.parquet")
    pl.DataFrame(RECORDING_GENRE_ROWS).write_parquet(silver_dir / "2_recording_genre.parquet")
    pl.DataFrame(GENRE_ROWS).write_parquet(bronze_dir / "genre.parquet")
    pl.DataFrame(RECORDING_ROWS).write_parquet(bronze_dir / "recording.parquet")
    pl.DataFrame(ARTIST_CREDIT_NAME_ROWS).write_parquet(bronze_dir / "artist_credit_name.parquet")
    pl.DataFrame(ARTIST_ROWS).write_parquet(bronze_dir / "artist.parquet")
    return bronze_dir, silver_dir


def test_song_example_joins_link_genre_and_artist_credit(tmp_path: Path) -> None:
    bronze_dir, silver_dir = _write_inputs(tmp_path)
    output_dir = tmp_path / "output"

    result = sl.song_example(bronze_dir, silver_dir, output_dir)

    assert result == output_dir / "3_song_example.parquet"
    rows = pl.read_parquet(result).sort("title").to_dicts()
    assert rows == [
        {"title": "Song A", "artist": "Artist X", "youtube_video_id": "aaaaaaaaaaa", "genre_name": "jazz"},
        {"title": "Song B", "artist": "Artist Y", "youtube_video_id": "bbbbbbbbbbb", "genre_name": "rock"},
        {"title": "Song C", "artist": "Artist X", "youtube_video_id": "ccccccccccc", "genre_name": "rock"},
        {"title": "Song D", "artist": "Artist Y", "youtube_video_id": "ddddddddddd", "genre_name": "rock"},
    ]


def test_song_example_drops_recording_without_youtube_link(tmp_path: Path) -> None:
    bronze_dir, silver_dir = _write_inputs(tmp_path)
    output_dir = tmp_path / "output"

    result = sl.song_example(bronze_dir, silver_dir, output_dir)

    titles = pl.read_parquet(result)["title"].to_list()
    assert "Song for 1003" not in titles


def test_song_example_caps_recordings_per_genre(tmp_path: Path) -> None:
    bronze_dir = tmp_path / "bronze"
    silver_dir = tmp_path / "silver"
    bronze_dir.mkdir()
    silver_dir.mkdir()

    n = sl.RECORDINGS_PER_GENRE + 3
    recording_link_rows = [
        {"recording_id": i, "url": f"https://youtu.be/vid{i:08d}", "link_type": "streaming"} for i in range(n)
    ]
    recording_genre_rows = [{"recording_id": i, "genre_id": 100, "weight": i} for i in range(n)]
    recording_rows = [{"id": i, "name": f"Song {i}", "artist_credit": 10} for i in range(n)]

    pl.DataFrame(recording_link_rows).write_parquet(silver_dir / "1_recording_link.parquet")
    pl.DataFrame(recording_genre_rows).write_parquet(silver_dir / "2_recording_genre.parquet")
    pl.DataFrame(GENRE_ROWS).write_parquet(bronze_dir / "genre.parquet")
    pl.DataFrame(recording_rows).write_parquet(bronze_dir / "recording.parquet")
    pl.DataFrame(ARTIST_CREDIT_NAME_ROWS).write_parquet(bronze_dir / "artist_credit_name.parquet")
    pl.DataFrame(ARTIST_ROWS).write_parquet(bronze_dir / "artist.parquet")

    result = sl.song_example(bronze_dir, silver_dir, tmp_path / "output")

    df = pl.read_parquet(result)
    assert df.height == sl.RECORDINGS_PER_GENRE
    # the highest-weight recordings (highest `i`) are kept, not an arbitrary subset
    kept_titles = set(df["title"].to_list())
    assert kept_titles == {f"Song {i}" for i in range(n - sl.RECORDINGS_PER_GENRE, n)}


def test_song_example_creates_output_dir(tmp_path: Path) -> None:
    bronze_dir, silver_dir = _write_inputs(tmp_path)
    output_dir = tmp_path / "does" / "not" / "exist"

    sl.song_example(bronze_dir, silver_dir, output_dir)

    assert output_dir.is_dir()
