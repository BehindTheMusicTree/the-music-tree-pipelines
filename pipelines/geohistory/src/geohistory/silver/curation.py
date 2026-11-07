from pathlib import Path
from typing import cast

from geohistory.silver.place_relation_resolution import Decision

import polars as pl

LISTS: dict[str, tuple[str, ...]] = {
    "entity_additions": ("entity_id", "entity_kind", "reason"),
    "entity_exclusions": ("entity_id", "reason"),
    "entity_membership_overrides": ("child_entity_id", "parent_entity_id", "action", "reason"),
    "place_relation_overrides": ("child_entity_id", "parent_entity_id", "extent", "reason"),
    "entity_time_span_overrides": ("entity_id", "start_year", "end_year", "reason"),
    "federated_state_overrides": ("entity_id", "action", "reason"),
    "region_overrides": ("entity_id", "region_id", "reason"),
    "tradition_additions": ("wikidata_id", "reason"),
    "tradition_exclusions": ("wikidata_id", "reason"),
    "tradition_placement_overrides": ("wikidata_id", "entity_id", "start_year", "end_year", "reason"),
    "tradition_link_overrides": ("from_wikidata_id", "to_wikidata_id", "kind", "action", "reason"),
}
ADDITION_KINDS = ("cultural_region", "macro_region", "city")
MEMBERSHIP_ACTIONS = ("add", "remove")
TRADITION_LINK_KINDS = ("lineage", "association")


def read_curation_list(curation_dir: Path, list_name: str) -> pl.DataFrame:
    path = curation_dir / f"{list_name}.csv"
    rules = pl.read_csv(path, infer_schema=False)
    if rules.columns != list(LISTS[list_name]):
        raise ValueError(f"{path} has columns {rules.columns}, expected {list(LISTS[list_name])}")
    return rules


def read_place_relation_overrides(curation_dir: Path) -> dict[tuple[str, str], tuple[Decision, str]]:
    path = curation_dir / "place_relation_overrides.csv"
    if not path.exists():
        raise FileNotFoundError(f"{path} is missing: refresh curation Bronze from a compatible grow-api export")
    rules = read_curation_list(curation_dir, "place_relation_overrides")
    result: dict[tuple[str, str], tuple[Decision, str]] = {}
    for child, parent, extent, reason in rules.iter_rows():
        if (
            not child
            or not parent
            or child == parent
            or extent not in {"full", "partial", "reject"}
            or not reason
            or not reason.strip()
        ):
            raise ValueError(f"invalid place relation override in {path}: {child}, {parent}")
        pair = (child, parent)
        if pair in result:
            raise ValueError(f"duplicate place relation override in {path}: {pair}")
        result[pair] = (cast(Decision, extent), reason)
    return result
