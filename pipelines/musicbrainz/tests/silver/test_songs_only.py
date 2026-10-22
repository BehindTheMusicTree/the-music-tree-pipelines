from pathlib import Path
from unittest.mock import MagicMock

import pytest

from musicbrainz.silver import songs_only


def _point_env_at(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> tuple[Path, Path, Path]:
    dirs = (tmp_path / "bronze", tmp_path / "silver", tmp_path / "curation")
    for name, directory in zip(("BRONZE_OUTPUT_DIR", "SILVER_OUTPUT_DIR", "CURATION_BRONZE_DIR"), dirs, strict=True):
        directory.mkdir()
        monkeypatch.setenv(name, str(directory))
    monkeypatch.delenv("YOUTUBE_API_KEY", raising=False)
    monkeypatch.delenv("YOUTUBE_STATUS_MAX_BATCHES_PER_RUN", raising=False)
    return dirs


def test_main_runs_only_songs(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    bronze_dir, silver_dir, curation_dir = _point_env_at(monkeypatch, tmp_path)
    for name in songs_only._BRONZE_INPUTS:
        (bronze_dir / f"{name}.parquet").touch()
    for name in songs_only._SILVER_INPUTS:
        (silver_dir / f"{name}.parquet").touch()
    (curation_dir / "manual_recording_youtube_video.csv").touch()
    songs = MagicMock()
    monkeypatch.setattr(songs_only, "songs", songs)

    songs_only.main()

    songs.assert_called_once_with(
        bronze_dir,
        silver_dir,
        silver_dir,
        curation_dir / "manual_recording_youtube_video.csv",
    )
    assert not any(name.startswith(("recording_", "youtube_")) for name in vars(songs_only))


def test_main_fails_fast_on_missing_input(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    _point_env_at(monkeypatch, tmp_path)
    songs = MagicMock()
    monkeypatch.setattr(songs_only, "songs", songs)

    with pytest.raises(FileNotFoundError, match="4_youtube_video_status.parquet"):
        songs_only.main()
    songs.assert_not_called()
