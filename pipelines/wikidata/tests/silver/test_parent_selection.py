from pathlib import Path

import polars as pl
import pytest

from wikidata.silver import parent_selection as ps

LABELS = {
    "Q9778": "popular music",
    "Q11399": "rock music",
    "Q131578": "J-pop",
    "Q133687529": "toypop",
    "Q159762": "Expressionism",
    "Q613707": "expressionist music",
    "Q65937946": "modern classical music",
    "Q1640319": "music of France",
    "Q3328676": "music of Europe",
    "Q1366009": "French rock",
    "Q1050379": "Breton music",
    "Q2903126": "Breton rock",
}
REGIONAL_IDS = {"Q1366009", "Q1050379", "Q2903126"}
OVERVIEW_IDS = {"Q1640319", "Q3328676"}


def _row(item_id: str, parent_id: str | None, relation_type: str | None = "P279", exclude: bool | None = None) -> dict:
    def url(qid: str | None) -> str | None:
        return None if qid is None else f"https://www.wikidata.org/wiki/{qid}"

    is_regional = item_id in REGIONAL_IDS or item_id in OVERVIEW_IDS
    return {
        "item_id": item_id,
        "item_label": LABELS[item_id],
        "item_display_label": LABELS[item_id],
        "item_url": url(item_id),
        "parent_id": parent_id,
        "parent_label": LABELS.get(parent_id),
        "parent_display_label": LABELS.get(parent_id),
        "parent_url": url(parent_id),
        "relation_type": None if parent_id is None else relation_type,
        "has_parent_label": parent_id is not None,
        "exclude_other_parents": exclude,
        "is_regional_overview": item_id in OVERVIEW_IDS,
        "is_regional": is_regional,
        "regional_reason": ("seed" if item_id in OVERVIEW_IDS else "direct") if is_regional else None,
    }


BASE_ROWS = [
    _row("Q9778", None),
    _row("Q11399", "Q9778"),  # rock music: a single parent, no secondary edge
    _row("Q131578", "Q9778"),
    # toypop: two canonical candidates, the lowest QID wins
    _row("Q133687529", "Q9778"),
    _row("Q133687529", "Q131578"),
    _row("Q3328676", None),
    _row("Q1640319", "Q3328676"),
    _row("Q1050379", "Q1640319"),
    _row("Q1366009", "Q1640319"),
    _row("Q1366009", "Q11399"),
]
# Breton rock: under rock music (canonical), French rock and Breton music (regional genres) and music of
# France (an overview, already reached through either regional genre parent)
BRETON_ROCK_ROWS = [
    _row("Q2903126", "Q11399"),
    _row("Q2903126", "Q1366009"),
    _row("Q2903126", "Q1050379"),
    _row("Q2903126", "Q1640319"),
]


def _select(tmp_path: Path, rows: list[dict], output_dir: Path | None = None) -> tuple[pl.DataFrame, pl.DataFrame]:
    path = tmp_path / "4_regional_classification.parquet"
    pl.DataFrame(rows).write_parquet(path)
    selection_path, secondary_path = ps.select_parents(path, output_dir or tmp_path / "silver")
    return pl.read_parquet(selection_path), pl.read_parquet(secondary_path)


def _item(selection: pl.DataFrame, item_id: str) -> dict:
    rows = selection.filter(pl.col("item_id") == item_id)
    assert rows.height == 1
    return rows.row(0, named=True)


def _secondary(secondary: pl.DataFrame, item_id: str) -> set[tuple[str, str]]:
    return set(secondary.filter(pl.col("item_id") == item_id).select("parent_id", "relation_type").rows())


def test_select_parents_writes_both_outputs(tmp_path: Path) -> None:
    path = tmp_path / "4_regional_classification.parquet"
    pl.DataFrame(BASE_ROWS).write_parquet(path)
    output_dir = tmp_path / "does" / "not" / "exist"

    selection_path, secondary_path = ps.select_parents(path, output_dir)

    assert selection_path == output_dir / "5_parent_selection.parquet"
    assert secondary_path == output_dir / "5_secondary_parents.parquet"
    assert pl.read_parquet(secondary_path).columns == ps.SECONDARY_PARENT_COLUMNS


def test_select_parents_falls_back_to_lowest_qid(tmp_path: Path) -> None:
    selection, secondary = _select(tmp_path, BASE_ROWS)

    toypop = _item(selection, "Q133687529")
    assert toypop["canonical_parent_id"] == toypop["parent_id"] == "Q9778"
    assert toypop["regional_genre_parent_id"] is None
    assert toypop["regional_overview_ids"] == []
    assert _secondary(secondary, "Q133687529") == {("Q131578", "P279")}
    assert _secondary(secondary, "Q11399") == set()


def test_select_parents_keeps_one_row_per_item_with_root_parentless(tmp_path: Path) -> None:
    selection, _ = _select(tmp_path, BASE_ROWS)

    assert selection.height == selection.get_column("item_id").n_unique()
    root = _item(selection, "Q9778")
    assert root["parent_id"] is None
    assert root["canonical_parent_id"] is None


def test_select_parents_prefers_genre_item_over_lower_qid_non_genre(tmp_path: Path) -> None:
    # Expressionism (Q159762) is an art movement, a parent outside the tree: modern classical music wins
    # despite its higher QID.
    rows = [_row("Q65937946", None), _row("Q613707", "Q159762"), _row("Q613707", "Q65937946")]

    selection, secondary = _select(tmp_path, rows)

    assert _item(selection, "Q613707")["canonical_parent_id"] == "Q65937946"
    assert _secondary(secondary, "Q613707") == {("Q159762", "P279")}


@pytest.mark.parametrize(("exclude", "expected_secondary"), [(False, {("Q9778", "P279")}), (True, set())])
def test_select_parents_prefers_manual_canonical_parent(
    tmp_path: Path, exclude: bool, expected_secondary: set[tuple[str, str]]
) -> None:
    rows = [*BASE_ROWS, _row("Q133687529", "Q131578", "manual_canonical_parent", exclude)]

    selection, secondary = _select(tmp_path, rows)

    toypop = _item(selection, "Q133687529")
    assert toypop["parent_id"] == "Q131578"
    assert toypop["relation_type"] == "manual_canonical_parent"
    assert toypop["exclude_other_parents"] is exclude
    # the Bronze edge to the same parent is not duplicated into secondary
    assert _secondary(secondary, "Q133687529") == expected_secondary


def test_select_parents_splits_regional_item_across_axes(tmp_path: Path) -> None:
    selection, secondary = _select(tmp_path, [*BASE_ROWS, *BRETON_ROCK_ROWS])

    breton_rock = _item(selection, "Q2903126")
    assert breton_rock["canonical_parent_id"] == "Q11399"
    assert breton_rock["regional_genre_parent_id"] == "Q1050379"
    # music of France is an ancestor of the regional genre parent, so it is not kept as an overview
    assert breton_rock["regional_overview_ids"] == []
    assert breton_rock["parent_id"] == "Q1050379"
    assert breton_rock["parent_label"] == "Breton music"
    assert _secondary(secondary, "Q2903126") == {("Q1366009", "P279")}


def test_select_parents_manual_regional_genre_parent_wins_its_axis(tmp_path: Path) -> None:
    rows = [*BASE_ROWS, *BRETON_ROCK_ROWS, _row("Q2903126", "Q1366009", "manual_regional_genre_parent")]

    selection, secondary = _select(tmp_path, rows)

    breton_rock = _item(selection, "Q2903126")
    assert breton_rock["regional_genre_parent_id"] == breton_rock["parent_id"] == "Q1366009"
    assert breton_rock["canonical_parent_id"] == "Q11399"
    assert _secondary(secondary, "Q2903126") == {("Q1050379", "P279")}


def test_select_parents_keeps_overviews_not_reached_through_another_parent(tmp_path: Path) -> None:
    # Breton music filed directly under both music of France and music of Europe: music of Europe is
    # an ancestor of music of France, so only music of France is kept.
    rows = [*BASE_ROWS, _row("Q1050379", "Q3328676")]

    selection, secondary = _select(tmp_path, rows)

    breton_music = _item(selection, "Q1050379")
    assert breton_music["regional_overview_ids"] == ["Q1640319"]
    assert breton_music["regional_genre_parent_id"] is None
    assert breton_music["parent_id"] == "Q1640319"
    assert _secondary(secondary, "Q1050379") == set()


def test_select_parents_keeps_several_unrelated_overviews_sorted(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setitem(LABELS, "Q1062222", "music of Brittany")
    monkeypatch.setattr(f"{__name__}.OVERVIEW_IDS", OVERVIEW_IDS | {"Q1062222"})
    rows = [*BASE_ROWS, _row("Q1062222", None), _row("Q1050379", "Q1062222")]

    selection, _ = _select(tmp_path, rows)

    breton_music = _item(selection, "Q1050379")
    assert breton_music["regional_overview_ids"] == ["Q1062222", "Q1640319"]
    assert breton_music["parent_id"] == "Q1062222"


def test_select_parents_exclusive_manual_overview_drops_other_overviews(tmp_path: Path) -> None:
    rows = [
        *BASE_ROWS,
        _row("Q1050379", "Q3328676", "manual_regional_overview", True),
    ]

    selection, _ = _select(tmp_path, rows)

    breton_music = _item(selection, "Q1050379")
    assert breton_music["regional_overview_ids"] == ["Q3328676"]
    assert breton_music["relation_type"] == "manual_regional_overview"


def test_select_parents_keeps_canonical_parent_of_regional_item_out_of_secondary(tmp_path: Path) -> None:
    selection, secondary = _select(tmp_path, BASE_ROWS)

    french_rock = _item(selection, "Q1366009")
    assert french_rock["canonical_parent_id"] == "Q11399"
    assert french_rock["regional_overview_ids"] == ["Q1640319"]
    assert french_rock["parent_id"] == "Q1640319"
    assert _secondary(secondary, "Q1366009") == set()
