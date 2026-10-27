import json
from pathlib import Path

import polars as pl
import pytest

from gold.genre_match import genre_match

HIERARCHY_ROWS = [
    {"item_id": "Q1", "item_label": "rock", "parent_id": None, "is_umbrella": True},
    {"item_id": "Q2", "item_label": "jazz", "parent_id": None, "is_umbrella": False},
    {"item_id": "Q3", "item_label": "drum and bass", "parent_id": None, "is_umbrella": False},
    {"item_id": "Q4", "item_label": "punk rock", "parent_id": "Q1", "is_umbrella": False},
    {"item_id": "Q5", "item_label": "pop", "parent_id": None, "is_umbrella": False},
    {"item_id": "Q6", "item_label": "pop rock", "parent_id": "Q1", "is_umbrella": False},
]


def _mbid(index: int) -> str:
    return f"00000000-0000-0000-0000-{index:012d}"


def _write_songs(tmp_path: Path, rows: list[dict]) -> tuple[Path, Path]:
    path = tmp_path / "5_songs.parquet"
    rows = [
        {
            "musicbrainz_recording_id": _mbid(i),
            "artist_musicbrainz_id": _mbid(100 + i),
            "youtube_unplayable_reason": None,
            "youtube_view_count": None,
            "first_release_year": None,
            **row,
        }
        for i, row in enumerate(rows)
    ]
    pl.DataFrame(
        [{k: v for k, v in row.items() if k not in ("genre_name", "genres")} for row in rows],
        schema_overrides={
            "youtube_unplayable_reason": pl.Utf8,
            "youtube_view_count": pl.Int64,
            "first_release_year": pl.Int16,
        },
    ).write_parquet(path)
    genre_path = tmp_path / "10_song_genre.parquet"
    tags = [
        {"musicbrainz_recording_id": row["musicbrainz_recording_id"], "genre_name": name, "weight": weight}
        for row in rows
        for name, weight in (row.get("genres") or ([(row["genre_name"], 1)] if row.get("genre_name") else []))
    ]
    pl.DataFrame(
        tags, schema={"musicbrainz_recording_id": pl.Utf8, "genre_name": pl.Utf8, "weight": pl.Int64}
    ).write_parquet(genre_path)
    return path, genre_path


def _write_hierarchy(tmp_path: Path, rows: list[dict] | None = None) -> Path:
    path = tmp_path / "7_canonical_hierarchy.parquet"
    pl.DataFrame(
        rows if rows is not None else HIERARCHY_ROWS,
        schema={"item_id": pl.Utf8, "item_label": pl.Utf8, "parent_id": pl.Utf8, "is_umbrella": pl.Boolean},
    ).write_parquet(path)
    return path


def _write_secondary_parents(tmp_path: Path, rows: list[dict] | None = None) -> Path:
    path = tmp_path / "5_secondary_parents.parquet"
    pl.DataFrame(rows or [], schema={"item_id": pl.Utf8, "parent_id": pl.Utf8}).write_parquet(path)
    return path


def _write_precedence_csv(tmp_path: Path, rows: list[dict] | None = None) -> Path:
    path = tmp_path / "manual_genre_precedence.csv"
    pl.DataFrame(rows or [], schema={"item_id": pl.Utf8, "over_item_id": pl.Utf8, "reason": pl.Utf8}).write_csv(path)
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
    precedence_rows: list[dict] | None = None,
    secondary_rows: list[dict] | None = None,
    output_dir: Path | None = None,
) -> Path:
    song_path, song_genre_path = _write_songs(tmp_path, song_rows)
    return genre_match(
        song_path,
        song_genre_path,
        _write_hierarchy(tmp_path, hierarchy_rows),
        _write_secondary_parents(tmp_path, secondary_rows),
        _write_alias_csv(tmp_path, alias_rows),
        _write_non_genre_csv(tmp_path, non_genre_rows),
        _write_precedence_csv(tmp_path, precedence_rows),
        output_dir or (tmp_path / "gold"),
    )


def _song(genre_name: str, title: str = "Song") -> dict:
    return {"title": title, "artist": "Artist", "youtube_video_id": "abc123abc12", "genre_name": genre_name}


def test_genre_match_exact_case_insensitive_match(tmp_path: Path) -> None:
    result = _run_genre_match(tmp_path, [_song("Rock")])

    df = pl.read_parquet(result)
    row = df.to_dicts()[0]
    assert row["musicbrainz_recording_id"] == _mbid(0)
    assert row["artist_musicbrainz_id"] == _mbid(100)
    assert row["wikidata_genre_name"] == "rock"
    assert row["match_method"] == "exact"


def test_genre_match_passes_youtube_view_count_through(tmp_path: Path) -> None:
    result = _run_genre_match(tmp_path, [{**_song("Rock"), "youtube_view_count": 42}])

    assert pl.read_parquet(result).to_dicts()[0]["youtube_view_count"] == 42


def test_genre_match_passes_youtube_unplayable_reason_through(tmp_path: Path) -> None:
    result = _run_genre_match(tmp_path, [{**_song("Rock"), "youtube_unplayable_reason": "not_embeddable"}])

    assert pl.read_parquet(result).to_dicts()[0]["youtube_unplayable_reason"] == "not_embeddable"


@pytest.mark.parametrize(
    ("tag", "label"),
    [("Jazz Music", "jazz"), ("rock", "rock music"), ("Hip Hop", "hip-hop"), ("rhythm_and  blues", "rhythm and blues")],
)
def test_genre_match_normalized_key_matches_either_side(tmp_path: Path, tag: str, label: str) -> None:
    hierarchy = [
        {"item_id": "Q1", "item_label": "jazz", "parent_id": None, "is_umbrella": False},
        {"item_id": "Q2", "item_label": "rock music", "parent_id": None, "is_umbrella": False},
        {"item_id": "Q3", "item_label": "hip-hop", "parent_id": None, "is_umbrella": False},
        {"item_id": "Q4", "item_label": "rhythm and blues", "parent_id": None, "is_umbrella": False},
    ]
    result = _run_genre_match(tmp_path, [_song(tag)], hierarchy_rows=hierarchy)

    row = pl.read_parquet(result).to_dicts()[0]
    assert row["wikidata_genre_name"] == label
    assert row["match_method"] == "normalized"


def test_genre_match_ambiguous_genre_key_matches_by_exact_name_only(tmp_path: Path) -> None:
    hierarchy = [
        {"item_id": "Q1", "item_label": "dance", "parent_id": None, "is_umbrella": False},
        {"item_id": "Q2", "item_label": "dance music", "parent_id": None, "is_umbrella": False},
    ]
    result = _run_genre_match(
        tmp_path, [_song("Dance Music", title="A"), _song("dance-music", title="B")], hierarchy_rows=hierarchy
    )

    rows = {row["title"]: row for row in pl.read_parquet(result).to_dicts()}
    assert (rows["A"]["wikidata_genre_name"], rows["A"]["match_method"]) == ("dance music", "exact")
    assert (rows["B"]["wikidata_genre_name"], rows["B"]["match_method"]) == (None, "unmatched")


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

    row = pl.read_parquet(result).to_dicts()[0]
    assert row["wikidata_genre_name"] is None
    assert row["match_method"] == "unmatched"

    unresolved = pl.read_csv(output_dir / "1_genre_match_unresolved.csv")
    assert unresolved.to_dicts() == [{"genre_name": "some random tag", "song_count": 1, "weight_sum": 1}]


def test_genre_match_writes_empty_unresolved_report_when_all_matched(tmp_path: Path) -> None:
    output_dir = tmp_path / "gold"
    _run_genre_match(tmp_path, [_song("Rock")], output_dir=output_dir)

    unresolved = pl.read_csv(output_dir / "1_genre_match_unresolved.csv")
    assert unresolved.is_empty()
    assert json.loads((output_dir / "1_genre_match_report.json").read_text()) == {"unresolvedGenreTagCount": 0}


def test_genre_match_report_counts_distinct_unresolved_genre_names(tmp_path: Path) -> None:
    output_dir = tmp_path / "gold"
    songs = [
        _song("some random tag", title="A"),
        _song("some random tag", title="B"),
        _song("other tag"),
        _song("Rock"),
    ]
    _run_genre_match(tmp_path, songs, output_dir=output_dir)

    assert json.loads((output_dir / "1_genre_match_report.json").read_text()) == {"unresolvedGenreTagCount": 2}


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


def test_genre_match_raises_on_alias_keys_differing_only_by_separator(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="duplicate"):
        _run_genre_match(
            tmp_path,
            [_song("Rock")],
            alias_rows=[
                {"musicbrainz_genre_name": "d-n-b", "wikidata_genre_name": "drum and bass", "reason": "abbreviation"},
                {"musicbrainz_genre_name": "d n b", "wikidata_genre_name": "drum and bass", "reason": "duplicate"},
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


def test_genre_match_raises_on_alias_that_normalizes_to_a_label(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="dead-weight"):
        _run_genre_match(
            tmp_path,
            [_song("Rock")],
            alias_rows=[
                {"musicbrainz_genre_name": "Pop-Rock", "wikidata_genre_name": "pop rock", "reason": "hyphenated"}
            ],
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


def test_genre_match_keeps_null_genre_name_as_no_genre(tmp_path: Path) -> None:
    result = _run_genre_match(
        tmp_path,
        [_song("Rock"), {"title": "Song", "artist": "Artist", "youtube_video_id": None, "genre_name": None}],
    )

    assert pl.read_parquet(result).select("youtube_video_id", "wikidata_genre_name", "match_method").to_dicts() == [
        {"youtube_video_id": "abc123abc12", "wikidata_genre_name": "rock", "match_method": "exact"},
        {"youtube_video_id": None, "wikidata_genre_name": None, "match_method": "no_genre"},
    ]
    assert json.loads((tmp_path / "gold" / "1_genre_match_report.json").read_text()) == {"unresolvedGenreTagCount": 0}


def test_genre_match_raises_on_empty_hierarchy(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="is empty"):
        _run_genre_match(tmp_path, [_song("Rock")], hierarchy_rows=[])


def test_genre_match_clears_malformed_youtube_video_id(tmp_path: Path) -> None:
    result = _run_genre_match(
        tmp_path,
        [
            _song("Rock", title="Good Song"),
            {
                "title": "Bad Song",
                "artist": "Artist",
                "youtube_video_id": "tooshort",
                "youtube_unplayable_reason": "not_found",
                "youtube_view_count": 7,
                "genre_name": "Rock",
            },
        ],
    )

    df = pl.read_parquet(result).sort("title", descending=True)
    assert df.select(
        "title", "youtube_video_id", "youtube_unplayable_reason", "youtube_view_count", "match_method"
    ).to_dicts() == [
        {
            "title": "Good Song",
            "youtube_video_id": "abc123abc12",
            "youtube_unplayable_reason": None,
            "youtube_view_count": None,
            "match_method": "exact",
        },
        {
            "title": "Bad Song",
            "youtube_video_id": None,
            "youtube_unplayable_reason": None,
            "youtube_view_count": None,
            "match_method": "exact",
        },
    ]


def test_genre_match_raises_on_non_genre_conflicting_with_alias_csv(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="conflict"):
        _run_genre_match(
            tmp_path,
            [_song("Rock")],
            alias_rows=[{"musicbrainz_genre_name": "d&b", "wikidata_genre_name": "drum and bass", "reason": "test"}],
            non_genre_rows=[{"musicbrainz_genre_name": "d&b", "reason": "mistake"}],
        )


def _by_title(result: Path) -> dict[str, dict]:
    return {row["title"]: row for row in pl.read_parquet(result).to_dicts()}


def test_genre_match_descendant_beats_ancestor_and_inherits_weight(tmp_path: Path) -> None:
    result = _run_genre_match(tmp_path, [{**_song("x", title="S"), "genres": [("rock", 5), ("punk rock", 1)]}])

    row = pl.read_parquet(result).to_dicts()[0]
    assert (row["wikidata_genre_item_id"], row["wikidata_genre_name"], row["is_umbrella"]) == ("Q4", "punk rock", False)


def test_genre_match_secondary_parent_counts_as_ancestor(tmp_path: Path) -> None:
    result = _run_genre_match(
        tmp_path,
        [{**_song("x"), "genres": [("pop", 9), ("pop rock", 1)]}],
        secondary_rows=[{"item_id": "Q6", "parent_id": "Q5"}],
    )

    assert pl.read_parquet(result).to_dicts()[0]["wikidata_genre_item_id"] == "Q6"


def test_genre_match_keeps_umbrella_when_it_is_the_best_genre(tmp_path: Path) -> None:
    result = _run_genre_match(tmp_path, [_song("Rock")])

    row = pl.read_parquet(result).to_dicts()[0]
    assert (row["wikidata_genre_item_id"], row["is_umbrella"]) == ("Q1", True)


def test_genre_match_manual_precedence_applies(tmp_path: Path) -> None:
    result = _run_genre_match(
        tmp_path,
        [{**_song("x"), "genres": [("jazz", 9), ("drum and bass", 1)]}],
        precedence_rows=[{"item_id": "Q3", "over_item_id": "Q2", "reason": "test"}],
    )

    assert pl.read_parquet(result).to_dicts()[0]["wikidata_genre_item_id"] == "Q3"


def test_genre_match_raises_on_precedence_reversing_lineage(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="cycle"):
        _run_genre_match(
            tmp_path,
            [_song("Rock")],
            precedence_rows=[{"item_id": "Q1", "over_item_id": "Q4", "reason": "test"}],
        )


def test_genre_match_raises_on_unknown_precedence_item(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="not found in the canonical hierarchy"):
        _run_genre_match(
            tmp_path,
            [_song("Rock")],
            precedence_rows=[{"item_id": "Q999", "over_item_id": "Q1", "reason": "test"}],
        )


def test_genre_match_unresolved_tag_does_not_beat_resolved_one(tmp_path: Path) -> None:
    result = _run_genre_match(tmp_path, [{**_song("x"), "genres": [("random tag", 9), ("jazz", 1)]}])

    row = pl.read_parquet(result).to_dicts()[0]
    assert (row["wikidata_genre_name"], row["match_method"]) == ("jazz", "exact")


def test_genre_match_ties_go_to_smallest_item_id(tmp_path: Path) -> None:
    result = _run_genre_match(tmp_path, [{**_song("x"), "genres": [("jazz", 2), ("drum and bass", 2)]}])

    assert pl.read_parquet(result).to_dicts()[0]["wikidata_genre_item_id"] == "Q2"


def test_genre_match_alias_and_non_genre_apply_across_spellings(tmp_path: Path) -> None:
    result = _run_genre_match(
        tmp_path,
        [_song("D-N-B", title="A"), _song("A S M R", title="B")],
        alias_rows=[{"musicbrainz_genre_name": "d n b", "wikidata_genre_name": "drum and bass", "reason": "abbr"}],
        non_genre_rows=[{"musicbrainz_genre_name": "a-s-m-r", "reason": "not a genre"}],
    )

    rows = {row["title"]: row for row in pl.read_parquet(result).to_dicts()}
    assert (rows["A"]["wikidata_genre_name"], rows["A"]["match_method"]) == ("drum and bass", "manual_alias")
    assert (rows["B"]["wikidata_genre_name"], rows["B"]["match_method"]) == (None, "accepted_non_genre")


def test_genre_match_raises_on_non_genre_that_normalizes_to_a_label(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="conflict"):
        _run_genre_match(
            tmp_path,
            [_song("Rock")],
            non_genre_rows=[{"musicbrainz_genre_name": "Jazz Music", "reason": "mistake"}],
        )


def test_genre_match_warns_on_ambiguous_canonical_genre_key(tmp_path: Path, caplog: pytest.LogCaptureFixture) -> None:
    hierarchy = [
        {"item_id": "Q1", "item_label": "dance", "parent_id": None, "is_umbrella": False},
        {"item_id": "Q2", "item_label": "dance music", "parent_id": None, "is_umbrella": False},
    ]
    with caplog.at_level("WARNING"):
        _run_genre_match(tmp_path, [_song("dance")], hierarchy_rows=hierarchy)

    assert "sharing a genre key" in caplog.text
