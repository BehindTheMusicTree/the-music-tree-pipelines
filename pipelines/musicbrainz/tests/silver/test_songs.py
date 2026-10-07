import re
from datetime import UTC, datetime
from pathlib import Path

import polars as pl
import pytest

from musicbrainz.silver import songs as sl

RECORDING_LINK_ROWS = [
    # Two YouTube URL shapes for the same recording — the lexicographically first `url` wins.
    {"recording_id": 1000, "url": "https://www.youtube.com/watch?v=aaaaaaaaaaa", "link_type": "free streaming"},
    {"recording_id": 1000, "url": "https://youtu.be/zzzzzzzzzzz", "link_type": "streaming"},
    {"recording_id": 1001, "url": "https://youtu.be/bbbbbbbbbbb", "link_type": "streaming"},
    {"recording_id": 1002, "url": "https://www.youtube.com/embed/ccccccccccc", "link_type": "free streaming"},
    # A recording with only a non-YouTube link is never a song candidate.
    {"recording_id": 1003, "url": "https://open.spotify.com/track/def", "link_type": "streaming"},
    # A bare playlist URL carries no video id and must be dropped, not crash extraction.
    {"recording_id": 1004, "url": "https://www.youtube.com/playlist?list=PLxyz", "link_type": "streaming"},
    {"recording_id": 1004, "url": "https://youtu.be/ddddddddddd", "link_type": "streaming"},
    # Same video under two URL shapes collapses to one candidate.
    {"recording_id": 1004, "url": "https://www.youtube.com/watch?v=ddddddddddd", "link_type": "free streaming"},
    # A recording with no genre is never status-checked.
    {"recording_id": 1005, "url": "https://youtu.be/eeeeeeeeeee", "link_type": "streaming"},
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


CHECKED_AT = datetime(2026, 1, 1, tzinfo=UTC)


def _write_status(silver_dir: Path, reasons: dict[str, str | None]) -> None:
    pl.DataFrame(
        [
            {"youtube_video_id": video_id, "youtube_unplayable_reason": reason, "checked_at": CHECKED_AT}
            for video_id, reason in reasons.items()
        ],
        schema_overrides={"youtube_unplayable_reason": pl.Utf8},
    ).write_parquet(silver_dir / "4_youtube_video_status.parquet")


ALL_PLAYABLE = {
    video_id: None for video_id in ["aaaaaaaaaaa", "zzzzzzzzzzz", "bbbbbbbbbbb", "ccccccccccc", "ddddddddddd"]
}


def _write_precedence(tmp_path: Path, rules: tuple[tuple[str, str], ...] = ()) -> Path:
    path = tmp_path / "manual_genre_precedence.csv"
    pl.DataFrame(
        [{"musicbrainz_genre_name": w, "over_musicbrainz_genre_name": o, "reason": "r"} for w, o in rules],
        schema=dict.fromkeys(("musicbrainz_genre_name", "over_musicbrainz_genre_name", "reason"), pl.Utf8),
    ).write_csv(path)
    return path


def _write_inputs(tmp_path: Path, reasons: dict[str, str | None] = ALL_PLAYABLE) -> tuple[Path, Path]:
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
    sl.youtube_candidates(silver_dir, silver_dir)
    _write_status(silver_dir, reasons)
    return bronze_dir, silver_dir


def test_youtube_candidates_ranks_every_candidate_of_recordings_with_a_genre(tmp_path: Path) -> None:
    _, silver_dir = _write_inputs(tmp_path)

    rows = pl.read_parquet(silver_dir / "3_youtube_candidates.parquet").sort("recording_id", "rank").to_dicts()

    assert rows == [
        {"recording_id": 1000, "youtube_video_id": "aaaaaaaaaaa", "rank": 1},
        {"recording_id": 1000, "youtube_video_id": "zzzzzzzzzzz", "rank": 2},
        {"recording_id": 1001, "youtube_video_id": "bbbbbbbbbbb", "rank": 1},
        {"recording_id": 1002, "youtube_video_id": "ccccccccccc", "rank": 1},
        {"recording_id": 1004, "youtube_video_id": "ddddddddddd", "rank": 1},
    ]


def test_songs_joins_link_genre_and_artist_credit(tmp_path: Path) -> None:
    bronze_dir, silver_dir = _write_inputs(tmp_path)
    output_dir = tmp_path / "output"

    result = sl.songs(bronze_dir, silver_dir, output_dir, _write_precedence(tmp_path))

    assert result == output_dir / "5_songs.parquet"
    rows = pl.read_parquet(result).sort("title").select("title", "artist", "youtube_video_id", "genre_name").to_dicts()
    assert rows == [
        {"title": "Song A", "artist": "Artist X", "youtube_video_id": "aaaaaaaaaaa", "genre_name": "jazz"},
        {"title": "Song B", "artist": "Artist Y", "youtube_video_id": "bbbbbbbbbbb", "genre_name": "rock"},
        {"title": "Song C", "artist": "Artist X", "youtube_video_id": "ccccccccccc", "genre_name": "rock"},
        {"title": "Song D", "artist": "Artist Y", "youtube_video_id": "ddddddddddd", "genre_name": "rock"},
    ]


def _song_a(tmp_path: Path, reasons: dict[str, str | None]) -> dict:
    bronze_dir, silver_dir = _write_inputs(tmp_path, {**ALL_PLAYABLE, **reasons})
    result = pl.read_parquet(sl.songs(bronze_dir, silver_dir, tmp_path / "output", _write_precedence(tmp_path)))
    return (
        result.filter(pl.col("title") == "Song A")
        .select("youtube_video_id", "youtube_unplayable_reason")
        .row(0, named=True)
    )


def test_songs_falls_back_to_next_playable_candidate(tmp_path: Path) -> None:
    assert _song_a(tmp_path, {"aaaaaaaaaaa": "not_embeddable"}) == {
        "youtube_video_id": "zzzzzzzzzzz",
        "youtube_unplayable_reason": None,
    }


def test_songs_keeps_rank_one_with_its_reason_when_no_candidate_is_playable(tmp_path: Path) -> None:
    assert _song_a(tmp_path, {"aaaaaaaaaaa": "not_found", "zzzzzzzzzzz": "private"}) == {
        "youtube_video_id": "aaaaaaaaaaa",
        "youtube_unplayable_reason": "not_found",
    }


def test_songs_raises_on_candidate_missing_from_status(tmp_path: Path) -> None:
    bronze_dir, silver_dir = _write_inputs(tmp_path, {"aaaaaaaaaaa": None})

    with pytest.raises(ValueError, match="missing from 4_youtube_video_status"):
        sl.songs(bronze_dir, silver_dir, tmp_path / "output", _write_precedence(tmp_path))


def test_songs_drops_recording_without_youtube_link(tmp_path: Path) -> None:
    bronze_dir, silver_dir = _write_inputs(tmp_path)
    output_dir = tmp_path / "output"

    result = sl.songs(bronze_dir, silver_dir, output_dir, _write_precedence(tmp_path))

    titles = pl.read_parquet(result)["title"].to_list()
    assert "Song for 1003" not in titles


def test_songs_creates_output_dir(tmp_path: Path) -> None:
    bronze_dir, silver_dir = _write_inputs(tmp_path)
    output_dir = tmp_path / "does" / "not" / "exist"

    sl.songs(bronze_dir, silver_dir, output_dir, _write_precedence(tmp_path))

    assert output_dir.is_dir()


PRECEDENCE_GENRES = [
    {"id": 100, "name": "rock"},
    {"id": 101, "name": "jazz"},
    {"id": 200, "name": "reggae"},
    {"id": 201, "name": "ska"},
    {"id": 202, "name": "dub"},
]


def _primary_genres(tmp_path: Path, genre_rows: list[dict], rules: tuple[tuple[str, str], ...]) -> dict[str, str]:
    bronze_dir, silver_dir = _write_inputs(tmp_path)
    pl.DataFrame(genre_rows).write_parquet(bronze_dir / "genre.parquet")
    pl.DataFrame(PRECEDENCE_RECORDING_GENRE_ROWS).write_parquet(silver_dir / "2_recording_genre.parquet")
    result = pl.read_parquet(sl.songs(bronze_dir, silver_dir, tmp_path / "output", _write_precedence(tmp_path, rules)))
    return dict(result.select("title", "genre_name").iter_rows())


PRECEDENCE_RECORDING_GENRE_ROWS = [
    # Song A: reggae outweighs ska, but ska is the more precise rule winner.
    {"recording_id": 1000, "genre_id": 200, "weight": 9},
    {"recording_id": 1000, "genre_id": 201, "weight": 1},
    # Song B: ska beats reggae and inherits its 9, which then beats jazz's 5.
    {"recording_id": 1001, "genre_id": 200, "weight": 9},
    {"recording_id": 1001, "genre_id": 201, "weight": 1},
    {"recording_id": 1001, "genre_id": 101, "weight": 5},
    # Song C: no rule applies, highest weight wins.
    {"recording_id": 1002, "genre_id": 100, "weight": 7},
    {"recording_id": 1002, "genre_id": 202, "weight": 2},
    # Song D: ska beats dub transitively via reggae, even with reggae absent.
    {"recording_id": 1004, "genre_id": 202, "weight": 8},
    {"recording_id": 1004, "genre_id": 201, "weight": 1},
]


def test_songs_genre_precedence_picks_precise_genre(tmp_path: Path) -> None:
    rules = (("ska", "reggae"), ("reggae", "dub"))

    assert _primary_genres(tmp_path, PRECEDENCE_GENRES, rules) == {
        "Song A": "ska",
        "Song B": "ska",
        "Song C": "rock",
        "Song D": "ska",
    }


@pytest.mark.parametrize(
    ("rules", "match"),
    [
        ((("ska", "ska"),), "self-pair or cycle"),
        ((("ska", "reggae"), ("reggae", "ska")), "self-pair or cycle"),
        ((("ska", "reggae"), ("reggae", "dub"), ("dub", "ska")), "self-pair or cycle"),
        ((("ska ", "reggae"),), r"absent from genre.parquet: \['ska '\]"),
    ],
)
def test_songs_genre_precedence_raises_on_invalid_rules(
    tmp_path: Path, rules: tuple[tuple[str, str], ...], match: str
) -> None:
    with pytest.raises(ValueError, match=match):
        _primary_genres(tmp_path, PRECEDENCE_GENRES, rules)


def test_video_id_pattern_truncates_stray_trailing_character() -> None:
    # Real MusicBrainz data has been seen with a stray trailing `-` after an otherwise-valid id.
    match = re.search(sl._VIDEO_ID_PATTERN, "https://youtu.be/eeeeeeeeeee-")
    assert match is not None
    assert match.group(1) == "eeeeeeeeeee"


def test_video_id_pattern_rejects_fewer_than_eleven_characters() -> None:
    match = re.search(sl._VIDEO_ID_PATTERN, "https://youtu.be/fffffffff")
    assert match is None
