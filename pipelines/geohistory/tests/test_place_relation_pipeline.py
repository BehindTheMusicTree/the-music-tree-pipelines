import json

import polars as pl
import shapely

from geohistory.silver.place_relation import resolve_place_candidates


def test_disk_resolution_preserves_partial_scoped_evidence_and_curated_rejection(tmp_path, monkeypatch):
    from geohistory.silver import place_relation as module

    for name in ("bronze", "wikidata", "silver", "curation"):
        (tmp_path / name).mkdir()
    wikidata = tmp_path / "wikidata"
    pl.DataFrame(
        {
            "statement_id": ["scoped", "unscoped"],
            "subject_item_id": ["Q1", "Q1"],
            "property": ["P706", "P361"],
            "object_item_id": ["Q2", "Q2"],
            "rank": ["normal", "normal"],
            "snak_type": ["value", "value"],
        }
    ).write_parquet(wikidata / "wikidata_place_statement.parquet")
    pl.DataFrame(
        {
            "statement_id": ["scoped"],
            "qualifier_property": ["P518"],
            "value": ["Q3"],
            "value_type": ["wikibase-item"],
            "time_precision": [None],
        }
    ).write_parquet(wikidata / "wikidata_place_qualifier.parquet")
    pl.DataFrame({"requested_id": ["Q1"]}).write_parquet(wikidata / "wikidata_place_metadata.parquet")
    (tmp_path / "curation/place_relation_overrides.csv").write_text(
        "child_entity_id,parent_entity_id,extent,reason\ncountry,other,reject,reviewed\n"
    )
    island = shapely.box(0, 0, 10, 10)
    country = shapely.union_all([island, shapely.box(20, 0, 30, 10)])
    monkeypatch.setattr(module, "reference_admin_geometries", lambda *_: {"country": country})
    monkeypatch.setattr(module, "reference_island_geometries", lambda *_: {"island": island})
    entities = pl.DataFrame(
        {"entity_id": ["country", "island", "other"], "kind": ["country", "macro_region", "macro_region"]}
    )
    crosswalk = pl.DataFrame({"entity_id": ["country", "island"], "wikidata_id": ["Q1", "Q2"]})
    candidates = pl.DataFrame(
        {"parent_id": ["island", "island", "other"], "child_id": ["country"] * 3, "source": ["p706", "p361", "p361"]}
    )
    result = resolve_place_candidates(
        tmp_path / "bronze",
        wikidata,
        tmp_path / "silver",
        tmp_path / "curation",
        candidates,
        entities,
        crosswalk,
        pl.DataFrame(),
    )
    assert result.is_empty()
    relations = pl.read_parquet(tmp_path / "silver/2_place_relation.parquet")
    assert relations.select("child_id", "parent_id", "extent").rows() == [("country", "island", "partial")]
    assert set(json.loads(relations["statement_ids"][0])) == {"scoped", "unscoped"}
    review = pl.read_csv(tmp_path / "silver/2_place_relation_review.csv")
    assert review["extent"].to_list() == ["reject"]
    report = json.loads((tmp_path / "silver/2_place_relation_report.json").read_text())
    assert report["decisions"] == {"partial": 1, "reject": 1}
    assert report["scopedStatements"] == 1
    from geohistory.silver.place_containment import place_containment

    silver = tmp_path / "silver"
    entities.write_parquet(silver / "1_cultural_entity.parquet")
    crosswalk.write_parquet(silver / "1_entity_wikidata_item.parquet")
    pl.DataFrame(
        schema={"ne_id": pl.String, "level": pl.Int8, "entity_id": pl.String, "country_entity_id": pl.String}
    ).write_parquet(silver / "1_entity_ne_admin.parquet")
    pl.DataFrame({"child_id": ["country"], "parent_id": ["island"], "source": ["wikidata_p361"]}).write_parquet(
        silver / "2_entity_membership.parquet"
    )
    pl.DataFrame({"item_id": ["Q1"], "property": ["P706"], "value": ["Q2"]}).write_parquet(
        wikidata / "wikidata_cultural_entity_claim.parquet"
    )
    pl.DataFrame(schema={"item_id": pl.String, "property": pl.String, "value": pl.String}).write_parquet(
        wikidata / "wikidata_admin_hierarchy.parquet"
    )
    (tmp_path / "curation/entity_membership_overrides.csv").write_text(
        "child_entity_id,parent_entity_id,action,reason\n"
    )
    path = place_containment(tmp_path / "bronze", wikidata, silver, tmp_path / "curation")
    assert pl.read_parquet(path).is_empty()
    assert pl.read_parquet(silver / "2_place_relation.parquet")["extent"].to_list() == ["partial"]
    from geohistory.silver.entity_coverage import entity_coverage
    from geohistory.silver.entity_geometry import entity_geometry

    pl.DataFrame({"entity_id": ["country"], "admin_id": ["country"], "rule": ["self"]}).write_parquet(
        silver / "8_entity_footprint.parquet"
    )
    monkeypatch.setattr("geohistory.silver.entity_geometry.reference_admin_geometries", lambda *_: {"country": country})
    entity_geometry(tmp_path / "bronze", silver)
    shapes = pl.read_parquet(silver / "8_entity_geometry.parquet")
    island_shape = shapely.from_wkb(shapes.filter(pl.col("entity_id") == "island")["geometry_wkb"].item())
    assert shapely.equals(island_shape, island)
    # Coverage uses actual geometry; the distant component of the country never appears on this island.
    monkeypatch.setattr("geohistory.silver.reference_land.reference_admin_geometries", lambda *_: {"country": country})
    coverage = pl.read_parquet(entity_coverage(tmp_path / "bronze", silver))
    island_coverage = coverage.filter(pl.col("entity_id") == "island")
    assert island_coverage["extent"].to_list() == ["partial"]
    assert shapely.equals(shapely.from_wkb(island_coverage["geometry_wkb"].item()), island)
