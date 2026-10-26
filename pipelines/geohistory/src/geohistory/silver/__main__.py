import logging

from common.env import load_pipeline_env, require_env, resolve_pipeline_path

import geohistory
from geohistory.silver.cultural_entity import cultural_entity
from geohistory.silver.entity_membership import entity_membership
from geohistory.silver.entity_succession import entity_succession
from geohistory.silver.entity_time_span import entity_time_span

logging.basicConfig(level=logging.INFO)
load_pipeline_env(geohistory.__file__)
bronze_dir = resolve_pipeline_path(geohistory.__file__, require_env("BRONZE_OUTPUT_DIR"))
wikidata_bronze_dir = resolve_pipeline_path(geohistory.__file__, require_env("WIKIDATA_BRONZE_DIR"))
silver_dir = resolve_pipeline_path(geohistory.__file__, require_env("SILVER_OUTPUT_DIR"))
cultural_entity(bronze_dir, wikidata_bronze_dir, silver_dir)
entity_membership(bronze_dir, wikidata_bronze_dir, silver_dir)
entity_succession(wikidata_bronze_dir, silver_dir)
entity_time_span(bronze_dir, wikidata_bronze_dir, silver_dir)
