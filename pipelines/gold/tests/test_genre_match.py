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


_TAG_KEYS = ("genre_name", "genres", "release_group_genres", "artist_genres")


def _write_songs(tmp_path: Path, rows: list[dict]) -> tuple[Path, Path, Path]:
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
        [{k: v for k, v in row.items() if k not in _TAG_KEYS} for row in rows],
        schema_overrides={
            "youtube_unplayable_reason": pl.Utf8,
            "youtube_view_count": pl.Int64,
            "first_release_year": pl.Int16,
        },
    ).write_parquet(path)
    genre_path = tmp_path / "10_song_genre.parquet"
    tags = [
        {
            "musicbrainz_recording_id": row["musicbrainz_recording_id"],
            "level": level,
            "genre_name": name,
            "weight": weight,
        }
        for row in rows
        for level, level_tags in (
            ("recording", row.get("genres") or ([(row["genre_name"], 1)] if row.get("genre_name") else [])),
            ("release_group", row.get("release_group_genres") or []),
        )
        for name, weight in level_tags
    ]
    pl.DataFrame(
        tags,
        schema={"musicbrainz_recording_id": pl.Utf8, "level": pl.Utf8, "genre_name": pl.Utf8, "weight": pl.Int64},
    ).write_parquet(genre_path)
    artist_genre_path = tmp_path / "13_artist_genre.parquet"
    artist_tags = {
        (row["artist_musicbrainz_id"], name): weight for row in rows for name, weight in row.get("artist_genres") or []
    }
    pl.DataFrame(
        [(artist, name, weight) for (artist, name), weight in artist_tags.items()],
        schema={"artist_musicbrainz_id": pl.Utf8, "genre_name": pl.Utf8, "weight": pl.Int64},
        orient="row",
    ).write_parquet(artist_genre_path)
    return path, genre_path, artist_genre_path


def _write_hierarchy(tmp_path: Path, rows: list[dict] | None = None) -> Path:
    path = tmp_path / "7_canonical_hierarchy.parquet"
    pl.DataFrame(
        rows if rows is not None else HIERARCHY_ROWS,
        schema={"item_id": pl.Utf8, "item_label": pl.Utf8, "parent_id": pl.Utf8, "is_umbrella": pl.Boolean},
    ).write_parquet(path)
    return path


_OVERVIEW_ID = "Q999000"


def _write_regional(tmp_path: Path, rows: list[dict] | None, overview_ids: set[str]) -> tuple[Path, Path]:
    path = tmp_path / "8_regional_hierarchy.parquet"
    if rows is None:
        rows = [{"item_id": _OVERVIEW_ID, "item_label": "music of testland", "parent_id": None}]
        overview_ids = overview_ids | {_OVERVIEW_ID}
    pl.DataFrame(rows, schema={"item_id": pl.Utf8, "item_label": pl.Utf8, "parent_id": pl.Utf8}).write_parquet(path)
    classification_path = tmp_path / "4_regional_classification.parquet"
    pl.DataFrame(
        [(row["item_id"], row["item_id"] in overview_ids) for row in rows],
        schema={"item_id": pl.Utf8, "is_regional_overview": pl.Boolean},
        orient="row",
    ).write_parquet(classification_path)
    return path, classification_path


PARENT_SELECTION_SCHEMA = {
    "item_id": pl.Utf8,
    "parent_id": pl.Utf8,
    "canonical_parent_id": pl.Utf8,
    "regional_genre_parent_id": pl.Utf8,
    "regional_overview_ids": pl.List(pl.Utf8),
}


def _write_parent_selection(tmp_path: Path, rows: list[dict] | None = None) -> Path:
    path = tmp_path / "5_parent_selection.parquet"
    pl.DataFrame(rows or [], schema=PARENT_SELECTION_SCHEMA).write_parquet(path)
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


def _write_wikidata_aliases(tmp_path: Path, rows: list[tuple[str, str]] | None = None) -> Path:
    path = tmp_path / "wikidata_genre_aliases.parquet"
    pl.DataFrame(rows or [], schema={"item_id": pl.Utf8, "alias": pl.Utf8}, orient="row").write_parquet(path)
    return path


def _write_disambiguation_csvs(
    tmp_path: Path, rows: list[tuple[str, str, bool]] | None, context_rows: list[tuple[str, str, str]] | None
) -> tuple[Path, Path]:
    path = tmp_path / "manual_genre_disambiguation.csv"
    pl.DataFrame(
        [(name, item_id, "true" if is_default else "", "x") for name, item_id, is_default in rows or []],
        schema={"musicbrainz_genre_name": pl.Utf8, "item_id": pl.Utf8, "is_default": pl.Utf8, "reason": pl.Utf8},
        orient="row",
    ).write_csv(path)
    context_path = tmp_path / "manual_genre_disambiguation_context.csv"
    pl.DataFrame(
        [(*row, "x") for row in context_rows or []],
        schema={
            "musicbrainz_genre_name": pl.Utf8,
            "item_id": pl.Utf8,
            "context_item_id": pl.Utf8,
            "reason": pl.Utf8,
        },
        orient="row",
    ).write_csv(context_path)
    return path, context_path


def _run_genre_match(
    tmp_path: Path,
    song_rows: list[dict],
    hierarchy_rows: list[dict] | None = None,
    alias_rows: list[dict] | None = None,
    non_genre_rows: list[dict] | None = None,
    precedence_rows: list[dict] | None = None,
    secondary_rows: list[dict] | None = None,
    selection_rows: list[dict] | None = None,
    wikidata_alias_rows: list[tuple[str, str]] | None = None,
    output_dir: Path | None = None,
    disambiguation_rows: list[tuple[str, str, bool]] | None = None,
    context_rows: list[tuple[str, str, str]] | None = None,
    regional_rows: list[dict] | None = None,
    overview_ids: set[str] | None = None,
) -> Path:
    song_path, song_genre_path, artist_genre_path = _write_songs(tmp_path, song_rows)
    return genre_match(
        song_path,
        song_genre_path,
        artist_genre_path,
        _write_hierarchy(tmp_path, hierarchy_rows),
        *_write_regional(tmp_path, regional_rows, overview_ids or set()),
        _write_parent_selection(tmp_path, selection_rows),
        _write_secondary_parents(tmp_path, secondary_rows),
        _write_alias_csv(tmp_path, alias_rows),
        _write_non_genre_csv(tmp_path, non_genre_rows),
        _write_precedence_csv(tmp_path, precedence_rows),
        *_write_disambiguation_csvs(tmp_path, disambiguation_rows, context_rows),
        _write_wikidata_aliases(tmp_path, wikidata_alias_rows),
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
    assert unresolved.to_dicts() == [
        {"genre_name": "some random tag", "level": "recording", "subject_count": 1, "weight_sum": 1}
    ]


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
    with pytest.raises(ValueError, match="not found in the canonical or regional hierarchy"):
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
    with pytest.raises(ValueError, match="not found in either genre tree"):
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


def _match(result: Path) -> tuple[str | None, str]:
    row = pl.read_parquet(result).to_dicts()[0]
    return row["wikidata_genre_name"], row["match_method"]


def test_genre_match_resolves_wikidata_alias(tmp_path: Path) -> None:
    result = _run_genre_match(tmp_path, [_song("D&B")], wikidata_alias_rows=[("Q3", "d&b")])

    assert _match(result) == ("drum and bass", "wikidata_alias")


def test_genre_match_ignores_wikidata_alias_colliding_with_a_label(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    result = _run_genre_match(tmp_path, [_song("Pop Music")], wikidata_alias_rows=[("Q3", "pop")])

    assert _match(result) == ("pop", "normalized")
    assert "pop" in caplog.text


def test_genre_match_ignores_wikidata_alias_colliding_with_an_ambiguous_label(tmp_path: Path) -> None:
    hierarchy = [*HIERARCHY_ROWS, {"item_id": "Q7", "item_label": "Ska", "parent_id": None, "is_umbrella": False}]
    hierarchy.append({"item_id": "Q8", "item_label": "ska", "parent_id": None, "is_umbrella": False})

    result = _run_genre_match(
        tmp_path, [_song("Ska Music")], hierarchy_rows=hierarchy, wikidata_alias_rows=[("Q3", "ska-music")]
    )

    assert _match(result) == (None, "unmatched")


def test_genre_match_leaves_ambiguous_wikidata_alias_without_context_unresolved(tmp_path: Path) -> None:
    output_dir = tmp_path / "gold"
    result = _run_genre_match(
        tmp_path,
        [_song("fusion")],
        wikidata_alias_rows=[("Q2", "fusion"), ("Q3", "Fusion")],
        output_dir=output_dir,
    )

    assert _match(result) == (None, "ambiguous")
    assert pl.read_csv(output_dir / "1_genre_match_unresolved.csv")["genre_name"].to_list() == ["fusion"]
    assert json.loads((output_dir / "1_genre_match_report.json").read_text()) == {"unresolvedGenreTagCount": 1}


def test_genre_match_wikidata_alias_on_one_lineage_resolves_to_the_deepest(tmp_path: Path) -> None:
    result = _run_genre_match(tmp_path, [_song("punk")], wikidata_alias_rows=[("Q1", "punk"), ("Q4", "punk")])

    assert _match(result) == ("punk rock", "wikidata_alias")


def test_genre_match_disambiguates_wikidata_alias_by_context(tmp_path: Path) -> None:
    hierarchy = [
        *HIERARCHY_ROWS,
        {"item_id": "Q7", "item_label": "jazz fusion", "parent_id": "Q2", "is_umbrella": False},
    ]

    result = _run_genre_match(
        tmp_path,
        [{**_song("x"), "genres": [("fusion", 5), ("jazz", 1)]}],
        hierarchy_rows=hierarchy,
        wikidata_alias_rows=[("Q7", "fusion"), ("Q3", "fusion")],
    )

    row = pl.read_parquet(result).to_dicts()[0]
    assert (row["wikidata_genre_name"], row["match_method"], row["secondary_genre_item_ids"]) == (
        "jazz fusion",
        "disambiguated",
        [],
    )


def test_genre_match_manual_alias_overrides_wikidata_alias(tmp_path: Path) -> None:
    result = _run_genre_match(
        tmp_path,
        [_song("dnb")],
        alias_rows=[{"musicbrainz_genre_name": "dnb", "wikidata_genre_name": "jazz", "reason": "x"}],
        wikidata_alias_rows=[("Q3", "dnb")],
    )

    assert _match(result) == ("jazz", "manual_alias")


def test_genre_match_accepted_non_genre_blocks_wikidata_alias(tmp_path: Path) -> None:
    result = _run_genre_match(
        tmp_path,
        [_song("dnb")],
        non_genre_rows=[{"musicbrainz_genre_name": "dnb", "reason": "x"}],
        wikidata_alias_rows=[("Q3", "dnb")],
    )

    assert _match(result) == (None, "accepted_non_genre")


def test_genre_match_ignores_wikidata_alias_of_non_canonical_item(tmp_path: Path) -> None:
    result = _run_genre_match(tmp_path, [_song("dnb")], wikidata_alias_rows=[("Q99", "dnb")])

    assert _match(result) == (None, "unmatched")


@pytest.mark.parametrize(
    ("genres", "expected_genre", "expected_secondaries"),
    [
        ([("punk rock", 5), ("rock", 3), ("jazz", 1)], "punk rock", ["Q2"]),
        ([("punk rock", 5), ("hardcore punk", 1), ("jazz", 2)], "hardcore punk", ["Q2"]),
        ([("jazz", 1), ("pop", 3), ("drum and bass", 2)], "pop", ["Q3", "Q2"]),
        ([("jazz", 2), ("random tag", 9)], "jazz", []),
    ],
)
def test_genre_match_keeps_secondary_genres_not_beaten_by_another(
    tmp_path: Path, genres: list[tuple[str, int]], expected_genre: str, expected_secondaries: list[str]
) -> None:
    hierarchy = [
        *HIERARCHY_ROWS,
        {"item_id": "Q7", "item_label": "hardcore punk", "parent_id": "Q4", "is_umbrella": False},
    ]

    result = _run_genre_match(tmp_path, [{**_song("x"), "genres": genres}], hierarchy_rows=hierarchy)

    row = pl.read_parquet(result).to_dicts()[0]
    assert (row["wikidata_genre_name"], row["secondary_genre_item_ids"]) == (expected_genre, expected_secondaries)


def test_genre_match_keeps_every_musicbrainz_genre_ordered_by_weight(tmp_path: Path) -> None:
    result = _run_genre_match(tmp_path, [{**_song("x"), "genres": [("jazz", 2), ("random tag", 9), ("a tag", 2)]}])

    assert pl.read_parquet(result).to_dicts()[0]["musicbrainz_genres"] == [
        {"name": "random tag", "weight": 9},
        {"name": "a tag", "weight": 2},
        {"name": "jazz", "weight": 2},
    ]


def test_genre_match_gives_empty_lists_to_a_song_without_tags(tmp_path: Path) -> None:
    result = _run_genre_match(tmp_path, [{**_song("x"), "genre_name": None}])

    row = pl.read_parquet(result).to_dicts()[0]
    assert (row["secondary_genre_item_ids"], row["musicbrainz_genres"]) == ([], [])


R_AND_B_HIERARCHY = [
    {"item_id": "Q10", "item_label": "pop music", "parent_id": None, "is_umbrella": False},
    {"item_id": "Q11", "item_label": "rhythm and blues", "parent_id": None, "is_umbrella": False},
    {"item_id": "Q12", "item_label": "contemporary R&B", "parent_id": "Q10", "is_umbrella": False},
    {"item_id": "Q13", "item_label": "soul music", "parent_id": "Q11", "is_umbrella": False},
    {"item_id": "Q14", "item_label": "quiet storm", "parent_id": "Q12", "is_umbrella": False},
    {"item_id": "Q15", "item_label": "hip-hop", "parent_id": None, "is_umbrella": False},
    {"item_id": "Q16", "item_label": "trap music", "parent_id": "Q15", "is_umbrella": False},
    {"item_id": "Q17", "item_label": "jazz", "parent_id": None, "is_umbrella": False},
]
R_AND_B = [("r&b", "Q11", True), ("R&B", "Q12", False)]
HIP_HOP_HINT = [("r&b", "Q12", "Q15")]


def _disambiguated(genres: list[tuple[str, int]], **kwargs: object) -> dict:
    tmp_path = kwargs.pop("tmp_path")
    assert isinstance(tmp_path, Path)
    result = _run_genre_match(
        tmp_path,
        [{**_song("x"), "genres": genres}],
        hierarchy_rows=kwargs.pop("hierarchy_rows", R_AND_B_HIERARCHY),
        disambiguation_rows=kwargs.pop("disambiguation_rows", R_AND_B),
        context_rows=kwargs.pop("context_rows", HIP_HOP_HINT),
        **kwargs,
    )
    return pl.read_parquet(result).to_dicts()[0]


@pytest.mark.parametrize(
    ("genres", "expected_genre", "expected_method"),
    [
        ([("r&b", 5), ("pop", 1)], "contemporary R&B", "disambiguated"),
        ([("r&b", 5), ("hip hop", 1)], "contemporary R&B", "disambiguated"),
        ([("r&b", 5), ("trap", 1)], "contemporary R&B", "disambiguated"),
        ([("r&b", 5)], "rhythm and blues", "disambiguated"),
        ([("r&b", 5), ("jazz", 1)], "rhythm and blues", "disambiguated"),
        ([("r&b", 5), ("soul", 1)], "soul music", "normalized"),
        ([("r&b", 5), ("quiet storm", 1)], "quiet storm", "exact"),
        ([("r&b", 5), ("rhythm and blues", 1)], "rhythm and blues", "exact"),
    ],
)
def test_genre_match_disambiguates_curated_tag_by_the_song_s_other_genres(
    tmp_path: Path, genres: list[tuple[str, int]], expected_genre: str, expected_method: str
) -> None:
    row = _disambiguated(genres, tmp_path=tmp_path)

    assert (row["wikidata_genre_name"], row["match_method"]) == (expected_genre, expected_method)


def test_genre_match_ignored_ambiguous_tag_adds_no_weight(tmp_path: Path) -> None:
    row = _disambiguated([("r&b", 5), ("soul", 1), ("jazz", 3)], tmp_path=tmp_path)

    assert (row["wikidata_genre_name"], row["secondary_genre_item_ids"]) == ("jazz", ["Q13"])


def test_genre_match_disambiguation_falls_back_to_default_when_fits_diverge(tmp_path: Path) -> None:
    hierarchy = [*R_AND_B_HIERARCHY, {"item_id": "Q18", "item_label": "blues", "parent_id": None, "is_umbrella": False}]
    row = _disambiguated(
        [("r&b", 5), ("hip hop", 1), ("blues", 1)],
        tmp_path=tmp_path,
        hierarchy_rows=hierarchy,
        context_rows=[*HIP_HOP_HINT, ("r&b", "Q11", "Q18")],
    )

    assert (row["wikidata_genre_name"], row["match_method"]) == ("rhythm and blues", "disambiguated")


def test_genre_match_disambiguation_keeps_the_most_precise_fitting_candidate(tmp_path: Path) -> None:
    row = _disambiguated(
        [("r&b", 5), ("hip hop", 1)],
        tmp_path=tmp_path,
        disambiguation_rows=[("r&b", "Q10", True), ("r&b", "Q12", False)],
        context_rows=[("r&b", "Q10", "Q15"), *HIP_HOP_HINT],
    )

    assert (row["wikidata_genre_name"], row["match_method"]) == ("contemporary R&B", "disambiguated")


def test_genre_match_curated_disambiguation_overrides_wikidata_candidates(tmp_path: Path) -> None:
    row = _disambiguated(
        [("r&b", 5)], tmp_path=tmp_path, wikidata_alias_rows=[("Q12", "r&b"), ("Q17", "r&b"), ("Q11", "r&b")]
    )

    assert (row["wikidata_genre_name"], row["match_method"]) == ("rhythm and blues", "disambiguated")


def test_genre_match_curated_disambiguation_without_default_leaves_tag_ambiguous(tmp_path: Path) -> None:
    row = _disambiguated(
        [("r&b", 5)], tmp_path=tmp_path, disambiguation_rows=[("r&b", "Q11", False), ("r&b", "Q12", False)]
    )

    assert (row["wikidata_genre_name"], row["match_method"]) == (None, "ambiguous")


@pytest.mark.parametrize(
    ("disambiguation_rows", "context_rows", "message"),
    [
        ([("r&b", "Q11", True), ("r&b", "Q99", False)], [], "no genre target"),
        ([("r&b", "Q11", True)], [], "fewer than 2 candidates"),
        ([("r&b", "Q11", True), ("r&b", "Q12", True)], [], "several defaults"),
        ([("jazz", "Q11", True), ("jazz", "Q13", False)], [], "already resolved"),
        ([("d&b", "Q11", True), ("d&b", "Q12", False)], [], "already resolved"),
        ([("asmr", "Q11", True), ("asmr", "Q12", False)], [], "already resolved"),
        (R_AND_B, [("r&b", "Q13", "Q15")], "not in manual_genre_disambiguation.csv"),
        (R_AND_B, [("r&b", "Q12", "Q99")], "context_item_id"),
        ([("r n b", "Q11", True), ("R-n-B", "Q11", False), ("r n b", "Q12", False)], [], "twice"),
    ],
)
def test_genre_match_raises_on_invalid_disambiguation(
    tmp_path: Path,
    disambiguation_rows: list[tuple[str, str, bool]],
    context_rows: list[tuple[str, str, str]],
    message: str,
) -> None:
    with pytest.raises(ValueError, match=message):
        _disambiguated(
            [("r&b", 1)],
            tmp_path=tmp_path,
            disambiguation_rows=disambiguation_rows,
            context_rows=context_rows,
            alias_rows=[{"musicbrainz_genre_name": "d&b", "wikidata_genre_name": "jazz", "reason": "x"}],
            non_genre_rows=[{"musicbrainz_genre_name": "asmr", "reason": "x"}],
        )


TRAP_HIERARCHY = [
    *R_AND_B_HIERARCHY,
    {"item_id": "Q18", "item_label": "electronic dance music", "parent_id": None, "is_umbrella": False},
    {"item_id": "Q19", "item_label": "EDM trap music", "parent_id": "Q18", "is_umbrella": False},
]
TRAP = [("trap", "Q16", True), ("trap", "Q19", False)]
EDM_HINT = [("trap", "Q19", "Q18")]


@pytest.mark.parametrize(
    ("genres", "expected_genre", "expected_method"),
    [
        ([("trap", 5)], "trap music", "disambiguated"),
        ([("trap", 5), ("hip hop", 1)], "trap music", "disambiguated"),
        ([("trap", 5), ("electronic dance music", 1)], "EDM trap music", "disambiguated"),
        ([("trap music", 5), ("electronic dance music", 1)], "trap music", "exact"),
    ],
)
def test_genre_match_curated_disambiguation_outranks_a_normalized_label_match(
    tmp_path: Path, genres: list[tuple[str, int]], expected_genre: str, expected_method: str
) -> None:
    row = _disambiguated(
        genres, tmp_path=tmp_path, hierarchy_rows=TRAP_HIERARCHY, disambiguation_rows=TRAP, context_rows=EDM_HINT
    )

    assert (row["wikidata_genre_name"], row["match_method"]) == (expected_genre, expected_method)


def _source(row: dict) -> tuple[str | None, str | None, str]:
    return row["wikidata_genre_name"], row["genre_source"], row["match_method"]


def test_genre_match_recording_genre_wins_over_heavier_inherited_genres(tmp_path: Path) -> None:
    song = {**_song("jazz"), "release_group_genres": [("rock", 50)], "artist_genres": [("pop", 90)]}

    row = pl.read_parquet(_run_genre_match(tmp_path, [song])).to_dicts()[0]

    assert _source(row) == ("jazz", "recording", "exact")
    assert row["musicbrainz_genres"] == [{"name": "jazz", "weight": 1}]


def test_genre_match_never_blends_levels(tmp_path: Path) -> None:
    song = {**_song("x"), "genres": [("jazz", 2)], "release_group_genres": [("pop", 1)], "artist_genres": [("rock", 1)]}

    row = pl.read_parquet(_run_genre_match(tmp_path, [song])).to_dicts()[0]

    assert (row["wikidata_genre_name"], row["secondary_genre_item_ids"]) == ("jazz", [])


def test_genre_match_falls_back_to_release_group_when_recording_tags_are_unmatched(tmp_path: Path) -> None:
    song = {**_song("some random tag"), "release_group_genres": [("Pop", 3)], "artist_genres": [("jazz", 9)]}

    row = pl.read_parquet(_run_genre_match(tmp_path, [song])).to_dicts()[0]

    assert _source(row) == ("pop", "release_group", "exact")
    assert row["musicbrainz_genres"] == [{"name": "Pop", "weight": 3}]


def test_genre_match_falls_back_to_artist_genre(tmp_path: Path) -> None:
    song = {
        **_song("x"),
        "genre_name": None,
        "release_group_genres": [("other tag", 2)],
        "artist_genres": [("Jazz", 4)],
    }

    row = pl.read_parquet(_run_genre_match(tmp_path, [song])).to_dicts()[0]

    assert _source(row) == ("jazz", "artist", "exact")


def test_genre_match_artist_genre_reaches_every_song_of_the_artist(tmp_path: Path) -> None:
    songs = [
        {
            **_song("x", title=title),
            "genre_name": None,
            "artist_musicbrainz_id": _mbid(999),
            "artist_genres": [("jazz", 1)],
        }
        for title in ("A", "B")
    ]

    rows = pl.read_parquet(_run_genre_match(tmp_path, songs)).to_dicts()

    assert [_source(row) for row in rows] == [("jazz", "artist", "exact")] * 2


def test_genre_match_unresolved_inherited_tags_keep_the_most_precise_level_s_method(tmp_path: Path) -> None:
    song = {**_song("x"), "genre_name": None, "release_group_genres": [("other tag", 2)], "artist_genres": [("a", 1)]}

    row = pl.read_parquet(_run_genre_match(tmp_path, [song])).to_dicts()[0]

    assert _source(row) == (None, None, "unmatched")
    assert row["musicbrainz_genres"] == [{"name": "other tag", "weight": 2}]


def test_genre_match_song_without_tags_at_any_level_has_no_genre_source(tmp_path: Path) -> None:
    row = pl.read_parquet(_run_genre_match(tmp_path, [{**_song("x"), "genre_name": None}])).to_dicts()[0]

    assert _source(row) == (None, None, "no_genre")


def test_genre_match_disambiguates_artist_tags_by_the_artist_s_other_genres(tmp_path: Path) -> None:
    song = {**_song("x"), "genre_name": None, "artist_genres": [("r&b", 5), ("hip hop", 1)]}

    result = _run_genre_match(
        tmp_path,
        [song],
        hierarchy_rows=R_AND_B_HIERARCHY,
        disambiguation_rows=R_AND_B,
        context_rows=HIP_HOP_HINT,
    )

    assert _source(pl.read_parquet(result).to_dicts()[0]) == ("contemporary R&B", "artist", "disambiguated")


def test_genre_match_unresolved_report_counts_subjects_per_level(tmp_path: Path) -> None:
    output_dir = tmp_path / "gold"
    songs = [
        {**_song("odd tag", title=title), "artist_musicbrainz_id": _mbid(999), "artist_genres": [("odd tag", 3)]}
        for title in ("A", "B")
    ]

    _run_genre_match(tmp_path, songs, output_dir=output_dir)

    assert pl.read_csv(output_dir / "1_genre_match_unresolved.csv").to_dicts() == [
        {"genre_name": "odd tag", "level": "recording", "subject_count": 2, "weight_sum": 2},
        {"genre_name": "odd tag", "level": "artist", "subject_count": 1, "weight_sum": 3},
    ]
    assert json.loads((output_dir / "1_genre_match_report.json").read_text()) == {"unresolvedGenreTagCount": 1}


REGIONAL_ROWS = [
    {"item_id": "Q700", "item_label": "music of Japan", "parent_id": None},
    {"item_id": "Q701", "item_label": "j-pop", "parent_id": "Q700"},
    {"item_id": "Q702", "item_label": "new wave", "parent_id": "Q700"},
]
_SHARED_LABEL_HIERARCHY = [
    *HIERARCHY_ROWS,
    {"item_id": "Q7", "item_label": "new wave", "parent_id": None, "is_umbrella": False},
]


def _run_with_regional(tmp_path: Path, song_rows: list[dict], **kwargs) -> dict:
    result = _run_genre_match(
        tmp_path,
        song_rows,
        hierarchy_rows=kwargs.pop("hierarchy_rows", _SHARED_LABEL_HIERARCHY),
        regional_rows=REGIONAL_ROWS,
        overview_ids={"Q700"},
        **kwargs,
    )
    return pl.read_parquet(result).to_dicts()[0]


def _pick(row: dict) -> tuple:
    return row["wikidata_genre_item_id"], row["genre_tree"], row["is_umbrella"], row["match_method"]


def test_genre_match_resolves_a_regional_tag(tmp_path: Path) -> None:
    assert _pick(_run_with_regional(tmp_path, [_song("J-Pop")])) == ("Q701", "regional", False, "exact")


def test_genre_match_canonical_tree_wins_a_label_shared_with_the_regional_tree(tmp_path: Path) -> None:
    assert _pick(_run_with_regional(tmp_path, [_song("New Wave")])) == ("Q7", "canonical", False, "exact")


def test_genre_match_alias_to_a_shared_label_goes_to_the_canonical_item(tmp_path: Path) -> None:
    alias = {"musicbrainz_genre_name": "nw", "wikidata_genre_name": "new wave", "reason": "abbreviation"}
    row = _run_with_regional(tmp_path, [_song("nw")], alias_rows=[alias])

    assert _pick(row) == ("Q7", "canonical", False, "manual_alias")


def test_genre_match_never_targets_a_regional_overview(tmp_path: Path) -> None:
    assert _pick(_run_with_regional(tmp_path, [_song("music of Japan")])) == (None, None, False, "unmatched")


def test_genre_match_wikidata_alias_of_a_regional_item_sharing_a_label_keeps_its_item(tmp_path: Path) -> None:
    row = _run_with_regional(
        tmp_path, [_song("japanese new wave")], wikidata_alias_rows=[("Q702", "japanese new wave")]
    )

    assert _pick(row)[:2] == ("Q702", "regional")


def test_genre_match_accepts_an_alias_to_a_regional_label(tmp_path: Path) -> None:
    alias = {"musicbrainz_genre_name": "japanese pop", "wikidata_genre_name": "j-pop", "reason": "synonym"}
    row = _run_with_regional(tmp_path, [_song("japanese pop")], alias_rows=[alias])

    assert _pick(row) == ("Q701", "regional", False, "manual_alias")


@pytest.mark.parametrize(
    ("alias_rows", "non_genre_rows", "match"),
    [
        ([{"musicbrainz_genre_name": "J Pop", "wikidata_genre_name": "j-pop", "reason": "x"}], None, "dead-weight"),
        ([{"musicbrainz_genre_name": "mj", "wikidata_genre_name": "music of Japan", "reason": "x"}], None, "overview"),
        (None, [{"musicbrainz_genre_name": "j-pop", "reason": "x"}], "j-pop"),
    ],
)
def test_genre_match_raises_on_curation_dead_because_of_a_regional_label(
    tmp_path: Path, alias_rows: list[dict] | None, non_genre_rows: list[dict] | None, match: str
) -> None:
    with pytest.raises(ValueError, match=match):
        _run_with_regional(tmp_path, [_song("x")], alias_rows=alias_rows, non_genre_rows=non_genre_rows)


def test_genre_match_regional_child_beats_its_secondary_canonical_parent(tmp_path: Path) -> None:
    song = {**_song("x"), "genres": [("pop", 9), ("j-pop", 1)]}

    assert _run_with_regional(tmp_path, [song])["wikidata_genre_item_id"] == "Q5"
    assert (
        _run_with_regional(tmp_path, [song], secondary_rows=[{"item_id": "Q701", "parent_id": "Q5"}])[
            "wikidata_genre_item_id"
        ]
        == "Q701"
    )


def test_genre_match_regional_child_beats_its_selected_canonical_parent(tmp_path: Path) -> None:
    song = {**_song("x"), "genres": [("pop", 9), ("j-pop", 1)]}
    selection = {
        "item_id": "Q701",
        "parent_id": "Q700",
        "canonical_parent_id": "Q5",
        "regional_genre_parent_id": None,
        "regional_overview_ids": ["Q700"],
    }

    assert _run_with_regional(tmp_path, [song], selection_rows=[selection])["wikidata_genre_item_id"] == "Q701"


def test_genre_match_raises_on_an_item_in_both_trees(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="'item_id' is not unique"):
        _run_genre_match(
            tmp_path, [_song("jazz")], regional_rows=[{"item_id": "Q2", "item_label": "jazz", "parent_id": None}]
        )
