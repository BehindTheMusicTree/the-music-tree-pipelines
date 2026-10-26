import json
import logging
from pathlib import Path
from typing import Any

import httpx
import polars as pl
from common.env import load_pipeline_env, require_env, resolve_pipeline_path

logger = logging.getLogger(__name__)

# Column order is each CSV's header, which the downstream Silver/Gold steps read by name. Listed
# here (not derived from the response) so an empty list still gets its header-only CSV. The API's
# key order may differ: rows are matched by column set and rebuilt in this order.
CURATION_LISTS: dict[str, tuple[str, ...]] = {
    "accepted_canonical_roots": ("item_id",),
    "canonical_parent_additions": ("item_id", "label", "reason"),
    "capitalized_words": ("word", "capitalized", "reason"),
    "duplicate_genres": ("item_id", "reason"),
    "indigenous_to_exclusions": ("item_id", "reason"),
    "label_overrides": ("item_id", "display_label", "reason"),
    "main_parent": ("item_id", "reason", "parent_item_id", "exclude_other_parents"),
    "out_of_scope_genres": ("item_id", "reason"),
    "overview_reclassifications": ("item_id", "reason"),
    "regional_overrides": ("item_id", "reason", "overview_item_id", "exclude_other_parents"),
    "regional_overview_additions": ("item_id", "label", "reason"),
    "technique_genres": ("item_id", "reason"),
    "theme_genres": ("item_id", "reason"),
    "non_stylistic_genres": ("item_id", "reason"),
    "umbrella_genres": ("item_id", "reason"),
    "accepted_non_genre_tags": ("musicbrainz_genre_name", "reason"),
    "canonical_genre_pop_side": ("root_genre_name", "pop_child_genre_name", "reason"),
    "genre_alias": ("musicbrainz_genre_name", "wikidata_genre_name", "reason"),
    "genre_precedence": ("item_id", "over_item_id", "reason"),
    "entity_exclusions": ("entity_id", "reason"),
    "entity_membership_overrides": ("child_entity_id", "parent_entity_id", "action", "reason"),
    "entity_time_span_overrides": ("entity_id", "start_year", "end_year", "reason"),
    "song_genre": ("musicbrainz_recording_id", "genre_item_id", "reason"),
    "recording_youtube_video": ("musicbrainz_recording_id", "youtube_video_id", "unplayable_reason", "reason"),
    "secondary_parents": ("item_id", "parent_id", "action", "reason"),
    "essential_songs": ("item_id", "musicbrainz_recording_id", "position", "reason"),
    "summary_overrides": ("item_id", "summary", "reason"),
}


def fetch_curation_export(base_url: str, api_key: str) -> dict[str, Any]:
    response = httpx.get(f"{base_url}curation/export/", headers={"X-API-Key": api_key}, timeout=60)
    response.raise_for_status()
    return response.json()


def write_curation_csvs(export: dict[str, Any], output_dir: Path) -> list[Path]:
    missing = sorted(CURATION_LISTS.keys() - export.keys())
    if missing:
        raise ValueError(f"curation export is missing list(s): {missing}")
    exported_on = export.get("exported_on")
    if not isinstance(exported_on, str) or not exported_on:
        raise ValueError(f"curation export has no 'exported_on' timestamp: {exported_on!r}")

    output_dir.mkdir(parents=True, exist_ok=True)
    paths = []
    for list_name, columns in CURATION_LISTS.items():
        rows = export[list_name]
        bad_rows = [row for row in rows if set(row) != set(columns)]
        if bad_rows:
            raise ValueError(f"curation list {list_name!r} has row(s) not matching columns {columns}: {bad_rows[:3]}")

        # Blank values become nulls so they're written as empty unquoted fields, matching the
        # hand-edited CSVs: polars quotes empty strings, which would read back as "" rather than null.
        df = pl.DataFrame(
            [[row[column] for column in columns] for row in rows],
            schema=dict.fromkeys(columns, pl.Utf8),
            orient="row",
        ).with_columns(pl.all().replace("", None))
        path = output_dir / f"manual_{list_name}.csv"
        df.write_csv(path)
        logger.info("wrote %d rows to %s", df.height, path)
        paths.append(path)

    # Gold stamps this into its tree exports so grow can tell which curation a tree was built from.
    path = output_dir / "manual_export.json"
    path.write_text(json.dumps({"exportedOn": exported_on}))
    logger.info("wrote curation export timestamp %s to %s", exported_on, path)
    paths.append(path)
    return paths


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    load_pipeline_env(__file__)
    output_dir = resolve_pipeline_path(__file__, require_env("BRONZE_OUTPUT_DIR"))
    write_curation_csvs(
        fetch_curation_export(require_env("GROW_API_BASE_URL"), require_env("GROW_API_KEY")), output_dir
    )
