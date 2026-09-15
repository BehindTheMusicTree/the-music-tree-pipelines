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
    # mezwed: a root item (no P279/P361 parent, no P2341 value) hand-flagged in
    # manual_regional_overrides.csv — regional via manual_override, not via any automated source.
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


def _write_manual_overrides(tmp_path: Path) -> Path:
    manual_overrides_path = tmp_path / "manual_regional_overrides.csv"
    pl.DataFrame(
        {
            "item_id": ["Q4118941"],
            "item_label": ["mezwed"],
            "reason": ["test override"],
            "overview_item_id": ["Q2579987"],  # music of Portugal, an existing seed in the fixture
        }
    ).write_csv(manual_overrides_path)
    return manual_overrides_path


def _write_manual_main_parent(tmp_path: Path) -> Path:
    manual_main_parent_path = tmp_path / "manual_main_parent.csv"
    pl.DataFrame(
        schema={"item_id": pl.Utf8, "item_label": pl.Utf8, "reason": pl.Utf8, "parent_item_id": pl.Utf8}
    ).write_csv(manual_main_parent_path)
    return manual_main_parent_path


def _write_manual_canonical_parent_additions(tmp_path: Path) -> Path:
    manual_canonical_parent_additions_path = tmp_path / "manual_canonical_parent_additions.csv"
    pl.DataFrame(schema={"item_id": pl.Utf8, "item_label": pl.Utf8, "reason": pl.Utf8}).write_csv(
        manual_canonical_parent_additions_path
    )
    return manual_canonical_parent_additions_path


def _write_manual_indigenous_to_exclusions(tmp_path: Path) -> Path:
    manual_indigenous_to_exclusions_path = tmp_path / "manual_indigenous_to_exclusions.csv"
    pl.DataFrame(schema={"item_id": pl.Utf8, "item_label": pl.Utf8, "reason": pl.Utf8}).write_csv(
        manual_indigenous_to_exclusions_path
    )
    return manual_indigenous_to_exclusions_path


def _classify_regional_genres(
    regional_overview_classification_path: Path,
    indigenous_to_path: Path,
    manual_overrides_path: Path,
    output_dir: Path,
    manual_main_parent_path: Path | None = None,
    manual_canonical_parent_additions_path: Path | None = None,
    manual_indigenous_to_exclusions_path: Path | None = None,
    *,
    tmp_path: Path | None = None,
) -> Path:
    tmp_path = tmp_path or output_dir.parent
    return sr.classify_regional_genres(
        regional_overview_classification_path,
        indigenous_to_path,
        manual_overrides_path,
        manual_canonical_parent_additions_path or _write_manual_canonical_parent_additions(tmp_path),
        manual_main_parent_path or _write_manual_main_parent(tmp_path),
        manual_indigenous_to_exclusions_path or _write_manual_indigenous_to_exclusions(tmp_path),
        output_dir,
    )


def test_classify_regional_genres_cascades_from_seeds(tmp_path: Path) -> None:
    regional_overview_classification_path = _write_genre_classification(tmp_path)
    indigenous_to_path = _write_indigenous_to(tmp_path)
    manual_overrides_path = _write_manual_overrides(tmp_path)
    output_dir = tmp_path / "silver"

    result = _classify_regional_genres(
        regional_overview_classification_path, indigenous_to_path, manual_overrides_path, output_dir
    )

    assert result == output_dir / "4_regional_classification.parquet"
    df = pl.read_parquet(result)

    by_item = {row["item_id"]: (row["is_regional"], row["regional_reason"]) for row in df.unique("item_id").to_dicts()}
    assert by_item == {
        "Q2579987": (True, "seed"),  # the seed itself, now a regional genre node, not excluded
        "Q106556293": (True, "direct"),  # multi-parent, one regional edge is enough
        "Q185676": (True, "inherited"),  # fado, two hops from the seed
        "Q8341": (False, None),  # jazz, no regional ancestor
        "Q9778": (False, None),  # root item, no parent
        "Q10376827": (True, "indigenous_to"),  # root item, flagged via P2341, no parent edge involved
        "Q999999991": (True, "direct"),  # direct child of an indigenous_to item, same as a seed child
        "Q1198131": (False, None),  # morna, country_of_origin no longer a classification source
        "Q4118941": (True, "manual_override"),  # root item, hand-flagged, no automated source or parent edge
        "Q999999993": (True, "direct"),  # direct child of a manual_override item, same as a seed child
    }


def test_classify_regional_genres_treats_manual_overview_reclassification_as_seed(tmp_path: Path) -> None:
    regional_overview_classification_path = tmp_path / "3_regional_overview_classification.parquet"
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
    pl.DataFrame(rows).write_parquet(regional_overview_classification_path)
    indigenous_to_path = _write_indigenous_to(tmp_path)
    manual_overrides_path = tmp_path / "manual_regional_overrides.csv"
    pl.DataFrame(
        {"item_id": [], "item_label": [], "reason": [], "overview_item_id": []},
        schema={"item_id": pl.Utf8, "item_label": pl.Utf8, "reason": pl.Utf8, "overview_item_id": pl.Utf8},
    ).write_csv(manual_overrides_path)
    output_dir = tmp_path / "silver"

    result = _classify_regional_genres(
        regional_overview_classification_path, indigenous_to_path, manual_overrides_path, output_dir
    )

    df = pl.read_parquet(result)
    by_item = {row["item_id"]: (row["is_regional"], row["regional_reason"]) for row in df.unique("item_id").to_dicts()}
    assert by_item == {
        "Q98528192": (True, "seed"),
        "Q1361992": (True, "direct"),
    }


def test_classify_regional_genres_nests_override_under_overview_item(tmp_path: Path) -> None:
    regional_overview_classification_path = _write_genre_classification(tmp_path)
    indigenous_to_path = _write_indigenous_to(tmp_path)
    manual_overrides_path = _write_manual_overrides(tmp_path)
    output_dir = tmp_path / "silver"

    result = _classify_regional_genres(
        regional_overview_classification_path, indigenous_to_path, manual_overrides_path, output_dir
    )

    df = pl.read_parquet(result)
    mezwed_row = df.filter(pl.col("item_id") == "Q4118941")
    assert mezwed_row.height == 1
    assert mezwed_row.row(0, named=True)["parent_id"] == "Q2579987"
    assert mezwed_row.row(0, named=True)["parent_label"] == "music of Portugal"
    assert mezwed_row.row(0, named=True)["relation_type"] == "manual_override_parent"
    assert mezwed_row.row(0, named=True)["is_regional"]
    assert mezwed_row.row(0, named=True)["regional_reason"] == "manual_override"


def test_classify_regional_genres_strips_whitespace_padded_overview_item_id(tmp_path: Path) -> None:
    regional_overview_classification_path = _write_genre_classification(tmp_path)
    indigenous_to_path = _write_indigenous_to(tmp_path)
    manual_overrides_path = tmp_path / "manual_regional_overrides.csv"
    pl.DataFrame(
        {
            "item_id": ["Q4118941"],
            "item_label": ["mezwed"],
            "reason": ["test override"],
            "overview_item_id": [" Q2579987 "],
        }
    ).write_csv(manual_overrides_path)
    output_dir = tmp_path / "silver"

    result = _classify_regional_genres(
        regional_overview_classification_path, indigenous_to_path, manual_overrides_path, output_dir
    )

    df = pl.read_parquet(result)
    mezwed_row = df.filter(pl.col("item_id") == "Q4118941")
    assert mezwed_row.row(0, named=True)["parent_id"] == "Q2579987"
    assert mezwed_row.row(0, named=True)["parent_label"] == "music of Portugal"


def test_classify_regional_genres_requires_known_override_item_id(tmp_path: Path) -> None:
    regional_overview_classification_path = _write_genre_classification(tmp_path)
    indigenous_to_path = _write_indigenous_to(tmp_path)
    manual_overrides_path = tmp_path / "manual_regional_overrides.csv"
    pl.DataFrame(
        {
            "item_id": ["Q999999999"],
            "item_label": ["not in the genre tree"],
            "reason": ["test override"],
            "overview_item_id": ["Q2579987"],
        }
    ).write_csv(manual_overrides_path)
    output_dir = tmp_path / "silver"

    with pytest.raises(ValueError, match="Q999999999"):
        _classify_regional_genres(
            regional_overview_classification_path, indigenous_to_path, manual_overrides_path, output_dir
        )


def test_classify_regional_genres_requires_known_overview_item_id(tmp_path: Path) -> None:
    regional_overview_classification_path = _write_genre_classification(tmp_path)
    indigenous_to_path = _write_indigenous_to(tmp_path)
    manual_overrides_path = tmp_path / "manual_regional_overrides.csv"
    pl.DataFrame(
        {
            "item_id": ["Q4118941"],
            "item_label": ["mezwed"],
            "reason": ["test override"],
            "overview_item_id": ["Q999999999"],
        }
    ).write_csv(manual_overrides_path)
    output_dir = tmp_path / "silver"

    with pytest.raises(ValueError, match="Q999999999"):
        _classify_regional_genres(
            regional_overview_classification_path, indigenous_to_path, manual_overrides_path, output_dir
        )


def test_classify_regional_genres_requires_overview_item_id_be_regional_overview(tmp_path: Path) -> None:
    regional_overview_classification_path = _write_genre_classification(tmp_path)
    indigenous_to_path = _write_indigenous_to(tmp_path)
    manual_overrides_path = tmp_path / "manual_regional_overrides.csv"
    pl.DataFrame(
        {
            "item_id": ["Q4118941"],
            "item_label": ["mezwed"],
            "reason": ["test override"],
            "overview_item_id": ["Q8341"],  # jazz, a known item but not flagged is_regional_overview
        }
    ).write_csv(manual_overrides_path)
    output_dir = tmp_path / "silver"

    with pytest.raises(ValueError, match="Q8341"):
        _classify_regional_genres(
            regional_overview_classification_path, indigenous_to_path, manual_overrides_path, output_dir
        )


def test_classify_regional_genres_requires_overview_item_id_column(tmp_path: Path) -> None:
    regional_overview_classification_path = _write_genre_classification(tmp_path)
    indigenous_to_path = _write_indigenous_to(tmp_path)
    manual_overrides_path = tmp_path / "manual_regional_overrides.csv"
    pl.DataFrame({"item_id": ["Q4118941"], "item_label": ["mezwed"], "reason": ["test override"]}).write_csv(
        manual_overrides_path
    )
    output_dir = tmp_path / "silver"

    with pytest.raises(ValueError, match="overview_item_id"):
        _classify_regional_genres(
            regional_overview_classification_path, indigenous_to_path, manual_overrides_path, output_dir
        )


def test_classify_regional_genres_requires_overview_item_id_value_when_column_is_all_null(
    tmp_path: Path,
) -> None:
    regional_overview_classification_path = _write_genre_classification(tmp_path)
    indigenous_to_path = _write_indigenous_to(tmp_path)
    manual_overrides_path = tmp_path / "manual_regional_overrides.csv"
    # An entirely-empty overview_item_id column makes Polars infer it as Null dtype rather than
    # Utf8, which previously raised a SchemaError on the strip_chars() call instead of the
    # intended ValueError.
    manual_overrides_path.write_text("item_id,item_label,reason,overview_item_id\nQ4118941,mezwed,test override,\n")
    output_dir = tmp_path / "silver"

    with pytest.raises(ValueError, match="Q4118941"):
        _classify_regional_genres(
            regional_overview_classification_path, indigenous_to_path, manual_overrides_path, output_dir
        )


def test_classify_regional_genres_requires_overview_item_id_value(tmp_path: Path) -> None:
    regional_overview_classification_path = _write_genre_classification(tmp_path)
    indigenous_to_path = _write_indigenous_to(tmp_path)
    manual_overrides_path = tmp_path / "manual_regional_overrides.csv"
    pl.DataFrame(
        {
            "item_id": ["Q4118941"],
            "item_label": ["mezwed"],
            "reason": ["test override"],
            "overview_item_id": [None],
        }
    ).write_csv(manual_overrides_path)
    output_dir = tmp_path / "silver"

    with pytest.raises(ValueError, match="Q4118941"):
        _classify_regional_genres(
            regional_overview_classification_path, indigenous_to_path, manual_overrides_path, output_dir
        )


def test_classify_regional_genres_requires_non_blank_overview_item_id_value(tmp_path: Path) -> None:
    regional_overview_classification_path = _write_genre_classification(tmp_path)
    indigenous_to_path = _write_indigenous_to(tmp_path)
    manual_overrides_path = tmp_path / "manual_regional_overrides.csv"
    pl.DataFrame(
        {
            "item_id": ["Q4118941"],
            "item_label": ["mezwed"],
            "reason": ["test override"],
            "overview_item_id": [" "],
        }
    ).write_csv(manual_overrides_path)
    output_dir = tmp_path / "silver"

    with pytest.raises(ValueError, match="Q4118941"):
        _classify_regional_genres(
            regional_overview_classification_path, indigenous_to_path, manual_overrides_path, output_dir
        )


def test_classify_regional_genres_creates_output_dir(tmp_path: Path) -> None:
    regional_overview_classification_path = _write_genre_classification(tmp_path)
    indigenous_to_path = _write_indigenous_to(tmp_path)
    manual_overrides_path = _write_manual_overrides(tmp_path)
    output_dir = tmp_path / "does" / "not" / "exist"

    _classify_regional_genres(
        regional_overview_classification_path, indigenous_to_path, manual_overrides_path, output_dir, tmp_path=tmp_path
    )

    assert output_dir.is_dir()


def test_classify_regional_genres_inherits_is_regional_through_manual_parent(tmp_path: Path) -> None:
    # jazz has no regional ancestor in the base fixture; point it at the regional seed via a manual
    # parent override and confirm is_regional/regional_reason are derived by the cascade, not by any
    # special-casing of the manual edge itself.
    regional_overview_classification_path = _write_genre_classification(tmp_path)
    indigenous_to_path = _write_indigenous_to(tmp_path)
    manual_overrides_path = tmp_path / "manual_regional_overrides.csv"
    pl.DataFrame(
        {"item_id": [], "item_label": [], "reason": [], "overview_item_id": []},
        schema={"item_id": pl.Utf8, "item_label": pl.Utf8, "reason": pl.Utf8, "overview_item_id": pl.Utf8},
    ).write_csv(manual_overrides_path)
    manual_main_parent_path = tmp_path / "manual_main_parent.csv"
    pl.DataFrame(
        {
            "item_id": ["Q1198131"],  # morna, a root item with no parent in the base fixture
            "item_label": ["morna"],
            "reason": ["test override"],
            "parent_item_id": ["Q106556293"],  # Portuguese folk music, regional but not an overview item
        }
    ).write_csv(manual_main_parent_path)
    output_dir = tmp_path / "silver"

    result = _classify_regional_genres(
        regional_overview_classification_path,
        indigenous_to_path,
        manual_overrides_path,
        output_dir,
        manual_main_parent_path,
    )

    df = pl.read_parquet(result)
    morna_row = df.filter(pl.col("item_id") == "Q1198131").row(0, named=True)
    assert morna_row["parent_id"] == "Q106556293"
    assert morna_row["relation_type"] == "manual_main_parent"
    assert morna_row["is_regional"]
    assert morna_row["regional_reason"] == "inherited"


def test_classify_regional_genres_manual_parent_stays_canonical_under_canonical_parent(tmp_path: Path) -> None:
    # Pointing a root at a canonical (non-regional) parent should leave the item canonical — the
    # cascade decides based on the parent's actual status, no special-casing needed either way.
    regional_overview_classification_path = _write_genre_classification(tmp_path)
    indigenous_to_path = _write_indigenous_to(tmp_path)
    manual_overrides_path = tmp_path / "manual_regional_overrides.csv"
    pl.DataFrame(
        {"item_id": [], "item_label": [], "reason": [], "overview_item_id": []},
        schema={"item_id": pl.Utf8, "item_label": pl.Utf8, "reason": pl.Utf8, "overview_item_id": pl.Utf8},
    ).write_csv(manual_overrides_path)
    manual_main_parent_path = tmp_path / "manual_main_parent.csv"
    pl.DataFrame(
        {
            "item_id": ["Q1198131"],  # morna, a root item with no parent in the base fixture
            "item_label": ["morna"],
            "reason": ["test override"],
            "parent_item_id": ["Q9778"],  # popular music, canonical
        }
    ).write_csv(manual_main_parent_path)
    output_dir = tmp_path / "silver"

    result = _classify_regional_genres(
        regional_overview_classification_path,
        indigenous_to_path,
        manual_overrides_path,
        output_dir,
        manual_main_parent_path,
    )

    df = pl.read_parquet(result)
    morna_row = df.filter(pl.col("item_id") == "Q1198131").row(0, named=True)
    assert morna_row["parent_id"] == "Q9778"
    assert not morna_row["is_regional"]
    assert morna_row["regional_reason"] is None


def test_classify_regional_genres_raises_on_missing_parent_item_id_column(tmp_path: Path) -> None:
    regional_overview_classification_path = _write_genre_classification(tmp_path)
    indigenous_to_path = _write_indigenous_to(tmp_path)
    manual_overrides_path = _write_manual_overrides(tmp_path)
    manual_main_parent_path = tmp_path / "manual_main_parent.csv"
    pl.DataFrame({"item_id": ["Q1198131"], "item_label": ["morna"], "reason": ["test"]}).write_csv(
        manual_main_parent_path
    )
    output_dir = tmp_path / "silver"

    with pytest.raises(ValueError, match="parent_item_id"):
        _classify_regional_genres(
            regional_overview_classification_path,
            indigenous_to_path,
            manual_overrides_path,
            output_dir,
            manual_main_parent_path,
        )


def test_classify_regional_genres_raises_on_unknown_parent_item_id(tmp_path: Path) -> None:
    regional_overview_classification_path = _write_genre_classification(tmp_path)
    indigenous_to_path = _write_indigenous_to(tmp_path)
    manual_overrides_path = _write_manual_overrides(tmp_path)
    manual_main_parent_path = tmp_path / "manual_main_parent.csv"
    pl.DataFrame(
        {
            "item_id": ["Q1198131"],
            "item_label": ["morna"],
            "reason": ["test"],
            "parent_item_id": ["Q0000000"],
        }
    ).write_csv(manual_main_parent_path)
    output_dir = tmp_path / "silver"

    with pytest.raises(ValueError, match="Q0000000"):
        _classify_regional_genres(
            regional_overview_classification_path,
            indigenous_to_path,
            manual_overrides_path,
            output_dir,
            manual_main_parent_path,
        )


def test_classify_regional_genres_raises_on_parent_item_id_is_regional_overview(tmp_path: Path) -> None:
    regional_overview_classification_path = _write_genre_classification(tmp_path)
    indigenous_to_path = _write_indigenous_to(tmp_path)
    manual_overrides_path = _write_manual_overrides(tmp_path)
    manual_main_parent_path = tmp_path / "manual_main_parent.csv"
    pl.DataFrame(
        {
            "item_id": ["Q1198131"],
            "item_label": ["morna"],
            "reason": ["test"],
            "parent_item_id": ["Q2579987"],  # music of Portugal, is_regional_overview=True
        }
    ).write_csv(manual_main_parent_path)
    output_dir = tmp_path / "silver"

    with pytest.raises(ValueError, match="Q2579987"):
        _classify_regional_genres(
            regional_overview_classification_path,
            indigenous_to_path,
            manual_overrides_path,
            output_dir,
            manual_main_parent_path,
        )


def test_classify_regional_genres_allows_override_for_item_with_existing_parent(tmp_path: Path) -> None:
    # fado already has a parent edge to Portuguese folk music in the base fixture — the root-only
    # restriction has been removed, so overriding it now succeeds: the override becomes the item's
    # main parent, and the old Bronze edge survives as a separate row for main_parent_selection.py
    # to resolve downstream.
    regional_overview_classification_path = _write_genre_classification(tmp_path)
    indigenous_to_path = _write_indigenous_to(tmp_path)
    manual_overrides_path = _write_manual_overrides(tmp_path)
    manual_main_parent_path = tmp_path / "manual_main_parent.csv"
    pl.DataFrame(
        {
            "item_id": ["Q185676"],  # fado, already has a parent edge to Portuguese folk music
            "item_label": ["fado"],
            "reason": ["test"],
            "parent_item_id": ["Q8341"],  # jazz
        }
    ).write_csv(manual_main_parent_path)
    output_dir = tmp_path / "silver"

    result = _classify_regional_genres(
        regional_overview_classification_path,
        indigenous_to_path,
        manual_overrides_path,
        output_dir,
        manual_main_parent_path,
    )

    df = pl.read_parquet(result)
    fado_rows = df.filter(pl.col("item_id") == "Q185676")
    parent_ids = set(fado_rows.select("parent_id").to_series().to_list())
    assert parent_ids == {"Q106556293", "Q8341"}
    override_row = fado_rows.filter(pl.col("parent_id") == "Q8341").row(0, named=True)
    assert override_row["relation_type"] == "manual_main_parent"


def test_classify_regional_genres_raises_on_duplicate_parent_override_item_id(tmp_path: Path) -> None:
    regional_overview_classification_path = _write_genre_classification(tmp_path)
    indigenous_to_path = _write_indigenous_to(tmp_path)
    manual_overrides_path = _write_manual_overrides(tmp_path)
    manual_main_parent_path = tmp_path / "manual_main_parent.csv"
    pl.DataFrame(
        {
            "item_id": ["Q1198131", "Q1198131"],
            "item_label": ["morna", "morna"],
            "reason": ["test", "test duplicate"],
            "parent_item_id": ["Q8341", "Q8341"],
        }
    ).write_csv(manual_main_parent_path)
    output_dir = tmp_path / "silver"

    with pytest.raises(ValueError, match="duplicate"):
        _classify_regional_genres(
            regional_overview_classification_path,
            indigenous_to_path,
            manual_overrides_path,
            output_dir,
            manual_main_parent_path,
        )


def test_add_manual_canonical_parent_items_adds_synthetic_node() -> None:
    df = pl.DataFrame(GENRE_CLASSIFICATION_ROWS)
    manual_additions = pl.DataFrame(
        {"item_id": ["LOCAL:reggae-dub"], "item_label": ["Reggae/Dub"], "reason": ["synthetic grouping node"]}
    )

    result = sr._add_manual_canonical_parent_items(df, manual_additions)

    added_row = result.filter(pl.col("item_id") == "LOCAL:reggae-dub").row(0, named=True)
    assert added_row["item_label"] == "Reggae/Dub"
    assert added_row["parent_id"] is None
    assert added_row["item_url"] == "https://www.wikidata.org/wiki/LOCAL:reggae-dub"
    assert added_row["is_regional_overview"] is False
    assert added_row["classification_reason"] == "manual_canonical_parent_addition"
    assert result.height == df.height + 1


def test_add_manual_canonical_parent_items_no_op_on_empty_additions() -> None:
    df = pl.DataFrame(GENRE_CLASSIFICATION_ROWS)

    result = sr._add_manual_canonical_parent_items(df, pl.DataFrame(schema=df.schema).select("item_id", "item_label"))

    assert result.equals(df)


def test_add_manual_canonical_parent_items_raises_on_blank_item_label() -> None:
    df = pl.DataFrame(GENRE_CLASSIFICATION_ROWS)
    manual_additions = pl.DataFrame({"item_id": ["LOCAL:reggae-dub"], "item_label": [""], "reason": ["test"]})

    with pytest.raises(ValueError, match="blank item_id or item_label"):
        sr._add_manual_canonical_parent_items(df, manual_additions)


def test_add_manual_canonical_parent_items_raises_on_non_local_prefix() -> None:
    df = pl.DataFrame(GENRE_CLASSIFICATION_ROWS)
    manual_additions = pl.DataFrame({"item_id": ["Q999999999"], "item_label": ["fabricated genre"], "reason": ["test"]})

    with pytest.raises(ValueError, match="LOCAL:"):
        sr._add_manual_canonical_parent_items(df, manual_additions)


def test_add_manual_canonical_parent_items_raises_on_duplicate_item_id() -> None:
    df = pl.DataFrame(GENRE_CLASSIFICATION_ROWS)
    manual_additions = pl.DataFrame(
        {
            "item_id": ["LOCAL:reggae-dub", "LOCAL:reggae-dub"],
            "item_label": ["Reggae/Dub", "Reggae/Dub again"],
            "reason": ["test", "test duplicate"],
        }
    )

    with pytest.raises(ValueError, match="duplicate"):
        sr._add_manual_canonical_parent_items(df, manual_additions)


def test_add_manual_canonical_parent_items_raises_on_item_id_already_present() -> None:
    df = pl.DataFrame(GENRE_CLASSIFICATION_ROWS).with_columns(
        item_id=pl.when(pl.col("item_id") == "Q8341").then(pl.lit("LOCAL:reggae-dub")).otherwise(pl.col("item_id"))
    )
    manual_additions = pl.DataFrame({"item_id": ["LOCAL:reggae-dub"], "item_label": ["Reggae/Dub"], "reason": ["test"]})

    with pytest.raises(ValueError, match="already present"):
        sr._add_manual_canonical_parent_items(df, manual_additions)


def test_classify_regional_genres_exclude_other_parents_drops_conflicting_regional_edge(tmp_path: Path) -> None:
    # fado has a genuine parent edge into Portuguese folk music (regional, via the seed). Overriding
    # fado's main parent to jazz (canonical) with exclude_other_parents=true must drop that other
    # edge too, not just the null-parent placeholder — otherwise the regional cascade would still see
    # the Portuguese-folk-music edge and keep fado is_regional=True regardless of the override.
    regional_overview_classification_path = _write_genre_classification(tmp_path)
    indigenous_to_path = _write_indigenous_to(tmp_path)
    manual_overrides_path = _write_manual_overrides(tmp_path)
    manual_main_parent_path = tmp_path / "manual_main_parent.csv"
    pl.DataFrame(
        {
            "item_id": ["Q185676"],  # fado, already has a parent edge to Portuguese folk music
            "item_label": ["fado"],
            "reason": ["test"],
            "parent_item_id": ["Q8341"],  # jazz, canonical
            "exclude_other_parents": ["true"],
        }
    ).write_csv(manual_main_parent_path)
    output_dir = tmp_path / "silver"

    result = _classify_regional_genres(
        regional_overview_classification_path,
        indigenous_to_path,
        manual_overrides_path,
        output_dir,
        manual_main_parent_path,
    )

    df = pl.read_parquet(result)
    fado_rows = df.filter(pl.col("item_id") == "Q185676")
    parent_ids = set(fado_rows.select("parent_id").to_series().to_list())
    assert parent_ids == {"Q8341"}
    override_row = fado_rows.row(0, named=True)
    assert override_row["relation_type"] == "manual_main_parent"
    assert not override_row["is_regional"]
    assert override_row["regional_reason"] is None


def test_classify_regional_genres_exclude_other_parents_false_keeps_other_edges(tmp_path: Path) -> None:
    # Same override as above but exclude_other_parents left blank/false: the other parent edge into
    # Portuguese folk music must survive, same as the existing default-behavior test, so fado stays
    # is_regional=True via the surviving edge into the regional seed.
    regional_overview_classification_path = _write_genre_classification(tmp_path)
    indigenous_to_path = _write_indigenous_to(tmp_path)
    manual_overrides_path = _write_manual_overrides(tmp_path)
    manual_main_parent_path = tmp_path / "manual_main_parent.csv"
    pl.DataFrame(
        {
            "item_id": ["Q185676"],
            "item_label": ["fado"],
            "reason": ["test"],
            "parent_item_id": ["Q8341"],
            "exclude_other_parents": [""],
        }
    ).write_csv(manual_main_parent_path)
    output_dir = tmp_path / "silver"

    result = _classify_regional_genres(
        regional_overview_classification_path,
        indigenous_to_path,
        manual_overrides_path,
        output_dir,
        manual_main_parent_path,
    )

    df = pl.read_parquet(result)
    fado_rows = df.filter(pl.col("item_id") == "Q185676")
    parent_ids = set(fado_rows.select("parent_id").to_series().to_list())
    assert parent_ids == {"Q106556293", "Q8341"}
    assert fado_rows.filter(pl.col("parent_id") == "Q106556293").row(0, named=True)["is_regional"]


def test_classify_regional_genres_overview_override_exclude_other_parents_drops_conflicting_edge(
    tmp_path: Path,
) -> None:
    # jazz has a genuine parent edge into popular music (canonical). Overriding jazz's main parent to
    # music of Portugal with exclude_other_parents=true must drop that other edge too, not just a
    # null-parent placeholder — otherwise main_parent_selection's lowest-QID fallback could still pick
    # the surviving popular-music edge over the override.
    regional_overview_classification_path = _write_genre_classification(tmp_path)
    indigenous_to_path = _write_indigenous_to(tmp_path)
    manual_overrides_path = tmp_path / "manual_regional_overrides.csv"
    pl.DataFrame(
        {
            "item_id": ["Q8341"],  # jazz, already has a parent edge to popular music
            "item_label": ["jazz"],
            "reason": ["test"],
            "overview_item_id": ["Q2579987"],  # music of Portugal
            "exclude_other_parents": ["true"],
        }
    ).write_csv(manual_overrides_path)
    output_dir = tmp_path / "silver"

    result = _classify_regional_genres(
        regional_overview_classification_path, indigenous_to_path, manual_overrides_path, output_dir
    )

    df = pl.read_parquet(result)
    jazz_rows = df.filter(pl.col("item_id") == "Q8341")
    parent_ids = set(jazz_rows.select("parent_id").to_series().to_list())
    assert parent_ids == {"Q2579987"}
    override_row = jazz_rows.row(0, named=True)
    assert override_row["relation_type"] == "manual_override_parent"
    assert override_row["is_regional"]


def test_classify_regional_genres_overview_override_exclude_other_parents_false_keeps_other_edges(
    tmp_path: Path,
) -> None:
    # Same override as above but exclude_other_parents left blank/false: the other parent edge into
    # popular music must survive alongside the synthetic override edge.
    regional_overview_classification_path = _write_genre_classification(tmp_path)
    indigenous_to_path = _write_indigenous_to(tmp_path)
    manual_overrides_path = tmp_path / "manual_regional_overrides.csv"
    pl.DataFrame(
        {
            "item_id": ["Q8341"],
            "item_label": ["jazz"],
            "reason": ["test"],
            "overview_item_id": ["Q2579987"],
            "exclude_other_parents": [""],
        }
    ).write_csv(manual_overrides_path)
    output_dir = tmp_path / "silver"

    result = _classify_regional_genres(
        regional_overview_classification_path, indigenous_to_path, manual_overrides_path, output_dir
    )

    df = pl.read_parquet(result)
    jazz_rows = df.filter(pl.col("item_id") == "Q8341")
    parent_ids = set(jazz_rows.select("parent_id").to_series().to_list())
    assert parent_ids == {"Q9778", "Q2579987"}


def test_classify_regional_genres_excludes_indigenous_to_false_positive(tmp_path: Path) -> None:
    # Han Chinese music carries a real P2341 value; excluding it must stop the automated
    # indigenous_to seed signal from firing, so it (and its subgenre) stay canonical.
    regional_overview_classification_path = _write_genre_classification(tmp_path)
    indigenous_to_path = _write_indigenous_to(tmp_path)
    manual_overrides_path = _write_manual_overrides(tmp_path)
    manual_indigenous_to_exclusions_path = tmp_path / "manual_indigenous_to_exclusions.csv"
    pl.DataFrame(
        {
            "item_id": ["Q10376827"],
            "item_label": ["Han Chinese music"],
            "reason": ["test exclusion"],
        }
    ).write_csv(manual_indigenous_to_exclusions_path)
    output_dir = tmp_path / "silver"

    result = _classify_regional_genres(
        regional_overview_classification_path,
        indigenous_to_path,
        manual_overrides_path,
        output_dir,
        manual_indigenous_to_exclusions_path=manual_indigenous_to_exclusions_path,
    )

    df = pl.read_parquet(result)
    han_row = df.filter(pl.col("item_id") == "Q10376827").row(0, named=True)
    assert not han_row["is_regional"]
    assert han_row["regional_reason"] is None
    subgenre_row = df.filter(pl.col("item_id") == "Q999999991").row(0, named=True)
    assert not subgenre_row["is_regional"]


def test_classify_regional_genres_requires_known_indigenous_to_exclusion_item_id(tmp_path: Path) -> None:
    regional_overview_classification_path = _write_genre_classification(tmp_path)
    indigenous_to_path = _write_indigenous_to(tmp_path)
    manual_overrides_path = _write_manual_overrides(tmp_path)
    manual_indigenous_to_exclusions_path = tmp_path / "manual_indigenous_to_exclusions.csv"
    pl.DataFrame(
        {
            "item_id": ["Q9730"],  # not in the fixture's indigenous_to rows
            "item_label": ["classical music"],
            "reason": ["test exclusion"],
        }
    ).write_csv(manual_indigenous_to_exclusions_path)
    output_dir = tmp_path / "silver"

    with pytest.raises(ValueError, match="no P2341 value"):
        _classify_regional_genres(
            regional_overview_classification_path,
            indigenous_to_path,
            manual_overrides_path,
            output_dir,
            manual_indigenous_to_exclusions_path=manual_indigenous_to_exclusions_path,
        )
