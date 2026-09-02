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
    # morna: a root item (no P279/P361 parent) with a P495 "country of origin" value — regional
    # via country_of_origin, not via any parent edge.
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
    # a subgenre of morna: no P495 of its own, regional via its direct parent edge
    {
        "item_id": "Q999999992",
        "item_label": "some morna subgenre",
        "parent_id": "Q1198131",
        "parent_label": "morna",
        "relation_type": "P279",
        "item_url": "https://www.wikidata.org/wiki/Q999999992",
        "parent_url": "https://www.wikidata.org/wiki/Q1198131",
        "is_regional_overview": False,
        "classification_reason": None,
    },
    # mezwed: a root item (no P279/P361 parent, no P2341/P495 value) hand-flagged in
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

INDIGENOUS_TO_ROWS = [
    {
        "item_id": "Q10376827",
        "indigenous_to_id": "Q49103",
        "indigenous_to_label": "Han Chinese",
    },
]

COUNTRY_OF_ORIGIN_ROWS = [
    {
        "item_id": "Q1198131",
        "country_of_origin_id": "Q1011",
        "country_of_origin_label": "Cape Verde",
    },
]


def _write_genre_classification(tmp_path: Path) -> Path:
    genre_classification_path = tmp_path / "2_regional_overview_classification.parquet"
    pl.DataFrame(GENRE_CLASSIFICATION_ROWS).write_parquet(genre_classification_path)
    return genre_classification_path


def _write_indigenous_to(tmp_path: Path) -> Path:
    indigenous_to_path = tmp_path / "wikidata_genre_indigenous_to.parquet"
    pl.DataFrame(INDIGENOUS_TO_ROWS).write_parquet(indigenous_to_path)
    return indigenous_to_path


def _write_country_of_origin(tmp_path: Path) -> Path:
    country_of_origin_path = tmp_path / "wikidata_genre_country_of_origin.parquet"
    pl.DataFrame(COUNTRY_OF_ORIGIN_ROWS).write_parquet(country_of_origin_path)
    return country_of_origin_path


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


def test_classify_regional_genres_cascades_from_seeds(tmp_path: Path) -> None:
    genre_classification_path = _write_genre_classification(tmp_path)
    indigenous_to_path = _write_indigenous_to(tmp_path)
    country_of_origin_path = _write_country_of_origin(tmp_path)
    manual_overrides_path = _write_manual_overrides(tmp_path)
    output_dir = tmp_path / "silver"

    result = sr.classify_regional_genres(
        genre_classification_path, indigenous_to_path, country_of_origin_path, manual_overrides_path, output_dir
    )

    assert result == output_dir / "3_regional_classification.parquet"
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
        "Q1198131": (True, "country_of_origin"),  # root item, flagged via P495, no parent edge involved
        "Q999999992": (True, "direct"),  # direct child of a country_of_origin item, same as a seed child
        "Q4118941": (True, "manual_override"),  # root item, hand-flagged, no automated source or parent edge
        "Q999999993": (True, "direct"),  # direct child of a manual_override item, same as a seed child
    }


def test_classify_regional_genres_nests_override_under_overview_item(tmp_path: Path) -> None:
    genre_classification_path = _write_genre_classification(tmp_path)
    indigenous_to_path = _write_indigenous_to(tmp_path)
    country_of_origin_path = _write_country_of_origin(tmp_path)
    manual_overrides_path = _write_manual_overrides(tmp_path)
    output_dir = tmp_path / "silver"

    result = sr.classify_regional_genres(
        genre_classification_path, indigenous_to_path, country_of_origin_path, manual_overrides_path, output_dir
    )

    df = pl.read_parquet(result)
    mezwed_row = df.filter(pl.col("item_id") == "Q4118941")
    assert mezwed_row.height == 1
    assert mezwed_row.row(0, named=True)["parent_id"] == "Q2579987"
    assert mezwed_row.row(0, named=True)["parent_label"] == "music of Portugal"
    assert mezwed_row.row(0, named=True)["relation_type"] == "manual_override_parent"
    assert mezwed_row.row(0, named=True)["is_regional"] is True
    assert mezwed_row.row(0, named=True)["regional_reason"] == "manual_override"


def test_classify_regional_genres_requires_overview_item_id_column(tmp_path: Path) -> None:
    genre_classification_path = _write_genre_classification(tmp_path)
    indigenous_to_path = _write_indigenous_to(tmp_path)
    country_of_origin_path = _write_country_of_origin(tmp_path)
    manual_overrides_path = tmp_path / "manual_regional_overrides.csv"
    pl.DataFrame({"item_id": ["Q4118941"], "item_label": ["mezwed"], "reason": ["test override"]}).write_csv(
        manual_overrides_path
    )
    output_dir = tmp_path / "silver"

    with pytest.raises(ValueError, match="overview_item_id"):
        sr.classify_regional_genres(
            genre_classification_path, indigenous_to_path, country_of_origin_path, manual_overrides_path, output_dir
        )


def test_classify_regional_genres_requires_overview_item_id_value(tmp_path: Path) -> None:
    genre_classification_path = _write_genre_classification(tmp_path)
    indigenous_to_path = _write_indigenous_to(tmp_path)
    country_of_origin_path = _write_country_of_origin(tmp_path)
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
        sr.classify_regional_genres(
            genre_classification_path, indigenous_to_path, country_of_origin_path, manual_overrides_path, output_dir
        )


def test_classify_regional_genres_creates_output_dir(tmp_path: Path) -> None:
    genre_classification_path = _write_genre_classification(tmp_path)
    indigenous_to_path = _write_indigenous_to(tmp_path)
    country_of_origin_path = _write_country_of_origin(tmp_path)
    manual_overrides_path = _write_manual_overrides(tmp_path)
    output_dir = tmp_path / "does" / "not" / "exist"

    sr.classify_regional_genres(
        genre_classification_path, indigenous_to_path, country_of_origin_path, manual_overrides_path, output_dir
    )

    assert output_dir.is_dir()
