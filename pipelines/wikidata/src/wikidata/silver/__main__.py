import logging

from common.env import load_pipeline_env, require_env, resolve_pipeline_path

import wikidata
from wikidata.silver.canonical_roots import extract_canonical_roots
from wikidata.silver.genre_parents import flag_genre_parents
from wikidata.silver.hierarchy import prune_genre_hierarchy
from wikidata.silver.item_links import add_item_links
from wikidata.silver.regional_classification import MANUAL_OVERRIDES_PATH, classify_regional_genres
from wikidata.silver.regional_overview_classification import classify_regional_from_overviews

logging.basicConfig(level=logging.INFO)
load_pipeline_env(wikidata.__file__)
bronze_dir = resolve_pipeline_path(wikidata.__file__, require_env("BRONZE_OUTPUT_DIR"))
silver_dir = resolve_pipeline_path(wikidata.__file__, require_env("SILVER_OUTPUT_DIR"))
item_links_path = add_item_links(bronze_dir / "wikidata_genre_tree.parquet", silver_dir)
regional_overview_classification_path = classify_regional_from_overviews(item_links_path, silver_dir)
regional_classification_path = classify_regional_genres(
    regional_overview_classification_path,
    bronze_dir / "wikidata_genre_indigenous_to.parquet",
    bronze_dir / "wikidata_genre_country_of_origin.parquet",
    MANUAL_OVERRIDES_PATH,
    silver_dir,
)
genre_parents_path = flag_genre_parents(regional_classification_path, silver_dir)
hierarchy_path, _ = prune_genre_hierarchy(genre_parents_path, silver_dir)
extract_canonical_roots(hierarchy_path, silver_dir)
