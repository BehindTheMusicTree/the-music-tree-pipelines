import logging

from common.env import load_pipeline_env, require_env, resolve_pipeline_path

import musicbrainz
from musicbrainz.silver.recording_genre import recording_genre
from musicbrainz.silver.recording_link import recording_link
from musicbrainz.silver.song_example import song_example

logging.basicConfig(level=logging.INFO)
load_pipeline_env(musicbrainz.__file__)
bronze_dir = resolve_pipeline_path(musicbrainz.__file__, require_env("BRONZE_OUTPUT_DIR"))
silver_dir = resolve_pipeline_path(musicbrainz.__file__, require_env("SILVER_OUTPUT_DIR"))
recording_link(bronze_dir, silver_dir)
recording_genre(bronze_dir, silver_dir)
song_example(bronze_dir, silver_dir, silver_dir)
