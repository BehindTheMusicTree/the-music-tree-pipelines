import logging
from pathlib import Path

from common.env import load_pipeline_env, require_env

import wikidata
from wikidata.silver.classification import classify_genre_tree
from wikidata.silver.genre_parents import flag_genre_parents
from wikidata.silver.hierarchy import prune_genre_hierarchy

logging.basicConfig(level=logging.INFO)
load_pipeline_env(wikidata.__file__)
bronze_dir = Path(require_env("BRONZE_OUTPUT_DIR"))
silver_dir = Path(require_env("SILVER_OUTPUT_DIR"))
classification_path = classify_genre_tree(bronze_dir / "wikidata_genre_tree.parquet", silver_dir)
genre_parents_path = flag_genre_parents(classification_path, silver_dir)
prune_genre_hierarchy(genre_parents_path, silver_dir)
