import logging
from datetime import UTC, datetime

import httpx
from common.env import load_pipeline_env, require_env, resolve_pipeline_path

import musicbrainz
from musicbrainz.silver.recording_genre import recording_genre
from musicbrainz.silver.recording_link import recording_link
from musicbrainz.silver.songs import songs, youtube_candidates
from musicbrainz.silver.youtube_video_status import youtube_video_status

logging.basicConfig(level=logging.INFO)
load_pipeline_env(musicbrainz.__file__)
bronze_dir = resolve_pipeline_path(musicbrainz.__file__, require_env("BRONZE_OUTPUT_DIR"))
silver_dir = resolve_pipeline_path(musicbrainz.__file__, require_env("SILVER_OUTPUT_DIR"))
curation_dir = resolve_pipeline_path(musicbrainz.__file__, require_env("CURATION_BRONZE_DIR"))
youtube_api_key = require_env("YOUTUBE_API_KEY")
youtube_status_max_batches = int(require_env("YOUTUBE_STATUS_MAX_BATCHES_PER_RUN"))
recording_link(bronze_dir, silver_dir)
recording_genre(bronze_dir, silver_dir)
youtube_candidates(silver_dir, silver_dir)
with httpx.Client() as client:
    youtube_video_status(silver_dir, silver_dir, client, youtube_api_key, datetime.now(UTC), youtube_status_max_batches)
songs(bronze_dir, silver_dir, silver_dir, curation_dir / "manual_genre_precedence.csv")
