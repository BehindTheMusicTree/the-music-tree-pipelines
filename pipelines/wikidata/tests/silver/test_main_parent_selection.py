from pathlib import Path

import polars as pl

from wikidata.silver import main_parent_selection as sg

REGIONAL_CLASSIFICATION_ROWS = [
    # toypop: two candidate parents, no manual override — auto fallback picks the lowest QID
    # (Q9778, popular music) over the higher one (Q131578, J-pop).
    {
        "item_id": "Q133687529",
        "item_label": "toypop",
        "parent_id": "Q9778",
        "parent_label": "popular music",
        "item_url": "https://www.wikidata.org/wiki/Q133687529",
        "parent_url": "https://www.wikidata.org/wiki/Q9778",
        "relation_type": "P279",
    },
    {
        "item_id": "Q133687529",
        "item_label": "toypop",
        "parent_id": "Q131578",
        "parent_label": "J-pop",
        "item_url": "https://www.wikidata.org/wiki/Q133687529",
        "parent_url": "https://www.wikidata.org/wiki/Q131578",
        "relation_type": "P279",
    },
    # rock music: single candidate parent, no override — stays as-is, no secondary edge produced.
    {
        "item_id": "Q11399",
        "item_label": "rock music",
        "parent_id": "Q9778",
        "parent_label": "popular music",
        "item_url": "https://www.wikidata.org/wiki/Q11399",
        "parent_url": "https://www.wikidata.org/wiki/Q9778",
        "relation_type": "P279",
    },
    # popular music: root item, no parent.
    {
        "item_id": "Q9778",
        "item_label": "popular music",
        "parent_id": None,
        "parent_label": None,
        "item_url": "https://www.wikidata.org/wiki/Q9778",
        "parent_url": None,
        "relation_type": None,
    },
]


def _write_regional_classification(tmp_path: Path, rows: list[dict] | None = None) -> Path:
    regional_classification_path = tmp_path / "4_regional_classification.parquet"
    rows = rows if rows is not None else REGIONAL_CLASSIFICATION_ROWS
    rows = [
        {**row, "item_display_label": row["item_label"], "parent_display_label": row["parent_label"]} for row in rows
    ]
    pl.DataFrame(rows).write_parquet(regional_classification_path)
    return regional_classification_path


def test_select_main_parents_falls_back_to_lowest_qid(tmp_path: Path) -> None:
    regional_classification_path = _write_regional_classification(tmp_path)
    output_dir = tmp_path / "silver"

    main_path, secondary_path = sg.select_main_parents(regional_classification_path, output_dir)

    assert main_path == output_dir / "5_main_parent_selection.parquet"
    assert secondary_path == output_dir / "5_secondary_parents.parquet"
    main_df = pl.read_parquet(main_path)
    toypop_row = main_df.filter(pl.col("item_id") == "Q133687529").row(0, named=True)
    assert toypop_row["parent_id"] == "Q9778"

    secondary_df = pl.read_parquet(secondary_path)
    assert secondary_df.select("item_id").to_series().to_list() == ["Q133687529"]
    assert secondary_df.row(0, named=True)["parent_id"] == "Q131578"


def test_select_main_parents_prefers_genre_item_candidate_over_lower_qid_non_genre(tmp_path: Path) -> None:
    # expressionist music: two candidate parents, no manual override. Q65937946 (modern classical
    # music) is itself a genre item in this dataset (has its own item_id row below) despite its
    # higher QID; Q159762 (Expressionism) is a lower-QID non-genre art-movement item present only as
    # a parent_label. The genre-item candidate must win despite the higher QID.
    rows = [
        {
            "item_id": "Q613707",
            "item_label": "expressionist music",
            "parent_id": "Q159762",
            "parent_label": "Expressionism",
            "item_url": "https://www.wikidata.org/wiki/Q613707",
            "parent_url": "https://www.wikidata.org/wiki/Q159762",
            "relation_type": "P279",
        },
        {
            "item_id": "Q613707",
            "item_label": "expressionist music",
            "parent_id": "Q65937946",
            "parent_label": "modern classical music",
            "item_url": "https://www.wikidata.org/wiki/Q613707",
            "parent_url": "https://www.wikidata.org/wiki/Q65937946",
            "relation_type": "P279",
        },
        {
            "item_id": "Q65937946",
            "item_label": "modern classical music",
            "parent_id": None,
            "parent_label": None,
            "item_url": "https://www.wikidata.org/wiki/Q65937946",
            "parent_url": None,
            "relation_type": None,
        },
    ]
    regional_classification_path = _write_regional_classification(tmp_path, rows)
    output_dir = tmp_path / "silver"

    main_path, secondary_path = sg.select_main_parents(regional_classification_path, output_dir)

    main_df = pl.read_parquet(main_path)
    main_row = main_df.filter(pl.col("item_id") == "Q613707").row(0, named=True)
    assert main_row["parent_id"] == "Q65937946"

    secondary_df = pl.read_parquet(secondary_path)
    secondary_row = secondary_df.filter(pl.col("item_id") == "Q613707").row(0, named=True)
    assert secondary_row["parent_id"] == "Q159762"


def test_select_main_parents_prefers_manual_override_over_lowest_qid(tmp_path: Path) -> None:
    rows = [*REGIONAL_CLASSIFICATION_ROWS]
    # toypop -> J-pop override, tagged manual_main_parent, coexisting with both automated candidates.
    rows.append(
        {
            "item_id": "Q133687529",
            "item_label": "toypop",
            "parent_id": "Q131578",
            "parent_label": "J-pop",
            "item_url": "https://www.wikidata.org/wiki/Q133687529",
            "parent_url": "https://www.wikidata.org/wiki/Q131578",
            "relation_type": "manual_main_parent",
        }
    )
    regional_classification_path = _write_regional_classification(tmp_path, rows)
    output_dir = tmp_path / "silver"

    main_path, secondary_path = sg.select_main_parents(regional_classification_path, output_dir)

    main_df = pl.read_parquet(main_path)
    toypop_rows = main_df.filter(pl.col("item_id") == "Q133687529")
    assert toypop_rows.height == 1
    assert toypop_rows.row(0, named=True)["parent_id"] == "Q131578"
    assert toypop_rows.row(0, named=True)["relation_type"] == "manual_main_parent"

    secondary_df = pl.read_parquet(secondary_path)
    secondary_item_parents = {
        (row["item_id"], row["parent_id"], row["relation_type"]) for row in secondary_df.to_dicts()
    }
    # toypop's auto lowest-QID candidate (popular music) becomes secondary since the override wins.
    # Its other candidate edge (P279 to J-pop) shares the exact (item_id, parent_id) pair the
    # override already selected as main, so it's redundant and isn't duplicated into secondary.
    assert secondary_item_parents == {("Q133687529", "Q9778", "P279")}


def test_select_main_parents_no_secondary_edges_for_single_parent_items(tmp_path: Path) -> None:
    regional_classification_path = _write_regional_classification(tmp_path)
    output_dir = tmp_path / "silver"

    _, secondary_path = sg.select_main_parents(regional_classification_path, output_dir)

    secondary_df = pl.read_parquet(secondary_path)
    assert "Q11399" not in secondary_df.select("item_id").to_series().to_list()


def test_select_main_parents_creates_output_dir(tmp_path: Path) -> None:
    regional_classification_path = _write_regional_classification(tmp_path)
    output_dir = tmp_path / "does" / "not" / "exist"

    sg.select_main_parents(regional_classification_path, output_dir)

    assert output_dir.is_dir()
