"""On-demand export of the Silver `3_song_example` dataset to JSON.

Not a scheduled/systemd job — run manually whenever the example song set needs refreshing,
then copy the resulting file into the downstream API repo next to its `genre_example_tree.json`
fixture (see `pipelines/musicbrainz/SCHEMA.md#silver`). Requires `musicbrainz.silver` to have
already been run (reads `SILVER_OUTPUT_DIR/3_song_example.parquet`).

Usage:
    uv run --package musicbrainz python scripts/export_song_example_json.py [output.json]

`output.json` defaults to `SILVER_OUTPUT_DIR/song_example.json` if omitted.
"""

import argparse
import json
import logging
from pathlib import Path

import polars as pl
from common.env import load_pipeline_env, require_env, resolve_pipeline_path

import musicbrainz

logger = logging.getLogger(__name__)


def export_song_example_json(silver_dir: Path, output_path: Path) -> Path:
    songs = pl.read_parquet(silver_dir / "3_song_example.parquet")
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(json.dumps(songs.to_dicts(), indent=2, ensure_ascii=False))
    logger.info("wrote %d songs to %s", songs.height, output_path)
    return output_path


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    load_pipeline_env(musicbrainz.__file__)
    silver_dir = resolve_pipeline_path(musicbrainz.__file__, require_env("SILVER_OUTPUT_DIR"))

    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("output", nargs="?", type=Path, default=silver_dir / "song_example.json")
    args = parser.parse_args()

    export_song_example_json(silver_dir, args.output)
