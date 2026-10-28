import logging
from concurrent.futures import ThreadPoolExecutor
from collections.abc import Mapping
from datetime import UTC, datetime, timedelta
from pathlib import Path
from urllib.parse import unquote

import httpx
import polars as pl
from common.env import load_pipeline_env, require_env, resolve_pipeline_path
from common.quality_checks import (
    MBID_PATTERN,
    check_non_empty,
    check_null_rate,
    check_reject_rate,
    check_unique_key,
)
from common.step_timer import step

from wikidata import wikidata_client, wikipedia_client
from wikidata.silver.regional_overview_classification import REGIONAL_OVERVIEW_PREFIX

logger = logging.getLogger(__name__)

WIKIDATA_ENTITY_PREFIX = "http://www.wikidata.org/entity/"
COMMONS_FILE_PATH_MARKER = "/Special:FilePath/"
ARTIST_IMAGE_MAX_REJECT_RATIO = 0.01
# How long a partition's rows may be reused when WDQS can't answer: past it the run fails instead of serving stale images.
ARTIST_IMAGE_MAX_STALENESS_DAYS = 7
ARTIST_IMAGE_FILENAME = "wikidata_artist_images.parquet"
NATURAL_EARTH_ADMIN_FILE = "natural_earth_admin.parquet"


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


def ingest_cultural_entity_aliases(output_dir: Path, patterns: Mapping[str, str]) -> Path:
    frames = []
    for kind in patterns:
        logger.info("querying Wikidata for English aliases of %s entities", kind)
        rows = wikidata_client.run_query(
            wikidata_client.cultural_entity_alias_query(kind, patterns),
            variables=wikidata_client.CULTURAL_ENTITY_ALIAS_QUERY_VARIABLES,
            timeout=wikidata_client.CULTURAL_ENTITY_QUERY_TIMEOUT,
        )
        frames.append(
            pl.DataFrame(
                {"item_id": [_qid(row["item"]) for row in rows], "alias": [row["alias"] for row in rows]},
                schema={"item_id": pl.String, "alias": pl.String},
            )
        )
    df = pl.concat(frames).unique().sort("item_id", "alias")
    check_non_empty(df, "wikidata_cultural_entity_alias")

    output_dir.mkdir(parents=True, exist_ok=True)
    output_path = output_dir / "wikidata_cultural_entity_alias.parquet"
    df.write_parquet(output_path)
    logger.info("wrote %d rows to %s", df.height, output_path)

    return output_path


def read_natural_earth_qids(geohistory_dir: Path) -> list[str]:
    path = geohistory_dir / NATURAL_EARTH_ADMIN_FILE
    if not path.exists():
        raise FileNotFoundError(f"{path} is missing: run geohistory Bronze before wikidata Bronze")
    qids = pl.read_parquet(path, columns=["wikidata_id"])["wikidata_id"].drop_nulls().unique().sort().to_list()
    if not qids:
        raise ValueError(f"{path} has no Wikidata QID")
    return qids


def ingest_admin_hierarchy(output_dir: Path, seed_qids: list[str]) -> Path:
    queries = wikidata_client.admin_hierarchy_queries(seed_qids)
    logger.info(
        "querying Wikidata for the P131 chains of %d admin unit(s) in %d chunk(s)", len(seed_qids), len(queries)
    )
    rows = [
        row
        for query in queries
        for row in wikidata_client.run_query(
            query,
            variables=wikidata_client.ADMIN_HIERARCHY_QUERY_VARIABLES,
            timeout=wikidata_client.CULTURAL_ENTITY_QUERY_TIMEOUT,
        )
    ]
    df = (
        pl.DataFrame(
            {
                "item_id": [_qid(row["item"]) for row in rows],
                "property": [row["property"] for row in rows],
                "value": [_qid(row["value"]) for row in rows],
                "label": [row["itemLabel"] for row in rows],
            },
            schema=dict.fromkeys(("item_id", "property", "value", "label"), pl.String),
        )
        .unique()
        .sort("item_id", "property", "value")
    )
    check_non_empty(df, "wikidata_admin_hierarchy")
    check_unique_key(
        df.select(pl.concat_str("item_id", "property", "value", separator="|").alias("key")),
        "key",
        "wikidata_admin_hierarchy",
    )

    output_dir.mkdir(parents=True, exist_ok=True)
    output_path = output_dir / "wikidata_admin_hierarchy.parquet"
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


def _commons_file(uri: str | None) -> str | None:
    # An "unknown value" P18 is a genid IRI, not a file path.
    if uri is None or COMMONS_FILE_PATH_MARKER not in uri:
        return None
    return unquote(uri.rsplit(COMMONS_FILE_PATH_MARKER, 1)[1])


_ARTIST_IMAGE_SCHEMA: dict[str, pl.DataType] = {
    "item_id": pl.Utf8(),
    "musicbrainz_artist_id": pl.Utf8(),
    "image_file": pl.Utf8(),
    "fetched_at": pl.Datetime("us", "UTC"),
}


def _previous_partition_rows(previous: pl.DataFrame | None, partition: str, now: datetime) -> pl.DataFrame | None:
    """The partition's rows from the last run, or None when they are missing or older than the staleness limit."""
    if previous is None or "fetched_at" not in previous.columns:
        return None
    rows = previous.filter(pl.col("musicbrainz_artist_id").str.starts_with(partition))
    if rows.is_empty() or rows.select(pl.col("fetched_at").min()).item() < now - timedelta(
        days=ARTIST_IMAGE_MAX_STALENESS_DAYS
    ):
        return None
    return rows


def ingest_artist_images(output_dir: Path, now: datetime) -> Path:
    """Artist images are an optional enrichment: when WDQS can't answer a partition, that partition's rows from the
    previous run are kept (for up to ARTIST_IMAGE_MAX_STALENESS_DAYS) instead of failing the whole nightly run."""
    logger.info("querying Wikidata for artist images")
    output_path = output_dir / ARTIST_IMAGE_FILENAME
    previous = pl.read_parquet(output_path) if output_path.exists() else None

    rows: list[dict[str, str | None]] = []
    reused: list[pl.DataFrame] = []
    wdqs_error: Exception | None = None
    for partition in wikidata_client.ARTIST_IMAGE_PARTITIONS:
        # Once a partition has used up its retry budget, WDQS is down: the rest go straight to the previous rows.
        if wdqs_error is None:
            try:
                rows.extend(
                    wikidata_client.run_query(
                        wikidata_client.artist_image_query(partition),
                        variables=wikidata_client.ARTIST_IMAGE_QUERY_VARIABLES,
                    )
                )
                continue
            except (httpx.HTTPError, wikidata_client.WdqsTimeout) as error:
                # A 4xx other than 429 means the query itself is wrong: stale images must not hide that.
                if not wikidata_client.is_transient_error(error):
                    raise
                wdqs_error = error
        previous_rows = _previous_partition_rows(previous, partition, now)
        if previous_rows is None:
            raise wdqs_error
        logger.warning(
            "WDQS unavailable for artist image partition %s (%s): reusing %d rows from %s",
            partition,
            wdqs_error,
            previous_rows.height,
            previous_rows["fetched_at"].min(),
        )
        reused.append(previous_rows)

    df = pl.DataFrame(
        {
            "item_id": [_qid(row["item"]) for row in rows],
            "musicbrainz_artist_id": [(row["mbid"] or "").strip().lower() for row in rows],
            "image_file": [_commons_file(row["image"]) for row in rows],
            "fetched_at": [now] * len(rows),
        },
        schema=_ARTIST_IMAGE_SCHEMA,
    )
    # Wikidata's P434 format constraint isn't enforced, so a malformed MBID is a data error there, not a pipeline bug.
    valid = pl.col("musicbrainz_artist_id").str.contains(MBID_PATTERN) & pl.col("image_file").is_not_null()
    check_reject_rate(df.filter(~valid).height, df.height, "wikidata_artist_images", ARTIST_IMAGE_MAX_REJECT_RATIO)
    df = pl.concat([df.filter(valid), *reused]).unique().sort(df.columns)
    check_non_empty(df, "wikidata_artist_images")

    output_dir.mkdir(parents=True, exist_ok=True)
    df.write_parquet(output_path)
    logger.info("wrote %d rows to %s", df.height, output_path)

    return output_path


def _ingest_sparql_lane(
    output_dir: Path, curation_dir: Path, genre_tree_path: Path, admin_seed_qids: list[str]
) -> None:
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
    with step("artist_images"):
        ingest_artist_images(output_dir, datetime.now(UTC))
    with step("admin_hierarchy"):
        ingest_admin_hierarchy(output_dir, admin_seed_qids)
    with step("cultural_entity_aliases"):
        ingest_cultural_entity_aliases(output_dir, entity_patterns)


def _ingest_api_lane(output_dir: Path, curation_dir: Path, genre_tree_path: Path) -> None:
    with step("descriptions"):
        ingest_descriptions(genre_tree_path, curation_dir / "manual_regional_overview_additions.csv", output_dir)


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    load_pipeline_env(__file__)
    output_dir = resolve_pipeline_path(__file__, require_env("BRONZE_OUTPUT_DIR"))
    curation_dir = resolve_pipeline_path(__file__, require_env("CURATION_BRONZE_DIR"))
    geohistory_dir = resolve_pipeline_path(__file__, require_env("GEOHISTORY_BRONZE_DIR"))
    admin_seed_qids = read_natural_earth_qids(geohistory_dir)
    with step("genre_tree"):
        genre_tree_path = ingest_genre_tree(output_dir)
    # One lane per host (WDQS; wikidata.org + Wikipedia APIs), each serial, so no endpoint sees concurrent requests.
    with ThreadPoolExecutor(max_workers=2) as pool:
        lanes = [
            pool.submit(_ingest_sparql_lane, output_dir, curation_dir, genre_tree_path, admin_seed_qids),
            pool.submit(_ingest_api_lane, output_dir, curation_dir, genre_tree_path),
        ]
        for lane in lanes:
            lane.result()
