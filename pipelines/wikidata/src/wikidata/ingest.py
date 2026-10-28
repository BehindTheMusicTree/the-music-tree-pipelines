import logging
from concurrent.futures import ThreadPoolExecutor
from collections.abc import Mapping
from pathlib import Path

import polars as pl
from common.env import load_pipeline_env, require_env, resolve_pipeline_path
from common.quality_checks import check_non_empty, check_null_rate, check_unique_key
from common.step_timer import step

from wikidata import wikidata_client, wikipedia_client
from wikidata.silver.regional_overview_classification import REGIONAL_OVERVIEW_PREFIX

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


def read_entity_additions(path: Path) -> list[str]:
    return pl.read_csv(path, infer_schema=False)["entity_id"].to_list()


def ingest_cultural_entities(output_dir: Path, patterns: Mapping[str, str]) -> Path:
    frames = []
    for kind in patterns:
        logger.info("querying Wikidata for cultural entities of kind %s", kind)
        rows = wikidata_client.run_query(
            wikidata_client.cultural_entity_query(kind, patterns),
            variables=wikidata_client.CULTURAL_ENTITY_QUERY_VARIABLES,
            timeout=wikidata_client.CULTURAL_ENTITY_QUERY_TIMEOUT,
        )
        frames.append(
            pl.DataFrame(
                {
                    "item_id": [_qid(row["item"]) for row in rows],
                    "item_label": [row["itemLabel"] for row in rows],
                    "kind": [kind] * len(rows),
                    "glottocode": [row["glottocode"] for row in rows],
                },
                schema=dict.fromkeys(("item_id", "item_label", "kind", "glottocode"), pl.String),
            )
        )
    df = pl.concat(frames).unique().sort("kind", "item_id", "glottocode")
    check_non_empty(df, "wikidata_cultural_entity")
    check_unique_key(
        df.select(pl.concat_str("item_id", "kind", "glottocode", separator="|", ignore_nulls=True).alias("key")),
        "key",
        "wikidata_cultural_entity",
    )

    output_dir.mkdir(parents=True, exist_ok=True)
    output_path = output_dir / "wikidata_cultural_entity.parquet"
    df.write_parquet(output_path)
    logger.info("wrote %d rows to %s", df.height, output_path)

    return output_path


def ingest_cultural_entity_claims(output_dir: Path, patterns: Mapping[str, str]) -> Path:
    frames = []
    for kind in patterns:
        for pid in wikidata_client.CULTURAL_CLAIM_PIDS:
            logger.info("querying Wikidata for %s claims of %s entities", pid, kind)
            rows = wikidata_client.run_query(
                wikidata_client.cultural_entity_claim_query(kind, pid, patterns),
                variables=wikidata_client.CULTURAL_ENTITY_CLAIM_QUERY_VARIABLES,
                timeout=wikidata_client.CULTURAL_ENTITY_QUERY_TIMEOUT,
            )
            frames.append(
                pl.DataFrame(
                    {
                        "item_id": [_qid(row["item"]) for row in rows],
                        "property": [pid] * len(rows),
                        "value": [_qid(row["value"]) for row in rows],
                        "time_precision": [row["precision"] for row in rows],
                    },
                    schema={
                        "item_id": pl.String,
                        "property": pl.String,
                        "value": pl.String,
                        "time_precision": pl.String,
                    },
                ).with_columns(pl.col("time_precision").cast(pl.Int8))
            )
    df = pl.concat(frames).unique().sort("property", "item_id", "value")
    check_non_empty(df, "wikidata_cultural_entity_claim")

    output_dir.mkdir(parents=True, exist_ok=True)
    output_path = output_dir / "wikidata_cultural_entity_claim.parquet"
    df.write_parquet(output_path)
    logger.info("wrote %d rows to %s", df.height, output_path)

    return output_path


def read_tradition_additions(path: Path) -> list[str]:
    return pl.read_csv(path, infer_schema=False)["wikidata_id"].to_list()


def tradition_extra_qids(
    genre_tree_path: Path, manual_overview_additions_path: Path, tradition_additions: list[str]
) -> list[str]:
    """Candidates no P31 music genre pattern reaches: overviews known only as tree parents, curated ones, additions."""
    tree = pl.read_parquet(genre_tree_path)
    overviews = tree.filter(
        pl.col("parent_label").str.starts_with(REGIONAL_OVERVIEW_PREFIX) & pl.col("parent_id").str.starts_with("Q")
    )["parent_id"].to_list()
    overview_additions = pl.read_csv(manual_overview_additions_path, schema_overrides={"item_id": pl.Utf8})["item_id"]
    curated = [qid for qid in overview_additions.to_list() if qid.startswith("Q")]
    return sorted({*overviews, *curated, *tradition_additions})


def ingest_music_tradition_claims(output_dir: Path, patterns: Mapping[str, str]) -> Path:
    frames = []
    for kind in patterns:
        for pid in wikidata_client.TRADITION_CLAIM_PIDS:
            logger.info("querying Wikidata for %s claims of %s tradition candidates", pid, kind)
            rows = wikidata_client.run_query(
                wikidata_client.tradition_claim_query(kind, pid, patterns),
                variables=wikidata_client.CULTURAL_ENTITY_CLAIM_QUERY_VARIABLES,
                timeout=wikidata_client.CULTURAL_ENTITY_QUERY_TIMEOUT,
            )
            frames.append(
                pl.DataFrame(
                    {
                        "item_id": [_qid(row["item"]) for row in rows],
                        "property": [pid] * len(rows),
                        "value": [_qid(row["value"]) for row in rows],
                        "time_precision": [row["precision"] for row in rows],
                    },
                    schema={
                        "item_id": pl.String,
                        "property": pl.String,
                        "value": pl.String,
                        "time_precision": pl.String,
                    },
                ).with_columns(pl.col("time_precision").cast(pl.Int8))
            )
    df = pl.concat(frames).unique().sort("property", "item_id", "value")
    check_non_empty(df, "wikidata_music_tradition_claims")
    check_null_rate(df, "value", "wikidata_music_tradition_claims")

    output_dir.mkdir(parents=True, exist_ok=True)
    output_path = output_dir / "wikidata_music_tradition_claims.parquet"
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
    logger.info("fetching descriptions, aliases and English Wikipedia articles for %d tree QID(s)", len(item_ids))
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

    aliases = pl.DataFrame(
        [(item_id, alias) for item_id, d in descriptions.items() for alias in d.aliases],
        schema={"item_id": pl.Utf8, "alias": pl.Utf8},
        orient="row",
    )
    check_non_empty(aliases, "wikidata_genre_aliases")
    if aliases.is_duplicated().any():
        raise ValueError("wikidata_genre_aliases: (item_id, alias) is not unique")
    aliases_path = output_dir / "wikidata_genre_aliases.parquet"
    aliases.write_parquet(aliases_path)
    logger.info("wrote %d rows to %s", aliases.height, aliases_path)

    return output_path


def _ingest_sparql_lane(output_dir: Path, curation_dir: Path, genre_tree_path: Path) -> None:
    with step("indigenous_to"):
        ingest_indigenous_to(output_dir)
    with step("country_of_origin"):
        ingest_country_of_origin(output_dir)
    entity_patterns = wikidata_client.curated_entity_patterns(
        read_entity_additions(curation_dir / "manual_entity_additions.csv")
    )
    with step("cultural_entities"):
        ingest_cultural_entities(output_dir, entity_patterns)
    with step("cultural_entity_claims"):
        ingest_cultural_entity_claims(output_dir, entity_patterns)
    with step("music_tradition_claims"):
        ingest_music_tradition_claims(
            output_dir,
            wikidata_client.tradition_candidate_patterns(
                tradition_extra_qids(
                    genre_tree_path,
                    curation_dir / "manual_regional_overview_additions.csv",
                    read_tradition_additions(curation_dir / "manual_tradition_additions.csv"),
                )
            ),
        )


def _ingest_api_lane(output_dir: Path, curation_dir: Path, genre_tree_path: Path) -> None:
    with step("descriptions"):
        ingest_descriptions(genre_tree_path, curation_dir / "manual_regional_overview_additions.csv", output_dir)


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    load_pipeline_env(__file__)
    output_dir = resolve_pipeline_path(__file__, require_env("BRONZE_OUTPUT_DIR"))
    curation_dir = resolve_pipeline_path(__file__, require_env("CURATION_BRONZE_DIR"))
    with step("genre_tree"):
        genre_tree_path = ingest_genre_tree(output_dir)
    # One lane per host (WDQS; wikidata.org + Wikipedia APIs), each serial, so no endpoint sees concurrent requests.
    with ThreadPoolExecutor(max_workers=2) as pool:
        lanes = [
            pool.submit(lane, output_dir, curation_dir, genre_tree_path)
            for lane in (_ingest_sparql_lane, _ingest_api_lane)
        ]
        for lane in lanes:
            lane.result()
