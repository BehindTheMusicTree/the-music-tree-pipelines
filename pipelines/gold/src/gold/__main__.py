import logging

from common.env import load_pipeline_env, require_env, resolve_pipeline_path

import gold
from gold.canonical_genre_tree_export import MANUAL_CANONICAL_GENRE_POP_SIDE_PATH, export_canonical_genre_tree
from gold.genre_match import MANUAL_ACCEPTED_NON_GENRE_TAGS_PATH, MANUAL_GENRE_ALIAS_PATH, genre_match
from gold.song_export import export_songs

logging.basicConfig(level=logging.INFO)
load_pipeline_env(gold.__file__)
musicbrainz_silver_dir = resolve_pipeline_path(gold.__file__, require_env("MUSICBRAINZ_SILVER_DIR"))
wikidata_silver_dir = resolve_pipeline_path(gold.__file__, require_env("WIKIDATA_SILVER_DIR"))
gold_output_dir = resolve_pipeline_path(gold.__file__, require_env("GOLD_OUTPUT_DIR"))

export_canonical_genre_tree(wikidata_silver_dir, gold_output_dir, MANUAL_CANONICAL_GENRE_POP_SIDE_PATH)
# regional genre tree export disabled: 8_regional_hierarchy.parquet currently produces
# duplicate node names (cross-region/era homonyms) that the tree builder rejects; re-enable
# once those are disambiguated (see manual_label_overrides.csv precedent for the canonical tree).
genre_match_path = genre_match(
    musicbrainz_silver_dir / "3_song_example.parquet",
    wikidata_silver_dir / "7_canonical_hierarchy.parquet",
    MANUAL_GENRE_ALIAS_PATH,
    MANUAL_ACCEPTED_NON_GENRE_TAGS_PATH,
    gold_output_dir,
)
export_songs(genre_match_path, gold_output_dir)
