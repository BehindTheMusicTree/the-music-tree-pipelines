from pathlib import Path

import polars as pl
import pytest

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
    pl.DataFrame([{"id": 1, "name": "Official"}, {"id": 3, "name": "Bootleg"}]).write_parquet(
        bronze_dir / "release_status.parquet"
    )
    # Recording 1000 on two editions (releases 1, 2) of release group 50; recording 1001 on a bootleg of group 51.
    pl.DataFrame(
        [{"id": 1, "release_group": 50, "status": 1}, {"id": 2, "release_group": 50, "status": 1}]
        + [{"id": 3, "release_group": 51, "status": 3}]
    ).write_parquet(bronze_dir / "release.parquet")
    pl.DataFrame([{"id": 10, "release": 1}, {"id": 20, "release": 2}, {"id": 30, "release": 3}]).write_parquet(
        bronze_dir / "medium.parquet"
    )
    pl.DataFrame(
        [{"recording": 1000, "medium": 10}, {"recording": 1000, "medium": 20}, {"recording": 1001, "medium": 30}]
    ).write_parquet(bronze_dir / "track.parquet")
    pl.DataFrame(
        [{"release_group_id": 50, "genre_id": 101, "weight": 7}, {"release_group_id": 51, "genre_id": 100, "weight": 9}]
    ).write_parquet(silver_dir / "12_release_group_genre.parquet")
    return bronze_dir, silver_dir


def test_song_genre_sums_weights_across_the_song_and_keys_by_lowest_recording(tmp_path: Path) -> None:
    bronze_dir, silver_dir = _write_inputs(tmp_path)

    result = sg.song_genre(bronze_dir, silver_dir, tmp_path / "out")

    assert result == tmp_path / "out" / "10_song_genre.parquet"
    assert pl.read_parquet(result).filter(level="recording").sort("genre_name").to_dicts() == [
        {"musicbrainz_recording_id": "gid-1000", "level": "recording", "genre_name": "jazz", "weight": 4},
        {"musicbrainz_recording_id": "gid-1000", "level": "recording", "genre_name": "rock", "weight": 5},
    ]


def test_song_genre_inherits_official_release_groups_once_per_song(tmp_path: Path) -> None:
    bronze_dir, silver_dir = _write_inputs(tmp_path)

    result = sg.song_genre(bronze_dir, silver_dir, tmp_path / "out")

    assert pl.read_parquet(result).filter(level="release_group").to_dicts() == [
        {"musicbrainz_recording_id": "gid-1000", "level": "release_group", "genre_name": "jazz", "weight": 7},
    ]


def test_song_genre_raises_without_a_single_official_status(tmp_path: Path) -> None:
    bronze_dir, silver_dir = _write_inputs(tmp_path)
    pl.DataFrame([{"id": 3, "name": "Bootleg"}]).write_parquet(bronze_dir / "release_status.parquet")

    with pytest.raises(ValueError, match="'Official'"):
        sg.song_genre(bronze_dir, silver_dir, tmp_path / "out")
