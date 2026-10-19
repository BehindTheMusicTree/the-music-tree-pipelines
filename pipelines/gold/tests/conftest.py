from collections.abc import Callable
from pathlib import Path

import polars as pl
import pytest

CURATION_COLUMNS = {
    "secondary_parents": ("item_id", "parent_id", "action", "item_label", "parent_label", "reason"),
    "essential_tracks": ("item_id", "musicbrainz_recording_id", "position", "reason"),
    "summary_overrides": ("item_id", "summary", "reason"),
    "recording_genre": ("musicbrainz_recording_id", "genre_item_id", "recording_label", "reason"),
    "recording_youtube_video": ("musicbrainz_recording_id", "youtube_video_id", "unplayable_reason", "reason"),
}


def _write(curation_dir: Path, name: str, rows: list[dict[str, str]]) -> None:
    columns = CURATION_COLUMNS[name]
    data = {column: [row.get(column) for row in rows] for column in columns}
    pl.DataFrame(data, schema=dict.fromkeys(columns, pl.Utf8)).write_csv(curation_dir / f"manual_{name}.csv")


@pytest.fixture
def curation_dir(tmp_path: Path) -> Path:
    path = tmp_path / "curation"
    path.mkdir()
    for name in CURATION_COLUMNS:
        _write(path, name, [])
    return path


@pytest.fixture
def write_curation(curation_dir: Path) -> Callable[[str, list[dict[str, str]]], None]:
    return lambda name, rows: _write(curation_dir, name, rows)
