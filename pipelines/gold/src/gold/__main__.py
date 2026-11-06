import logging
import os

from common.env import load_pipeline_env, require_env, resolve_pipeline_path

import gold
from gold.build import build_gold

logging.basicConfig(level=logging.INFO)
load_pipeline_env(gold.__file__)
build_gold(
    resolve_pipeline_path(gold.__file__, require_env("MUSICBRAINZ_SILVER_DIR")),
    resolve_pipeline_path(gold.__file__, require_env("WIKIDATA_SILVER_DIR")),
    resolve_pipeline_path(gold.__file__, require_env("WIKIDATA_BRONZE_DIR")),
    resolve_pipeline_path(gold.__file__, require_env("GEOHISTORY_SILVER_DIR")),
    resolve_pipeline_path(gold.__file__, require_env("CURATION_BRONZE_DIR")),
    resolve_pipeline_path(gold.__file__, require_env("GOLD_OUTPUT_DIR")),
    resolve_pipeline_path(gold.__file__, require_env("ARTIST_IMAGES_PUBLISHED_DIR")),
    artist_images_snapshot_id=os.environ.get("ARTIST_IMAGES_SNAPSHOT_ID"),
)
