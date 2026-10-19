import gzip
import json
from collections.abc import Callable
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
        "first_release_year": 1977,
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
        {
            "musicbrainz_recording_id": _mbid(i),
            "artist_musicbrainz_id": _mbid(100 + i),
            "youtube_unplayable_reason": None,
            "first_release_year": None,
            **row,
        }
        for i, row in enumerate(rows if rows is not None else MATCH_ROWS)
    ]
    pl.DataFrame(
        rows, schema_overrides={"youtube_unplayable_reason": pl.Utf8, "first_release_year": pl.Int16}
    ).write_parquet(path)
    return path


def _write_silver(tmp_path: Path) -> Path:
    silver_dir = tmp_path / "wikidata_silver"
    silver_dir.mkdir(exist_ok=True)
    pl.DataFrame(
        {"item_id": ["Q1", "Q2"], "item_label": ["rock", "punk"], "item_display_label": [None, "Punk"]},
        schema_overrides={"item_display_label": pl.Utf8},
    ).write_parquet(silver_dir / "7_canonical_hierarchy.parquet")
    pl.DataFrame({"item_id": ["Q10"], "item_label": ["samba"]}).write_parquet(
        silver_dir / "8_regional_hierarchy.parquet"
    )
    return silver_dir


def _read_parts(parts_dir: Path) -> list[dict]:
    return [
        json.loads(line)
        for part in sorted(parts_dir.iterdir())
        for line in gzip.decompress(part.read_bytes()).decode().splitlines()
    ]


def test_export_songs_keeps_every_song_with_a_genre_only_when_resolved(tmp_path: Path, curation_dir: Path) -> None:
    result = export_songs(_write_genre_match(tmp_path), tmp_path / "gold", _write_silver(tmp_path), curation_dir)

    assert result == tmp_path / "gold" / "2_songs"
    assert [(song["title"], song["genre_name"]) for song in _read_parts(result)] == [
        ("Resolved Song", "rock"),
        ("Flagged Song", "jazz"),
        ("Non Genre Song", None),
        ("Unmatched Song", None),
        ("Bare Song", None),
    ]


def test_export_songs_writes_snake_case_ndjson(tmp_path: Path, curation_dir: Path) -> None:
    songs = _read_parts(
        export_songs(_write_genre_match(tmp_path), tmp_path / "gold", _write_silver(tmp_path), curation_dir)
    )

    assert songs[1] == {
        "musicbrainz_recording_id": _mbid(1),
        "title": "Flagged Song",
        "artist": "Artist D",
        "artist_musicbrainz_id": _mbid(101),
        "youtube_video_id": "jkl012jkl01",
        "youtube_unplayable_reason": "not_embeddable",
        "first_release_year": 1977,
        "genre_name": "jazz",
    }
    assert songs[4]["youtube_video_id"] is None


def test_export_songs_splits_parts_and_clears_stale_ones(
    tmp_path: Path, curation_dir: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(song_export, "ROWS_PER_PART", 2)
    parts_dir = tmp_path / "gold" / "2_songs"
    parts_dir.mkdir(parents=True)
    (parts_dir / "part-00009.ndjson.gz").write_bytes(b"")

    export_songs(_write_genre_match(tmp_path), tmp_path / "gold", _write_silver(tmp_path), curation_dir)

    assert sorted(p.name for p in parts_dir.iterdir()) == [
        "part-00000.ndjson.gz",
        "part-00001.ndjson.gz",
        "part-00002.ndjson.gz",
    ]
    assert len(_read_parts(parts_dir)) == len(MATCH_ROWS)


def test_export_songs_raises_on_unknown_youtube_unplayable_reason(tmp_path: Path, curation_dir: Path) -> None:
    genre_match_path = _write_genre_match(tmp_path, rows=[{**MATCH_ROWS[0], "youtube_unplayable_reason": "deleted"}])

    with pytest.raises(ValueError, match="unknown youtube_unplayable_reason"):
        export_songs(genre_match_path, tmp_path / "gold", _write_silver(tmp_path), curation_dir)


@pytest.mark.parametrize(("year", "expected"), [(999, None), (2101, None), (1000, 1000), (2100, 2100), (None, None)])
def test_export_songs_nulls_out_of_range_first_release_year(
    tmp_path: Path, curation_dir: Path, year: int | None, expected: int | None
) -> None:
    genre_match_path = _write_genre_match(tmp_path, rows=[{**MATCH_ROWS[0], "first_release_year": year}])

    export_songs(genre_match_path, tmp_path / "gold", _write_silver(tmp_path), curation_dir)

    assert [r["first_release_year"] for r in _read_parts(tmp_path / "gold" / "2_songs")] == [expected]


def test_export_songs_raises_on_empty_result(tmp_path: Path, curation_dir: Path) -> None:
    genre_match_path = _write_genre_match(tmp_path)
    pl.read_parquet(genre_match_path).clear().write_parquet(genre_match_path)

    with pytest.raises(ValueError, match="is empty"):
        export_songs(genre_match_path, tmp_path / "gold", _write_silver(tmp_path), curation_dir)


@pytest.mark.parametrize("column", ["artist", "artist_musicbrainz_id"])
def test_export_songs_raises_on_null_artist(tmp_path: Path, curation_dir: Path, column: str) -> None:
    genre_match_path = _write_genre_match(tmp_path, rows=[{**MATCH_ROWS[0], column: None}])

    with pytest.raises(ValueError, match=f"'{column}' null rate"):
        export_songs(genre_match_path, tmp_path / "gold", _write_silver(tmp_path), curation_dir)


@pytest.mark.parametrize("column", ["title", "artist"])
def test_export_songs_raises_on_too_long_text(tmp_path: Path, curation_dir: Path, column: str) -> None:
    genre_match_path = _write_genre_match(tmp_path, rows=[{**MATCH_ROWS[0], column: "x" * 2049}])

    with pytest.raises(ValueError, match=f"1 row\\(s\\) with '{column}' longer than 2048"):
        export_songs(genre_match_path, tmp_path / "gold", _write_silver(tmp_path), curation_dir)


def test_export_songs_raises_on_malformed_youtube_video_id(tmp_path: Path, curation_dir: Path) -> None:
    # grow-the-music-tree-api stores the id in a `varchar(11)` column.
    genre_match_path = _write_genre_match(tmp_path, rows=[{**MATCH_ROWS[0], "youtube_video_id": "abc123abc123"}])

    with pytest.raises(ValueError, match="malformed youtube_video_id"):
        export_songs(genre_match_path, tmp_path / "gold", _write_silver(tmp_path), curation_dir)


def test_export_songs_raises_on_duplicate_musicbrainz_recording_id(tmp_path: Path, curation_dir: Path) -> None:
    genre_match_path = _write_genre_match(
        tmp_path,
        rows=[{**row, "musicbrainz_recording_id": _mbid(0)} for row in MATCH_ROWS[:2]],
    )

    with pytest.raises(ValueError, match="'musicbrainz_recording_id' is not unique"):
        export_songs(genre_match_path, tmp_path / "gold", _write_silver(tmp_path), curation_dir)


def test_export_songs_raises_on_malformed_musicbrainz_recording_id(tmp_path: Path, curation_dir: Path) -> None:
    genre_match_path = _write_genre_match(tmp_path, rows=[{**MATCH_ROWS[0], "musicbrainz_recording_id": "not-a-uuid"}])

    with pytest.raises(ValueError, match="malformed musicbrainz_recording_id"):
        export_songs(genre_match_path, tmp_path / "gold", _write_silver(tmp_path), curation_dir)


def test_export_songs_applies_recording_genre_overrides(
    tmp_path: Path, curation_dir: Path, write_curation: Callable[[str, list[dict[str, str]]], None]
) -> None:
    write_curation(
        "recording_genre",
        [
            {"musicbrainz_recording_id": _mbid(0), "genre_item_id": None, "reason": "genreless"},
            {"musicbrainz_recording_id": _mbid(2), "genre_item_id": "Q2", "reason": "x"},
            {"musicbrainz_recording_id": _mbid(3), "genre_item_id": "Q10", "reason": "x"},
        ],
    )

    songs = _read_parts(
        export_songs(_write_genre_match(tmp_path), tmp_path / "gold", _write_silver(tmp_path), curation_dir)
    )

    assert [song["genre_name"] for song in songs] == [None, "jazz", "Punk", "samba", None]


@pytest.mark.parametrize(
    ("name", "row", "match"),
    [
        (
            "recording_genre",
            {"musicbrainz_recording_id": _mbid(0), "genre_item_id": "rock", "reason": "x"},
            "malformed",
        ),
        ("recording_genre", {"musicbrainz_recording_id": _mbid(0), "genre_item_id": "Q404", "reason": "x"}, "Q404"),
        ("recording_genre", {"musicbrainz_recording_id": _mbid(99), "reason": "x"}, "unknown musicbrainz_recording_id"),
    ],
)
def test_export_songs_raises_on_bad_recording_override(
    tmp_path: Path,
    curation_dir: Path,
    write_curation: Callable[[str, list[dict[str, str]]], None],
    name: str,
    row: dict[str, str],
    match: str,
) -> None:
    write_curation(name, [row])

    with pytest.raises(ValueError, match=match):
        export_songs(_write_genre_match(tmp_path), tmp_path / "gold", _write_silver(tmp_path), curation_dir)
