import polars as pl
import pytest
from geohistory.silver.curation import LISTS
from geohistory.silver.entity_footprint import entity_footprint
from geohistory.silver.entity_membership import _assert_acyclic
from geohistory.silver.genealogy_lane import _lift_admin_anchors
from geohistory.silver.place_containment import place_containment
from geohistory.silver.tradition import _inherited_start

ENTITIES = [
    ("fr", "country", "France"),
    ("bre", "admin_unit", "Bretagne"),
    ("mor", "admin_unit", "Morbihan"),
    ("loi", "admin_unit", "Loire-Atlantique"),
    ("q327", "historical_region", "Brittany (old)"),
    ("es", "country", "Spain"),
    ("cat", "admin_unit", "Catalonia"),
    ("old", "historical_country", "Old France"),
    ("pen", "macro_region", "Peninsula"),
    ("kr", "country", "Korea"),
    ("lang", "language", "Language"),
]
CROSSWALK = [(e, f"Q{e}") for e, kind, _ in ENTITIES if kind != "language"]
CLAIM_SCHEMA = {"item_id": pl.String, "property": pl.String, "value": pl.String, "time_precision": pl.Int8}


def write_curation(curation, **rows):
    curation.mkdir()
    for list_name, columns in LISTS.items():
        lines = [",".join(columns), *(",".join(row) for row in rows.get(list_name, []))]
        (curation / f"manual_{list_name}.csv").write_text("\n".join(lines) + "\n")
    return curation


def hierarchy_row(item, prop, value):
    return (item, prop, value, None)


def build(tmp_path, hierarchy=(), claims=(), membership=(), overrides=(), ne_map=()):
    wikidata, silver = tmp_path / "wikidata", tmp_path / "silver"
    wikidata.mkdir()
    silver.mkdir()
    curation = write_curation(tmp_path / "curation", entity_membership_overrides=list(overrides))
    pl.DataFrame(
        [(e, k, n) for e, k, n in ENTITIES], schema=["entity_id", "kind", "label"], orient="row"
    ).write_parquet(silver / "1_cultural_entity.parquet")
    pl.DataFrame(CROSSWALK, schema=["entity_id", "wikidata_id"], orient="row").write_parquet(
        silver / "1_entity_wikidata_item.parquet"
    )
    pl.DataFrame(
        list(ne_map),
        schema={"ne_id": pl.String, "level": pl.Int8, "entity_id": pl.String, "country_entity_id": pl.String},
        orient="row",
    ).write_parquet(silver / "1_entity_ne_admin.parquet")
    pl.DataFrame(
        list(membership), schema={"child_id": pl.String, "parent_id": pl.String, "source": pl.String}, orient="row"
    ).write_parquet(silver / "2_entity_membership.parquet")
    pl.DataFrame(
        list(hierarchy),
        schema={"item_id": pl.String, "property": pl.String, "value": pl.String, "label": pl.String},
        orient="row",
    ).write_parquet(wikidata / "wikidata_admin_hierarchy.parquet")
    pl.DataFrame(list(claims), schema=CLAIM_SCHEMA, orient="row").write_parquet(
        wikidata / "wikidata_cultural_entity_claim.parquet"
    )
    place_containment(wikidata, silver, curation)
    return silver


def edges(silver):
    return set(pl.read_parquet(silver / "2_place_containment.parquet").rows())


def test_containment_sources_priority_and_p131_chain(tmp_path):
    silver = build(
        tmp_path,
        hierarchy=[
            hierarchy_row("Qmor", "P131", "Qmid"),
            hierarchy_row("Qmid", "P131", "Qbre"),
            hierarchy_row("Qbre", "P131", "Qfr"),
            hierarchy_row("Qcat", "P361", "Qes"),
            hierarchy_row("Qloi", "P361", "Qbre"),
        ],
        claims=[("Qkr", "P30", "Qpen", None), ("Qkr", "P706", "Qes", None), ("Qloi", "P706", "Qbre", None)],
        membership=[("lang", "fr", "p361"), ("q327", "fr", "p361")],
    )
    rows = {(p, c): s for p, c, s in pl.read_parquet(silver / "2_place_containment.parquet").rows()}
    assert rows[("bre", "mor")] == "p131" and rows[("fr", "bre")] == "p131"
    assert rows[("es", "cat")] == "p361" and rows[("bre", "loi")] == "p361"
    assert rows[("pen", "kr")] == "p30" and rows[("es", "kr")] == "p706"
    assert ("fr", "lang") not in rows and ("fr", "q327") in rows


def test_mutual_claims_lose_to_the_higher_priority_source_and_ties_drop_both(tmp_path):
    silver = build(
        tmp_path,
        hierarchy=[
            hierarchy_row("Qes", "P131", "Qcat"),
            hierarchy_row("Qcat", "P131", "Qes"),
            hierarchy_row("Qkr", "P361", "Qpen"),
        ],
        claims=[("Qpen", "P706", "Qkr", None)],
    )
    found = edges(silver)
    assert ("pen", "kr", "p361") in found and ("kr", "pen", "p706") not in found
    assert not {("es", "cat", "p131"), ("cat", "es", "p131")} & found


def test_natural_earth_fallback_adds_missing_country_links_only(tmp_path):
    silver = build(
        tmp_path,
        hierarchy=[hierarchy_row("Qmor", "P131", "Qbre")],
        ne_map=[("a", 1, "mor", "fr"), ("b", 1, "bre", "fr"), ("c", 1, "fr", "fr"), ("d", 1, None, "fr")],
    )
    assert edges(silver) == {("bre", "mor", "p131"), ("fr", "bre", "natural_earth")}


def test_membership_override_removes_a_hierarchy_p361_edge(tmp_path):
    silver = build(
        tmp_path,
        hierarchy=[hierarchy_row("Qcat", "P361", "Qes")],
        overrides=[("cat", "es", "remove", "x")],
    )
    assert edges(silver) == set()


def test_longer_containment_cycle_raises_naming_the_edges(tmp_path):
    with pytest.raises(ValueError, match="2_place_containment.*fr.*bre"):
        build(
            tmp_path,
            hierarchy=[
                hierarchy_row("Qbre", "P131", "Qfr"),
                hierarchy_row("Qmor", "P131", "Qbre"),
                hierarchy_row("Qfr", "P131", "Qmor"),
            ],
        )


def test_assert_acyclic_names_the_offending_edges():
    with pytest.raises(ValueError, match="x.*y"):
        _assert_acyclic(pl.DataFrame({"parent_id": ["x", "y"], "child_id": ["y", "x"]}), "demo")


def footprint(tmp_path, containment, ne_ids, succession=()):
    silver = tmp_path / "fp"
    silver.mkdir()
    pl.DataFrame(
        [(e, k, n) for e, k, n in ENTITIES], schema=["entity_id", "kind", "label"], orient="row"
    ).write_parquet(silver / "1_cultural_entity.parquet")
    pl.DataFrame(
        [(f"ne{i}", 1, e, "fr") for i, e in enumerate(ne_ids)],
        schema={"ne_id": pl.String, "level": pl.Int8, "entity_id": pl.String, "country_entity_id": pl.String},
        orient="row",
    ).write_parquet(silver / "1_entity_ne_admin.parquet")
    pl.DataFrame(list(containment), schema=["parent_id", "child_id", "source"], orient="row").write_parquet(
        silver / "2_place_containment.parquet"
    )
    pl.DataFrame(list(succession), schema=["predecessor_id", "successor_id", "source"], orient="row").write_parquet(
        silver / "3_entity_succession.parquet"
    )
    entity_footprint(silver)
    return {(e, a): r for e, a, r in pl.read_parquet(silver / "8_entity_footprint.parquet").rows()}


def test_footprint_self_cover_and_succession(tmp_path):
    found = footprint(
        tmp_path,
        containment=[("q327", "bre", "p131"), ("q327", "loi", "p131"), ("fr", "bre", "p131"), ("bre", "mor", "p131")],
        ne_ids=["bre", "loi", "es"],
        succession=[("old", "fr", "P1366")],
    )
    assert found[("bre", "bre")] == "self" and found[("es", "es")] == "self"
    assert found[("q327", "bre")] == found[("q327", "loi")] == "cover"
    assert found[("fr", "bre")] == "cover"
    assert found[("old", "bre")] == "succession"
    assert not any(entity == "cat" for entity, _ in found)


def test_footprint_collapses_non_ne_admin_when_all_children_are_covered(tmp_path):
    found = footprint(
        tmp_path,
        containment=[("mor", "bre", "p131"), ("mor", "loi", "p131")],
        ne_ids=["bre", "loi"],
    )
    assert found[("mor", "mor")] == "self"


def test_lift_admin_anchors_stand_in_as_their_nearest_non_admin_place():
    entities = pl.DataFrame([(e, k) for e, k, _ in ENTITIES], schema=["entity_id", "kind"], orient="row")
    containment = pl.DataFrame(
        [("bre", "mor", "p131"), ("fr", "bre", "p131")], schema=["parent_id", "child_id", "source"], orient="row"
    )
    traditions = pl.DataFrame({"anchor_id": ["mor", "es", "cat"]})
    assert _lift_admin_anchors(traditions, entities, containment)["anchor_id"].to_list() == ["fr", "es", "cat"]


def test_inherited_start_takes_the_nearest_dated_container():
    spans = {"fr": (500, None), "pen": (900, None), "bre": (None, None)}
    parents = {"mor": ["bre"], "bre": ["fr", "pen"]}
    assert _inherited_start("mor", spans, parents) == 500
    assert _inherited_start("kr", spans, parents) is None
