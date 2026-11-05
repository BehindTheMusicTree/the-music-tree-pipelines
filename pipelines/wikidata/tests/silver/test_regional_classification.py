from pathlib import Path

import polars as pl
import pytest

from wikidata.silver import regional_classification as sr

GENRE_CLASSIFICATION_ROWS = [
    # music of Portugal: the seed itself — a regional_overview item, not a genre, but becomes a
    # regional genre node ("seed") rather than being dropped
    {
        "item_id": "Q2579987",
        "item_label": "music of Portugal",
        "parent_id": None,
        "parent_label": None,
        "relation_type": None,
        "item_url": "https://www.wikidata.org/wiki/Q2579987",
        "parent_url": None,
        "is_regional_overview": True,
        "classification_reason": "regional_overview",
    },
    # Portuguese folk music: multi-parent — one edge into the seed (direct), one into a clean genre
    # parent. Still regional overall: ANY parent being regional is enough, not ALL.
    {
        "item_id": "Q106556293",
        "item_label": "Portuguese folk music",
        "parent_id": "Q2579987",
        "parent_label": "music of Portugal",
        "relation_type": "P279",
        "item_url": "https://www.wikidata.org/wiki/Q106556293",
        "parent_url": "https://www.wikidata.org/wiki/Q2579987",
        "is_regional_overview": False,
        "classification_reason": None,
    },
    {
        "item_id": "Q106556293",
        "item_label": "Portuguese folk music",
        "parent_id": "Q98528192",
        "parent_label": "European folk music",
        "relation_type": "P279",
        "item_url": "https://www.wikidata.org/wiki/Q106556293",
        "parent_url": "https://www.wikidata.org/wiki/Q98528192",
        "is_regional_overview": False,
        "classification_reason": None,
    },
    # fado: two hops from the seed via Portuguese folk music — inherited, not direct
    {
        "item_id": "Q185676",
        "item_label": "fado",
        "parent_id": "Q106556293",
        "parent_label": "Portuguese folk music",
        "relation_type": "P279",
        "item_url": "https://www.wikidata.org/wiki/Q185676",
        "parent_url": "https://www.wikidata.org/wiki/Q106556293",
        "is_regional_overview": False,
        "classification_reason": None,
    },
    # jazz -> popular music: no regional ancestor anywhere, stays clean
    {
        "item_id": "Q8341",
        "item_label": "jazz",
        "parent_id": "Q9778",
        "parent_label": "popular music",
        "relation_type": "P279",
        "item_url": "https://www.wikidata.org/wiki/Q8341",
        "parent_url": "https://www.wikidata.org/wiki/Q9778",
        "is_regional_overview": False,
        "classification_reason": None,
    },
    # popular music: root item, no parent, clean
    {
        "item_id": "Q9778",
        "item_label": "popular music",
        "parent_id": None,
        "parent_label": None,
        "relation_type": None,
        "item_url": "https://www.wikidata.org/wiki/Q9778",
        "parent_url": None,
        "is_regional_overview": False,
        "classification_reason": None,
    },
    # Han Chinese music: a root item (no P279/P361 parent) with a P2341 "indigenous to" value —
    # regional via indigenous_to, not via any parent edge.
    {
        "item_id": "Q10376827",
        "item_label": "Han Chinese music",
        "parent_id": None,
        "parent_label": None,
        "relation_type": None,
        "item_url": "https://www.wikidata.org/wiki/Q10376827",
        "parent_url": None,
        "is_regional_overview": False,
        "classification_reason": None,
    },
    # a subgenre of Han Chinese music: no P2341 of its own, regional via its direct parent edge
    {
        "item_id": "Q999999991",
        "item_label": "some Han Chinese music subgenre",
        "parent_id": "Q10376827",
        "parent_label": "Han Chinese music",
        "relation_type": "P279",
        "item_url": "https://www.wikidata.org/wiki/Q999999991",
        "parent_url": "https://www.wikidata.org/wiki/Q10376827",
        "is_regional_overview": False,
        "classification_reason": None,
    },
    # morna: a root item (no P279/P361 parent, no P2341 value) — country_of_origin is no longer a
    # classification source, so this stays clean unless caught by another mechanism.
    {
        "item_id": "Q1198131",
        "item_label": "morna",
        "parent_id": None,
        "parent_label": None,
        "relation_type": None,
        "item_url": "https://www.wikidata.org/wiki/Q1198131",
        "parent_url": None,
        "is_regional_overview": False,
        "classification_reason": None,
    },
    # mezwed: a root item (no P279/P361 parent, no P2341 value) filed under an overview in
    # regional_overviews.csv — regional via manual_regional_overview, not via any automated source.
    {
        "item_id": "Q4118941",
        "item_label": "mezwed",
        "parent_id": None,
        "parent_label": None,
        "relation_type": None,
        "item_url": "https://www.wikidata.org/wiki/Q4118941",
        "parent_url": None,
        "is_regional_overview": False,
        "classification_reason": None,
    },
    # a subgenre of mezwed: no override of its own, regional via its direct parent edge
    {
        "item_id": "Q999999993",
        "item_label": "some mezwed subgenre",
        "parent_id": "Q4118941",
        "parent_label": "mezwed",
        "relation_type": "P279",
        "item_url": "https://www.wikidata.org/wiki/Q999999993",
        "parent_url": "https://www.wikidata.org/wiki/Q4118941",
        "is_regional_overview": False,
        "classification_reason": None,
    },
]

GENRE_CLASSIFICATION_ROWS = [
    {**row, "item_display_label": row["item_label"], "parent_display_label": row["parent_label"]}
    for row in GENRE_CLASSIFICATION_ROWS
]

INDIGENOUS_TO_ROWS = [
    {
        "item_id": "Q10376827",
        "indigenous_to_id": "Q49103",
        "indigenous_to_label": "Han Chinese",
    },
]


def _write_genre_classification(tmp_path: Path) -> Path:
    regional_overview_classification_path = tmp_path / "3_regional_overview_classification.parquet"
    pl.DataFrame(GENRE_CLASSIFICATION_ROWS).write_parquet(regional_overview_classification_path)
    return regional_overview_classification_path


def _write_indigenous_to(tmp_path: Path) -> Path:
    indigenous_to_path = tmp_path / "wikidata_genre_indigenous_to.parquet"
    pl.DataFrame(INDIGENOUS_TO_ROWS).write_parquet(indigenous_to_path)
    return indigenous_to_path


PARENT_SCHEMA = {
    "item_id": pl.Utf8,
    "item_label": pl.Utf8,
    "reason": pl.Utf8,
    "parent_item_id": pl.Utf8,
    "exclude_other_parents": pl.Utf8,
}
OVERVIEW_SCHEMA = {
    "item_id": pl.Utf8,
    "item_label": pl.Utf8,
    "reason": pl.Utf8,
    "overview_item_id": pl.Utf8,
    "exclude_other_parents": pl.Utf8,
}
ITEM_SCHEMA = {"item_id": pl.Utf8, "item_label": pl.Utf8, "reason": pl.Utf8}
# mezwed filed under music of Portugal, an existing seed in the fixture
DEFAULT_OVERVIEW_ROWS = [{"item_id": "Q4118941", "overview_item_id": "Q2579987"}]


def _write_csv(path: Path, rows: list[dict], schema: dict) -> Path:
    full_rows = [{"item_label": row["item_id"], "reason": "test", **row} for row in rows]
    pl.DataFrame(full_rows, schema=schema).write_csv(path)
    return path


def _classify_regional_genres(
    tmp_path: Path,
    output_dir: Path | None = None,
    *,
    genre_classification_path: Path | None = None,
    canonical_parent: list[dict] | None = None,
    regional_genre_parent: list[dict] | None = None,
    regional_overviews: list[dict] | None = None,
    forced_canonical_genres: list[str] | None = None,
    accepted_roots: list[str] | None = None,
    indigenous_to_exclusions: list[str] | None = None,
    regional_overviews_path: Path | None = None,
) -> Path:
    def ids(item_ids: list[str] | None) -> list[dict]:
        return [{"item_id": item_id} for item_id in item_ids or []]

    return sr.classify_regional_genres(
        genre_classification_path or _write_genre_classification(tmp_path),
        _write_indigenous_to(tmp_path),
        _write_csv(tmp_path / "canonical_parent_additions.csv", [], {"item_id": pl.Utf8, "label": pl.Utf8}),
        _write_csv(tmp_path / "canonical_parent.csv", canonical_parent or [], PARENT_SCHEMA),
        _write_csv(tmp_path / "regional_genre_parent.csv", regional_genre_parent or [], PARENT_SCHEMA),
        regional_overviews_path
        or _write_csv(
            tmp_path / "regional_overviews.csv",
            DEFAULT_OVERVIEW_ROWS if regional_overviews is None else regional_overviews,
            OVERVIEW_SCHEMA,
        ),
        _write_csv(tmp_path / "forced_canonical_genres.csv", ids(forced_canonical_genres), ITEM_SCHEMA),
        _write_csv(tmp_path / "accepted_canonical_roots.csv", ids(accepted_roots), ITEM_SCHEMA),
        _write_csv(tmp_path / "indigenous_to_exclusions.csv", ids(indigenous_to_exclusions), ITEM_SCHEMA),
        output_dir or tmp_path / "silver",
    )


def _by_item(result: Path) -> dict[str, tuple[bool | None, str | None]]:
    df = pl.read_parquet(result)
    return {row["item_id"]: (row["is_regional"], row["regional_reason"]) for row in df.unique("item_id").to_dicts()}


def _rows(result: Path, item_id: str) -> pl.DataFrame:
    return pl.read_parquet(result).filter(pl.col("item_id") == item_id)


def test_classify_regional_genres_cascades_from_seeds(tmp_path: Path) -> None:
    result = _classify_regional_genres(tmp_path)

    assert result == tmp_path / "silver" / "4_regional_classification.parquet"
    assert _by_item(result) == {
        "Q2579987": (True, "seed"),  # the seed itself, now a regional genre node, not excluded
        "Q106556293": (True, "direct"),  # multi-parent, one regional edge is enough
        "Q185676": (True, "inherited"),  # fado, two hops from the seed
        "Q8341": (False, None),  # jazz, no regional ancestor
        "Q9778": (False, None),  # root item, no parent
        "Q10376827": (True, "indigenous_to"),  # root item, flagged via P2341, no parent edge involved
        "Q999999991": (True, "direct"),  # direct child of an indigenous_to item, same as a seed child
        "Q1198131": (False, None),  # morna, country_of_origin no longer a classification source
        "Q4118941": (True, "manual_regional_overview"),  # root item filed under an overview by a curator
        "Q999999993": (True, "direct"),  # direct child of a manual_regional_overview item, same as a seed child
    }


def test_classify_regional_genres_treats_manual_overview_reclassification_as_seed(tmp_path: Path) -> None:
    genre_classification_path = tmp_path / "3_regional_overview_classification.parquet"
    rows = [
        # European folk music: reclassified as a regional overview (not via the "music of "
        # prefix rule), so it must seed the cascade the same as a regular regional_overview item.
        {
            "item_id": "Q98528192",
            "item_label": "European folk music",
            "parent_id": None,
            "parent_label": None,
            "relation_type": None,
            "item_url": "https://www.wikidata.org/wiki/Q98528192",
            "parent_url": None,
            "is_regional_overview": True,
            "classification_reason": "manual_overview_reclassification",
        },
        # Hungarian folk music: direct child of the reclassified seed
        {
            "item_id": "Q1361992",
            "item_label": "Hungarian folk music",
            "parent_id": "Q98528192",
            "parent_label": "European folk music",
            "relation_type": "P279",
            "item_url": "https://www.wikidata.org/wiki/Q1361992",
            "parent_url": "https://www.wikidata.org/wiki/Q98528192",
            "is_regional_overview": False,
            "classification_reason": None,
        },
    ]
    rows = [
        {**row, "item_display_label": row["item_label"], "parent_display_label": row["parent_label"]} for row in rows
    ]
    pl.DataFrame(rows).write_parquet(genre_classification_path)

    result = _classify_regional_genres(
        tmp_path, genre_classification_path=genre_classification_path, regional_overviews=[]
    )

    assert _by_item(result) == {"Q98528192": (True, "seed"), "Q1361992": (True, "direct")}


def test_classify_regional_genres_nests_manual_overview_item_under_overview(tmp_path: Path) -> None:
    mezwed_rows = _rows(_classify_regional_genres(tmp_path), "Q4118941")

    assert mezwed_rows.height == 1
    row = mezwed_rows.row(0, named=True)
    assert row["parent_id"] == "Q2579987"
    assert row["parent_label"] == "music of Portugal"
    assert row["relation_type"] == "manual_regional_overview"
    assert row["is_regional"]
    assert row["regional_reason"] == "manual_regional_overview"
    assert row["exclude_other_parents"] is False


def test_classify_regional_genres_keeps_several_manual_overviews_of_one_item(tmp_path: Path) -> None:
    # Portuguese folk music already has a Bronze edge into music of Portugal; filing it under that
    # overview again replaces the Bronze edge rather than duplicating it.
    result = _classify_regional_genres(
        tmp_path,
        regional_overviews=[
            *DEFAULT_OVERVIEW_ROWS,
            {"item_id": "Q106556293", "overview_item_id": "Q2579987"},
        ],
    )

    rows = _rows(result, "Q106556293")
    assert sorted(rows.select("parent_id", "relation_type").rows()) == [
        ("Q2579987", "manual_regional_overview"),
        ("Q98528192", "P279"),
    ]


def test_classify_regional_genres_strips_whitespace_padded_overview_item_id(tmp_path: Path) -> None:
    result = _classify_regional_genres(
        tmp_path, regional_overviews=[{"item_id": "Q4118941", "overview_item_id": " Q2579987 "}]
    )

    row = _rows(result, "Q4118941").row(0, named=True)
    assert row["parent_id"] == "Q2579987"
    assert row["parent_label"] == "music of Portugal"


@pytest.mark.parametrize(
    ("rows", "match"),
    [
        ([{"item_id": "Q999999999", "overview_item_id": "Q2579987"}], "Q999999999"),
        ([{"item_id": "Q4118941", "overview_item_id": "Q999999999"}], "Q999999999"),
        # jazz is a known item but not flagged is_regional_overview
        ([{"item_id": "Q4118941", "overview_item_id": "Q8341"}], "Q8341"),
        ([{"item_id": "Q4118941", "overview_item_id": None}], "Q4118941"),
        ([{"item_id": "Q4118941", "overview_item_id": " "}], "Q4118941"),
        ([{"item_id": "Q2579987", "overview_item_id": "Q2579987"}], "self-parent"),
        (DEFAULT_OVERVIEW_ROWS * 2, "duplicate"),
    ],
)
def test_classify_regional_genres_validates_manual_regional_overviews(
    tmp_path: Path, rows: list[dict], match: str
) -> None:
    with pytest.raises(ValueError, match=match):
        _classify_regional_genres(tmp_path, regional_overviews=rows)


def test_classify_regional_genres_requires_overview_item_id_column(tmp_path: Path) -> None:
    path = _write_csv(tmp_path / "regional_overviews.csv", [{"item_id": "Q4118941"}], ITEM_SCHEMA)

    with pytest.raises(ValueError, match="overview_item_id"):
        _classify_regional_genres(tmp_path, regional_overviews_path=path)


def test_classify_regional_genres_requires_overview_item_id_value_when_column_is_all_null(tmp_path: Path) -> None:
    path = tmp_path / "regional_overviews.csv"
    path.write_text(
        "item_id,item_label,reason,overview_item_id,exclude_other_parents\nQ4118941,mezwed,test override,,\n"
    )

    with pytest.raises(ValueError, match="Q4118941"):
        _classify_regional_genres(tmp_path, regional_overviews_path=path)


def test_classify_regional_genres_creates_output_dir(tmp_path: Path) -> None:
    output_dir = tmp_path / "does" / "not" / "exist"

    _classify_regional_genres(tmp_path, output_dir)

    assert output_dir.is_dir()


def test_classify_regional_genres_inherits_is_regional_through_manual_regional_genre_parent(tmp_path: Path) -> None:
    # morna, a root item, under Portuguese folk music (regional but not an overview): is_regional is
    # derived by the cascade, not by any special-casing of the manual edge itself.
    result = _classify_regional_genres(
        tmp_path, regional_genre_parent=[{"item_id": "Q1198131", "parent_item_id": "Q106556293"}]
    )

    row = _rows(result, "Q1198131").row(0, named=True)
    assert row["parent_id"] == "Q106556293"
    assert row["relation_type"] == "manual_regional_genre_parent"
    assert row["is_regional"]
    assert row["regional_reason"] == "inherited"


def test_classify_regional_genres_manual_canonical_parent_stays_canonical(tmp_path: Path) -> None:
    result = _classify_regional_genres(
        tmp_path, canonical_parent=[{"item_id": "Q1198131", "parent_item_id": "Q9778", "exclude_other_parents": "TRUE"}]
    )

    row = _rows(result, "Q1198131").row(0, named=True)
    assert row["parent_id"] == "Q9778"
    assert row["relation_type"] == "manual_canonical_parent"
    assert row["exclude_other_parents"] is True
    assert not row["is_regional"]
    assert row["regional_reason"] is None


def test_classify_regional_genres_keeps_other_edges_of_manual_parent_item(tmp_path: Path) -> None:
    # exclude_other_parents is only carried on the edge for parent_selection.py: the item's other edges
    # stay, so fado stays regional through Portuguese folk music.
    result = _classify_regional_genres(
        tmp_path, canonical_parent=[{"item_id": "Q185676", "parent_item_id": "Q8341", "exclude_other_parents": "true"}]
    )

    fado_rows = _rows(result, "Q185676")
    assert set(fado_rows.get_column("parent_id")) == {"Q106556293", "Q8341"}
    assert fado_rows.get_column("is_regional").all()


@pytest.mark.parametrize(
    ("list_name", "rows", "match"),
    [
        ("canonical_parent", [{"item_id": "Q1198131", "parent_item_id": "Q0000000"}], "Q0000000"),
        ("canonical_parent", [{"item_id": "Q0000000", "parent_item_id": "Q9778"}], "Q0000000"),
        ("canonical_parent", [{"item_id": "Q1198131", "parent_item_id": "Q1198131"}], "self-parent"),
        # music of Portugal is an overview: regional_overviews.csv is the list for it
        ("canonical_parent", [{"item_id": "Q1198131", "parent_item_id": "Q2579987"}], "regional_overviews.csv"),
        ("regional_genre_parent", [{"item_id": "Q1198131", "parent_item_id": "Q2579987"}], "Q2579987"),
        ("canonical_parent", [{"item_id": "Q2579987", "parent_item_id": "Q9778"}], "Q2579987"),
        ("canonical_parent", [{"item_id": "Q1198131", "parent_item_id": "Q8341"}] * 2, "duplicate"),
        # Portuguese folk music is regional, popular music canonical
        ("canonical_parent", [{"item_id": "Q1198131", "parent_item_id": "Q106556293"}], "not canonical"),
        ("regional_genre_parent", [{"item_id": "Q1198131", "parent_item_id": "Q9778"}], "not regional"),
        (
            "canonical_parent",
            [{"item_id": "Q1198131", "parent_item_id": "Q9778", "exclude_other_parents": "yes"}],
            "true, false or blank",
        ),
    ],
)
def test_classify_regional_genres_validates_manual_parents(
    tmp_path: Path, list_name: str, rows: list[dict], match: str
) -> None:
    with pytest.raises(ValueError, match=match):
        _classify_regional_genres(tmp_path, **{list_name: rows})


def test_classify_regional_genres_raises_on_missing_parent_item_id_column(tmp_path: Path) -> None:
    path = _write_csv(tmp_path / "canonical_parent.csv", [{"item_id": "Q1198131"}], ITEM_SCHEMA)

    with pytest.raises(ValueError, match="parent_item_id"):
        sr._read_manual_parents(path, "parent_item_id")


def test_classify_regional_genres_raises_on_missing_exclude_other_parents_column(tmp_path: Path) -> None:
    path = tmp_path / "canonical_parent.csv"
    path.write_text("item_id,item_label,reason,parent_item_id\nQ1198131,morna,test,Q9778\n")

    with pytest.raises(ValueError, match="exclude_other_parents"):
        sr._read_manual_parents(path, "parent_item_id")


def test_classify_regional_genres_manual_edge_replaces_identical_bronze_edge(tmp_path: Path) -> None:
    result = _classify_regional_genres(
        tmp_path, regional_genre_parent=[{"item_id": "Q185676", "parent_item_id": "Q106556293"}]
    )

    fado_rows = _rows(result, "Q185676")
    assert fado_rows.select("parent_id", "relation_type").rows() == [("Q106556293", "manual_regional_genre_parent")]


def test_add_manual_canonical_parent_items_adds_synthetic_node() -> None:
    df = pl.DataFrame(GENRE_CLASSIFICATION_ROWS).with_columns(exclude_other_parents=pl.lit(None, dtype=pl.Boolean))
    manual_additions = pl.DataFrame(
        {"item_id": ["LOCAL:reggae-dub"], "label": ["Reggae/Dub"], "reason": ["synthetic grouping node"]}
    )

    result = sr._add_manual_canonical_parent_items(df, manual_additions)

    added_row = result.filter(pl.col("item_id") == "LOCAL:reggae-dub").row(0, named=True)
    assert added_row["item_label"] == "Reggae/Dub"
    assert added_row["parent_id"] is None
    assert added_row["item_url"] == "https://www.wikidata.org/wiki/LOCAL:reggae-dub"
    assert added_row["is_regional_overview"] is False
    assert added_row["classification_reason"] == "manual_canonical_parent_addition"
    assert added_row["exclude_other_parents"] is None
    assert result.height == df.height + 1


def test_add_manual_canonical_parent_items_no_op_on_empty_additions() -> None:
    df = pl.DataFrame(GENRE_CLASSIFICATION_ROWS)

    result = sr._add_manual_canonical_parent_items(
        df, pl.DataFrame(schema=df.schema).select("item_id").with_columns(label=pl.lit(""))
    )

    assert result.equals(df)


def test_add_manual_canonical_parent_items_raises_on_blank_item_label() -> None:
    df = pl.DataFrame(GENRE_CLASSIFICATION_ROWS)
    manual_additions = pl.DataFrame({"item_id": ["LOCAL:reggae-dub"], "label": [""], "reason": ["test"]})

    with pytest.raises(ValueError, match="blank item_id or label"):
        sr._add_manual_canonical_parent_items(df, manual_additions)


def test_add_manual_canonical_parent_items_raises_on_non_local_prefix() -> None:
    df = pl.DataFrame(GENRE_CLASSIFICATION_ROWS)
    manual_additions = pl.DataFrame({"item_id": ["Q999999999"], "label": ["fabricated genre"], "reason": ["test"]})

    with pytest.raises(ValueError, match="LOCAL:"):
        sr._add_manual_canonical_parent_items(df, manual_additions)


def test_add_manual_canonical_parent_items_raises_on_duplicate_item_id() -> None:
    df = pl.DataFrame(GENRE_CLASSIFICATION_ROWS)
    manual_additions = pl.DataFrame(
        {
            "item_id": ["LOCAL:reggae-dub", "LOCAL:reggae-dub"],
            "label": ["Reggae/Dub", "Reggae/Dub again"],
            "reason": ["test", "test duplicate"],
        }
    )

    with pytest.raises(ValueError, match="duplicate"):
        sr._add_manual_canonical_parent_items(df, manual_additions)


def test_add_manual_canonical_parent_items_raises_on_item_id_already_present() -> None:
    df = pl.DataFrame(GENRE_CLASSIFICATION_ROWS).with_columns(
        item_id=pl.when(pl.col("item_id") == "Q8341").then(pl.lit("LOCAL:reggae-dub")).otherwise(pl.col("item_id"))
    )
    manual_additions = pl.DataFrame({"item_id": ["LOCAL:reggae-dub"], "label": ["Reggae/Dub"], "reason": ["test"]})

    with pytest.raises(ValueError, match="already present"):
        sr._add_manual_canonical_parent_items(df, manual_additions)


def test_classify_regional_genres_forced_canonical_genre_stops_cascade(tmp_path: Path) -> None:
    # Portuguese folk music sits under music of Portugal; kept canonical, it no longer passes regional
    # status down to fado, whose only parent it is.
    by_item = _by_item(_classify_regional_genres(tmp_path, forced_canonical_genres=["Q106556293"]))

    assert by_item["Q106556293"] == (False, None)
    assert by_item["Q185676"] == (False, None)


def test_classify_regional_genres_forced_canonical_genre_beats_indigenous_to_and_manual_overview(
    tmp_path: Path,
) -> None:
    by_item = _by_item(_classify_regional_genres(tmp_path, forced_canonical_genres=["Q10376827", "Q4118941"]))

    assert by_item["Q10376827"] == (False, None)
    assert by_item["Q999999991"] == (False, None)
    assert by_item["Q4118941"] == (False, None)
    assert by_item["Q999999993"] == (False, None)


@pytest.mark.parametrize(
    ("item_ids", "match"),
    [(["Q999999999"], "Q999999999"), (["Q2579987"], "is_regional_overview"), (["Q8341", "Q8341"], "duplicate")],
)
def test_classify_regional_genres_validates_forced_canonical_genres(
    tmp_path: Path, item_ids: list[str], match: str
) -> None:
    with pytest.raises(ValueError, match=match):
        _classify_regional_genres(tmp_path, forced_canonical_genres=item_ids)


def test_classify_regional_genres_pins_accepted_roots(tmp_path: Path) -> None:
    # fado's only parent chain is regional (via Portuguese folk music). Accepting it as a canonical root
    # must drop that edge before the cascade, the way a Wikidata re-parenting of hip-hop under a
    # regional genre must not pull the curated hip-hop root into the regional graph.
    fado_rows = _rows(_classify_regional_genres(tmp_path, accepted_roots=["Q185676"]), "Q185676")

    assert fado_rows.height == 1
    row = fado_rows.row(0, named=True)
    assert row["parent_id"] is None
    assert not row["is_regional"]
    assert row["regional_reason"] is None


@pytest.mark.parametrize("list_name", ["canonical_parent", "regional_genre_parent"])
def test_classify_regional_genres_accepted_root_with_manual_parent_raises(tmp_path: Path, list_name: str) -> None:
    parent_id = "Q8341" if list_name == "canonical_parent" else "Q106556293"

    with pytest.raises(ValueError, match="Q185676"):
        _classify_regional_genres(
            tmp_path,
            accepted_roots=["Q185676"],
            **{list_name: [{"item_id": "Q185676", "parent_item_id": parent_id}]},
        )


def test_classify_regional_genres_ignores_indigenous_to_on_accepted_root(tmp_path: Path) -> None:
    by_item = _by_item(_classify_regional_genres(tmp_path, accepted_roots=["Q10376827"]))

    assert by_item["Q10376827"] == (False, None)


@pytest.mark.parametrize("item_id", ["Q4118941", "Q2579987", "Q999999999"])
def test_classify_regional_genres_accepted_root_regional_or_absent_raises(tmp_path: Path, item_id: str) -> None:
    # mezwed has a regional_overviews.csv row, music of Portugal is a regional overview, and
    # Q999999999 is absent from the tree (e.g. a deleted or merged QID).
    with pytest.raises(ValueError, match=item_id):
        _classify_regional_genres(tmp_path, accepted_roots=[item_id])


def test_classify_regional_genres_excludes_indigenous_to_false_positive(tmp_path: Path) -> None:
    # Han Chinese music carries a real P2341 value; excluding it must stop the automated
    # indigenous_to seed signal from firing, so it (and its subgenre) stay canonical.
    by_item = _by_item(_classify_regional_genres(tmp_path, indigenous_to_exclusions=["Q10376827"]))

    assert by_item["Q10376827"] == (False, None)
    assert by_item["Q999999991"] == (False, None)


def test_classify_regional_genres_requires_known_indigenous_to_exclusion_item_id(tmp_path: Path) -> None:
    # classical music is not in the fixture's indigenous_to rows
    with pytest.raises(ValueError, match="no P2341 value"):
        _classify_regional_genres(tmp_path, indigenous_to_exclusions=["Q9730"])
