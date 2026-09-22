from pathlib import Path

import polars as pl
import pytest

from gold.genre_match import genre_match

HIERARCHY_ROWS = [
    {"item_id": "Q1", "item_label": "rock"},
    {"item_id": "Q2", "item_label": "jazz"},
    {"item_id": "Q3", "item_label": "drum and bass"},
]


def _write_songs(tmp_path: Path, rows: list[dict]) -> Path:
    path = tmp_path / "3_songs.parquet"
    pl.DataFrame(rows).write_parquet(path)
    return path


def _write_hierarchy(tmp_path: Path, rows: list[dict] | None = None) -> Path:
    path = tmp_path / "7_canonical_hierarchy.parquet"
    pl.DataFrame(rows if rows is not None else HIERARCHY_ROWS).write_parquet(path)
    return path


def _write_alias_csv(tmp_path: Path, rows: list[dict] | None = None) -> Path:
    path = tmp_path / "manual_genre_alias.csv"
    schema = {"musicbrainz_genre_name": pl.Utf8, "wikidata_genre_name": pl.Utf8, "reason": pl.Utf8}
    pl.DataFrame(rows or [], schema=schema).write_csv(path)
    return path


def _write_non_genre_csv(tmp_path: Path, rows: list[dict] | None = None) -> Path:
    path = tmp_path / "manual_accepted_non_genre_tags.csv"
    schema = {"musicbrainz_genre_name": pl.Utf8, "reason": pl.Utf8}
    pl.DataFrame(rows or [], schema=schema).write_csv(path)
    return path


def _run_genre_match(
    tmp_path: Path,
    song_rows: list[dict],
    hierarchy_rows: list[dict] | None = None,
    alias_rows: list[dict] | None = None,
    non_genre_rows: list[dict] | None = None,
    output_dir: Path | None = None,
) -> Path:
    song_path = _write_songs(tmp_path, song_rows)
    hierarchy_path = _write_hierarchy(tmp_path, hierarchy_rows)
    alias_path = _write_alias_csv(tmp_path, alias_rows)
    non_genre_path = _write_non_genre_csv(tmp_path, non_genre_rows)
    return genre_match(song_path, hierarchy_path, alias_path, non_genre_path, output_dir or (tmp_path / "gold"))


def _song(genre_name: str, title: str = "Song") -> dict:
    return {"title": title, "artist": "Artist", "youtube_video_id": "abc123abc12", "genre_name": genre_name}


def test_genre_match_exact_case_insensitive_match(tmp_path: Path) -> None:
    result = _run_genre_match(tmp_path, [_song("Rock")])

    df = pl.read_parquet(result)
    row = df.to_dicts()[0]
    assert row["wikidata_genre_name"] == "rock"
    assert row["match_method"] == "exact"


def test_genre_match_music_suffix_normalization(tmp_path: Path) -> None:
    result = _run_genre_match(tmp_path, [_song("Jazz Music")])

    row = pl.read_parquet(result).to_dicts()[0]
    assert row["wikidata_genre_name"] == "jazz"
    assert row["match_method"] == "music_suffix"


def test_genre_match_uses_manual_alias_csv(tmp_path: Path) -> None:
    result = _run_genre_match(
        tmp_path,
        [_song("d&b")],
        alias_rows=[
            {"musicbrainz_genre_name": "d&b", "wikidata_genre_name": "drum and bass", "reason": "abbreviation"}
        ],
    )

    row = pl.read_parquet(result).to_dicts()[0]
    assert row["wikidata_genre_name"] == "drum and bass"
    assert row["match_method"] == "manual_alias"


def test_genre_match_accepted_non_genre_tags_pass_through_unmatched(tmp_path: Path) -> None:
    result = _run_genre_match(
        tmp_path,
        [_song("asmr")],
        non_genre_rows=[{"musicbrainz_genre_name": "asmr", "reason": "not a genre"}],
    )

    df = pl.read_parquet(result)
    row = df.to_dicts()[0]
    assert row["wikidata_genre_name"] is None
    assert row["match_method"] == "accepted_non_genre"
    assert df.height == 1


def test_genre_match_writes_unresolved_report_for_untriaged_names(tmp_path: Path) -> None:
    output_dir = tmp_path / "gold"
    result = _run_genre_match(tmp_path, [_song("some random tag", title="Untriaged Song")], output_dir=output_dir)

    df = pl.read_parquet(result)
    row = df.to_dicts()[0]
    assert row["wikidata_genre_name"] is None
    assert row["match_method"] == "unmatched"

    unresolved = pl.read_csv(output_dir / "1_genre_match_unresolved.csv")
    assert unresolved.to_dicts() == [
        {
            "genre_name": "some random tag",
            "title": "Untriaged Song",
            "artist": "Artist",
            "youtube_video_id": "abc123abc12",
        }
    ]


def test_genre_match_writes_empty_unresolved_report_when_all_matched(tmp_path: Path) -> None:
    output_dir = tmp_path / "gold"
    _run_genre_match(tmp_path, [_song("Rock")], output_dir=output_dir)

    unresolved = pl.read_csv(output_dir / "1_genre_match_unresolved.csv")
    assert unresolved.is_empty()


def test_genre_match_creates_output_dir(tmp_path: Path) -> None:
    output_dir = tmp_path / "does" / "not" / "exist"

    _run_genre_match(tmp_path, [_song("Rock")], output_dir=output_dir)

    assert output_dir.is_dir()


def test_genre_match_raises_on_blank_alias_field(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="null/blank"):
        _run_genre_match(
            tmp_path,
            [_song("Rock")],
            alias_rows=[{"musicbrainz_genre_name": "d&b", "wikidata_genre_name": "", "reason": "abbreviation"}],
        )


def test_genre_match_raises_on_duplicate_alias_key(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="duplicate"):
        _run_genre_match(
            tmp_path,
            [_song("Rock")],
            alias_rows=[
                {"musicbrainz_genre_name": "d&b", "wikidata_genre_name": "drum and bass", "reason": "abbreviation"},
                {"musicbrainz_genre_name": "D&B", "wikidata_genre_name": "drum and bass", "reason": "duplicate"},
            ],
        )


def test_genre_match_raises_on_unknown_alias_wikidata_genre_name(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="not found in the canonical hierarchy"):
        _run_genre_match(
            tmp_path,
            [_song("Rock")],
            alias_rows=[{"musicbrainz_genre_name": "d&b", "wikidata_genre_name": "not a real genre", "reason": "test"}],
        )


def test_genre_match_raises_on_dead_weight_alias(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="dead-weight"):
        _run_genre_match(
            tmp_path,
            [_song("Rock")],
            alias_rows=[{"musicbrainz_genre_name": "rock", "wikidata_genre_name": "rock", "reason": "already matches"}],
        )


def test_genre_match_raises_on_blank_non_genre_field(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="null/blank"):
        _run_genre_match(
            tmp_path,
            [_song("Rock")],
            non_genre_rows=[{"musicbrainz_genre_name": "asmr", "reason": ""}],
        )


def test_genre_match_raises_on_duplicate_non_genre_key(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="duplicate"):
        _run_genre_match(
            tmp_path,
            [_song("Rock")],
            non_genre_rows=[
                {"musicbrainz_genre_name": "asmr", "reason": "not a genre"},
                {"musicbrainz_genre_name": "ASMR", "reason": "duplicate"},
            ],
        )


def test_genre_match_raises_on_non_genre_conflicting_with_auto_match(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="conflict"):
        _run_genre_match(
            tmp_path,
            [_song("Rock")],
            non_genre_rows=[{"musicbrainz_genre_name": "rock", "reason": "mistake"}],
        )


def test_genre_match_raises_on_empty_songs(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="is empty"):
        _run_genre_match(tmp_path, [])


def test_genre_match_raises_on_null_genre_name(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="null rate"):
        _run_genre_match(
            tmp_path,
            [{"title": "Song", "artist": "Artist", "youtube_video_id": "abc123abc12", "genre_name": None}],
        )


def test_genre_match_raises_on_empty_hierarchy(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="is empty"):
        _run_genre_match(tmp_path, [_song("Rock")], hierarchy_rows=[])


def test_genre_match_drops_malformed_youtube_video_id(tmp_path: Path) -> None:
    result = _run_genre_match(
        tmp_path,
        [
            _song("Rock", title="Good Song"),
            {"title": "Bad Song", "artist": "Artist", "youtube_video_id": "tooshort", "genre_name": "Rock"},
        ],
    )

    df = pl.read_parquet(result)
    assert df.to_dicts() == [
        {
            "title": "Good Song",
            "artist": "Artist",
            "youtube_video_id": "abc123abc12",
            "genre_name": "Rock",
            "wikidata_genre_name": "rock",
            "match_method": "exact",
        }
    ]


def test_genre_match_raises_when_all_youtube_video_ids_malformed(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="is empty"):
        _run_genre_match(
            tmp_path,
            [{"title": "Bad Song", "artist": "Artist", "youtube_video_id": "tooshort", "genre_name": "Rock"}],
        )


def test_genre_match_raises_on_non_genre_conflicting_with_alias_csv(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="conflict"):
        _run_genre_match(
            tmp_path,
            [_song("Rock")],
            alias_rows=[{"musicbrainz_genre_name": "d&b", "wikidata_genre_name": "drum and bass", "reason": "test"}],
            non_genre_rows=[{"musicbrainz_genre_name": "d&b", "reason": "mistake"}],
        )
