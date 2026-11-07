import polars as pl
import pytest

from geohistory.silver.cultural_membership import build_cultural_memberships, build_cultural_roles, cultural_membership
from geohistory.silver.curation import LISTS


def rules(name, rows=()):
    return pl.DataFrame(rows, schema={c: pl.String for c in LISTS[name]}, orient="row")


def entities():
    return pl.DataFrame(
        {
            "entity_id": ["Q30", "Q782", "Q1", "Q35942"],
            "kind": ["country", "admin_unit", "admin_unit", "cultural_region"],
        }
    )


def roles():
    return build_cultural_roles(entities(), rules("entity_cultural_roles"))


def edges():
    return pl.DataFrame({"child_id": ["Q782", "Q1"], "parent_id": ["Q30", "Q782"]})


def test_hawaii_keeps_explicit_polynesia_and_inherited_us_membership():
    result = build_cultural_memberships(
        roles(), edges(), rules("cultural_membership_overrides", [("Q782", "Q35942", "add", "documented")])
    )
    assert set(result.select("child_id", "parent_id", "source").rows()) == {
        ("Q782", "Q30", "administrative_inheritance"),
        ("Q1", "Q30", "transitive"),
        ("Q1", "Q782", "administrative_inheritance"),
        ("Q1", "Q35942", "transitive"),
        ("Q782", "Q35942", "curation"),
    }
    assert roles().filter(pl.col("entity_id") == "Q782")["is_cultural"].item() is None


def test_administrative_inheritance_is_automatic_and_cultural_links_spread():
    result = build_cultural_memberships(
        roles(), edges(), rules("cultural_membership_overrides", [("Q782", "Q35942", "add", "documented")])
    )
    assert set(result.select("child_id", "parent_id").rows()) == {
        ("Q782", "Q30"),
        ("Q782", "Q35942"),
        ("Q1", "Q782"),
        ("Q1", "Q30"),
        ("Q1", "Q35942"),
    }


def test_curated_exclusion_wins_over_derived_membership():
    result = build_cultural_memberships(
        roles(), edges(), rules("cultural_membership_overrides", [("Q782", "Q30", "remove", "exception")])
    )
    assert result.select("child_id", "parent_id").rows() == [("Q1", "Q782")]


@pytest.mark.parametrize(
    "row",
    [
        ("Q999", "true", "unknown"),
        ("Q30", "maybe", "invalid"),
        ("Q30", "true", ""),
    ],
)
def test_invalid_roles_fail(row):
    with pytest.raises(ValueError):
        build_cultural_roles(entities(), rules("entity_cultural_roles", [row]))


def test_cycles_in_administrative_inheritance_fail():
    with pytest.raises(ValueError, match="cycle"):
        build_cultural_memberships(
            roles(),
            pl.DataFrame({"child_id": ["Q30", "Q782"], "parent_id": ["Q782", "Q30"]}),
            rules("cultural_membership_overrides"),
        )


def test_spatial_containment_does_not_create_cultural_membership(tmp_path):
    entities().write_parquet(tmp_path / "1_cultural_entity.parquet")
    edges().with_columns(pl.lit("reference_geometry").alias("source")).write_parquet(
        tmp_path / "2_place_containment.parquet"
    )
    edges().with_columns(
        pl.lit("full").alias("extent"),
        pl.lit(False).alias("conflict"),
        pl.lit('[{"source":"reference_geometry","extent":"full"}]').alias("evidence_json"),
    ).write_parquet(tmp_path / "2_place_relation.parquet")
    rules("entity_cultural_roles", [("Q30", "true", "approved")]).write_csv(tmp_path / "entity_cultural_roles.csv")
    rules("cultural_membership_overrides").write_csv(tmp_path / "cultural_membership_overrides.csv")
    _, path = cultural_membership(tmp_path, tmp_path)
    assert pl.read_parquet(path).is_empty()


def test_duplicate_role_rules_fail():
    row = ("Q30", "true", "approved")
    with pytest.raises(ValueError, match="duplicate"):
        build_cultural_roles(entities(), rules("entity_cultural_roles", [row, row]))


@pytest.mark.parametrize(
    "row",
    [
        ("Q999", "Q35942", "add", "unknown child"),
        ("Q782", "Q999", "add", "unknown parent"),
        ("Q35942", "Q35942", "add", "self link"),
        ("Q782", "Q35942", "invalid", "invalid action"),
        ("Q782", "Q35942", "add", " "),
    ],
)
def test_invalid_membership_rules_fail(row):
    with pytest.raises(ValueError, match="invalid or duplicate"):
        build_cultural_memberships(roles(), edges(), rules("cultural_membership_overrides", [row]))


def test_conflicting_membership_rules_fail():
    with pytest.raises(ValueError, match="duplicate"):
        build_cultural_memberships(
            roles(),
            edges(),
            rules(
                "cultural_membership_overrides",
                [
                    ("Q782", "Q35942", "add", "include"),
                    ("Q782", "Q35942", "remove", "exclude"),
                ],
            ),
        )


@pytest.mark.parametrize("source", ["administrative_p131", "natural_earth"])
@pytest.mark.parametrize("conflict,retained,expected", [(False, True, 3), (True, True, 0), (False, False, 0)])
def test_inheritance_requires_current_uncontested_retained_administration(
    tmp_path, source, conflict, retained, expected
):
    entities().write_parquet(tmp_path / "1_cultural_entity.parquet")
    (edges() if retained else edges().clear()).write_parquet(tmp_path / "2_place_containment.parquet")
    edges().with_columns(
        pl.lit("full").alias("extent"),
        pl.lit(conflict).alias("conflict"),
        pl.lit('[{"source":"' + source + '","extent":"full"}]').alias("evidence_json"),
    ).write_parquet(tmp_path / "2_place_relation.parquet")
    rules("entity_cultural_roles", [("Q30", "true", "approved")]).write_csv(tmp_path / "entity_cultural_roles.csv")
    rules("cultural_membership_overrides").write_csv(tmp_path / "cultural_membership_overrides.csv")
    _, path = cultural_membership(tmp_path, tmp_path)
    assert pl.read_parquet(path).height == expected


def test_cultural_cycle_added_to_administration_fails():
    with pytest.raises(ValueError, match="cycle"):
        build_cultural_memberships(
            roles(), edges(), rules("cultural_membership_overrides", [("Q30", "Q1", "add", "cycle")])
        )


def test_exclusion_cannot_be_reintroduced_by_an_alternative_path():
    administrative = pl.DataFrame({"child_id": ["Q1", "Q1", "Q782"], "parent_id": ["Q782", "Q30", "Q30"]})
    result = build_cultural_memberships(
        roles(),
        administrative,
        rules("cultural_membership_overrides", [("Q1", "Q30", "remove", "exception")]),
    )
    assert ("Q1", "Q30") not in result.select("child_id", "parent_id").rows()
    assert ("Q1", "Q782") in result.select("child_id", "parent_id").rows()


def test_excluded_ancestor_does_not_spread_to_descendants():
    administrative = pl.DataFrame({"child_id": ["Q1", "Q782", "Q30"], "parent_id": ["Q782", "Q30", "Q35942"]})
    result = build_cultural_memberships(
        roles(),
        administrative,
        rules("cultural_membership_overrides", [("Q782", "Q35942", "remove", "exception")]),
    )
    pairs = result.select("child_id", "parent_id").rows()
    assert ("Q782", "Q35942") not in pairs
    assert ("Q1", "Q35942") not in pairs
    assert ("Q1", "Q30") in pairs


def test_transitive_path_is_deterministic_and_direct_curation_wins():
    import json

    curated = rules("cultural_membership_overrides", [("Q782", "Q35942", "add", "documented")])
    result = build_cultural_memberships(roles(), edges(), curated)
    assert json.loads(
        result.filter((pl.col("child_id") == "Q1") & (pl.col("parent_id") == "Q35942"))["reason"].item()
    ) == ["Q1", "Q782", "Q35942"]
    assert result.equals(build_cultural_memberships(roles(), edges().reverse(), curated))
    assert (
        result.filter((pl.col("child_id") == "Q782") & (pl.col("parent_id") == "Q35942"))["source"].item() == "curation"
    )


def test_empty_graph_preserves_membership_schema():
    assert build_cultural_memberships(roles(), edges().clear(), rules("cultural_membership_overrides")).is_empty()


def test_unknown_administrative_reference_fails():
    with pytest.raises(ValueError, match="unknown"):
        build_cultural_memberships(
            roles(),
            pl.DataFrame({"child_id": ["Q999"], "parent_id": ["Q30"]}),
            rules("cultural_membership_overrides"),
        )
