import logging

from common.env import load_pipeline_env, require_env, resolve_pipeline_path

import gold
from gold.canonical_genre_tree_export import export_canonical_genre_tree
from gold.genre_match import genre_match
from gold.regional_genre_tree_export import export_regional_genre_tree
from gold.song_export import export_songs

logging.basicConfig(level=logging.INFO)
load_pipeline_env(gold.__file__)
musicbrainz_silver_dir = resolve_pipeline_path(gold.__file__, require_env("MUSICBRAINZ_SILVER_DIR"))
wikidata_silver_dir = resolve_pipeline_path(gold.__file__, require_env("WIKIDATA_SILVER_DIR"))
curation_dir = resolve_pipeline_path(gold.__file__, require_env("CURATION_BRONZE_DIR"))
gold_output_dir = resolve_pipeline_path(gold.__file__, require_env("GOLD_OUTPUT_DIR"))

export_canonical_genre_tree(wikidata_silver_dir, gold_output_dir, curation_dir / "manual_canonical_genre_pop_side.csv")
export_regional_genre_tree(wikidata_silver_dir, gold_output_dir, curation_dir / "manual_regional_secondary_parents.csv")
genre_match_path = genre_match(
    musicbrainz_silver_dir / "5_songs.parquet",
    wikidata_silver_dir / "7_canonical_hierarchy.parquet",
    curation_dir / "manual_genre_alias.csv",
    curation_dir / "manual_accepted_non_genre_tags.csv",
    gold_output_dir,
)
export_songs(genre_match_path, gold_output_dir)
