import logging

from common.env import load_pipeline_env, require_env, resolve_pipeline_path

import wikidata
from wikidata.silver.canonical_hierarchy import prune_canonical_hierarchy
from wikidata.silver.canonical_parents import flag_canonical_parents
from wikidata.silver.canonical_roots import extract_canonical_roots
from wikidata.silver.item_links import add_item_links
from wikidata.silver.main_parent_selection import select_main_parents
from wikidata.silver.non_genre_pruning import prune_non_genre_items
from wikidata.silver.regional_classification import classify_regional_genres
from wikidata.silver.regional_hierarchy import prune_regional_hierarchy
from wikidata.silver.regional_overview_classification import classify_regional_from_overviews

logging.basicConfig(level=logging.INFO)
load_pipeline_env(wikidata.__file__)
bronze_dir = resolve_pipeline_path(wikidata.__file__, require_env("BRONZE_OUTPUT_DIR"))
silver_dir = resolve_pipeline_path(wikidata.__file__, require_env("SILVER_OUTPUT_DIR"))
curation_dir = resolve_pipeline_path(wikidata.__file__, require_env("CURATION_BRONZE_DIR"))
item_links_path = add_item_links(
    bronze_dir / "wikidata_genre_tree.parquet",
    silver_dir,
    curation_dir / "manual_label_overrides.csv",
    curation_dir / "manual_capitalized_words.csv",
)
non_genre_pruning_path = prune_non_genre_items(
    item_links_path,
    curation_dir / "manual_theme_genres.csv",
    curation_dir / "manual_technique_genres.csv",
    curation_dir / "manual_out_of_scope_genres.csv",
    curation_dir / "manual_umbrella_canonical_genres.csv",
    curation_dir / "manual_duplicate_genres.csv",
    silver_dir,
)
regional_overview_classification_path = classify_regional_from_overviews(
    non_genre_pruning_path,
    curation_dir / "manual_regional_overview_additions.csv",
    curation_dir / "manual_overview_reclassifications.csv",
    silver_dir,
)
regional_classification_path = classify_regional_genres(
    regional_overview_classification_path,
    bronze_dir / "wikidata_genre_indigenous_to.parquet",
    curation_dir / "manual_regional_overrides.csv",
    curation_dir / "manual_canonical_parent_additions.csv",
    curation_dir / "manual_main_parent.csv",
    curation_dir / "manual_accepted_canonical_roots.csv",
    curation_dir / "manual_indigenous_to_exclusions.csv",
    silver_dir,
)
main_parent_selection_path, _secondary_parents_path = select_main_parents(regional_classification_path, silver_dir)
canonical_parents_path = flag_canonical_parents(main_parent_selection_path, silver_dir)
canonical_hierarchy_path = prune_canonical_hierarchy(canonical_parents_path, silver_dir)
prune_regional_hierarchy(canonical_parents_path, silver_dir)
extract_canonical_roots(canonical_hierarchy_path, curation_dir / "manual_accepted_canonical_roots.csv", silver_dir)
