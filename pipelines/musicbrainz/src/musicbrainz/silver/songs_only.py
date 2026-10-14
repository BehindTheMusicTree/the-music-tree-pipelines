"""Recompute only `5_songs.parquet` from the existing Bronze + Silver, for a curation run: no MusicBrainz ingest,
no `1_recording_link`/`2_recording_genre`/`3_youtube_candidates`/`4_youtube_video_status` (so no YouTube quota)."""

import logging

from common.env import load_pipeline_env, require_env, resolve_pipeline_path

import musicbrainz
from musicbrainz.silver.songs import songs

_BRONZE_INPUTS = (
    "genre",
    "recording",
    "artist_credit_name",
    "artist",
    "l_recording_work",
    "recording_first_release_date",
)
_SILVER_INPUTS = ("2_recording_genre", "3_youtube_candidates", "4_youtube_video_status")


def main() -> None:
    load_pipeline_env(musicbrainz.__file__)
    bronze_dir = resolve_pipeline_path(musicbrainz.__file__, require_env("BRONZE_OUTPUT_DIR"))
    silver_dir = resolve_pipeline_path(musicbrainz.__file__, require_env("SILVER_OUTPUT_DIR"))
    curation_dir = resolve_pipeline_path(musicbrainz.__file__, require_env("CURATION_BRONZE_DIR"))
    inputs = [bronze_dir / f"{name}.parquet" for name in _BRONZE_INPUTS]
    inputs += [silver_dir / f"{name}.parquet" for name in _SILVER_INPUTS]
    inputs.append(curation_dir / "manual_genre_precedence.csv")
    missing = [str(path) for path in inputs if not path.is_file()]
    if missing:
        raise FileNotFoundError(f"songs-only run needs a previous full run's output, missing: {missing}")
    songs(bronze_dir, silver_dir, silver_dir, curation_dir / "manual_genre_precedence.csv")


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    main()
