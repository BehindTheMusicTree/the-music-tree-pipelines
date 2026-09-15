import json
from pathlib import Path

import polars as pl
import pytest

from gold.song_export import export_songs

MATCH_ROWS = [
    {
        "title": "Resolved Song",
        "artist": "Artist A",
        "youtube_video_id": "abc123",
        "genre_name": "Rock",
        "wikidata_genre_name": "rock",
        "match_method": "exact",
    },
    {
        "title": "Non Genre Song",
        "artist": "Artist B",
        "youtube_video_id": "def456",
        "genre_name": "asmr",
        "wikidata_genre_name": None,
        "match_method": "accepted_non_genre",
    },
    {
        "title": "Unmatched Song",
        "artist": "Artist C",
        "youtube_video_id": "ghi789",
        "genre_name": "some random tag",
        "wikidata_genre_name": None,
        "match_method": "unmatched",
    },
]


def _write_genre_match(tmp_path: Path, rows: list[dict] | None = None) -> Path:
    path = tmp_path / "1_genre_match.parquet"
    pl.DataFrame(rows if rows is not None else MATCH_ROWS).write_parquet(path)
    return path


def test_export_songs_filters_out_unmatched_and_accepted_non_genre_rows(tmp_path: Path) -> None:
    genre_match_path = _write_genre_match(tmp_path)

    result = export_songs(genre_match_path, tmp_path / "gold")

    songs = json.loads(result.read_text())
    assert len(songs) == 1
    assert songs[0]["title"] == "Resolved Song"


def test_export_songs_renames_wikidata_genre_name_to_genre_name(tmp_path: Path) -> None:
    genre_match_path = _write_genre_match(tmp_path)

    result = export_songs(genre_match_path, tmp_path / "gold")

    songs = json.loads(result.read_text())
    assert songs[0] == {
        "title": "Resolved Song",
        "artist": "Artist A",
        "youtube_video_id": "abc123",
        "genre_name": "rock",
    }


def test_export_songs_creates_output_dir(tmp_path: Path) -> None:
    genre_match_path = _write_genre_match(tmp_path)
    output_dir = tmp_path / "does" / "not" / "exist"

    export_songs(genre_match_path, output_dir)

    assert output_dir.is_dir()


def test_export_songs_raises_on_empty_result(tmp_path: Path) -> None:
    genre_match_path = _write_genre_match(tmp_path, rows=[row for row in MATCH_ROWS if row["match_method"] != "exact"])

    with pytest.raises(ValueError, match="is empty"):
        export_songs(genre_match_path, tmp_path / "gold")


def test_export_songs_raises_on_schema_violation(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    import gold.song_export as module

    genre_match_path = _write_genre_match(
        tmp_path,
        rows=[
            {
                "title": "Missing Artist",
                "artist": "",
                "youtube_video_id": "abc123",
                "genre_name": "Rock",
                "wikidata_genre_name": "rock",
                "match_method": "exact",
            }
        ],
    )

    with pytest.raises(ValueError, match="schema validation"):
        module.export_songs(genre_match_path, tmp_path / "gold")
