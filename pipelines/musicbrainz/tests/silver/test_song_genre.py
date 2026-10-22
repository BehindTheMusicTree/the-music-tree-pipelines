from pathlib import Path

import polars as pl

from musicbrainz.silver import song_genre as sg


def _write_inputs(tmp_path: Path) -> tuple[Path, Path]:
    bronze_dir = tmp_path / "bronze"
    silver_dir = tmp_path / "silver"
    bronze_dir.mkdir()
    silver_dir.mkdir()
    pl.DataFrame([{"id": 100, "name": "rock"}, {"id": 101, "name": "jazz"}]).write_parquet(bronze_dir / "genre.parquet")
    pl.DataFrame(
        [{"id": i, "gid": f"gid-{i}", "name": "Song", "artist_credit": 10} for i in (1000, 1001, 1002)]
    ).write_parquet(bronze_dir / "recording.parquet")
    pl.DataFrame([{"artist_credit": 10, "position": 0, "artist": 500, "name": "X", "join_phrase": ""}]).write_parquet(
        bronze_dir / "artist_credit_name.parquet"
    )
    pl.DataFrame([{"id": 500, "gid": "artist-500", "name": "X"}]).write_parquet(bronze_dir / "artist.parquet")
    pl.DataFrame(schema={"id": pl.Int64, "link": pl.Int64, "entity0": pl.Int64, "entity1": pl.Int64}).write_parquet(
        bronze_dir / "l_recording_work.parquet"
    )
    pl.DataFrame(
        [
            {"recording_id": 1000, "genre_id": 100, "weight": 3},
            {"recording_id": 1001, "genre_id": 100, "weight": 2},
            {"recording_id": 1002, "genre_id": 101, "weight": 4},
        ]
    ).write_parquet(silver_dir / "2_recording_genre.parquet")
    return bronze_dir, silver_dir


def test_song_genre_sums_weights_across_the_song_and_keys_by_lowest_recording(tmp_path: Path) -> None:
    bronze_dir, silver_dir = _write_inputs(tmp_path)

    result = sg.song_genre(bronze_dir, silver_dir, tmp_path / "out")

    assert result == tmp_path / "out" / "10_song_genre.parquet"
    assert pl.read_parquet(result).sort("genre_name").to_dicts() == [
        {"musicbrainz_recording_id": "gid-1000", "genre_name": "jazz", "weight": 4},
        {"musicbrainz_recording_id": "gid-1000", "genre_name": "rock", "weight": 5},
    ]
