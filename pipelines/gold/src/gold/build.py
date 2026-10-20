import json
import logging
import os
import subprocess
from datetime import UTC, datetime
from pathlib import Path

import polars as pl

from gold.artist_export import export_artists
from gold.canonical_genre_tree_export import export_canonical_genre_tree
from gold.genre_match import genre_match
from gold.regional_genre_tree_export import export_regional_genre_tree
from gold.song_export import check_known_recordings, export_songs

MANIFEST_NAME = "_manifest.json"

logger = logging.getLogger(__name__)


def pipelines_commit() -> str:
    result = subprocess.run(
        ["git", "rev-parse", "HEAD"], cwd=Path(__file__).parent, capture_output=True, text=True, check=True
    )
    return result.stdout.strip()


def read_curation_exported_on(curation_dir: Path) -> str:
    exported_on = json.loads((curation_dir / "manual_export.json").read_text()).get("exportedOn")
    if not isinstance(exported_on, str) or not exported_on:
        raise ValueError(f"{curation_dir / 'manual_export.json'} has no 'exportedOn': {exported_on!r}")
    return exported_on


def write_manifest(gold_output_dir: Path, curation_exported_on: str, commit: str) -> Path:
    path = gold_output_dir / MANIFEST_NAME
    tmp_path = path.with_name(f"{MANIFEST_NAME}.tmp")
    manifest = {
        "builtOn": datetime.now(UTC).isoformat(),
        "curationExportedOn": curation_exported_on,
        "pipelinesCommit": commit,
    }
    tmp_path.write_text(json.dumps(manifest, indent=2))
    os.replace(tmp_path, path)
    logger.info("wrote %s", path)
    return path


def build_gold(
    musicbrainz_silver_dir: Path,
    wikidata_silver_dir: Path,
    wikidata_bronze_dir: Path,
    curation_dir: Path,
    gold_output_dir: Path,
) -> Path:
    # Unlinked first so a failed run never leaves a previous manifest vouching for partial outputs.
    (gold_output_dir / MANIFEST_NAME).unlink(missing_ok=True)
    commit = pipelines_commit()
    curation_exported_on = read_curation_exported_on(curation_dir)
    descriptions_path = wikidata_bronze_dir / "wikidata_genre_descriptions.parquet"

    export_canonical_genre_tree(
        wikidata_silver_dir,
        gold_output_dir,
        curation_dir / "manual_canonical_genre_pop_side.csv",
        curation_dir,
        descriptions_path,
        curation_exported_on,
    )
    export_regional_genre_tree(
        wikidata_silver_dir,
        gold_output_dir,
        curation_dir,
        descriptions_path,
        curation_exported_on,
    )
    genre_match_path = genre_match(
        musicbrainz_silver_dir / "5_songs.parquet",
        wikidata_silver_dir / "7_canonical_hierarchy.parquet",
        curation_dir / "manual_genre_alias.csv",
        curation_dir / "manual_accepted_non_genre_tags.csv",
        gold_output_dir,
    )
    essential_tracks_csv = "manual_essential_tracks.csv"
    check_known_recordings(
        pl.read_csv(curation_dir / essential_tracks_csv, columns=["musicbrainz_recording_id"], infer_schema=False),
        pl.read_parquet(genre_match_path, columns=["musicbrainz_recording_id"]),
        essential_tracks_csv,
    )
    export_songs(genre_match_path, gold_output_dir, wikidata_silver_dir, curation_dir)
    export_artists(genre_match_path, musicbrainz_silver_dir, gold_output_dir)
    return write_manifest(gold_output_dir, curation_exported_on, commit)
