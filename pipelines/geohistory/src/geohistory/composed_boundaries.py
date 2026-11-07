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
        if selected_ids - (definitions.keys() | {"Q35942"}):
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
    if selected_ids is None or "Q35942" in selected_ids:
        if subdivisions is None:
            raise ValueError("Polynesia composition requires Natural Earth administrative subdivisions")
        rows.append(polynesia_composition(collection, subdivisions, version))
    if subdivisions is not None and selected_ids is None:
        rows.extend(display_compositions(collection, subdivisions, version))
        rows.extend(administrative_compositions(subdivisions, version))
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


def administrative_compositions(subdivisions: dict, version: str) -> list[tuple]:
    definitions = {
        "Q5777": (
            ("ES-BA", "ES-CC"),
            "Current Extremadura: Badajoz and Caceres provinces; Statute of Autonomy, article 2 "
            "(https://www.boe.es/eli/es/lo/2011/01/28/1/con)",
        )
    }
    rows = []
    for qid, (codes, definition) in definitions.items():
        members = [f for f in subdivisions["features"] if f["properties"].get("iso_3166_2") in codes]
        if sorted(f["properties"]["iso_3166_2"] for f in members) != sorted(codes):
            raise ValueError(f"administrative composition {qid} requires exactly one member for each of {codes}")
        if any(f["geometry"] is None for f in members):
            raise ValueError(f"administrative composition {qid} has a member without geometry")
        geometry = shapely.union_all([shapely.make_valid(shapely.geometry.shape(f["geometry"])) for f in members])
        rows.append(
            (
                qid,
                geometry.wkb,
                "natural_earth_admin_unit_union",
                version,
                definition,
                json.dumps([{"field": "iso_3166_2", "value": code} for code in sorted(codes)]),
                True,
                "containment",
            )
        )
    return rows


def polynesia_composition(collection: dict, subdivisions: dict, version: str) -> tuple:
    """Select named map units and whole island components, never an enclosing ocean triangle."""
    units = ("PCN", "PYF", "COK", "TON", "WLF", "WSM", "TUV", "NIU", "ASM", "TKL")
    selected = [f for f in collection["features"] if f["properties"].get("GU_A3") in units]
    missing = set(units) - {f["properties"]["GU_A3"] for f in selected}
    if missing or len(selected) != len(units):
        raise ValueError(f"Polynesia composition requires exactly one of each map unit; missing: {sorted(missing)}")
    hawaii = [f for f in subdivisions["features"] if f["properties"].get("iso_3166_2") == "US-HI"]
    if len(hawaii) != 1:
        raise ValueError("Polynesia composition requires exactly one US-HI subdivision")
    members = []
    identities: list[dict] = []
    for feature in [*selected, *hawaii]:
        if feature["geometry"] is None:
            raise ValueError("Polynesia composition has a member without geometry")
        members.append(shapely.make_valid(shapely.geometry.shape(feature["geometry"])))
        properties = feature["properties"]
        identities.append(
            {"field": "NE_ID", "value": str(properties["NE_ID"] if "NE_ID" in properties else properties["ne_id"])}
        )
    # Windows identify whole land components; they never clip land or create a border.
    for unit, name, bounds in (
        ("NZL", "Aotearoa main islands", (165, -47.5, 180, -33)),
        ("NZL", "Chatham Islands", (-178, -45, -175, -43)),
        ("CHL", "Rapa Nui", (-110, -28, -109, -27)),
    ):
        candidates = [f for f in collection["features"] if f["properties"].get("GU_A3") == unit]
        if len(candidates) != 1 or candidates[0]["geometry"] is None:
            raise ValueError(f"Polynesia composition requires one mapped source for {name}")
        window = shapely.box(*bounds)
        parts = list(shapely.get_parts(shapely.make_valid(shapely.geometry.shape(candidates[0]["geometry"]))))
        hits = [part for part in parts if part.intersects(window)]
        if not hits or any(not window.covers(part) for part in hits):
            raise ValueError(f"Polynesia component selector is missing or cuts land: {name}")
        members.extend(hits)
        identities.append({"field": "GU_A3/component_window", "value": unit, "name": name, "bounds": bounds})
    return (
        "Q35942",
        shapely.union_all(members).wkb,
        "natural_earth_map_unit_union",
        version,
        "Polynesian core land convention: Pacific island map units, Hawaii, Aotearoa main and Chatham islands, "
        "and Rapa Nui; excludes subantarctic/Kermadec islands and Polynesian outliers outside the triangle. "
        "https://teara.govt.nz/mi/map/43839/polynesian-languages",
        json.dumps(sorted(identities, key=lambda item: (item["field"], item["value"], item.get("name", "")))),
        False,
        "containment",
    )
