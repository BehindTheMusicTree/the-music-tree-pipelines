from pathlib import Path

import polars as pl
import pytest
from geohistory.silver.curation import LISTS
from geohistory.silver.cultural_entity import cultural_entity, wikidata_points
from geohistory.silver.entity_membership import entity_membership
from geohistory.silver.entity_succession import entity_succession
from geohistory.silver.entity_time_span import entity_time_span

LANGUOID_SCHEMA = {
    "glottocode": pl.String,
    "name": pl.String,
    "level": pl.String,
    "category": pl.String,
    "classification": pl.String,
    "latitude": pl.Float64,
    "longitude": pl.Float64,
    "macroarea": pl.String,
    "first_year_of_documentation": pl.Int32,
    "last_year_of_documentation": pl.Int32,
}
CLAIM_SCHEMA = {"item_id": pl.String, "property": pl.String, "value": pl.String, "time_precision": pl.Int8}
NE_SCHEMA = {
    "ne_id": pl.String,
    "level": pl.Int8,
    "wikidata_id": pl.String,
    "adm0_a3": pl.String,
    "name": pl.String,
}
HIERARCHY_SCHEMA = {"item_id": pl.String, "property": pl.String, "value": pl.String, "label": pl.String}
ENTITY_SCHEMA = {"item_id": pl.String, "item_label": pl.String, "kind": pl.String, "glottocode": pl.String}


def languoid(glottocode, level="language", category="Spoken_L1_Language", classification=None, **extra):
    row = dict.fromkeys(LANGUOID_SCHEMA)
    row.update(glottocode=glottocode, name=glottocode.upper(), level=level, category=category)
    row.update(classification=classification, **extra)
    return row


def claim(item_id, property_, value, precision=None):
    return {"item_id": item_id, "property": property_, "value": value, "time_precision": precision}


def entity(item_id, kind, glottocode=None):
    return {"item_id": item_id, "item_label": f"label {item_id}", "kind": kind, "glottocode": glottocode}


@pytest.fixture
def dirs(tmp_path: Path):
    bronze, wikidata, silver = tmp_path / "bronze", tmp_path / "wikidata", tmp_path / "silver"
    bronze.mkdir()
    wikidata.mkdir()
    silver.mkdir()
    write_curation(tmp_path / "curation")
    return bronze, wikidata, silver


def write_curation(curation: Path, **rows: list[tuple[str, ...]]) -> Path:
    curation.mkdir(exist_ok=True)
    for list_name, columns in LISTS.items():
        lines = [",".join(columns), *(",".join(row) for row in rows.get(list_name, []))]
        (curation / f"manual_{list_name}.csv").write_text("\n".join(lines) + "\n")
    return curation


def write_admin(dirs, natural_earth=(), hierarchy=()):
    bronze, wikidata, _ = dirs
    pl.DataFrame(list(natural_earth), schema=NE_SCHEMA, orient="row").write_parquet(
        bronze / "natural_earth_admin.parquet"
    )
    pl.DataFrame(list(hierarchy), schema=HIERARCHY_SCHEMA, orient="row").write_parquet(
        wikidata / "wikidata_admin_hierarchy.parquet"
    )


def write_bronze(dirs, languoids, entities, claims):
    bronze, wikidata, _ = dirs
    write_admin(dirs)
    pl.DataFrame(languoids, schema=LANGUOID_SCHEMA).write_parquet(bronze / "glottolog_languoid.parquet")
    pl.DataFrame(entities, schema=ENTITY_SCHEMA).write_parquet(wikidata / "wikidata_cultural_entity.parquet")
    pl.DataFrame(claims, schema=CLAIM_SCHEMA).write_parquet(wikidata / "wikidata_cultural_entity_claim.parquet")


def base_bronze(dirs, languoids=None, entities=None, claims=None):
    write_bronze(
        dirs,
        languoids
        or [
            languoid("fam1", "family", "Family"),
            languoid("lang1", classification="fam1", latitude=1.0, longitude=2.0),
            languoid("dial1", "dialect", "Spoken_L1_Language", classification="fam1/lang1"),
        ],
        entities or [entity("Q1", "has_glottocode", "lang1")],
        claims or [claim("Q1", "P625", "Point(10.5 20.5)")],
    )


def test_wikidata_points_parses_lon_lat_deterministically():
    claims = pl.DataFrame(
        [claim("Q1", "P625", "Point(30 40)"), claim("Q1", "P625", "Point(10 20)"), claim("Q2", "P625", "bad")],
        schema=CLAIM_SCHEMA,
    )

    points = wikidata_points(claims)

    assert points.to_dicts() == [{"wikidata_id": "Q1", "longitude": 10.0, "latitude": 20.0}]


def test_cultural_entity_excludes_categories_and_descendants(dirs):
    languoids = [
        languoid("fam1", "family", "Family"),
        languoid("sign", "family", "Sign_Language"),
        languoid("sign1", classification="sign"),
        languoid("lang1", classification="fam1"),
        languoid("book", "family", "Bookkeeping"),
    ]
    base_bronze(dirs, languoids, [], [])
    bronze, wikidata, silver = dirs
    curation = silver.parent / "curation"

    cultural_entity(bronze, wikidata, silver, curation)

    assert pl.read_parquet(silver / "1_cultural_entity.parquet")["entity_id"].to_list() == ["fam1", "lang1"]


def test_cultural_entity_maps_wikidata_items_to_glottocodes_and_prefers_glottolog_coordinates(dirs):
    entities = [
        entity("Q1", "has_glottocode", "lang1"),
        entity("Q1", "has_glottocode", "dial1"),
        entity("Q2", "has_glottocode", "lang1"),
        entity("Q3", "has_glottocode", "missing"),
        entity("Q4", "archaeological_culture"),
        entity("Q5", "has_glottocode", "dial1"),
        entity("Q5", "historical_country"),
    ]
    claims = [claim("Q1", "P625", "Point(10.5 20.5)"), claim("Q4", "P625", "Point(3 4)")]
    base_bronze(dirs, entities=entities, claims=claims)
    bronze, wikidata, silver = dirs
    curation = silver.parent / "curation"

    entity_path, crosswalk_path = cultural_entity(bronze, wikidata, silver, curation)

    entities_out = pl.read_parquet(entity_path).sort("entity_id")
    assert entities_out["entity_id"].to_list() == ["Q4", "dial1", "fam1", "lang1"]
    rows = {row["entity_id"]: row for row in entities_out.to_dicts()}
    assert (rows["lang1"]["latitude"], rows["lang1"]["coordinate_source"]) == (1.0, "glottolog")
    assert (rows["dial1"]["latitude"], rows["dial1"]["longitude"], rows["dial1"]["coordinate_source"]) == (
        20.5,
        10.5,
        "wikidata",
    )
    assert (rows["Q4"]["latitude"], rows["Q4"]["longitude"], rows["Q4"]["kind"]) == (4.0, 3.0, "archaeological_culture")
    assert rows["fam1"]["coordinate_source"] is None
    assert pl.read_parquet(crosswalk_path).rows() == [
        ("Q4", "Q4"),
        ("dial1", "Q1"),
        ("dial1", "Q5"),
        ("lang1", "Q1"),
        ("lang1", "Q2"),
    ]


def test_cultural_entity_raises_when_nothing_is_in_scope(dirs):
    base_bronze(dirs, [languoid("sign", "family", "Sign_Language")], [], [])
    bronze, wikidata, silver = dirs
    curation = silver.parent / "curation"

    with pytest.raises(ValueError):
        cultural_entity(bronze, wikidata, silver, curation)


def test_entity_membership_uses_glottolog_parent_and_wikidata_p361(dirs):
    entities = [
        entity("Q1", "has_glottocode", "lang1"),
        entity("Q10", "archaeological_culture"),
        entity("Q11", "archaeological_culture"),
        entity("Q12", "archaeological_culture"),
    ]
    claims = [
        claim("Q10", "P361", "Q11"),
        claim("Q10", "P361", "Q10"),
        claim("Q11", "P361", "Q1"),
        claim("Q12", "P361", "Q99"),
    ]
    base_bronze(dirs, entities=entities, claims=claims)
    bronze, wikidata, silver = dirs
    curation = silver.parent / "curation"
    cultural_entity(bronze, wikidata, silver, curation)

    path = entity_membership(bronze, wikidata, silver, curation)

    assert pl.read_parquet(path).rows() == [
        ("Q10", "Q11", "wikidata_p361"),
        ("Q11", "lang1", "wikidata_p361"),
        ("dial1", "lang1", "glottolog"),
        ("lang1", "fam1", "glottolog"),
    ]


def test_entity_membership_lets_glottolog_override_contradicting_p361(dirs):
    entities = [entity("Q1", "has_glottocode", "lang1"), entity("Q2", "has_glottocode", "fam1")]
    claims = [claim("Q2", "P361", "Q1")]
    base_bronze(dirs, entities=entities, claims=claims)
    bronze, wikidata, silver = dirs
    curation = silver.parent / "curation"
    cultural_entity(bronze, wikidata, silver, curation)

    path = entity_membership(bronze, wikidata, silver, curation)

    assert ("fam1", "lang1", "wikidata_p361") not in pl.read_parquet(path).rows()


def test_cultural_entity_keeps_the_most_specific_kind_of_a_multi_kind_item(dirs):
    entities = [
        entity("Q1", "country"),
        entity("Q1", "historical_country"),
        entity("Q2", "cultural_region"),
        entity("Q2", "country"),
        entity("Q3", "cultural_region"),
        entity("Q4", "macro_region"),
        entity("Q4", "cultural_region"),
        entity("Q5", "ethnic_group"),
        entity("Q5", "cultural_region"),
        entity("Q6", "civilization"),
        entity("Q6", "historical_country"),
        entity("Q7", "historical_region"),
        entity("Q7", "ethnic_group"),
    ]
    base_bronze(dirs, entities=entities, claims=[])
    bronze, wikidata, silver = dirs

    entity_path, _ = cultural_entity(bronze, wikidata, silver, silver.parent / "curation")

    out = pl.read_parquet(entity_path).filter(pl.col("entity_id").str.starts_with("Q"))
    kinds = dict(out.select("entity_id", "kind").rows())
    assert kinds == {
        "Q1": "historical_country",
        "Q2": "country",
        "Q3": "cultural_region",
        "Q4": "cultural_region",
        "Q5": "ethnic_group",
        "Q6": "civilization",
        "Q7": "historical_region",
    }


def test_entity_membership_raises_on_cycle(dirs):
    entities = [entity("Q10", "archaeological_culture"), entity("Q11", "archaeological_culture")]
    claims = [claim("Q10", "P361", "Q11"), claim("Q11", "P361", "Q10")]
    base_bronze(dirs, entities=entities, claims=claims)
    bronze, wikidata, silver = dirs
    curation = silver.parent / "curation"
    cultural_entity(bronze, wikidata, silver, curation)

    with pytest.raises(ValueError, match="cycle"):
        entity_membership(bronze, wikidata, silver, curation)


def test_entity_succession_orients_p1365_and_p1366_and_merges_sources(dirs):
    entities = [
        entity("Q10", "historical_country"),
        entity("Q11", "historical_country"),
        entity("Q12", "historical_country"),
    ]
    claims = [
        claim("Q11", "P1365", "Q10"),
        claim("Q10", "P1366", "Q11"),
        claim("Q12", "P1365", "Q12"),
        claim("Q12", "P1366", "Q99"),
    ]
    base_bronze(dirs, entities=entities, claims=claims)
    bronze, wikidata, silver = dirs
    curation = silver.parent / "curation"
    cultural_entity(bronze, wikidata, silver, curation)

    path = entity_succession(wikidata, silver)

    assert pl.read_parquet(path).rows() == [("Q10", "Q11", "P1365,P1366")]


def test_entity_time_span_follows_priorities_and_bce_years(dirs):
    entities = [
        entity("Q10", "archaeological_culture"),
        entity("Q11", "archaeological_culture"),
        entity("Q12", "archaeological_culture"),
        entity("Q13", "archaeological_culture"),
        entity("Q1", "has_glottocode", "lang1"),
    ]
    claims = [
        claim("Q13", "P580", "-2500000000-01-01T00:00:00Z", 1),
        claim("Q10", "P571", "-3500-01-01T00:00:00Z", 7),
        claim("Q10", "P571", "-3000-01-01T00:00:00Z", 7),
        claim("Q10", "P580", "-4000-01-01T00:00:00Z", 9),
        claim("Q10", "P576", "-1800-01-01T00:00:00Z", 7),
        claim("Q10", "P582", "-1000-01-01T00:00:00Z", 9),
        claim("Q11", "P2348", "Q20"),
        claim("Q20", "P580", "+0500-01-01T00:00:00Z", 8),
        claim("Q20", "P582", "+0700-01-01T00:00:00Z", 8),
        claim("Q12", "P580", "+0900-01-01T00:00:00Z", 9),
        claim("Q12", "P582", "+0800-01-01T00:00:00Z", 9),
    ]
    languoids = [
        languoid("fam1", "family", "Family"),
        languoid("lang1", first_year_of_documentation=1800, last_year_of_documentation=1900, classification="fam1"),
    ]
    base_bronze(dirs, languoids, entities, claims)
    bronze, wikidata, silver = dirs
    curation = silver.parent / "curation"
    cultural_entity(bronze, wikidata, silver, curation)

    path = entity_time_span(bronze, wikidata, silver, curation)

    rows = {row["entity_id"]: row for row in pl.read_parquet(path).to_dicts()}
    assert (rows["Q10"]["start_year"], rows["Q10"]["start_precision"], rows["Q10"]["start_source"]) == (
        -3500,
        7,
        "P571",
    )
    assert (rows["Q10"]["end_year"], rows["Q10"]["end_source"]) == (-1800, "P576")
    assert (rows["Q11"]["start_year"], rows["Q11"]["start_source"]) == (500, "period")
    assert (rows["Q11"]["end_year"], rows["Q11"]["end_source"]) == (700, "period")
    assert (rows["lang1"]["start_year"], rows["lang1"]["start_source"]) == (1800, "glottolog")
    assert (rows["lang1"]["end_year"], rows["lang1"]["end_source"]) == (1900, "glottolog")
    assert rows["Q12"]["start_year"] > rows["Q12"]["end_year"]
    assert rows["Q13"]["start_year"] is None
    assert rows["fam1"]["start_year"] is None


def test_cultural_entity_drops_excluded_entities_and_their_edges(dirs):
    base_bronze(dirs)
    bronze, wikidata, silver = dirs
    curation = write_curation(silver.parent / "curation", entity_exclusions=[("lang1", "not a tradition")])

    entity_path, crosswalk_path = cultural_entity(bronze, wikidata, silver, curation)
    edges = pl.read_parquet(entity_membership(bronze, wikidata, silver, curation))

    assert "lang1" not in pl.read_parquet(entity_path)["entity_id"].to_list()
    assert pl.read_parquet(crosswalk_path)["entity_id"].to_list() == []
    assert edges.filter((pl.col("child_id") == "lang1") | (pl.col("parent_id") == "lang1")).height == 0


def test_entity_membership_overrides_remove_and_add_edges(dirs):
    base_bronze(dirs)
    bronze, wikidata, silver = dirs
    curation = write_curation(
        silver.parent / "curation",
        entity_membership_overrides=[
            ("dial1", "lang1", "remove", "wrong parent"),
            ("dial1", "fam1", "add", "belongs to the family"),
        ],
    )
    cultural_entity(bronze, wikidata, silver, curation)

    path = entity_membership(bronze, wikidata, silver, curation)

    rows = pl.read_parquet(path).filter(pl.col("child_id") == "dial1").rows()
    assert rows == [("dial1", "fam1", "curation")]


def test_entity_membership_overrides_raise_on_invalid_action_or_unknown_entity(dirs):
    base_bronze(dirs)
    bronze, wikidata, silver = dirs
    curation = write_curation(
        silver.parent / "curation", entity_membership_overrides=[("dial1", "lang1", "move", "typo")]
    )
    cultural_entity(bronze, wikidata, silver, curation)
    with pytest.raises(ValueError, match="invalid action"):
        entity_membership(bronze, wikidata, silver, curation)

    curation = write_curation(
        silver.parent / "curation", entity_membership_overrides=[("dial1", "zzzz9999", "add", "unknown")]
    )
    with pytest.raises(ValueError, match="unknown entities"):
        entity_membership(bronze, wikidata, silver, curation)


def test_entity_time_span_overrides_win_and_blank_year_keeps_sourced_value(dirs):
    languoids = [
        languoid("fam1", "family", "Family"),
        languoid("lang1", first_year_of_documentation=1800, last_year_of_documentation=1900, classification="fam1"),
    ]
    base_bronze(dirs, languoids, [entity("Q1", "has_glottocode", "lang1")])
    bronze, wikidata, silver = dirs
    curation = write_curation(
        silver.parent / "curation", entity_time_span_overrides=[("lang1", "-500", "", "dated by excavation")]
    )
    cultural_entity(bronze, wikidata, silver, curation)

    path = entity_time_span(bronze, wikidata, silver, curation)

    row = pl.read_parquet(path).filter(pl.col("entity_id") == "lang1").to_dicts()[0]
    assert (row["start_year"], row["start_precision"], row["start_source"]) == (-500, None, "curation")
    assert (row["end_year"], row["end_source"]) == (1900, "glottolog")


def test_cultural_entity_adds_curated_items_after_the_bulk_kinds(dirs):
    entities = [entity("Q1", "curated"), entity("Q2", "curated"), entity("Q2", "country")]
    base_bronze(dirs, entities=entities, claims=[])
    bronze, wikidata, silver = dirs
    curation = write_curation(silver.parent / "curation", entity_additions=[("Q1", "site"), ("Q2", "dup")])

    entity_path, _ = cultural_entity(bronze, wikidata, silver, curation)

    out = pl.read_parquet(entity_path).filter(pl.col("entity_id").str.starts_with("Q"))
    assert dict(out.select("entity_id", "kind").rows()) == {"Q1": "curated", "Q2": "country"}


def test_cultural_entity_raises_when_an_addition_is_missing_from_the_bronze(dirs):
    base_bronze(dirs)
    bronze, wikidata, silver = dirs
    curation = write_curation(silver.parent / "curation", entity_additions=[("Q999", "gone")])

    with pytest.raises(ValueError, match="Q999"):
        cultural_entity(bronze, wikidata, silver, curation)


def test_read_curation_list_raises_on_unexpected_columns(dirs):
    base_bronze(dirs)
    bronze, wikidata, silver = dirs
    curation = silver.parent / "curation"
    (curation / "manual_entity_exclusions.csv").write_text("entity_id\n")

    with pytest.raises(ValueError, match="expected"):
        cultural_entity(bronze, wikidata, silver, curation)


def test_cultural_entity_builds_admin_units_from_natural_earth_and_p131_intermediates(dirs):
    bronze, wikidata, silver = dirs
    base_bronze(dirs, entities=[entity("Q142", "country")], claims=[claim("Q142", "P625", "Point(2 46)")])
    write_admin(
        dirs,
        natural_earth=[
            ("c0", 0, "Q142", "FRA", "France"),
            ("a1", 1, "Q12130", "FRA", "Bretagne"),
            ("a2", 1, "Q142", "FRA", "France bis"),
            ("a3", 1, None, "FRA", "Corse"),
            ("a4", 1, None, "FRA", None),
        ],
        hierarchy=[
            ("Q12130", "P131", "Q500", None),
            ("Q500", "label", None, "Grand Ouest"),
            ("Q142", "P131", "Q500", None),
            ("Q600", "P131", "Q700", None),
            ("Q700", "label", None, "Lonely parent"),
        ],
    )
    cultural_entity(bronze, wikidata, silver, silver.parent / "curation")

    kinds = dict(pl.read_parquet(silver / "1_cultural_entity.parquet").select("entity_id", "kind").rows())
    assert kinds["Q12130"] == kinds["Q500"] == kinds["ne:a3"] == "admin_unit"
    assert "Q700" not in kinds and "ne:a2" not in kinds and "ne:a4" not in kinds
    ne_map = {r["ne_id"]: r for r in pl.read_parquet(silver / "1_entity_ne_admin.parquet").to_dicts()}
    assert ne_map["a2"]["entity_id"] == "Q142" and ne_map["a4"]["entity_id"] is None
    assert ne_map["a1"]["country_entity_id"] == "Q142" and ne_map["c0"]["entity_id"] == "Q142"
    crosswalk = pl.read_parquet(silver / "1_entity_wikidata_item.parquet")
    assert ("Q12130", "Q12130") in crosswalk.rows() and not any(e.startswith("ne:") for e in crosswalk["entity_id"])
