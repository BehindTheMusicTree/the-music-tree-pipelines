import json
from pathlib import Path

import polars as pl
import pytest

from jsonschema import validate

from gold.song_export import SONGS_SCHEMA_PATH, export_songs

MATCH_ROWS = [
    {
        "title": "Resolved Song",
        "artist": "Artist A",
        "youtube_video_id": "abc123abc12",
        "youtube_unplayable_reason": None,
        "genre_name": "Rock",
        "wikidata_genre_name": "rock",
        "match_method": "exact",
    },
    {
        "title": "Flagged Song",
        "artist": "Artist D",
        "youtube_video_id": "jkl012jkl01",
        "youtube_unplayable_reason": "not_embeddable",
        "genre_name": "Jazz",
        "wikidata_genre_name": "jazz",
        "match_method": "exact",
    },
    {
        "title": "Non Genre Song",
        "artist": "Artist B",
        "youtube_video_id": "def456def45",
        "genre_name": "asmr",
        "wikidata_genre_name": None,
        "match_method": "accepted_non_genre",
    },
    {
        "title": "Unmatched Song",
        "artist": "Artist C",
        "youtube_video_id": "ghi789ghi78",
        "genre_name": "some random tag",
        "wikidata_genre_name": None,
        "match_method": "unmatched",
    },
]


def _write_genre_match(tmp_path: Path, rows: list[dict] | None = None) -> Path:
    path = tmp_path / "1_genre_match.parquet"
    rows = [{"youtube_unplayable_reason": None, **row} for row in (rows if rows is not None else MATCH_ROWS)]
    pl.DataFrame(rows, schema_overrides={"youtube_unplayable_reason": pl.Utf8}).write_parquet(path)
    return path


def test_export_songs_filters_out_unmatched_and_accepted_non_genre_rows(tmp_path: Path) -> None:
    genre_match_path = _write_genre_match(tmp_path)

    result = export_songs(genre_match_path, tmp_path / "gold")

    songs = json.loads(result.read_text())
    assert [song["title"] for song in songs] == ["Resolved Song", "Flagged Song"]


def test_export_songs_renames_wikidata_genre_name_to_genre_name(tmp_path: Path) -> None:
    genre_match_path = _write_genre_match(tmp_path)

    result = export_songs(genre_match_path, tmp_path / "gold")

    songs = json.loads(result.read_text())
    assert songs[0] == {
        "title": "Resolved Song",
        "artist": "Artist A",
        "youtube_video_id": "abc123abc12",
        "youtube_unplayable_reason": None,
        "genre_name": "rock",
    }


def test_export_songs_keeps_flagged_songs_with_their_reason(tmp_path: Path) -> None:
    genre_match_path = _write_genre_match(tmp_path)

    result = export_songs(genre_match_path, tmp_path / "gold")

    songs = json.loads(result.read_text())
    assert songs[1] == {
        "title": "Flagged Song",
        "artist": "Artist D",
        "youtube_video_id": "jkl012jkl01",
        "youtube_unplayable_reason": "not_embeddable",
        "genre_name": "jazz",
    }
    validate(songs, json.loads(SONGS_SCHEMA_PATH.read_text()))


def test_export_songs_raises_on_unknown_youtube_unplayable_reason(tmp_path: Path) -> None:
    genre_match_path = _write_genre_match(tmp_path, rows=[{**MATCH_ROWS[0], "youtube_unplayable_reason": "deleted"}])

    with pytest.raises(ValueError, match="schema validation"):
        export_songs(genre_match_path, tmp_path / "gold")


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


def test_export_songs_raises_on_malformed_youtube_video_id(tmp_path: Path) -> None:
    # Regression: real MusicBrainz data has produced ids that aren't exactly 11 characters
    # (a stray trailing character, or a truncated id) — this must be caught here, not shipped
    # downstream to blow up grow-the-music-tree-api's `varchar(11)` column.
    genre_match_path = _write_genre_match(
        tmp_path,
        rows=[
            {
                "title": "Malformed Id Song",
                "artist": "Artist",
                "youtube_video_id": "abc123abc123",
                "genre_name": "Rock",
                "wikidata_genre_name": "rock",
                "match_method": "exact",
            }
        ],
    )

    with pytest.raises(ValueError, match="schema validation"):
        export_songs(genre_match_path, tmp_path / "gold")
