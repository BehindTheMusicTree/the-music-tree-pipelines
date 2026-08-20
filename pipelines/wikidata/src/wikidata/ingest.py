import logging
from pathlib import Path

import polars as pl
from common.env import load_pipeline_env, require_env, resolve_pipeline_path

from wikidata import wikidata_client

logger = logging.getLogger(__name__)

WIKIDATA_ENTITY_PREFIX = "http://www.wikidata.org/entity/"


def _qid(uri: str | None) -> str | None:
    if uri is None:
        return None
    return uri.removeprefix(WIKIDATA_ENTITY_PREFIX)


def ingest_genre_tree(output_dir: Path) -> Path:
    logger.info("querying Wikidata for the music genre tree")
    rows = wikidata_client.run_query(wikidata_client.GENRE_TREE_QUERY)

    df = pl.DataFrame(
        {
            "item_id": [_qid(row["item"]) for row in rows],
            "item_label": [row["itemLabel"] for row in rows],
            "parent_id": [_qid(row["parent"]) for row in rows],
            "parent_label": [row["parentLabel"] for row in rows],
            "relation_type": [row["relation"] for row in rows],
        }
    )

    output_dir.mkdir(parents=True, exist_ok=True)
    output_path = output_dir / "wikidata_genre_tree.parquet"
    df.write_parquet(output_path)
    logger.info("wrote %d rows to %s", df.height, output_path)

    return output_path


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    load_pipeline_env(__file__)
    output_dir = resolve_pipeline_path(__file__, require_env("BRONZE_OUTPUT_DIR"))
    ingest_genre_tree(output_dir)
