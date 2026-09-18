import logging

from common.env import load_pipeline_env, require_env, resolve_pipeline_path

import wikidata
from wikidata.silver.canonical_hierarchy import prune_canonical_hierarchy
from wikidata.silver.canonical_parents import flag_canonical_parents
from wikidata.silver.canonical_roots import MANUAL_ACCEPTED_ROOTS_PATH, extract_canonical_roots
from wikidata.silver.item_links import MANUAL_CAPITALIZED_WORDS_PATH, MANUAL_LABEL_OVERRIDES_PATH, add_item_links
from wikidata.silver.main_parent_selection import select_main_parents
from wikidata.silver.non_genre_pruning import (
    MANUAL_DUPLICATE_GENRES_PATH,
    MANUAL_OUT_OF_SCOPE_GENRES_PATH,
    MANUAL_TECHNIQUE_GENRES_PATH,
    MANUAL_THEME_GENRES_PATH,
    MANUAL_UMBRELLA_CANONICAL_GENRES_PATH,
    prune_non_genre_items,
)
from wikidata.silver.regional_classification import (
    MANUAL_CANONICAL_PARENT_ADDITIONS_PATH,
    MANUAL_INDIGENOUS_TO_EXCLUSIONS_PATH,
    MANUAL_MAIN_PARENT_PATH,
    MANUAL_OVERRIDES_PATH,
    classify_regional_genres,
)
from wikidata.silver.regional_hierarchy import prune_regional_hierarchy
from wikidata.silver.regional_overview_classification import (
    MANUAL_OVERVIEW_ADDITIONS_PATH,
    MANUAL_OVERVIEW_RECLASSIFICATIONS_PATH,
    classify_regional_from_overviews,
)

logging.basicConfig(level=logging.INFO)
load_pipeline_env(wikidata.__file__)
bronze_dir = resolve_pipeline_path(wikidata.__file__, require_env("BRONZE_OUTPUT_DIR"))
silver_dir = resolve_pipeline_path(wikidata.__file__, require_env("SILVER_OUTPUT_DIR"))
item_links_path = add_item_links(
    bronze_dir / "wikidata_genre_tree.parquet", silver_dir, MANUAL_LABEL_OVERRIDES_PATH, MANUAL_CAPITALIZED_WORDS_PATH
)
non_genre_pruning_path = prune_non_genre_items(
    item_links_path,
    MANUAL_THEME_GENRES_PATH,
    MANUAL_TECHNIQUE_GENRES_PATH,
    MANUAL_OUT_OF_SCOPE_GENRES_PATH,
    MANUAL_UMBRELLA_CANONICAL_GENRES_PATH,
    MANUAL_DUPLICATE_GENRES_PATH,
    silver_dir,
)
regional_overview_classification_path = classify_regional_from_overviews(
    non_genre_pruning_path, MANUAL_OVERVIEW_ADDITIONS_PATH, MANUAL_OVERVIEW_RECLASSIFICATIONS_PATH, silver_dir
)
regional_classification_path = classify_regional_genres(
    regional_overview_classification_path,
    bronze_dir / "wikidata_genre_indigenous_to.parquet",
    MANUAL_OVERRIDES_PATH,
    MANUAL_CANONICAL_PARENT_ADDITIONS_PATH,
    MANUAL_MAIN_PARENT_PATH,
    MANUAL_INDIGENOUS_TO_EXCLUSIONS_PATH,
    silver_dir,
)
main_parent_selection_path, _secondary_parents_path = select_main_parents(regional_classification_path, silver_dir)
canonical_parents_path = flag_canonical_parents(main_parent_selection_path, silver_dir)
canonical_hierarchy_path = prune_canonical_hierarchy(canonical_parents_path, silver_dir)
prune_regional_hierarchy(canonical_parents_path, silver_dir)
extract_canonical_roots(canonical_hierarchy_path, MANUAL_ACCEPTED_ROOTS_PATH, silver_dir)
