"""Explicit boundary conventions built from independent Natural Earth map units."""

import json

import polars as pl
import shapely

COMPOSED_BOUNDARY_SCHEMA = {
    "wikidata_id": pl.String,
    "geometry_wkb": pl.Binary,
    "source": pl.String,
    "version": pl.String,
    "definition": pl.String,
    "members_json": pl.String,
    "complete": pl.Boolean,
    "usage": pl.String,
}


def build_composed_boundaries(
    collection: dict, version: str, subdivisions: dict | None = None, *, selected_ids: set[str] | None = None
) -> pl.DataFrame:
    definitions: dict[str, tuple[str, str | tuple[str, ...], str]] = {
        "Q664609": ("SUBREGION", "Caribbean", "Natural Earth Caribbean map units; excludes mainland coastal regions"),
        "Q29999": ("SOV_A3", "NL1", "All Natural Earth map units under Netherlands sovereignty"),
    }
    definitions.update(
        {
            "Q18097": ("ADM0_A3", ("KOR", "PRK"), "North and South Korean map units, including the DMZ"),
            "Q21195": ("ADM0_A3", ("DNK", "NOR", "SWE"), "Denmark, Norway and Sweden; excludes Danish dependencies"),
            "Q38272": ("ADM0_A3", ("GBR", "IRL", "JEY", "GGY", "IMN"), "UK, Ireland and Crown Dependency map units"),
            "Q52062": (
                "ADM0_A3",
                ("DNK", "NOR", "SWE", "FIN", "ISL", "GRL", "FRO", "ALD"),
                "Nordic countries and self-governing territories; includes Norway's northern dependencies",
            ),
        }
    )
    subregions = {
        "Q11708": "South-Eastern Asia",
        "Q27231": "Eastern Asia",
        "Q27275": "Central Asia",
        "Q27381": "Northern Africa",
        "Q27394": "Southern Africa",
        "Q27407": "Eastern Africa",
        "Q27433": "Middle Africa",
        "Q27449": "Southern Europe",
        "Q27468": "Eastern Europe",
        "Q27479": "Northern Europe",
        "Q27496": "Western Europe",
        "Q27611": "Central America",
        "Q35942": "Polynesia",
        "Q4412": "Western Africa",
        "Q771405": "Southern Asia",
    }
    definitions.update(
        {qid: ("SUBREGION", name, f"Natural Earth statistical subregion: {name}") for qid, name in subregions.items()}
    )
    continents = {
        "Q15": "Africa",
        "Q46": "Europe",
        "Q48": "Asia",
        "Q55643": "Oceania",
        "Q49": "North America",
        "Q18": "South America",
    }
    definitions.update(
        {
            qid: ("CONTINENT", name, f"Natural Earth continent classification: {name}")
            for qid, name in continents.items()
        }
    )
    definitions["Q828"] = (
        "CONTINENT",
        ("North America", "South America"),
        "Natural Earth North and South American map units",
    )
    if selected_ids is not None:
        if selected_ids - definitions.keys():
            raise ValueError("unknown composed boundary selection")
        definitions = {qid: definition for qid, definition in definitions.items() if qid in selected_ids}
    rows = []
    for qid, (field, value, definition) in definitions.items():
        allowed = (value,) if isinstance(value, str) else value
        members = [feature for feature in collection["features"] if feature["properties"][field] in allowed]
        if not members:
            raise ValueError(f"Natural Earth has no members for composed boundary {qid}")
        missing = set(allowed) - {feature["properties"][field] for feature in members}
        if missing:
            raise ValueError(f"Natural Earth composed boundary {qid} is missing members: {sorted(missing)}")
        if any(feature["geometry"] is None for feature in members):
            raise ValueError(f"Natural Earth composed boundary {qid} has a member without geometry")
        geometry = shapely.union_all(
            [shapely.make_valid(shapely.geometry.shape(feature["geometry"])) for feature in members]
        )
        identities = sorted(
            ({"ne_id": str(f["properties"]["NE_ID"]), "name": f["properties"]["NAME"]} for f in members),
            key=lambda member: (member["ne_id"], member["name"]),
        )
        rows.append(
            (
                qid,
                geometry.wkb,
                "natural_earth_map_unit_union",
                version,
                definition,
                json.dumps(identities),
                True,
                "containment",
            )
        )
    if subdivisions is not None and selected_ids is None:
        rows.extend(display_compositions(collection, subdivisions, version))
    return (
        pl.DataFrame(rows, schema=COMPOSED_BOUNDARY_SCHEMA, orient="row")
        .with_columns(
            pl.lit(f"https://github.com/nvkelso/natural-earth-vector/tree/{version}/geojson").alias("source_url"),
            pl.lit("Public domain (Natural Earth)").alias("license"),
        )
        .sort("wikidata_id")
    )


def display_compositions(collection: dict, subdivisions: dict, version: str) -> list[tuple]:
    definitions = {
        "Q236626": (
            "GU_A3",
            ("IRL", "NIR", "SCT", "WLS", "IMN"),
            ("GB-CON", "FR-22", "FR-29", "FR-35", "FR-44", "FR-56"),
            "Six Celtic nations; Ireland includes Northern Ireland; Brittany uses the five historic departments",
            True,
        ),
        "Q23522": (
            "ADM0_A3",
            ("ALB", "BIH", "BGR", "HRV", "GRC", "KOS", "MNE", "MKD", "ROU", "SRB", "SVN"),
            (),
            "Broad Balkan display convention: eleven whole countries; excludes European Turkey; not a historical border",
            False,
        ),
    }
    rows = []
    for qid, (unit_field, units, departments, definition, complete) in definitions.items():
        members = []
        identities = []
        for source, field, names in (
            (collection, unit_field, units),
            (subdivisions, "iso_3166_2", departments),
        ):
            selected = [f for f in source["features"] if f["properties"].get(field) in names]
            missing = set(names) - {f["properties"][field] for f in selected}
            if missing:
                raise ValueError(f"display composition {qid} is missing members: {sorted(missing)}")
            for feature in selected:
                if feature["geometry"] is None:
                    raise ValueError(f"display composition {qid} has a member without geometry")
                members.append(shapely.make_valid(shapely.geometry.shape(feature["geometry"])))
                identities.append({"field": field, "value": feature["properties"][field]})
        rows.append(
            (
                qid,
                shapely.union_all(members).wkb,
                "natural_earth_display_union",
                version,
                definition,
                json.dumps(sorted(identities, key=lambda row: (row["field"], row["value"]))),
                complete,
                "display",
            )
        )
    return rows
