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
    # A recording with no genre is still a song candidate.
    {"recording_id": 1005, "url": "https://youtu.be/eeeeeeeeeee", "link_type": "streaming"},
]

RECORDING_ROWS = [
    {"id": 1000, "gid": "00000000-0000-0000-0000-000000001000", "name": "Song A", "artist_credit": 10},
    {"id": 1001, "gid": "00000000-0000-0000-0000-000000001001", "name": "Song B", "artist_credit": 11},
    {"id": 1002, "gid": "00000000-0000-0000-0000-000000001002", "name": "Song C", "artist_credit": 10},
    {"id": 1004, "gid": "00000000-0000-0000-0000-000000001004", "name": "Song D", "artist_credit": 11},
    {"id": 1003, "gid": "00000000-0000-0000-0000-000000001003", "name": "Song E", "artist_credit": 10},
    {"id": 1005, "gid": "00000000-0000-0000-0000-000000001005", "name": "Song F", "artist_credit": 11},
]

L_RECORDING_WORK_SCHEMA = {"id": pl.Int64, "link": pl.Int64, "entity0": pl.Int64, "entity1": pl.Int64}

ARTIST_CREDIT_NAME_ROWS = [
    {"artist_credit": 10, "position": 0, "artist": 500, "name": "Artist X", "join_phrase": ""},
    {"artist_credit": 11, "position": 0, "artist": 501, "name": "Artist Y", "join_phrase": " feat. "},
    {"artist_credit": 11, "position": 1, "artist": 502, "name": "Artist Z", "join_phrase": ""},
]

ARTIST_ROWS = [
    {"id": 500, "gid": "00000000-0000-0000-0000-000000000500", "name": "Artist X"},
    {"id": 501, "gid": "00000000-0000-0000-0000-000000000501", "name": "Artist Y"},
    {"id": 502, "gid": "00000000-0000-0000-0000-000000000502", "name": "Artist Z"},
]

RECORDING_FIRST_RELEASE_DATE_ROWS = [
    {"recording": 1000, "year": 1999, "month": 5, "day": None},
    {"recording": 1001, "year": None, "month": None, "day": None},
    {"recording": 1002, "year": 2001, "month": None, "day": None},
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
    video_id: None
    for video_id in ["aaaaaaaaaaa", "zzzzzzzzzzz", "bbbbbbbbbbb", "ccccccccccc", "ddddddddddd", "eeeeeeeeeee"]
}


VIDEO_CSV = "manual_recording_youtube_video.csv"
VIDEO_COLUMNS = ("musicbrainz_recording_id", "youtube_video_id", "unplayable_reason", "reason")


def _write_videos(tmp_path: Path, rows: tuple[dict[str, str | None], ...] = ()) -> Path:
    path = tmp_path / VIDEO_CSV
    pl.DataFrame(
        [{column: row.get(column) for column in VIDEO_COLUMNS} for row in rows],
        schema=dict.fromkeys(VIDEO_COLUMNS, pl.Utf8),
    ).write_csv(path)
    return path


def _songs(bronze_dir: Path, silver_dir: Path, output_dir: Path) -> Path:
    return sl.songs(bronze_dir, silver_dir, output_dir, bronze_dir.parent / VIDEO_CSV)


def _write_inputs(
    tmp_path: Path, reasons: dict[str, str | None] = ALL_PLAYABLE, videos: tuple[dict[str, str | None], ...] = ()
) -> tuple[Path, Path]:
    bronze_dir = tmp_path / "bronze"
    silver_dir = tmp_path / "silver"
    bronze_dir.mkdir()
    silver_dir.mkdir()
    pl.DataFrame(RECORDING_LINK_ROWS).write_parquet(silver_dir / "1_recording_link.parquet")
    pl.DataFrame(RECORDING_ROWS).write_parquet(bronze_dir / "recording.parquet")
    pl.DataFrame(ARTIST_CREDIT_NAME_ROWS).write_parquet(bronze_dir / "artist_credit_name.parquet")
    pl.DataFrame(ARTIST_ROWS).write_parquet(bronze_dir / "artist.parquet")
    pl.DataFrame(RECORDING_FIRST_RELEASE_DATE_ROWS, schema_overrides={"year": pl.Int16}).write_parquet(
        bronze_dir / "recording_first_release_date.parquet"
    )
    pl.DataFrame(schema=L_RECORDING_WORK_SCHEMA).write_parquet(bronze_dir / "l_recording_work.parquet")
    sl.youtube_candidates(bronze_dir, silver_dir, silver_dir, _write_videos(tmp_path, videos))
    _write_status(silver_dir, reasons)
    return bronze_dir, silver_dir


def test_youtube_candidates_ranks_every_candidate(tmp_path: Path) -> None:
    _, silver_dir = _write_inputs(tmp_path)

    rows = pl.read_parquet(silver_dir / "3_youtube_candidates.parquet").sort("recording_id", "rank").to_dicts()

    assert rows == [
        {"recording_id": 1000, "youtube_video_id": "aaaaaaaaaaa", "rank": 1, "pinned": False},
        {"recording_id": 1000, "youtube_video_id": "zzzzzzzzzzz", "rank": 2, "pinned": False},
        {"recording_id": 1001, "youtube_video_id": "bbbbbbbbbbb", "rank": 1, "pinned": False},
        {"recording_id": 1002, "youtube_video_id": "ccccccccccc", "rank": 1, "pinned": False},
        {"recording_id": 1004, "youtube_video_id": "ddddddddddd", "rank": 1, "pinned": False},
        {"recording_id": 1005, "youtube_video_id": "eeeeeeeeeee", "rank": 1, "pinned": False},
    ]


def test_songs_joins_link_and_artist_credit(tmp_path: Path) -> None:
    bronze_dir, silver_dir = _write_inputs(tmp_path)
    output_dir = tmp_path / "output"

    result = _songs(bronze_dir, silver_dir, output_dir)

    assert result == output_dir / "5_songs.parquet"
    rows = (
        pl.read_parquet(result)
        .sort("title")
        .select("musicbrainz_recording_id", "title", "artist", "artist_musicbrainz_id", "youtube_video_id")
        .to_dicts()
    )
    assert rows == [
        {
            "musicbrainz_recording_id": "00000000-0000-0000-0000-000000001000",
            "title": "Song A",
            "artist": "Artist X",
            "artist_musicbrainz_id": "00000000-0000-0000-0000-000000000500",
            "youtube_video_id": "aaaaaaaaaaa",
        },
        {
            "musicbrainz_recording_id": "00000000-0000-0000-0000-000000001001",
            "title": "Song B",
            "artist": "Artist Y",
            "artist_musicbrainz_id": "00000000-0000-0000-0000-000000000501",
            "youtube_video_id": "bbbbbbbbbbb",
        },
        {
            "musicbrainz_recording_id": "00000000-0000-0000-0000-000000001002",
            "title": "Song C",
            "artist": "Artist X",
            "artist_musicbrainz_id": "00000000-0000-0000-0000-000000000500",
            "youtube_video_id": "ccccccccccc",
        },
        {
            "musicbrainz_recording_id": "00000000-0000-0000-0000-000000001004",
            "title": "Song D",
            "artist": "Artist Y",
            "artist_musicbrainz_id": "00000000-0000-0000-0000-000000000501",
            "youtube_video_id": "ddddddddddd",
        },
        {
            "musicbrainz_recording_id": "00000000-0000-0000-0000-000000001003",
            "title": "Song E",
            "artist": "Artist X",
            "artist_musicbrainz_id": "00000000-0000-0000-0000-000000000500",
            "youtube_video_id": None,
        },
        {
            "musicbrainz_recording_id": "00000000-0000-0000-0000-000000001005",
            "title": "Song F",
            "artist": "Artist Y",
            "artist_musicbrainz_id": "00000000-0000-0000-0000-000000000501",
            "youtube_video_id": "eeeeeeeeeee",
        },
    ]


def _first_release_years(tmp_path: Path, years: dict[int, int | None]) -> dict[str, int | None]:
    bronze_dir, silver_dir = _write_inputs(tmp_path)
    # Recording 1006 re-records "Song A" by Artist X (same normalized title), so it joins song 1000's group.
    recordings = pl.read_parquet(bronze_dir / "recording.parquet")
    pl.concat(
        [
            recordings,
            pl.DataFrame(
                [{"id": 1006, "gid": "00000000-0000-0000-0000-000000001006", "name": " song  a", "artist_credit": 10}],
                schema=recordings.schema,
            ),
        ]
    ).write_parquet(bronze_dir / "recording.parquet")
    dates = pl.read_parquet(bronze_dir / "recording_first_release_date.parquet")
    pl.concat(
        [
            dates.filter(~pl.col("recording").is_in(list(years))),
            pl.DataFrame(
                [
                    {"recording": recording, "year": year, "month": None, "day": None}
                    for recording, year in years.items()
                ],
                schema=dates.schema,
            ),
        ]
    ).write_parquet(bronze_dir / "recording_first_release_date.parquet")
    return {
        row["title"]: row["first_release_year"]
        for row in pl.read_parquet(_songs(bronze_dir, silver_dir, tmp_path / "output")).to_dicts()
    }


def test_songs_first_release_year_is_the_earliest_across_the_group(tmp_path: Path) -> None:
    years = _first_release_years(tmp_path, {1006: 1985})

    assert years == {
        "Song A": 1985,
        "Song B": None,
        "Song C": 2001,
        "Song D": None,
        "Song E": None,
        "Song F": None,
    }


def test_songs_first_release_year_ignores_out_of_range_years(tmp_path: Path) -> None:
    # Song A: a corrupt early year (20) must not erase the valid 1985. Song C: only out-of-range years -> null.
    years = _first_release_years(tmp_path, {1000: 20, 1006: 1985, 1002: 3036})

    assert years["Song A"] == 1985
    assert years["Song C"] is None


def _song_a(tmp_path: Path, reasons: dict[str, str | None]) -> dict:
    bronze_dir, silver_dir = _write_inputs(tmp_path, {**ALL_PLAYABLE, **reasons})
    result = pl.read_parquet(_songs(bronze_dir, silver_dir, tmp_path / "output"))
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


def test_songs_skips_unchecked_candidates(tmp_path: Path) -> None:
    bronze_dir, silver_dir = _write_inputs(tmp_path, {"zzzzzzzzzzz": "private"})

    result = pl.read_parquet(_songs(bronze_dir, silver_dir, tmp_path / "output"))

    assert result.filter(pl.col("title") == "Song A").select(
        "youtube_video_id", "youtube_unplayable_reason"
    ).to_dicts() == [{"youtube_video_id": "zzzzzzzzzzz", "youtube_unplayable_reason": "private"}]
    assert result.filter(pl.col("title") == "Song B")["youtube_video_id"].to_list() == [None]


def _dedup_songs(tmp_path: Path, recordings: list[dict], works: list[tuple[int, int]]) -> list:
    bronze_dir, silver_dir = _write_inputs(tmp_path)
    pl.DataFrame(recordings).write_parquet(bronze_dir / "recording.parquet")
    pl.DataFrame(
        [{"id": i, "link": 1, "entity0": r, "entity1": w} for i, (r, w) in enumerate(works)],
        schema=L_RECORDING_WORK_SCHEMA,
    ).write_parquet(bronze_dir / "l_recording_work.parquet")
    result = pl.read_parquet(_songs(bronze_dir, silver_dir, tmp_path / "output"))
    return result.sort("musicbrainz_recording_id").select("musicbrainz_recording_id", "youtube_video_id").rows()


def _recording(recording_id: int, name: str, artist_credit: int = 10) -> dict:
    return {"id": recording_id, "gid": f"gid-{recording_id}", "name": name, "artist_credit": artist_credit}


def test_songs_groups_recordings_of_one_work_by_primary_artist(tmp_path: Path) -> None:
    recordings = [
        _recording(1002, "Song (live)"),
        _recording(1000, "Song"),
        _recording(1001, "Song", artist_credit=11),  # same work, other artist: its own song
        _recording(1004, "Medley"),  # two works: grouped by title, not by either work
    ]
    works = [(1000, 7), (1002, 7), (1001, 7), (1004, 7), (1004, 8)]

    assert _dedup_songs(tmp_path, recordings, works) == [
        # Lowest id keys the group; 1000's video wins on rank.
        ("gid-1000", "aaaaaaaaaaa"),
        ("gid-1001", "bbbbbbbbbbb"),
        ("gid-1004", "ddddddddddd"),
    ]


def test_songs_groups_workless_recordings_by_normalized_title(tmp_path: Path) -> None:
    recordings = [
        _recording(1002, "  song   a "),
        _recording(1004, "SONG A"),
        _recording(1000, "Song A"),  # has a work: not merged with the title group
        _recording(1001, "Song A", artist_credit=11),
    ]

    assert _dedup_songs(tmp_path, recordings, [(1000, 7)]) == [
        ("gid-1000", "aaaaaaaaaaa"),
        ("gid-1001", "bbbbbbbbbbb"),
        ("gid-1002", "ccccccccccc"),
    ]


def test_songs_creates_output_dir(tmp_path: Path) -> None:
    bronze_dir, silver_dir = _write_inputs(tmp_path)
    output_dir = tmp_path / "does" / "not" / "exist"

    _songs(bronze_dir, silver_dir, output_dir)

    assert output_dir.is_dir()


def test_video_id_pattern_truncates_stray_trailing_character() -> None:
    # Real MusicBrainz data has been seen with a stray trailing `-` after an otherwise-valid id.
    match = re.search(sl._VIDEO_ID_PATTERN, "https://youtu.be/eeeeeeeeeee-")
    assert match is not None
    assert match.group(1) == "eeeeeeeeeee"


def test_video_id_pattern_rejects_fewer_than_eleven_characters() -> None:
    match = re.search(sl._VIDEO_ID_PATTERN, "https://youtu.be/fffffffff")
    assert match is None


def _mbid(recording_id: int) -> str:
    return f"00000000-0000-0000-0000-{recording_id:012d}"


def test_youtube_candidates_pins_curated_videos_at_rank_zero(tmp_path: Path) -> None:
    videos = (
        {"musicbrainz_recording_id": _mbid(1003), "youtube_video_id": "mmmmmmmmmmm", "reason": "x"},
        # Already a linked candidate: kept once, as the pinned one.
        {"musicbrainz_recording_id": _mbid(1000), "youtube_video_id": "zzzzzzzzzzz", "reason": "x"},
        # No video: nothing to check.
        {"musicbrainz_recording_id": _mbid(1001), "reason": "x"},
    )
    _, silver_dir = _write_inputs(tmp_path, videos=videos)

    rows = pl.read_parquet(silver_dir / "3_youtube_candidates.parquet").sort("recording_id", "rank").to_dicts()

    assert [row for row in rows if row["recording_id"] in (1000, 1003)] == [
        {"recording_id": 1000, "youtube_video_id": "zzzzzzzzzzz", "rank": 0, "pinned": True},
        {"recording_id": 1000, "youtube_video_id": "aaaaaaaaaaa", "rank": 1, "pinned": False},
        {"recording_id": 1003, "youtube_video_id": "mmmmmmmmmmm", "rank": 0, "pinned": True},
    ]


def _videos(tmp_path: Path, videos: tuple[dict[str, str | None], ...], reasons: dict[str, str | None]) -> dict:
    bronze_dir, silver_dir = _write_inputs(tmp_path, {**ALL_PLAYABLE, **reasons}, videos)
    result = pl.read_parquet(_songs(bronze_dir, silver_dir, tmp_path / "output"))
    return {row[0]: row[1:] for row in result.select("title", "youtube_video_id", "youtube_unplayable_reason").rows()}


def test_songs_curated_video_always_wins(tmp_path: Path) -> None:
    videos = (
        # Unplayable when checked: still wins over the playable linked candidate, with its checked reason.
        {"musicbrainz_recording_id": _mbid(1000), "youtube_video_id": "zzzzzzzzzzz", "reason": "x"},
        # The rule's reason beats the checked status.
        {
            "musicbrainz_recording_id": _mbid(1001),
            "youtube_video_id": "eeeeeeeeeee",
            "unplayable_reason": "private",
            "reason": "x",
        },
        # Empty video id: the song goes out without a video.
        {"musicbrainz_recording_id": _mbid(1002), "unplayable_reason": "not_found", "reason": "x"},
        # Unchecked yet: no reason, i.e. playable.
        {"musicbrainz_recording_id": _mbid(1003), "youtube_video_id": "mmmmmmmmmmm", "reason": "x"},
    )

    assert _videos(tmp_path, videos, {"zzzzzzzzzzz": "not_embeddable"}) == {
        "Song A": ("zzzzzzzzzzz", "not_embeddable"),
        "Song B": ("eeeeeeeeeee", "private"),
        "Song C": (None, None),
        "Song D": ("ddddddddddd", None),
        "Song E": ("mmmmmmmmmmm", None),
        "Song F": ("eeeeeeeeeee", None),
    }


@pytest.mark.parametrize(
    ("row", "match"),
    [
        ({"youtube_video_id": "aaaaaaaaaaa", "reason": "x"}, "blank musicbrainz_recording_id"),
        ({"musicbrainz_recording_id": _mbid(1000), "youtube_video_id": "short", "reason": "x"}, "malformed"),
        (
            {"musicbrainz_recording_id": _mbid(1000), "unplayable_reason": "gone", "reason": "x"},
            "unknown unplayable_reason",
        ),
        ({"musicbrainz_recording_id": _mbid(9999), "reason": "x"}, "unknown musicbrainz_recording_id"),
    ],
)
def test_youtube_candidates_raises_on_bad_curated_video(tmp_path: Path, row: dict[str, str | None], match: str) -> None:
    with pytest.raises(ValueError, match=match):
        _write_inputs(tmp_path, videos=(row,))


def test_youtube_candidates_raises_on_duplicate_curated_recording(tmp_path: Path) -> None:
    row = {"musicbrainz_recording_id": _mbid(1000), "reason": "x"}

    with pytest.raises(ValueError, match="duplicate musicbrainz_recording_id"):
        _write_inputs(tmp_path, videos=(row, row))
