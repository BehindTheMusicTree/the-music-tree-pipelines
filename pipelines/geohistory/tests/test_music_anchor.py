import polars as pl
import pytest
from geohistory.silver.curation import LISTS
from geohistory.silver.music_anchor import music_anchor

CLAIM_SCHEMA = {"item_id": pl.String, "property": pl.String, "value": pl.String, "time_precision": pl.Int8}
ENTITIES = [
    ("fr", "country", "France"),
    ("bre", "admin_unit", "Brittany"),
    ("mor", "historical_region", "Morbihan"),
    ("us", "country", "United States"),
    ("ge", "country", "Georgia"),
    ("gaus", "historical_region", "Georgia"),
    ("kr", "country", "South Korea"),
    ("pop", "ethnic_group", "Pop people"),
    ("bre_z", "historical_country", "Brittany"),
    ("duchy", "historical_country", "Brittany"),
]
CROSSWALK = {"fr": "Q142", "bre": "Q12130", "mor": "Q12642", "us": "Q30", "ge": "Q230", "gaus": "Q1428", "kr": "Q884"}


def claim(item_id, property_, value):
    return {"item_id": item_id, "property": property_, "value": value, "time_precision": None}


def label(item_id, text):
    return claim(item_id, "label", text)


def run(
    tmp_path,
    claims,
    membership=(("mor", "bre"), ("bre", "fr")),
    containment=(),
    aliases=(),
    placements=(),
    additions=(),
    exclusions=(),
    footprint=(),
):
    wikidata, silver, curation = tmp_path / "wikidata", tmp_path / "silver", tmp_path / "curation"
    for directory in (wikidata, silver, curation):
        directory.mkdir()
    for list_name, columns in LISTS.items():
        rows = {
            "tradition_placement_overrides": placements,
            "tradition_additions": [(q, "r") for q in additions],
            "tradition_exclusions": [(q, "r") for q in exclusions],
        }.get(list_name, ())
        lines = [",".join(columns), *(",".join(row) for row in rows)]
        (curation / f"manual_{list_name}.csv").write_text("\n".join(lines) + "\n")
    pl.DataFrame(
        [(e, kind, name, None, None) for e, kind, name in ENTITIES],
        schema=["entity_id", "kind", "label", "latitude", "longitude"],
        orient="row",
    ).write_parquet(silver / "1_cultural_entity.parquet")
    pl.DataFrame(
        [(e, q) for e, q in CROSSWALK.items()], schema=["entity_id", "wikidata_id"], orient="row"
    ).write_parquet(silver / "1_entity_wikidata_item.parquet")
    pl.DataFrame(list(membership), schema=["child_id", "parent_id"], orient="row").write_parquet(
        silver / "2_entity_membership.parquet"
    )
    pl.DataFrame(list(containment), schema=["child_id", "parent_id"], orient="row").write_parquet(
        silver / "2_place_containment.parquet"
    )
    pl.DataFrame(list(footprint), schema=["entity_id", "admin_id", "rule"], orient="row").write_parquet(
        silver / "8_entity_footprint.parquet"
    )
    pl.DataFrame(schema={"predecessor_id": pl.String, "successor_id": pl.String}).write_parquet(
        silver / "3_entity_succession.parquet"
    )
    pl.DataFrame(schema={"entity_id": pl.String, "start_year": pl.Int32, "end_year": pl.Int32}).write_parquet(
        silver / "4_entity_time_span.parquet"
    )
    pl.DataFrame(claims, schema=CLAIM_SCHEMA).write_parquet(wikidata / "wikidata_music_tradition_claims.parquet")
    genres = [
        row["item_id"] for row in claims if row["property"] == "label" and not row["value"].startswith("music of")
    ]
    pl.DataFrame({"item_id": genres, "parent_id": ["Q188451"] * len(genres)}).write_parquet(
        wikidata / "wikidata_genre_tree.parquet"
    )
    pl.DataFrame(list(aliases), schema={"item_id": pl.String, "alias": pl.String}, orient="row").write_parquet(
        wikidata / "wikidata_cultural_entity_alias.parquet"
    )
    music_anchor(wikidata, silver, curation)
    anchors = {r["wikidata_id"]: r for r in pl.read_parquet(silver / "5_music_anchor.parquet").to_dicts()}
    return anchors, pl.read_csv(silver / "5_music_anchor_unplaced.csv", infer_schema=False)


def test_most_specific_candidate_beats_its_ancestors(tmp_path):
    claims = [
        label("Q1", "Breton folk"),
        claim("Q1", "P495", "Q142"),
        claim("Q1", "P17", "Q142"),
        claim("Q1", "P276", "Q12642"),
        claim("Q1", "P2596", "Q12130"),
    ]
    anchors, _ = run(tmp_path, claims)
    assert (anchors["Q1"]["anchor_id"], anchors["Q1"]["rule"]) == ("mor", "P276")


def test_equally_specific_candidates_take_the_first_rule(tmp_path):
    claims = [label("Q1", "Folk"), claim("Q1", "P17", "Q142"), claim("Q1", "P495", "Q884")]
    anchors, _ = run(tmp_path, claims)
    assert (anchors["Q1"]["anchor_id"], anchors["Q1"]["rule"]) == ("kr", "P495")


def test_place_properties_ignore_non_place_entities(tmp_path):
    claims = [label("Q1", "Folk"), claim("Q1", "P17", "Q142"), claim("Q1", "P921", "Q99")]
    anchors, _ = run(tmp_path, claims)
    assert anchors["Q1"]["anchor_id"] == "fr"


def test_override_sets_the_anchor(tmp_path):
    claims = [label("Q1", "Folk"), claim("Q1", "P495", "Q884")]
    anchors, _ = run(tmp_path, claims, placements=[("Q1", "fr", "", "", "r")])
    assert anchors["Q1"]["anchor_id"] == "fr"


def test_override_wins_over_a_more_specific_candidate(tmp_path):
    claims = [label("Q1", "Folk"), claim("Q1", "P495", "Q12130")]
    anchors, _ = run(tmp_path, claims, placements=[("Q1", "fr", "", "", "r")])
    assert (anchors["Q1"]["anchor_id"], anchors["Q1"]["rule"]) == ("fr", "override")


def test_curated_addition_that_is_not_a_genre_is_anchored(tmp_path):
    anchors, _ = run(tmp_path, [label("Q9", "Not a genre"), claim("Q9", "P495", "Q884")], additions=["Q9"])
    assert anchors["Q9"]["anchor_id"] == "kr"


def test_overview_label_matches_labels_and_aliases(tmp_path):
    claims = [label("Q1", "music of South Korea"), label("Q2", "music of the Bretons"), label("Q3", "music of Korea")]
    anchors, unplaced = run(tmp_path, claims, aliases=[("Q12130", "Bretons"), ("Q884", "Korea")])
    assert (anchors["Q1"]["anchor_id"], anchors["Q1"]["rule"], anchors["Q1"]["is_overview"]) == ("kr", "label", True)
    assert anchors["Q2"]["anchor_id"] == "bre"
    assert anchors["Q3"]["anchor_id"] == "kr"
    assert unplaced.height == 0


def test_ambiguous_overview_label_is_unplaced(tmp_path):
    anchors, unplaced = run(tmp_path, [label("Q1", "music of Georgia")])
    assert "Q1" not in anchors
    assert unplaced.rows() == [("Q1", "music of Georgia", "gaus|ge")]


def test_overview_label_matching_an_admin_and_its_exact_zone_anchors_on_the_admin(tmp_path):
    footprint = [("bre", "bre", "self"), ("bre_z", "bre", "cover")]
    anchors, unplaced = run(tmp_path, [label("Q1", "music of Brittany")], footprint=footprint)
    assert (anchors["Q1"]["anchor_id"], anchors["Q1"]["rule"]) == ("bre", "label")
    assert unplaced.height == 0


def test_overview_label_matching_a_zone_with_a_wider_footprint_stays_ambiguous(tmp_path):
    footprint = [("bre", "bre", "self"), ("bre_z", "bre", "cover"), ("bre_z", "fr", "cover")]
    anchors, unplaced = run(tmp_path, [label("Q1", "music of Brittany")], footprint=footprint)
    assert "Q1" not in anchors and unplaced.height == 1


def test_overview_label_ignores_a_match_without_footprint(tmp_path):
    anchors, unplaced = run(tmp_path, [label("Q1", "music of Georgia")], footprint=[("gaus", "us", "cover")])
    assert (anchors["Q1"]["anchor_id"], anchors["Q1"]["rule"]) == ("gaus", "label")
    assert unplaced.height == 0


def test_ambiguous_label_does_not_override_a_real_candidate(tmp_path):
    anchors, unplaced = run(tmp_path, [label("Q1", "music of Georgia"), claim("Q1", "P17", "Q230")])
    assert anchors["Q1"]["anchor_id"] == "ge"
    assert unplaced.height == 0


def test_genre_without_candidate_is_not_listed_unplaced(tmp_path):
    anchors, unplaced = run(tmp_path, [label("Q1", "Floating genre")])
    assert "Q1" not in anchors and unplaced.height == 0


def test_unknown_override_entity_raises(tmp_path):
    with pytest.raises(ValueError, match="unknown entity"):
        run(tmp_path, [label("Q1", "Folk")], placements=[("Q1", "nope", "", "", "r")])


def test_containment_makes_the_contained_place_more_specific(tmp_path):
    claims = [label("Q1", "Breton folk"), claim("Q1", "P17", "Q142"), claim("Q1", "P276", "Q12130")]
    anchors, _ = run(tmp_path, claims, membership=(), containment=(("bre", "fr"),))
    assert (anchors["Q1"]["anchor_id"], anchors["Q1"]["rule"]) == ("bre", "P276")


def test_excluded_item_gets_no_anchor(tmp_path):
    claims = [label("Q1", "Folk"), claim("Q1", "P495", "Q884")]
    anchors, _ = run(tmp_path, claims, exclusions=["Q1"])
    assert "Q1" not in anchors
