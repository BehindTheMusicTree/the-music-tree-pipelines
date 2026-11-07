import json

import polars as pl
import pytest
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
    from geohistory.composed_boundaries import COMPOSED_BOUNDARY_SCHEMA

    pl.DataFrame(schema=COMPOSED_BOUNDARY_SCHEMA).write_parquet(
        tmp_path / "bronze/natural_earth_composed_boundary.parquet"
    )
    entities = pl.DataFrame(
        {"entity_id": ["country", "island", "other"], "kind": ["country", "macro_region", "macro_region"]}
    ).with_columns(pl.lit(None, dtype=pl.Float64).alias("latitude"), pl.lit(None, dtype=pl.Float64).alias("longitude"))
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


def test_current_admin_p131_can_resolve_without_a_parent_polygon(tmp_path, monkeypatch):
    from geohistory.silver import place_relation as module
    from wikidata.place_statements import QUALIFIER_SCHEMA, SCHEMA

    for name in ("bronze", "wikidata", "silver", "curation"):
        (tmp_path / name).mkdir()
    wikidata = tmp_path / "wikidata"
    pl.DataFrame([("admin", "Q1", "P131", "Q2", "normal", "value")], schema=SCHEMA, orient="row").write_parquet(
        wikidata / "wikidata_place_statement.parquet"
    )
    pl.DataFrame(schema=QUALIFIER_SCHEMA).write_parquet(wikidata / "wikidata_place_qualifier.parquet")
    pl.DataFrame({"requested_id": ["Q1", "Q2"]}).write_parquet(wikidata / "wikidata_place_metadata.parquet")
    (tmp_path / "curation/place_relation_overrides.csv").write_text("child_entity_id,parent_entity_id,extent,reason\n")
    monkeypatch.setattr(module, "reference_admin_geometries", lambda *_: {})
    monkeypatch.setattr(module, "reference_island_geometries", lambda *_: {})
    from geohistory.composed_boundaries import COMPOSED_BOUNDARY_SCHEMA

    pl.DataFrame(schema=COMPOSED_BOUNDARY_SCHEMA).write_parquet(
        tmp_path / "bronze/natural_earth_composed_boundary.parquet"
    )
    entities = pl.DataFrame({"entity_id": ["child", "parent"], "kind": ["admin_unit", "country"]})
    candidates = pl.DataFrame({"parent_id": ["parent"], "child_id": ["child"], "source": ["p131"]})
    args = (
        tmp_path / "bronze",
        wikidata,
        tmp_path / "silver",
        tmp_path / "curation",
        candidates,
        entities,
        pl.DataFrame({"entity_id": ["child", "parent"], "wikidata_id": ["Q1", "Q2"]}),
        pl.DataFrame({"entity_id": ["child"]}),
    )
    assert module.resolve_place_candidates(*args).equals(candidates)
    for property_, value, value_type in (("P518", "Q3", "wikibase-item"), ("P580", "+1900-01-01T00:00:00Z", "time")):
        pl.DataFrame(
            [("admin", property_, value, value_type, "value", None)], schema=QUALIFIER_SCHEMA, orient="row"
        ).write_parquet(wikidata / "wikidata_place_qualifier.parquet")
        assert module.resolve_place_candidates(*args).is_empty()


@pytest.mark.parametrize("crosses_parent", [False, True])
def test_collapsed_hierarchy_cannot_invent_unscoped_statement(tmp_path, monkeypatch, crosses_parent):
    from geohistory.silver import place_relation as module
    from geohistory.composed_boundaries import COMPOSED_BOUNDARY_SCHEMA
    from wikidata.place_statements import QUALIFIER_SCHEMA, SCHEMA

    for name in ("bronze", "wikidata", "silver", "curation"):
        (tmp_path / name).mkdir()
    wikidata = tmp_path / "wikidata"
    pl.DataFrame(
        [("dated-intermediate", "Q1", "P131", "Q9", "normal", "value")], schema=SCHEMA, orient="row"
    ).write_parquet(wikidata / "wikidata_place_statement.parquet")
    pl.DataFrame(
        [("dated-intermediate", "P580", "+1900-01-01T00:00:00Z", "time", "value", 9)],
        schema=QUALIFIER_SCHEMA,
        orient="row",
    ).write_parquet(wikidata / "wikidata_place_qualifier.parquet")
    pl.DataFrame({"requested_id": ["Q1", "Q2", "Q9"]}).write_parquet(wikidata / "wikidata_place_metadata.parquet")
    pl.DataFrame(schema=COMPOSED_BOUNDARY_SCHEMA).write_parquet(
        tmp_path / "bronze/natural_earth_composed_boundary.parquet"
    )
    (tmp_path / "curation/place_relation_overrides.csv").write_text("child_entity_id,parent_entity_id,extent,reason\n")
    monkeypatch.setattr(
        module,
        "reference_admin_geometries",
        lambda *_: {"child": shapely.box(1, 1, 4 if crosses_parent else 2, 2), "parent": shapely.box(0, 0, 3, 3)},
    )
    monkeypatch.setattr(module, "reference_island_geometries", lambda *_: {})
    result = module.resolve_place_candidates(
        tmp_path / "bronze",
        wikidata,
        tmp_path / "silver",
        tmp_path / "curation",
        pl.DataFrame({"parent_id": ["parent"], "child_id": ["child"], "source": ["p131"]}),
        pl.DataFrame({"entity_id": ["child", "parent"], "kind": ["admin_unit", "country"]}),
        pl.DataFrame({"entity_id": ["child", "parent"], "wikidata_id": ["Q1", "Q2"]}),
        pl.DataFrame({"entity_id": ["child", "parent"]}),
    )
    assert result.is_empty()
    assert pl.read_parquet(tmp_path / "silver/2_place_relation.parquet")["extent"].to_list() == ["unknown"]
    # A collapsed path is usable only when every original leg is independently current and unscoped.
    pl.DataFrame(
        [("first", "Q1", "P131", "Q9", "normal", "value"), ("second", "Q9", "P131", "Q2", "normal", "value")],
        schema=SCHEMA,
        orient="row",
    ).write_parquet(wikidata / "wikidata_place_statement.parquet")
    for prop, expected in (("P580", "full"), ("P582", "unknown"), ("P518", "unknown")):
        pl.DataFrame(
            [
                (
                    "first",
                    prop,
                    "Q3" if prop == "P518" else "+1900-01-01T00:00:00Z",
                    "wikibase-item" if prop == "P518" else "time",
                    "value",
                    9,
                )
            ],
            schema=QUALIFIER_SCHEMA,
            orient="row",
        ).write_parquet(wikidata / "wikidata_place_qualifier.parquet")
        module.resolve_place_candidates(
            tmp_path / "bronze",
            wikidata,
            tmp_path / "silver",
            tmp_path / "curation",
            pl.DataFrame({"parent_id": ["parent"], "child_id": ["child"], "source": ["p131"]}),
            pl.DataFrame({"entity_id": ["child", "parent"], "kind": ["admin_unit", "country"]}),
            pl.DataFrame({"entity_id": ["child", "parent"], "wikidata_id": ["Q1", "Q2"]}),
            pl.DataFrame({"entity_id": ["child", "parent"]}),
        )
        relation = pl.read_parquet(tmp_path / "silver/2_place_relation.parquet")
        assert relation["extent"].to_list() == ["unknown" if crosses_parent else expected]
        assert relation["conflict"].item() == (crosses_parent and expected == "full")
        if expected == "full" and not crosses_parent:
            assert {
                entry["statement_id"]
                for entry in json.loads(relation["evidence_json"].item())
                if entry["source"] == "administrative_p131"
            } == {"first", "second"}
    pl.DataFrame(schema=QUALIFIER_SCHEMA).write_parquet(wikidata / "wikidata_place_qualifier.parquet")
    module.resolve_place_candidates(
        tmp_path / "bronze",
        wikidata,
        tmp_path / "silver",
        tmp_path / "curation",
        pl.DataFrame({"parent_id": ["parent"], "child_id": ["child"], "source": ["p131"]}),
        pl.DataFrame(
            {"entity_id": ["child", "parent", "historical"], "kind": ["admin_unit", "country", "historical_region"]}
        ),
        pl.DataFrame({"entity_id": ["child", "parent", "historical"], "wikidata_id": ["Q1", "Q2", "Q9"]}),
        pl.DataFrame({"entity_id": ["child", "parent"]}),
    )
    assert pl.read_parquet(tmp_path / "silver/2_place_relation.parquet")["extent"].to_list() == ["unknown"]
