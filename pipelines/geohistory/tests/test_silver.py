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
        (curation / f"{list_name}.csv").write_text("\n".join(lines) + "\n")
    return curation


CITY_SCHEMA = {"item_id": pl.String, "label": pl.String, "p625": pl.String}


def write_admin(dirs, natural_earth=(), hierarchy=(), cities=()):
    bronze, wikidata, _ = dirs
    pl.DataFrame(list(natural_earth), schema=NE_SCHEMA, orient="row").write_parquet(
        bronze / "natural_earth_admin.parquet"
    )
    pl.DataFrame(list(hierarchy), schema=HIERARCHY_SCHEMA, orient="row").write_parquet(
        wikidata / "wikidata_admin_hierarchy.parquet"
    )
    pl.DataFrame(list(cities), schema=CITY_SCHEMA, orient="row").write_parquet(
        wikidata / "wikidata_city_candidate.parquet"
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
        entity("Q8", "ethnic_group"),
        entity("Q8", "religion"),
        entity("Q9", "archaeological_culture"),
        entity("Q9", "religion"),
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
        "Q8": "religion",
        "Q9": "archaeological_culture",
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
    curation = write_curation(silver.parent / "curation", entity_additions=[("Q1", "", "site"), ("Q2", "", "dup")])

    entity_path, _ = cultural_entity(bronze, wikidata, silver, curation)

    out = pl.read_parquet(entity_path).filter(pl.col("entity_id").str.starts_with("Q"))
    assert dict(out.select("entity_id", "kind").rows()) == {"Q1": "curated", "Q2": "country"}


def test_cultural_entity_gives_an_addition_its_entity_kind(dirs):
    entities = [entity("Q1", "curated"), entity("Q2", "curated"), entity("Q3", "curated")]
    base_bronze(dirs, entities=entities, claims=[])
    bronze, wikidata, silver = dirs
    curation = write_curation(
        silver.parent / "curation",
        entity_additions=[("Q1", "cultural_region", "zone"), ("Q2", "macro_region", "zone"), ("Q3", "", "site")],
    )

    entity_path, _ = cultural_entity(bronze, wikidata, silver, curation)

    out = pl.read_parquet(entity_path).filter(pl.col("entity_id").str.starts_with("Q"))
    assert dict(out.select("entity_id", "kind").rows()) == {
        "Q1": "cultural_region",
        "Q2": "macro_region",
        "Q3": "curated",
    }


def test_cultural_entity_keeps_the_bulk_kind_of_an_addition_with_an_entity_kind(dirs):
    base_bronze(dirs, entities=[entity("Q2", "curated"), entity("Q2", "country")], claims=[])
    bronze, wikidata, silver = dirs
    curation = write_curation(silver.parent / "curation", entity_additions=[("Q2", "macro_region", "dup")])

    entity_path, _ = cultural_entity(bronze, wikidata, silver, curation)

    out = pl.read_parquet(entity_path).filter(pl.col("entity_id") == "Q2")
    assert out["kind"].to_list() == ["country"]


def test_cultural_entity_raises_on_an_unknown_addition_kind(dirs):
    base_bronze(dirs, entities=[entity("Q1", "curated")], claims=[])
    bronze, wikidata, silver = dirs
    curation = write_curation(silver.parent / "curation", entity_additions=[("Q1", "country", "bad")])

    with pytest.raises(ValueError, match="entity_kind"):
        cultural_entity(bronze, wikidata, silver, curation)


def test_cultural_entity_raises_when_an_addition_is_missing_from_the_bronze(dirs):
    base_bronze(dirs)
    bronze, wikidata, silver = dirs
    curation = write_curation(silver.parent / "curation", entity_additions=[("Q999", "", "gone")])

    with pytest.raises(ValueError, match="Q999"):
        cultural_entity(bronze, wikidata, silver, curation)


def test_read_curation_list_raises_on_unexpected_columns(dirs):
    base_bronze(dirs)
    bronze, wikidata, silver = dirs
    curation = silver.parent / "curation"
    (curation / "entity_exclusions.csv").write_text("entity_id\n")

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


def test_cultural_entity_gives_the_sole_unit_of_an_unsubdivided_territory_its_country_qid(dirs):
    bronze, wikidata, silver = dirs
    base_bronze(dirs, entities=[entity("Q672", "country")], claims=[claim("Q672", "P625", "Point(179 -8)")])
    write_admin(
        dirs,
        natural_earth=[
            ("c-vgb", 0, "Q25305", "VGB", "British Virgin Is."),
            ("VGB+00?", 1, None, "VGB", "British Virgin Islands"),
            ("c-tuv", 0, "Q672", "TUV", "Tuvalu"),
            ("TUV+00?", 1, None, "TUV", "Tuvalu"),
            ("c-xxx", 0, None, "XXX", "Nowhere"),
            ("XXX+00?", 1, None, "XXX", "Nowhere"),
            ("c-ggy", 0, "Q25230", "GGY", "Guernsey"),
            ("GGY+00?", 1, None, "GGY", "Sark"),
        ],
        hierarchy=[
            ("Q25230", "label", None, "Bailiwick of Guernsey"),
            ("Q25305", "label", None, "British Virgin Islands"),
        ],
    )
    cultural_entity(bronze, wikidata, silver, silver.parent / "curation")

    entities = pl.read_parquet(silver / "1_cultural_entity.parquet")
    kinds = dict(entities.select("entity_id", "kind").rows())
    labels = dict(entities.select("entity_id", "label").rows())
    assert labels["Q25230"] == "Bailiwick of Guernsey" and labels["Q25305"] == "British Virgin Islands"
    assert kinds["Q25305"] == "admin_unit" and kinds["Q672"] == "country" and kinds["ne:XXX+00?"] == "admin_unit"
    assert "ne:VGB+00?" not in kinds and "ne:TUV+00?" not in kinds
    ne_map = {r["ne_id"]: r["entity_id"] for r in pl.read_parquet(silver / "1_entity_ne_admin.parquet").to_dicts()}
    assert ne_map["VGB+00?"] == "Q25305" and ne_map["TUV+00?"] == "Q672"
    crosswalk = pl.read_parquet(silver / "1_entity_wikidata_item.parquet")
    assert ("Q25305", "Q25305") in crosswalk.rows()


def test_cultural_entity_makes_a_country_without_an_entity_an_admin_unit(dirs):
    bronze, wikidata, silver = dirs
    base_bronze(dirs)
    write_admin(
        dirs,
        natural_earth=[
            ("c-jey", 0, "Q785", "JEY", "Jersey"),
            ("JEY+00?", 1, "Q785", "JEY", "Jersey"),
            ("c-vir", 0, "Q11703", "VIR", "U.S. Virgin Is."),
            ("VIR-4868", 1, None, "VIR", "Saint Thomas"),
            ("VIR-4869", 1, "Q548816", "VIR", "Saint John"),
            ("c-kas", 0, "Q333946", "KAS", "Siachen Glacier"),
        ],
        hierarchy=[("Q11703", "P361", "Q1", "United States Virgin Islands")],
    )
    cultural_entity(bronze, wikidata, silver, silver.parent / "curation")

    entities = pl.read_parquet(silver / "1_cultural_entity.parquet")
    kinds = dict(entities.select("entity_id", "kind").rows())
    labels = dict(entities.select("entity_id", "label").rows())
    assert kinds["Q785"] == kinds["Q11703"] == kinds["Q333946"] == kinds["ne:VIR-4868"] == "admin_unit"
    assert labels["Q11703"] == "United States Virgin Islands" and labels["Q333946"] == "Siachen Glacier"
    ne_map = {r["ne_id"]: r for r in pl.read_parquet(silver / "1_entity_ne_admin.parquet").to_dicts()}
    assert ne_map["JEY+00?"]["entity_id"] == ne_map["c-jey"]["entity_id"] == "Q785"
    assert ne_map["c-vir"]["entity_id"] == "Q11703"
    assert ne_map["VIR-4868"]["country_entity_id"] == ne_map["VIR-4869"]["country_entity_id"] == "Q11703"
    crosswalk = pl.read_parquet(silver / "1_entity_wikidata_item.parquet")
    assert ("Q785", "Q785") in crosswalk.rows() and ("Q11703", "Q11703") in crosswalk.rows()


def test_cultural_entity_makes_a_wikidata_country_that_is_a_natural_earth_admin_1_unit_an_admin_unit(dirs):
    bronze, wikidata, silver = dirs
    base_bronze(
        dirs, entities=[entity("Q1", "has_glottocode", "lang1"), entity("Q668", "country"), entity("Q1168", "country")]
    )
    write_admin(
        dirs,
        natural_earth=[("c-ind", 0, "Q668", "IND", "India"), ("IND-1", 1, "Q1168", "IND", "Chhattisgarh")],
    )
    cultural_entity(bronze, wikidata, silver, silver.parent / "curation")

    kinds = dict(pl.read_parquet(silver / "1_cultural_entity.parquet").select("entity_id", "kind").rows())
    assert kinds["Q668"] == "country" and kinds["Q1168"] == "admin_unit"


def _federated_state_setup(dirs, curation_rows=None):
    bronze, wikidata, silver = dirs
    base_bronze(
        dirs,
        entities=[
            entity("Q668", "country"),
            entity("Q1168", "country"),
            entity("Q1168", "federated_state"),
            entity("Q1169", "country"),
        ],
    )
    write_admin(
        dirs,
        natural_earth=[
            ("c-ind", 0, "Q668", "IND", "India"),
            ("IND-1", 1, "Q1168", "IND", "Chhattisgarh"),
            ("IND-2", 1, "Q1169", "IND", "Goa"),
            ("IND-3", 1, None, "IND", "Ladakh"),
        ],
    )
    return write_curation(silver.parent / "curation", **(curation_rows or {}))


def test_cultural_entity_flags_the_admin_units_wikidata_types_as_federated_states(dirs):
    bronze, wikidata, silver = dirs
    curation = _federated_state_setup(dirs)

    cultural_entity(bronze, wikidata, silver, curation)

    entities = pl.read_parquet(silver / "1_cultural_entity.parquet")
    flags = dict(entities.select("entity_id", "federated_state").rows())
    assert flags["Q1168"] is True
    assert flags["Q1169"] is flags["Q668"] is flags["ne:IND-3"] is False
    assert "federated_state" not in set(entities["kind"])


def test_cultural_entity_rejects_two_federated_state_overrides_for_one_entity(dirs):
    bronze, wikidata, silver = dirs
    curation = _federated_state_setup(
        dirs, {"federated_state_overrides": [("Q1168", "remove", "a"), ("Q1168", "add", "b")]}
    )

    with pytest.raises(ValueError, match="federated_state_overrides"):
        cultural_entity(bronze, wikidata, silver, curation)


def test_cultural_entity_applies_federated_state_overrides(dirs):
    bronze, wikidata, silver = dirs
    curation = _federated_state_setup(
        dirs,
        {
            "federated_state_overrides": [
                ("Q1168", "remove", "not a state"),
                ("Q1169", "add", "a state"),
                ("ne:IND-3", "add", "a state"),
            ]
        },
    )

    cultural_entity(bronze, wikidata, silver, curation)

    flags = dict(pl.read_parquet(silver / "1_cultural_entity.parquet").select("entity_id", "federated_state").rows())
    assert flags["Q1168"] is False and flags["Q1169"] is True and flags["ne:IND-3"] is True


@pytest.mark.parametrize(
    ("row", "message"),
    [
        (("Q999", "add", "unknown"), "not admin units"),
        (("Q668", "add", "a country"), "not admin units"),
        (("Q1168", "toggle", "bad"), "action must be one of"),
    ],
)
def test_cultural_entity_rejects_invalid_federated_state_overrides(dirs, row, message):
    bronze, wikidata, silver = dirs
    curation = _federated_state_setup(dirs, {"federated_state_overrides": [row]})

    with pytest.raises(ValueError, match=message):
        cultural_entity(bronze, wikidata, silver, curation)


def test_cultural_entity_adds_a_city_with_its_point_below_every_other_kind(dirs):
    base_bronze(dirs, entities=[entity("Q2", "country")], claims=[])
    bronze, wikidata, silver = dirs
    write_admin(dirs, cities=[("Q2", "Twice", "Point(1 2)"), ("Q60", "Detroit", "Point(-83.04 42.33)")])
    curation = silver.parent / "curation"

    entity_path, crosswalk_path = cultural_entity(bronze, wikidata, silver, curation)

    out = pl.read_parquet(entity_path).filter(pl.col("entity_id").str.starts_with("Q"))
    assert out.select("entity_id", "kind", "label", "latitude", "longitude", "coordinate_source").sort(
        "entity_id"
    ).rows() == [
        ("Q2", "country", "label Q2", None, None, None),
        ("Q60", "city", "Detroit", 42.33, -83.04, "wikidata"),
    ]
    assert ("Q60", "Q60") in pl.read_parquet(crosswalk_path).rows()


def test_cultural_entity_accepts_city_as_an_addition_kind(dirs):
    base_bronze(dirs, entities=[entity("Q1", "curated")], claims=[])
    bronze, wikidata, silver = dirs
    curation = write_curation(silver.parent / "curation", entity_additions=[("Q1", "city", "missed")])

    entity_path, _ = cultural_entity(bronze, wikidata, silver, curation)

    out = pl.read_parquet(entity_path).filter(pl.col("entity_id") == "Q1")
    assert out["kind"].to_list() == ["city"]
