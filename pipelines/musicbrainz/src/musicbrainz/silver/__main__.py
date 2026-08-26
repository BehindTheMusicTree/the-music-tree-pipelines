import logging

from common.env import load_pipeline_env, require_env, resolve_pipeline_path

import musicbrainz
from musicbrainz.silver.recording_youtube_url import recording_youtube_url

logging.basicConfig(level=logging.INFO)
load_pipeline_env(musicbrainz.__file__)
bronze_dir = resolve_pipeline_path(musicbrainz.__file__, require_env("BRONZE_OUTPUT_DIR"))
silver_dir = resolve_pipeline_path(musicbrainz.__file__, require_env("SILVER_OUTPUT_DIR"))
recording_youtube_url(bronze_dir, silver_dir)
