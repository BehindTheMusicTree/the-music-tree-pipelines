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
    ("gaus", "admin_unit", "Georgia"),
    ("kr", "country", "South Korea"),
    ("pop", "ethnic_group", "Pop people"),
    ("bre_z", "cultural_region", "Brittany"),
    ("duchy", "historical_country", "Brittany"),
    ("es", "country", "Spain"),
    ("gal", "admin_unit", "Galicia"),
    ("gal_ee", "historical_region", "Galicia"),
    ("gal_c", "cultural_region", "Galicia"),
    ("gal_k", "historical_country", "Galicia"),
    ("uk", "country", "United Kingdom"),
    ("sco", "cultural_region", "Scotland"),
    ("sco_k", "historical_country", "Scotland"),
    ("khk", "admin_unit", "Khakass"),
    ("chn", "cultural_region", "China"),
    ("prc", "country", "People's Republic of China"),
    ("det", "city", "Detroit"),
]
CROSSWALK = {
    "fr": "Q142",
    "bre": "Q12130",
    "mor": "Q12642",
    "us": "Q30",
    "ge": "Q230",
    "gaus": "Q1428",
    "kr": "Q884",
    "es": "Q29",
    "gal": "Q3908",
    "uk": "Q145",
    "sco": "Q22",
    "khk": "Q6543",
    "chn": "Q29520",
    "prc": "Q148",
    "det": "Q12439",
}


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
        (curation / f"{list_name}.csv").write_text("\n".join(lines) + "\n")
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
    anchors, _ = run(tmp_path, claims, containment=(("mor", "bre"), ("bre", "fr")))
    assert (anchors["Q1"]["anchor_id"], anchors["Q1"]["rule"]) == ("mor", "P276")


def test_equally_specific_candidates_take_the_first_rule(tmp_path):
    claims = [label("Q1", "Folk"), claim("Q1", "P17", "Q142"), claim("Q1", "P495", "Q884")]
    anchors, _ = run(tmp_path, claims)
    assert (anchors["Q1"]["anchor_id"], anchors["Q1"]["rule"]) == ("kr", "P495")


def test_place_properties_ignore_non_place_entities(tmp_path):
    claims = [label("Q1", "Folk"), claim("Q1", "P17", "Q142"), claim("Q1", "P921", "Q99")]
    anchors, _ = run(tmp_path, claims)
    assert anchors["Q1"]["anchor_id"] == "fr"


def test_city_claim_anchors_on_the_city_below_its_country(tmp_path):
    claims = [label("Q1", "Detroit techno"), claim("Q1", "P495", "Q30"), claim("Q1", "P740", "Q12439")]
    anchors, _ = run(tmp_path, claims, containment=[("det", "us")])
    assert (anchors["Q1"]["anchor_id"], anchors["Q1"]["rule"]) == ("det", "P740")


def test_city_is_not_matched_by_an_overview_label(tmp_path):
    anchors, unplaced = run(tmp_path, [label("Q1", "music of Detroit")])
    assert "Q1" not in anchors or anchors["Q1"]["anchor_id"] != "det"


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


def test_overview_ranks_only_when_its_label_names_the_anchor(tmp_path):
    claims = [
        label("Q1", "music of France"),
        label("Q2", "music of Occitania"),
        claim("Q2", "P17", "Q142"),
        label("Q3", "Not an overview"),
        claim("Q3", "P17", "Q142"),
    ]
    anchors, _ = run(tmp_path, claims)
    assert (anchors["Q1"]["anchor_id"], anchors["Q1"]["overview_rank"]) == ("fr", 0)
    assert (anchors["Q2"]["anchor_id"], anchors["Q2"]["overview_rank"]) == ("fr", None)
    assert anchors["Q3"]["overview_rank"] is None


def test_overviews_naming_the_same_anchor_are_ranked_by_rule_priority(tmp_path):
    claims = [label("Q1", "music of France"), label("Q2", "music of the French Republic"), claim("Q2", "P1269", "Q142")]
    anchors, _ = run(tmp_path, claims, aliases=[("Q142", "French Republic")])
    assert (anchors["Q2"]["rule"], anchors["Q2"]["overview_rank"]) == ("P1269", 0)
    assert (anchors["Q1"]["rule"], anchors["Q1"]["overview_rank"]) == ("label", 1)


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


def test_overview_override_names_a_matching_place_without_a_footprint(tmp_path):
    anchors, _ = run(
        tmp_path,
        [label("Q1", "music of Georgia")],
        placements=[("Q1", "ge", "", "", "r")],
        footprint=[("gaus", "us", "cover")],
    )
    assert (anchors["Q1"]["anchor_id"], anchors["Q1"]["overview_rank"]) == ("ge", 0)


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


def ambiguous(tmp_path):
    return pl.read_csv(tmp_path / "silver" / "5_music_anchor_ambiguous.csv", infer_schema=False)


def test_ambiguous_label_resolves_to_the_place_inside_the_claimed_country(tmp_path):
    claims = [label("Q1", "music of Galicia"), claim("Q1", "P495", "Q29")]
    containment = [("gal", "es"), ("gal_k", "es")]
    anchors, _ = run(tmp_path, claims, containment=containment)
    assert (anchors["Q1"]["anchor_id"], anchors["Q1"]["rule"], anchors["Q1"]["overview_rank"]) == ("gal", "label", 0)
    assert ambiguous(tmp_path).height == 0


def test_narrowed_label_is_not_outranked_by_an_unrelated_claim(tmp_path):
    claims = [label("Q1", "music of Galicia"), claim("Q1", "P495", "Q29"), claim("Q1", "P17", "Q142")]
    anchors, _ = run(tmp_path, claims, containment=[("gal", "es"), ("gal_k", "es")])
    assert (anchors["Q1"]["anchor_id"], anchors["Q1"]["rule"]) == ("gal", "label")


def test_ambiguous_label_prefers_a_current_place_over_a_historical_state(tmp_path):
    claims = [label("Q1", "music of Scotland"), claim("Q1", "P17", "Q145")]
    anchors, _ = run(tmp_path, claims, containment=[("sco", "uk"), ("sco_k", "uk")])
    assert (anchors["Q1"]["anchor_id"], anchors["Q1"]["rule"]) == ("sco", "label")


def test_label_without_any_claim_prefers_a_current_place_over_historical_ones(tmp_path):
    anchors, unplaced = run(tmp_path, [label("Q1", "music of Georgia"), label("Q2", "music of Scotland")])
    assert (anchors["Q2"]["anchor_id"], anchors["Q2"]["rule"]) == ("sco", "label")
    assert "Q1" not in anchors and unplaced.rows() == [("Q1", "music of Georgia", "gaus|ge")]


def test_label_with_several_current_places_and_no_claimed_place_is_listed(tmp_path):
    anchors, _ = run(tmp_path, [label("Q1", "music of Galicia"), claim("Q1", "P17", "Q29")])
    assert anchors["Q1"]["anchor_id"] == "es"
    assert ambiguous(tmp_path).rows() == [("Q1", "music of Galicia", "gal|gal_c|gal_ee|gal_k")]


def test_era_only_label_does_not_discard_a_claim_outside_every_match(tmp_path):
    claims = [label("Q1", "music of Scotland"), claim("Q1", "P17", "Q30")]
    anchors, _ = run(tmp_path, claims)
    assert (anchors["Q1"]["anchor_id"], anchors["Q1"]["rule"]) == ("us", "P17")


def test_overview_naming_its_country_keeps_names_anchor(tmp_path):
    anchors, _ = run(tmp_path, [label("Q1", "music of the United Kingdom"), claim("Q1", "P17", "Q145")])
    assert (anchors["Q1"]["anchor_id"], anchors["Q1"]["overview_rank"]) == ("uk", 0)


def test_overview_label_matches_a_place_through_its_alias(tmp_path):
    anchors, _ = run(tmp_path, [label("Q1", "music of Khakassia")], aliases=[("Q6543", "Khakassia")])
    assert (anchors["Q1"]["anchor_id"], anchors["Q1"]["rule"]) == ("khk", "label")


def test_overview_whose_label_names_only_placeless_entities_names_its_claimed_anchor(tmp_path):
    anchors, unplaced = run(tmp_path, [label("Q1", "music of China"), claim("Q1", "P1269", "Q148")])
    assert (anchors["Q1"]["anchor_id"], anchors["Q1"]["rule"], anchors["Q1"]["overview_rank"]) == ("prc", "P1269", 0)
    assert unplaced.height == 0


def test_overview_naming_its_anchor_by_label_ranks_before_one_naming_it_by_claim(tmp_path):
    claims = [
        label("Q1", "music of China"),
        claim("Q1", "P1269", "Q148"),
        label("Q2", "music of the People's Republic of China"),
    ]
    anchors, _ = run(tmp_path, claims)
    assert (anchors["Q2"]["anchor_id"], anchors["Q2"]["overview_rank"]) == ("prc", 0)
    assert (anchors["Q1"]["anchor_id"], anchors["Q1"]["overview_rank"]) == ("prc", 1)


def test_overview_whose_label_names_only_placeless_entities_does_not_name_its_country_of_origin(tmp_path):
    anchors, _ = run(tmp_path, [label("Q1", "music of China"), claim("Q1", "P495", "Q148")])
    assert (anchors["Q1"]["anchor_id"], anchors["Q1"]["overview_rank"]) == ("prc", None)


def test_overviews_naming_an_anchor_by_claim_rank_by_rule_then_qid(tmp_path):
    claims = [
        label("Q3", "music of China"),
        claim("Q3", "P1269", "Q148"),
        label("Q2", "music of China"),
        claim("Q2", "P2596", "Q148"),
        label("Q1", "music of China"),
        claim("Q1", "P1269", "Q148"),
    ]
    anchors, _ = run(tmp_path, claims)
    assert [anchors[q]["overview_rank"] for q in ("Q2", "Q1", "Q3")] == [0, 1, 2]


def test_claimed_anchor_does_not_take_the_name_of_a_placeable_label_match(tmp_path):
    claims = [label("Q1", "music of China"), claim("Q1", "P1269", "Q148")]
    anchors, _ = run(tmp_path, claims, footprint=[("chn", "khk", "cover")])
    assert anchors["Q1"]["overview_rank"] is None


def test_place_membership_alone_does_not_narrow_an_anchor(tmp_path):
    claims = [label("Q1", "Breton folk"), claim("Q1", "P276", "Q12642"), claim("Q1", "P2596", "Q12130")]
    anchors, _ = run(tmp_path, claims, membership=(("mor", "bre"),), containment=())
    assert (anchors["Q1"]["anchor_id"], anchors["Q1"]["rule"]) == ("bre", "P2596")
