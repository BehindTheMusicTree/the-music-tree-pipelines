import logging
from pathlib import Path

import polars as pl
from common.env import load_pipeline_env, require_env, resolve_pipeline_path
from common.quality_checks import check_non_empty, check_unique_key

from wikidata import wikidata_client, wikipedia_client

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
    check_non_empty(df, "wikidata_genre_tree")

    output_dir.mkdir(parents=True, exist_ok=True)
    output_path = output_dir / "wikidata_genre_tree.parquet"
    df.write_parquet(output_path)
    logger.info("wrote %d rows to %s", df.height, output_path)

    return output_path


def ingest_indigenous_to(output_dir: Path) -> Path:
    logger.info("querying Wikidata for genre indigenous-to relations")
    rows = wikidata_client.run_query(
        wikidata_client.INDIGENOUS_TO_QUERY, variables=wikidata_client.INDIGENOUS_TO_QUERY_VARIABLES
    )

    df = pl.DataFrame(
        {
            "item_id": [_qid(row["item"]) for row in rows],
            "indigenous_to_id": [_qid(row["indigenousTo"]) for row in rows],
            "indigenous_to_label": [row["indigenousToLabel"] for row in rows],
        }
    )
    check_non_empty(df, "wikidata_genre_indigenous_to")

    output_dir.mkdir(parents=True, exist_ok=True)
    output_path = output_dir / "wikidata_genre_indigenous_to.parquet"
    df.write_parquet(output_path)
    logger.info("wrote %d rows to %s", df.height, output_path)

    return output_path


def ingest_country_of_origin(output_dir: Path) -> Path:
    logger.info("querying Wikidata for genre country-of-origin relations")
    rows = wikidata_client.run_query(
        wikidata_client.COUNTRY_OF_ORIGIN_QUERY, variables=wikidata_client.COUNTRY_OF_ORIGIN_QUERY_VARIABLES
    )

    df = pl.DataFrame(
        {
            "item_id": [_qid(row["item"]) for row in rows],
            "country_of_origin_id": [_qid(row["countryOfOrigin"]) for row in rows],
            "country_of_origin_label": [row["countryOfOriginLabel"] for row in rows],
        }
    )
    check_non_empty(df, "wikidata_genre_country_of_origin")

    output_dir.mkdir(parents=True, exist_ok=True)
    output_path = output_dir / "wikidata_genre_country_of_origin.parquet"
    df.write_parquet(output_path)
    logger.info("wrote %d rows to %s", df.height, output_path)

    return output_path


def _tree_qids(genre_tree_path: Path, manual_overview_additions_path: Path) -> list[str]:
    """QIDs of every node Silver can emit: Bronze tree children and parents, plus curated regional overviews."""
    tree = pl.read_parquet(genre_tree_path)
    additions = pl.read_csv(manual_overview_additions_path, schema_overrides={"item_id": pl.Utf8})
    ids = pl.concat([tree["item_id"], tree["parent_id"], additions["item_id"]]).drop_nulls()
    return sorted(set(ids.filter(ids.str.starts_with("Q")).to_list()))


def ingest_descriptions(genre_tree_path: Path, manual_overview_additions_path: Path, output_dir: Path) -> Path:
    item_ids = _tree_qids(genre_tree_path, manual_overview_additions_path)
    logger.info("fetching descriptions and English Wikipedia articles for %d tree QID(s)", len(item_ids))
    descriptions = wikidata_client.fetch_descriptions(item_ids)
    titles = [d.wikipedia_title for d in descriptions.values() if d.wikipedia_title is not None]
    logger.info("fetching %d Wikipedia intro(s)", len(titles))
    intros = wikipedia_client.fetch_intros(titles)

    df = pl.DataFrame(
        {
            "item_id": list(descriptions),
            "description": [d.description for d in descriptions.values()],
            "wikipedia_url": [d.wikipedia_url for d in descriptions.values()],
            "wikipedia_intro": [
                intros[d.wikipedia_title] if d.wikipedia_title is not None else None for d in descriptions.values()
            ],
        },
        schema={
            "item_id": pl.Utf8,
            "description": pl.Utf8,
            "wikipedia_url": pl.Utf8,
            "wikipedia_intro": pl.Utf8,
        },
    )
    check_non_empty(df, "wikidata_genre_descriptions")
    check_unique_key(df, "item_id", "wikidata_genre_descriptions")

    output_dir.mkdir(parents=True, exist_ok=True)
    output_path = output_dir / "wikidata_genre_descriptions.parquet"
    df.write_parquet(output_path)
    logger.info("wrote %d rows to %s", df.height, output_path)

    return output_path


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    load_pipeline_env(__file__)
    output_dir = resolve_pipeline_path(__file__, require_env("BRONZE_OUTPUT_DIR"))
    curation_dir = resolve_pipeline_path(__file__, require_env("CURATION_BRONZE_DIR"))
    genre_tree_path = ingest_genre_tree(output_dir)
    ingest_indigenous_to(output_dir)
    ingest_country_of_origin(output_dir)
    ingest_descriptions(genre_tree_path, curation_dir / "manual_regional_overview_additions.csv", output_dir)
