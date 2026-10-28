import logging
from datetime import UTC, datetime

import httpx
from common.env import load_pipeline_env, require_env, resolve_pipeline_path
from common.step_timer import step

import musicbrainz
from musicbrainz.silver.artist_aliases import artist_aliases
from musicbrainz.silver.artist_links import artist_links
from musicbrainz.silver.artist_members import artist_members
from musicbrainz.silver.artist_release_groups import artist_release_groups
from musicbrainz.silver.artists import artists
from musicbrainz.silver.recording_genre import recording_genre
from musicbrainz.silver.recording_link import recording_link
from musicbrainz.silver.song_genre import song_genre
from musicbrainz.silver.songs import songs, youtube_candidates
from musicbrainz.silver.youtube_video_status import youtube_video_status

logging.basicConfig(level=logging.INFO)
load_pipeline_env(musicbrainz.__file__)
bronze_dir = resolve_pipeline_path(musicbrainz.__file__, require_env("BRONZE_OUTPUT_DIR"))
silver_dir = resolve_pipeline_path(musicbrainz.__file__, require_env("SILVER_OUTPUT_DIR"))
curation_dir = resolve_pipeline_path(musicbrainz.__file__, require_env("CURATION_BRONZE_DIR"))
youtube_api_key = require_env("YOUTUBE_API_KEY")
youtube_status_max_batches = int(require_env("YOUTUBE_STATUS_MAX_BATCHES_PER_RUN"))
with step("1_recording_link"):
    recording_link(bronze_dir, silver_dir)
with step("2_recording_genre"):
    recording_genre(bronze_dir, silver_dir)
with step("3_youtube_candidates"):
    youtube_candidates(bronze_dir, silver_dir, silver_dir, curation_dir / "manual_recording_youtube_video.csv")
with step("4_youtube_video_status"), httpx.Client() as client:
    youtube_video_status(silver_dir, silver_dir, client, youtube_api_key, datetime.now(UTC), youtube_status_max_batches)
with step("5_songs"):
    songs(
        bronze_dir,
        silver_dir,
        silver_dir,
        curation_dir / "manual_recording_youtube_video.csv",
    )
with step("6_artists"):
    artists(bronze_dir, silver_dir)
with step("7_artist_members"):
    artist_members(bronze_dir, silver_dir)
with step("8_artist_links"):
    artist_links(bronze_dir, silver_dir)
with step("9_artist_release_groups"):
    artist_release_groups(bronze_dir, silver_dir)
with step("11_artist_aliases"):
    artist_aliases(bronze_dir, silver_dir)
with step("10_song_genre"):
    song_genre(bronze_dir, silver_dir, silver_dir)
