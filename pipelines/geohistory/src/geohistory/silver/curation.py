from pathlib import Path

import polars as pl

LISTS: dict[str, tuple[str, ...]] = {
    "entity_additions": ("entity_id", "reason"),
    "entity_exclusions": ("entity_id", "reason"),
    "entity_membership_overrides": ("child_entity_id", "parent_entity_id", "action", "reason"),
    "entity_time_span_overrides": ("entity_id", "start_year", "end_year", "reason"),
}
MEMBERSHIP_ACTIONS = ("add", "remove")


def read_curation_list(curation_dir: Path, list_name: str) -> pl.DataFrame:
    path = curation_dir / f"manual_{list_name}.csv"
    rules = pl.read_csv(path, infer_schema=False)
    if rules.columns != list(LISTS[list_name]):
        raise ValueError(f"{path} has columns {rules.columns}, expected {list(LISTS[list_name])}")
    return rules
