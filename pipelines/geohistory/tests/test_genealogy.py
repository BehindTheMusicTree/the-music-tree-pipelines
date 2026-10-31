import json
from pathlib import Path

import polars as pl
import pytest
from geohistory.silver.column_region import REGION_RANK, column_region
from geohistory.silver.curation import LISTS
from geohistory.silver.genealogy_lane import genealogy_lane
from geohistory.silver.lane_order import lane_order
from geohistory.silver.music_anchor import music_anchor
from geohistory.silver.tradition import tradition

CLAIM_SCHEMA = {"item_id": pl.String, "property": pl.String, "value": pl.String, "time_precision": pl.Int8}
TREE_SCHEMA = {"item_id": pl.String, "parent_id": pl.String}
ENTITIES = [
    ("A", "Lang A", 0.0),
    ("B", "Lang B", 10.0),
    ("C", "Region C", 5.0),
    ("D", "Lang D", 1.0),
    ("E", "Lang E", None),
]
CROSSWALK = [("A", "Q10"), ("B", "Q11"), ("C", "Q12"), ("D", "Q13"), ("E", "Q14")]
SPANS = [("A", 1000, 1500), ("B", 800, 1900), ("C", None, None), ("D", 1500, 1900), ("E", 500, 600)]


def write_column_region(silver: Path, regions: dict[str, str]) -> None:
    pl.DataFrame(
        [(column, region, region.title(), REGION_RANK[region]) for column, region in regions.items()],
        schema=["column_id", "region_id", "region_label", "region_rank"],
        orient="row",
    ).write_parquet(silver / "6_column_region.parquet")


def claim(item_id, property_, value, precision=None):
    return {"item_id": item_id, "property": property_, "value": value, "time_precision": precision}


def write_curation(curation: Path, **rows: list[tuple[str, ...]]) -> Path:
    curation.mkdir(exist_ok=True)
    for list_name, columns in LISTS.items():
        lines = [",".join(columns), *(",".join(row) for row in rows.get(list_name, []))]
        (curation / f"manual_{list_name}.csv").write_text("\n".join(lines) + "\n")
    return curation


def build_tradition(wikidata, silver, curation):
    music_anchor(wikidata, silver, curation)
    return tradition(wikidata, silver, curation)


def label(item_id, text):
    return claim(item_id, "label", text)


@pytest.fixture
def paths(tmp_path: Path):
    wikidata, silver = tmp_path / "wikidata", tmp_path / "silver"
    wikidata.mkdir()
    silver.mkdir()
    curation = write_curation(tmp_path / "curation")
    pl.DataFrame(
        [(e, "kind", name, None, lon) for e, name, lon in ENTITIES],
        schema=["entity_id", "kind", "label", "latitude", "longitude"],
        orient="row",
    ).write_parquet(silver / "1_cultural_entity.parquet")
    pl.DataFrame(CROSSWALK, schema=["entity_id", "wikidata_id"], orient="row").write_parquet(
        silver / "1_entity_wikidata_item.parquet"
    )
    pl.DataFrame(
        [("A", "C", "x"), ("B", "C", "x")], schema=["child_id", "parent_id", "source"], orient="row"
    ).write_parquet(silver / "2_entity_membership.parquet")
    pl.DataFrame(schema={"child_id": pl.String, "parent_id": pl.String, "source": pl.String}).write_parquet(
        silver / "2_place_containment.parquet"
    )
    pl.DataFrame(schema={"entity_id": pl.String, "admin_id": pl.String, "rule": pl.String}).write_parquet(
        silver / "8_entity_footprint.parquet"
    )
    pl.DataFrame(
        [("A", "D", "P1366")], schema=["predecessor_id", "successor_id", "source"], orient="row"
    ).write_parquet(silver / "3_entity_succession.parquet")
    pl.DataFrame(SPANS, schema=["entity_id", "start_year", "end_year"], orient="row").write_parquet(
        silver / "4_entity_time_span.parquet"
    )
    claims = [
        label("Q100", "Genre one"),
        claim("Q100", "P2596", "Q10"),
        claim("Q100", "P571", "+1200-00-00T00:00:00Z", 9),
        label("Q101", "Genre two"),
        claim("Q101", "P495", "Q10"),
        claim("Q101", "P571", "+1600-00-00T00:00:00Z", 9),
        claim("Q101", "P737", "Q103"),
        label("Q102", "Modern genre"),
        claim("Q102", "P571", "+1990-00-00T00:00:00Z", 9),
        label("Q103", "music of B"),
        claim("Q103", "P1269", "Q11"),
        label("Q104", "Floating genre"),
        claim("Q104", "P571", "+1300-00-00T00:00:00Z", 9),
    ]
    pl.DataFrame(claims, schema=CLAIM_SCHEMA).write_parquet(wikidata / "wikidata_music_tradition_claims.parquet")
    pl.DataFrame(
        [("Q100", "Q188451"), ("Q101", "Q100"), ("Q102", "Q188451"), ("Q104", "Q188451")],
        schema=TREE_SCHEMA,
        orient="row",
    ).write_parquet(wikidata / "wikidata_genre_tree.parquet")
    pl.DataFrame(schema={"item_id": pl.String, "alias": pl.String}).write_parquet(
        wikidata / "wikidata_cultural_entity_alias.parquet"
    )
    return wikidata, silver, curation


def curation_with(paths, **rows):
    wikidata, silver, curation = paths
    write_curation(curation, **rows)
    return wikidata, silver, curation


def test_tradition_anchors_dates_and_links(paths):
    wikidata, silver, curation = paths
    build_tradition(wikidata, silver, curation)

    rows = {r["wikidata_id"]: r for r in pl.read_parquet(silver / "5_tradition.parquet").to_dicts()}
    assert set(rows) == {"Q100", "Q101", "Q103", "Q104"}
    assert (rows["Q100"]["anchor_id"], rows["Q100"]["anchor_rule"], rows["Q100"]["start_year"]) == ("A", "P2596", 1200)
    assert (rows["Q101"]["anchor_id"], rows["Q101"]["anchor_rule"]) == ("D", "P495")
    assert (rows["Q103"]["anchor_id"], rows["Q103"]["start_year"], rows["Q103"]["end_year"]) == ("B", 800, 1950)
    assert rows["Q104"]["anchor_id"] is None
    links = pl.read_parquet(silver / "5_tradition_link.parquet").rows()
    assert ("Q100", "Q101", "lineage") in links
    assert ("Q103", "Q101", "association") in links
    assert pl.read_csv(silver / "5_tradition_unplaced.csv")["wikidata_id"].to_list() == ["Q104"]
    assert json.loads((silver / "5_tradition_report.json").read_text()) == {"unplacedTraditionCount": 1}


def test_tradition_curation_overrides(paths):
    wikidata, silver, curation = curation_with(
        paths,
        tradition_exclusions=[("Q100", "no")],
        tradition_additions=[("Q102", "yes")],
        tradition_placement_overrides=[("Q104", "Q14", "", "1400", "placed"), ("Q102", "", "1800", "", "dated")],
        tradition_link_overrides=[
            ("Q103", "Q101", "association", "remove", "wrong"),
            ("Q103", "Q104", "lineage", "add", "x"),
        ],
    )
    build_tradition(wikidata, silver, curation)

    rows = {r["wikidata_id"]: r for r in pl.read_parquet(silver / "5_tradition.parquet").to_dicts()}
    assert "Q100" not in rows
    assert rows["Q104"]["anchor_id"] == "E" and rows["Q104"]["anchor_rule"] == "override"
    assert rows["Q104"]["end_year"] == 1400 and rows["Q104"]["start_year"] == 1300
    assert rows["Q102"]["start_year"] == 1800
    assert pl.read_parquet(silver / "5_tradition_link.parquet").rows() == [("Q103", "Q104", "lineage")]


@pytest.mark.parametrize(
    ("lists", "message"),
    [
        ({"tradition_additions": [("Q1", "r")], "tradition_exclusions": [("Q1", "r")]}, "share"),
        ({"tradition_placement_overrides": [("Q104", "Nope", "", "", "r")]}, "unknown entity"),
        ({"tradition_placement_overrides": [("Q104", "", "", "", "r")]}, "neither"),
        ({"tradition_link_overrides": [("Q100", "Q100", "lineage", "add", "r")]}, "itself"),
        ({"tradition_link_overrides": [("Q100", "Q103", "kin", "add", "r")]}, "kind"),
        ({"tradition_link_overrides": [("Q100", "Q103", "lineage", "drop", "r")]}, "action"),
        ({"tradition_link_overrides": [("Q101", "Q100", "lineage", "add", "r")]}, "cycle"),
    ],
)
def test_tradition_fails_fast(paths, lists, message):
    wikidata, silver, curation = curation_with(paths, **lists)
    with pytest.raises(ValueError, match=message):
        build_tradition(wikidata, silver, curation)


def test_genealogy_lane_and_order(paths):
    wikidata, silver, curation = paths
    build_tradition(wikidata, silver, curation)
    genealogy_lane(silver)
    write_column_region(silver, {"A": "europe", "B": "europe"})
    lane_order(silver)

    lanes = {r["entity_id"]: r for r in pl.read_parquet(silver / "6_genealogy_lane.parquet").to_dicts()}
    assert set(lanes) == {"A", "B", "D"}
    assert lanes["A"]["column_id"] == lanes["D"]["column_id"] == "A"
    assert lanes["B"]["column_id"] == "B"
    tradition_lane = pl.read_parquet(silver / "6_genealogy_tradition_lane.parquet")
    anchored = pl.read_parquet(silver / "5_tradition.parquet").drop_nulls("anchor_id")
    assert tradition_lane.height == anchored.height
    assert set(tradition_lane["lane_id"]) <= set(lanes) | {"C"}
    bands = pl.read_parquet(silver / "6_genealogy_band.parquet").sort("column_id")
    assert bands.select("entity_id", "column_id").rows() == [("C", "A"), ("C", "B")]
    assert pl.read_parquet(silver / "7_lane_order.parquet").sort("position").rows() == [
        ("A", 0, "europe", "Europe"),
        ("B", 1, "europe", "Europe"),
    ]
    fragments = pl.read_parquet(silver / "7_band_fragment.parquet")
    assert fragments.select("entity_id", "fragment", "start_position", "end_position").rows() == [("C", 0, 0, 1)]


def test_lane_order_splits_noncontiguous_band_and_swaps_to_close_gaps(tmp_path):
    lanes = [("W", "w", "W", 0, 10), ("X", "x", "X", 0, 10), ("Y", "y", "Y", 0, 10)]
    pl.DataFrame(
        lanes, schema=["entity_id", "label", "column_id", "start_year", "end_year"], orient="row"
    ).write_parquet(tmp_path / "6_genealogy_lane.parquet")
    bands = [("B", "b", "W", 0, 10), ("B", "b", "Y", 0, 10)]
    pl.DataFrame(
        bands, schema=["entity_id", "label", "column_id", "start_year", "end_year"], orient="row"
    ).write_parquet(tmp_path / "6_genealogy_band.parquet")
    pl.DataFrame(
        [("W", 0.0), ("X", 5.0), ("Y", 10.0), ("B", None)], schema=["entity_id", "longitude"], orient="row"
    ).write_parquet(tmp_path / "1_cultural_entity.parquet")

    write_column_region(tmp_path, {"W": "europe", "X": "europe", "Y": "europe"})
    lane_order(tmp_path)

    assert pl.read_parquet(tmp_path / "7_lane_order.parquet").sort("position")["column_id"].to_list() == ["X", "W", "Y"]
    assert pl.read_parquet(tmp_path / "7_band_fragment.parquet")["fragment"].to_list() == [0]


def test_lane_order_keeps_regions_contiguous_even_when_a_swap_would_close_a_band_gap(tmp_path):
    lanes = [("W", "w", "W", 0, 10), ("X", "x", "X", 0, 10), ("Y", "y", "Y", 0, 10)]
    pl.DataFrame(
        lanes, schema=["entity_id", "label", "column_id", "start_year", "end_year"], orient="row"
    ).write_parquet(tmp_path / "6_genealogy_lane.parquet")
    pl.DataFrame(
        [("B", "b", "W", 0, 10), ("B", "b", "Y", 0, 10)],
        schema=["entity_id", "label", "column_id", "start_year", "end_year"],
        orient="row",
    ).write_parquet(tmp_path / "6_genealogy_band.parquet")
    pl.DataFrame(
        [("W", 0.0), ("X", 5.0), ("Y", 10.0), ("B", None)], schema=["entity_id", "longitude"], orient="row"
    ).write_parquet(tmp_path / "1_cultural_entity.parquet")
    write_column_region(tmp_path, {"W": "americas", "X": "europe", "Y": "asia"})

    lane_order(tmp_path)

    assert pl.read_parquet(tmp_path / "7_lane_order.parquet").sort("position").select(
        "column_id", "region_id"
    ).rows() == [
        ("W", "americas"),
        ("X", "europe"),
        ("Y", "asia"),
    ]


def test_column_region_votes_by_footprint_continent_and_falls_back_to_other(tmp_path):
    bronze, silver = tmp_path / "bronze", tmp_path / "silver"
    bronze.mkdir()
    silver.mkdir()
    pl.DataFrame(
        [
            ("TUR", 0, "TUR", "Asia"),
            ("GRC", 0, "GRC", "Europe"),
            ("TUR-1", 1, "TUR", None),
            ("TUR-2", 1, "TUR", None),
            ("GRC-1", 1, "GRC", None),
            ("ATA", 0, "ATA", "Antarctica"),
        ],
        schema=["ne_id", "level", "adm0_a3", "continent"],
        orient="row",
    ).write_parquet(bronze / "natural_earth_admin.parquet")
    pl.DataFrame(
        [("TUR-1", 1, "u1", "T"), ("TUR-2", 1, "u2", "T"), ("GRC-1", 1, "u3", "G"), ("GRC", 0, "G", "G")],
        schema=["ne_id", "level", "entity_id", "country_entity_id"],
        orient="row",
    ).write_parquet(silver / "1_entity_ne_admin.parquet")
    pl.DataFrame(
        [("EMPIRE", "u1"), ("EMPIRE", "u2"), ("EMPIRE", "u3"), ("NOWHERE", "missing")],
        schema=["entity_id", "admin_id"],
        orient="row",
    ).with_columns(rule=pl.lit("cover")).write_parquet(silver / "8_entity_footprint.parquet")
    pl.DataFrame(
        [("EMPIRE", "EMPIRE"), ("G", "GRCOL"), ("NOWHERE", "NOWHERE")],
        schema=["entity_id", "column_id"],
        orient="row",
    ).write_parquet(silver / "6_genealogy_lane.parquet")

    column_region(bronze, silver)

    assert pl.read_parquet(silver / "6_column_region.parquet").sort("column_id").select(
        "column_id", "region_id", "region_label"
    ).rows() == [("EMPIRE", "asia", "Asia"), ("GRCOL", "europe", "Europe"), ("NOWHERE", "other", "Other")]


def test_column_region_ties_and_direct_fallback(tmp_path):
    bronze, silver = tmp_path / "bronze", tmp_path / "silver"
    bronze.mkdir()
    silver.mkdir()
    pl.DataFrame(
        [("A", 0, "A", "Europe"), ("B", 0, "B", "Asia")],
        schema=["ne_id", "level", "adm0_a3", "continent"],
        orient="row",
    ).write_parquet(bronze / "natural_earth_admin.parquet")
    pl.DataFrame([("A", "a"), ("B", "b")], schema=["ne_id", "entity_id"], orient="row").write_parquet(
        silver / "1_entity_ne_admin.parquet"
    )
    pl.DataFrame([("a", "a"), ("a", "b")], schema=["entity_id", "admin_id"], orient="row").write_parquet(
        silver / "8_entity_footprint.parquet"
    )
    pl.DataFrame([("a", "tie"), ("b", "direct")], schema=["entity_id", "column_id"], orient="row").write_parquet(
        silver / "6_genealogy_lane.parquet"
    )
    column_region(bronze, silver)
    rows = dict(pl.read_parquet(silver / "6_column_region.parquet").select("column_id", "region_id").rows())
    assert rows == {"tie": "europe", "direct": "asia"}
