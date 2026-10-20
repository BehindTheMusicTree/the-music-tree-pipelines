import polars as pl
import pytest

from gold.genre_tree_builder import build_genre_tree

TREE_ROWS = [
    {"item_id": "Q1", "item_label": "rock", "parent_id": None},
    {"item_id": "Q2", "item_label": "punk rock", "parent_id": "Q1"},
    {"item_id": "Q3", "item_label": "hardcore punk", "parent_id": "Q2"},
    {"item_id": "Q4", "item_label": "pop rock", "parent_id": "Q1"},
    {"item_id": "Q5", "item_label": "jazz", "parent_id": None},
]


def _hierarchy() -> pl.DataFrame:
    return pl.DataFrame(TREE_ROWS)


def test_build_genre_tree_nests_children_under_parent() -> None:
    tree = build_genre_tree(_hierarchy())

    rock = next(node for node in tree["tree"] if node["name"] == "rock")
    child_names = {child["name"] for child in rock["children"]}
    assert child_names == {"punk rock", "pop rock"}

    punk_rock = next(child for child in rock["children"] if child["name"] == "punk rock")
    assert punk_rock["children"] == [{"id": "Q3", "name": "hardcore punk", "children": []}]


def test_build_genre_tree_handles_multiple_roots() -> None:
    tree = build_genre_tree(_hierarchy())

    root_names = {node["name"] for node in tree["tree"]}
    assert root_names == {"rock", "jazz"}


def test_build_genre_tree_marks_pop_side_on_direct_child_only() -> None:
    tree = build_genre_tree(_hierarchy(), pop_sides={"rock": {"pop rock"}})

    rock = next(node for node in tree["tree"] if node["name"] == "rock")
    pop_rock = next(child for child in rock["children"] if child["name"] == "pop rock")
    punk_rock = next(child for child in rock["children"] if child["name"] == "punk rock")
    assert pop_rock["side"] == "pop"
    assert "side" not in punk_rock
    assert "side" not in punk_rock["children"][0]  # hardcore punk, a grandchild


def test_build_genre_tree_marks_multiple_pop_sides_on_same_root() -> None:
    rows = [*TREE_ROWS, {"item_id": "Q6", "item_label": "soft rock", "parent_id": "Q1"}]
    tree = build_genre_tree(pl.DataFrame(rows), pop_sides={"rock": {"pop rock", "soft rock"}})

    rock = next(node for node in tree["tree"] if node["name"] == "rock")
    sides = {child["name"]: child.get("side") for child in rock["children"]}
    assert sides == {"punk rock": None, "pop rock": "pop", "soft rock": "pop"}


def test_build_genre_tree_ignores_pop_side_for_root_without_entry() -> None:
    tree = build_genre_tree(_hierarchy(), pop_sides={"rock": {"pop rock"}})

    jazz = next(node for node in tree["tree"] if node["name"] == "jazz")
    assert "side" not in jazz


def test_build_genre_tree_raises_on_empty_hierarchy() -> None:
    with pytest.raises(ValueError, match="is empty"):
        build_genre_tree(pl.DataFrame({"item_id": [], "item_label": [], "parent_id": []}))


def test_build_genre_tree_raises_on_null_item_id() -> None:
    rows = [*TREE_ROWS, {"item_id": None, "item_label": "orphan", "parent_id": None}]
    with pytest.raises(ValueError, match="null rate"):
        build_genre_tree(pl.DataFrame(rows))


def test_build_genre_tree_raises_on_duplicate_item_id() -> None:
    rows = [*TREE_ROWS, {"item_id": "Q1", "item_label": "rock duplicate", "parent_id": None}]
    with pytest.raises(ValueError, match="not unique"):
        build_genre_tree(pl.DataFrame(rows))


def test_build_genre_tree_uses_display_label_for_name_but_real_label_for_pop_sides() -> None:
    rows = [
        {"item_id": "Q1", "item_label": "popular music", "item_display_label": "Mainstream Pop", "parent_id": None},
        {"item_id": "Q2", "item_label": "pop rock", "item_display_label": None, "parent_id": "Q1"},
    ]
    tree = build_genre_tree(pl.DataFrame(rows), pop_sides={"popular music": {"pop rock"}})

    root = tree["tree"][0]
    assert root["name"] == "Mainstream Pop"
    pop_rock = root["children"][0]
    assert pop_rock["name"] == "pop rock"
    assert pop_rock["side"] == "pop"


def test_build_genre_tree_raises_on_duplicate_node_name() -> None:
    rows = [
        {"item_id": "Q1", "item_label": "rock", "parent_id": None},
        {"item_id": "Q2", "item_label": "electro", "parent_id": "Q1"},
        {"item_id": "Q3", "item_label": "electro", "parent_id": "Q1"},
    ]
    with pytest.raises(ValueError, match=r"duplicate node name.*electro"):
        build_genre_tree(pl.DataFrame(rows))


def test_build_genre_tree_raises_on_duplicate_node_name_case_insensitive() -> None:
    rows = [
        {"item_id": "Q1", "item_label": "rock", "parent_id": None},
        {"item_id": "Q2", "item_label": "Pop reggae", "parent_id": "Q1"},
        {"item_id": "Q3", "item_label": "pop reggae", "parent_id": "Q1"},
    ]
    with pytest.raises(ValueError, match=r"duplicate node name.*[Pp]op reggae"):
        build_genre_tree(pl.DataFrame(rows))


def test_build_genre_tree_raises_on_parent_id_cycle() -> None:
    # Q6 <-> Q7 point at each other: both have a known parent, so neither is a root, and neither is
    # reachable from a real root — they'd silently vanish from the tree without the node-count check.
    rows = [
        {"item_id": "Q6", "item_label": "cycle a", "parent_id": "Q7"},
        {"item_id": "Q7", "item_label": "cycle b", "parent_id": "Q6"},
    ]
    with pytest.raises(ValueError, match="genre tree build"):
        build_genre_tree(pl.DataFrame(rows))


def test_build_genre_tree_emits_extra_parents() -> None:
    tree = build_genre_tree(
        _hierarchy(), primary_parents={"Q3": ["Q4", "Q100"]}, secondary_parents={"Q5": ["Q1"]}, external_ids={"Q100"}
    )

    rock = next(node for node in tree["tree"] if node["name"] == "rock")
    hardcore_punk = rock["children"][0]["children"][0]
    assert hardcore_punk["primaryParents"] == ["Q4", "Q100"]
    jazz = next(node for node in tree["tree"] if node["name"] == "jazz")
    assert jazz["secondaryParents"] == ["Q1"]
    assert "primaryParents" not in rock


def test_build_genre_tree_raises_on_unknown_parent_ref() -> None:
    with pytest.raises(ValueError, match=r"secondaryParents.*Q100"):
        build_genre_tree(_hierarchy(), secondary_parents={"Q3": ["Q100"]})


def test_build_genre_tree_raises_on_unknown_extra_parent_item() -> None:
    with pytest.raises(ValueError, match=r"primaryParents.*Q100"):
        build_genre_tree(_hierarchy(), primary_parents={"Q100": ["Q1"]})


WIKI = "https://en.wikipedia.org/wiki/Jazz"


def _jazz(descriptions: dict[str, dict[str, str | None]], summaries: dict[str, str | None] | None = None) -> dict:
    tree = build_genre_tree(_hierarchy(), summaries=summaries, descriptions=descriptions)
    return next(node for node in tree["tree"] if node["id"] == "Q5")


def test_build_genre_tree_emits_description_and_wikipedia_summary() -> None:
    node = _jazz({"Q5": {"description": "music genre", "wikipedia_url": WIKI, "wikipedia_intro": "Jazz is..."}})

    assert node["description"] == "music genre"
    assert node["summary"] == "Jazz is..."
    assert node["summarySourceUrl"] == WIKI


def test_build_genre_tree_manual_summary_beats_wikipedia_intro_without_attribution() -> None:
    node = _jazz(
        {"Q5": {"description": None, "wikipedia_url": WIKI, "wikipedia_intro": "Jazz is..."}},
        summaries={"Q5": "Curated."},
    )

    assert node["summary"] == "Curated."
    assert "summarySourceUrl" not in node
    assert "description" not in node


@pytest.mark.parametrize("override", [None, "", "  "])
def test_build_genre_tree_blank_manual_summary_suppresses_wikipedia_intro(override: str | None) -> None:
    node = _jazz(
        {"Q5": {"description": "music genre", "wikipedia_url": WIKI, "wikipedia_intro": "Jazz is..."}},
        summaries={"Q5": override},
    )

    assert "summary" not in node
    assert "summarySourceUrl" not in node
    assert node["description"] == "music genre"


def test_build_genre_tree_without_intro_emits_no_summary() -> None:
    node = _jazz({"Q5": {"description": "music genre", "wikipedia_url": None, "wikipedia_intro": None}})

    assert "summary" not in node
    assert "summarySourceUrl" not in node
