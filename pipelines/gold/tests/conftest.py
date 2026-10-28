from collections.abc import Callable
from pathlib import Path

import polars as pl
import pytest

CURATION_COLUMNS = {
    "secondary_parents": ("item_id", "parent_id", "action", "reason"),
    "essential_songs": ("item_id", "musicbrainz_recording_id", "position", "reason"),
    "summary_overrides": ("item_id", "summary", "reason"),
    "canonical_genre_scene": ("item_id", "scene", "reason"),
    "song_genre": ("musicbrainz_recording_id", "genre_item_id", "reason"),
    "genre_precedence": ("item_id", "over_item_id", "reason"),
    "genre_disambiguation": ("musicbrainz_genre_name", "item_id", "is_default", "reason"),
    "genre_disambiguation_context": ("musicbrainz_genre_name", "item_id", "context_item_id", "reason"),
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
def descriptions_path(tmp_path: Path) -> Path:
    path = tmp_path / "wikidata_genre_descriptions.parquet"
    pl.DataFrame(
        schema={"item_id": pl.Utf8, "description": pl.Utf8, "wikipedia_url": pl.Utf8, "wikipedia_intro": pl.Utf8}
    ).write_parquet(path)
    return path


@pytest.fixture
def write_curation(curation_dir: Path) -> Callable[[str, list[dict[str, str]]], None]:
    return lambda name, rows: _write(curation_dir, name, rows)
