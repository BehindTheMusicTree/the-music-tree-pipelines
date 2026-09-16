from pathlib import Path

import polars as pl
import pytest

from wikidata.silver import non_genre_pruning as ngp


def _write_manual_theme_genres(tmp_path: Path, rows: list[dict] | None = None) -> Path:
    path = tmp_path / "manual_theme_genres.csv"
    schema = {"item_id": pl.Utf8, "item_label": pl.Utf8, "reason": pl.Utf8}
    pl.DataFrame(rows or [], schema=schema).write_csv(path)
    return path


def _write_manual_technique_genres(tmp_path: Path, rows: list[dict] | None = None) -> Path:
    path = tmp_path / "manual_technique_genres.csv"
    schema = {"item_id": pl.Utf8, "item_label": pl.Utf8, "reason": pl.Utf8}
    pl.DataFrame(rows or [], schema=schema).write_csv(path)
    return path


def _write_manual_out_of_scope_genres(tmp_path: Path, rows: list[dict] | None = None) -> Path:
    path = tmp_path / "manual_out_of_scope_genres.csv"
    schema = {"item_id": pl.Utf8, "item_label": pl.Utf8, "reason": pl.Utf8}
    pl.DataFrame(rows or [], schema=schema).write_csv(path)
    return path


def _write_manual_umbrella_canonical_genres(tmp_path: Path, rows: list[dict] | None = None) -> Path:
    path = tmp_path / "manual_umbrella_canonical_genres.csv"
    schema = {"item_id": pl.Utf8, "item_label": pl.Utf8, "reason": pl.Utf8}
    pl.DataFrame(rows or [], schema=schema).write_csv(path)
    return path


def _write_manual_duplicate_genres(tmp_path: Path, rows: list[dict] | None = None) -> Path:
    path = tmp_path / "manual_duplicate_genres.csv"
    schema = {"item_id": pl.Utf8, "item_label": pl.Utf8, "reason": pl.Utf8}
    pl.DataFrame(rows or [], schema=schema).write_csv(path)
    return path


def _write_genre_classification(tmp_path: Path) -> Path:
    genre_classification_path = tmp_path / "1_item_links.parquet"
    pl.DataFrame(
        [
            # music of X: the seed
            {
                "item_id": "Q1",
                "item_label": "music of X",
                "parent_id": None,
                "parent_label": None,
                "relation_type": None,
                "item_url": "https://www.wikidata.org/wiki/Q1",
                "parent_url": None,
                "is_regional_overview": True,
                "classification_reason": "regional_overview",
            },
            # a mistagged item, direct child of the seed — dropped in the tests below
            {
                "item_id": "Q2",
                "item_label": "mistagged item",
                "parent_id": "Q1",
                "parent_label": "music of X",
                "relation_type": "P279",
                "item_url": "https://www.wikidata.org/wiki/Q2",
                "parent_url": "https://www.wikidata.org/wiki/Q1",
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
        ]
    ).write_parquet(genre_classification_path)
    return genre_classification_path


def _prune_non_genre_items(
    tmp_path: Path,
    genre_classification_path: Path,
    output_dir: Path,
    theme_rows: list[dict] | None = None,
    technique_rows: list[dict] | None = None,
    out_of_scope_rows: list[dict] | None = None,
    umbrella_rows: list[dict] | None = None,
    duplicate_rows: list[dict] | None = None,
) -> Path:
    manual_theme_genres_path = _write_manual_theme_genres(tmp_path, theme_rows)
    manual_technique_genres_path = _write_manual_technique_genres(tmp_path, technique_rows)
    manual_out_of_scope_genres_path = _write_manual_out_of_scope_genres(tmp_path, out_of_scope_rows)
    manual_umbrella_canonical_genres_path = _write_manual_umbrella_canonical_genres(tmp_path, umbrella_rows)
    manual_duplicate_genres_path = _write_manual_duplicate_genres(tmp_path, duplicate_rows)
    return ngp.prune_non_genre_items(
        genre_classification_path,
        manual_theme_genres_path,
        manual_technique_genres_path,
        manual_out_of_scope_genres_path,
        manual_umbrella_canonical_genres_path,
        manual_duplicate_genres_path,
        output_dir,
    )


def test_prune_non_genre_items_drops_theme_items(tmp_path: Path) -> None:
    genre_classification_path = _write_genre_classification(tmp_path)
    output_dir = tmp_path / "silver"

    result = _prune_non_genre_items(
        tmp_path,
        genre_classification_path,
        output_dir,
        theme_rows=[{"item_id": "Q2", "item_label": "mistagged item", "reason": "test"}],
    )

    assert result == output_dir / "2_non_genre_pruning.parquet"
    df = pl.read_parquet(result)
    assert "Q2" not in set(df.select("item_id").unique().to_series())
    assert {"Q1", "Q9778"} == set(df.select("item_id").unique().to_series())


def test_prune_non_genre_items_drops_technique_items(tmp_path: Path) -> None:
    genre_classification_path = _write_genre_classification(tmp_path)
    output_dir = tmp_path / "silver"

    result = _prune_non_genre_items(
        tmp_path,
        genre_classification_path,
        output_dir,
        technique_rows=[{"item_id": "Q2", "item_label": "mistagged item", "reason": "test"}],
    )

    df = pl.read_parquet(result)
    assert "Q2" not in set(df.select("item_id").unique().to_series())


def test_prune_non_genre_items_drops_out_of_scope_items(tmp_path: Path) -> None:
    genre_classification_path = _write_genre_classification(tmp_path)
    output_dir = tmp_path / "silver"

    result = _prune_non_genre_items(
        tmp_path,
        genre_classification_path,
        output_dir,
        out_of_scope_rows=[{"item_id": "Q2", "item_label": "mistagged item", "reason": "test"}],
    )

    df = pl.read_parquet(result)
    assert "Q2" not in set(df.select("item_id").unique().to_series())


def test_prune_non_genre_items_drops_umbrella_canonical_items(tmp_path: Path) -> None:
    genre_classification_path = _write_genre_classification(tmp_path)
    output_dir = tmp_path / "silver"

    result = _prune_non_genre_items(
        tmp_path,
        genre_classification_path,
        output_dir,
        umbrella_rows=[{"item_id": "Q2", "item_label": "mistagged item", "reason": "test"}],
    )

    df = pl.read_parquet(result)
    assert "Q2" not in set(df.select("item_id").unique().to_series())


def test_prune_non_genre_items_drops_duplicate_genre_items(tmp_path: Path) -> None:
    genre_classification_path = _write_genre_classification(tmp_path)
    output_dir = tmp_path / "silver"

    result = _prune_non_genre_items(
        tmp_path,
        genre_classification_path,
        output_dir,
        duplicate_rows=[{"item_id": "Q2", "item_label": "mistagged item", "reason": "test"}],
    )

    df = pl.read_parquet(result)
    assert "Q2" not in set(df.select("item_id").unique().to_series())


def test_prune_non_genre_items_raises_on_unknown_duplicate_item_id(tmp_path: Path) -> None:
    genre_classification_path = _write_genre_classification(tmp_path)
    output_dir = tmp_path / "silver"

    with pytest.raises(ValueError, match="Q0000000"):
        _prune_non_genre_items(
            tmp_path,
            genre_classification_path,
            output_dir,
            duplicate_rows=[{"item_id": "Q0000000", "item_label": "not in the tree", "reason": "test"}],
        )


def test_prune_non_genre_items_raises_on_duplicate_duplicate_item_id(tmp_path: Path) -> None:
    genre_classification_path = _write_genre_classification(tmp_path)
    output_dir = tmp_path / "silver"

    with pytest.raises(ValueError, match="duplicate"):
        _prune_non_genre_items(
            tmp_path,
            genre_classification_path,
            output_dir,
            duplicate_rows=[
                {"item_id": "Q9778", "item_label": "popular music", "reason": "test"},
                {"item_id": "Q9778", "item_label": "popular music", "reason": "test duplicate"},
            ],
        )


def test_prune_non_genre_items_raises_on_unknown_umbrella_item_id(tmp_path: Path) -> None:
    genre_classification_path = _write_genre_classification(tmp_path)
    output_dir = tmp_path / "silver"

    with pytest.raises(ValueError, match="Q0000000"):
        _prune_non_genre_items(
            tmp_path,
            genre_classification_path,
            output_dir,
            umbrella_rows=[{"item_id": "Q0000000", "item_label": "not in the tree", "reason": "test"}],
        )


def test_prune_non_genre_items_raises_on_duplicate_umbrella_item_id(tmp_path: Path) -> None:
    genre_classification_path = _write_genre_classification(tmp_path)
    output_dir = tmp_path / "silver"

    with pytest.raises(ValueError, match="duplicate"):
        _prune_non_genre_items(
            tmp_path,
            genre_classification_path,
            output_dir,
            umbrella_rows=[
                {"item_id": "Q9778", "item_label": "popular music", "reason": "test"},
                {"item_id": "Q9778", "item_label": "popular music", "reason": "test duplicate"},
            ],
        )


def test_prune_non_genre_items_raises_on_unknown_theme_item_id(tmp_path: Path) -> None:
    genre_classification_path = _write_genre_classification(tmp_path)
    output_dir = tmp_path / "silver"

    with pytest.raises(ValueError, match="Q0000000"):
        _prune_non_genre_items(
            tmp_path,
            genre_classification_path,
            output_dir,
            theme_rows=[{"item_id": "Q0000000", "item_label": "not in the tree", "reason": "test"}],
        )


def test_prune_non_genre_items_raises_on_blank_theme_item_id(tmp_path: Path) -> None:
    genre_classification_path = _write_genre_classification(tmp_path)
    output_dir = tmp_path / "silver"

    with pytest.raises(ValueError, match="null/blank"):
        _prune_non_genre_items(
            tmp_path,
            genre_classification_path,
            output_dir,
            theme_rows=[{"item_id": "", "item_label": "blank id", "reason": "test"}],
        )


def test_prune_non_genre_items_raises_on_duplicate_theme_item_id(tmp_path: Path) -> None:
    genre_classification_path = _write_genre_classification(tmp_path)
    output_dir = tmp_path / "silver"

    with pytest.raises(ValueError, match="duplicate"):
        _prune_non_genre_items(
            tmp_path,
            genre_classification_path,
            output_dir,
            theme_rows=[
                {"item_id": "Q9778", "item_label": "popular music", "reason": "test"},
                {"item_id": "Q9778", "item_label": "popular music", "reason": "test duplicate"},
            ],
        )


def test_prune_non_genre_items_raises_on_unknown_technique_item_id(tmp_path: Path) -> None:
    genre_classification_path = _write_genre_classification(tmp_path)
    output_dir = tmp_path / "silver"

    with pytest.raises(ValueError, match="Q0000000"):
        _prune_non_genre_items(
            tmp_path,
            genre_classification_path,
            output_dir,
            technique_rows=[{"item_id": "Q0000000", "item_label": "not in the tree", "reason": "test"}],
        )


def test_prune_non_genre_items_raises_on_duplicate_technique_item_id(tmp_path: Path) -> None:
    genre_classification_path = _write_genre_classification(tmp_path)
    output_dir = tmp_path / "silver"

    with pytest.raises(ValueError, match="duplicate"):
        _prune_non_genre_items(
            tmp_path,
            genre_classification_path,
            output_dir,
            technique_rows=[
                {"item_id": "Q9778", "item_label": "popular music", "reason": "test"},
                {"item_id": "Q9778", "item_label": "popular music", "reason": "test duplicate"},
            ],
        )


def test_prune_non_genre_items_raises_on_unknown_out_of_scope_item_id(tmp_path: Path) -> None:
    genre_classification_path = _write_genre_classification(tmp_path)
    output_dir = tmp_path / "silver"

    with pytest.raises(ValueError, match="Q0000000"):
        _prune_non_genre_items(
            tmp_path,
            genre_classification_path,
            output_dir,
            out_of_scope_rows=[{"item_id": "Q0000000", "item_label": "not in the tree", "reason": "test"}],
        )


def test_prune_non_genre_items_raises_on_duplicate_out_of_scope_item_id(tmp_path: Path) -> None:
    genre_classification_path = _write_genre_classification(tmp_path)
    output_dir = tmp_path / "silver"

    with pytest.raises(ValueError, match="duplicate"):
        _prune_non_genre_items(
            tmp_path,
            genre_classification_path,
            output_dir,
            out_of_scope_rows=[
                {"item_id": "Q9778", "item_label": "popular music", "reason": "test"},
                {"item_id": "Q9778", "item_label": "popular music", "reason": "test duplicate"},
            ],
        )


def test_prune_non_genre_items_creates_output_dir(tmp_path: Path) -> None:
    genre_classification_path = _write_genre_classification(tmp_path)
    output_dir = tmp_path / "does" / "not" / "exist"

    _prune_non_genre_items(tmp_path, genre_classification_path, output_dir)

    assert output_dir.is_dir()
