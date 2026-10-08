import gzip
import json
from pathlib import Path

import polars as pl
import pytest

from gold import song_export
from gold.song_export import export_songs

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
    {
        "title": "Bare Song",
        "artist": "Artist E",
        "youtube_video_id": None,
        "genre_name": None,
        "wikidata_genre_name": None,
        "match_method": "no_genre",
    },
]


def _mbid(index: int) -> str:
    return f"00000000-0000-0000-0000-{index:012d}"


def _write_genre_match(tmp_path: Path, rows: list[dict] | None = None) -> Path:
    path = tmp_path / "1_genre_match.parquet"
    rows = [
        {"musicbrainz_recording_id": _mbid(i), "youtube_unplayable_reason": None, **row}
        for i, row in enumerate(rows if rows is not None else MATCH_ROWS)
    ]
    pl.DataFrame(rows, schema_overrides={"youtube_unplayable_reason": pl.Utf8}).write_parquet(path)
    return path


def _read_parts(parts_dir: Path) -> list[dict]:
    return [
        json.loads(line)
        for part in sorted(parts_dir.iterdir())
        for line in gzip.decompress(part.read_bytes()).decode().splitlines()
    ]


def test_export_songs_keeps_every_song_with_a_genre_only_when_resolved(tmp_path: Path) -> None:
    result = export_songs(_write_genre_match(tmp_path), tmp_path / "gold")

    assert result == tmp_path / "gold" / "2_songs"
    assert [(song["title"], song["genre_name"]) for song in _read_parts(result)] == [
        ("Resolved Song", "rock"),
        ("Flagged Song", "jazz"),
        ("Non Genre Song", None),
        ("Unmatched Song", None),
        ("Bare Song", None),
    ]


def test_export_songs_writes_snake_case_ndjson(tmp_path: Path) -> None:
    songs = _read_parts(export_songs(_write_genre_match(tmp_path), tmp_path / "gold"))

    assert songs[1] == {
        "musicbrainz_recording_id": _mbid(1),
        "title": "Flagged Song",
        "artist": "Artist D",
        "youtube_video_id": "jkl012jkl01",
        "youtube_unplayable_reason": "not_embeddable",
        "genre_name": "jazz",
    }
    assert songs[4]["youtube_video_id"] is None


def test_export_songs_splits_parts_and_clears_stale_ones(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(song_export, "ROWS_PER_PART", 2)
    parts_dir = tmp_path / "gold" / "2_songs"
    parts_dir.mkdir(parents=True)
    (parts_dir / "part-00009.ndjson.gz").write_bytes(b"")

    export_songs(_write_genre_match(tmp_path), tmp_path / "gold")

    assert sorted(p.name for p in parts_dir.iterdir()) == [
        "part-00000.ndjson.gz",
        "part-00001.ndjson.gz",
        "part-00002.ndjson.gz",
    ]
    assert len(_read_parts(parts_dir)) == len(MATCH_ROWS)


def test_export_songs_raises_on_unknown_youtube_unplayable_reason(tmp_path: Path) -> None:
    genre_match_path = _write_genre_match(tmp_path, rows=[{**MATCH_ROWS[0], "youtube_unplayable_reason": "deleted"}])

    with pytest.raises(ValueError, match="unknown youtube_unplayable_reason"):
        export_songs(genre_match_path, tmp_path / "gold")


def test_export_songs_raises_on_empty_result(tmp_path: Path) -> None:
    genre_match_path = _write_genre_match(tmp_path)
    pl.read_parquet(genre_match_path).clear().write_parquet(genre_match_path)

    with pytest.raises(ValueError, match="is empty"):
        export_songs(genre_match_path, tmp_path / "gold")


def test_export_songs_raises_on_null_artist(tmp_path: Path) -> None:
    genre_match_path = _write_genre_match(tmp_path, rows=[{**MATCH_ROWS[0], "artist": None}])

    with pytest.raises(ValueError, match="'artist' null rate"):
        export_songs(genre_match_path, tmp_path / "gold")


def test_export_songs_raises_on_malformed_youtube_video_id(tmp_path: Path) -> None:
    # grow-the-music-tree-api stores the id in a `varchar(11)` column.
    genre_match_path = _write_genre_match(tmp_path, rows=[{**MATCH_ROWS[0], "youtube_video_id": "abc123abc123"}])

    with pytest.raises(ValueError, match="malformed youtube_video_id"):
        export_songs(genre_match_path, tmp_path / "gold")


def test_export_songs_raises_on_duplicate_musicbrainz_recording_id(tmp_path: Path) -> None:
    genre_match_path = _write_genre_match(
        tmp_path,
        rows=[{**row, "musicbrainz_recording_id": _mbid(0)} for row in MATCH_ROWS[:2]],
    )

    with pytest.raises(ValueError, match="'musicbrainz_recording_id' is not unique"):
        export_songs(genre_match_path, tmp_path / "gold")


def test_export_songs_raises_on_malformed_musicbrainz_recording_id(tmp_path: Path) -> None:
    genre_match_path = _write_genre_match(tmp_path, rows=[{**MATCH_ROWS[0], "musicbrainz_recording_id": "not-a-uuid"}])

    with pytest.raises(ValueError, match="malformed musicbrainz_recording_id"):
        export_songs(genre_match_path, tmp_path / "gold")
