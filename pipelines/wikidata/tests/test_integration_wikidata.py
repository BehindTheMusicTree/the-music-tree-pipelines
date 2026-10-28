from pathlib import Path

import polars as pl
import pytest

from wikidata import ingest, wikidata_client

# Hits the public WDQS/Wikidata endpoints: run by the Live contract workflow, never by PR CI.
pytestmark = [pytest.mark.integration, pytest.mark.live]


def test_genre_tree_query_returns_rows_from_the_live_endpoint() -> None:
    rows = wikidata_client.run_query(wikidata_client.GENRE_TREE_QUERY, timeout=60.0)

    assert len(rows) > 1000
    assert any(row["item"] == "http://www.wikidata.org/entity/Q11399" for row in rows)  # rock music


def test_indigenous_to_query_returns_rows_from_the_live_endpoint() -> None:
    rows = wikidata_client.run_query(
        wikidata_client.INDIGENOUS_TO_QUERY, variables=wikidata_client.INDIGENOUS_TO_QUERY_VARIABLES, timeout=60.0
    )

    assert len(rows) > 0
    assert any(row["item"] == "http://www.wikidata.org/entity/Q10376827" for row in rows)  # Han Chinese music


def test_country_of_origin_query_returns_rows_from_the_live_endpoint() -> None:
    rows = wikidata_client.run_query(
        wikidata_client.COUNTRY_OF_ORIGIN_QUERY,
        variables=wikidata_client.COUNTRY_OF_ORIGIN_QUERY_VARIABLES,
        timeout=60.0,
    )

    assert len(rows) > 0
    assert any(row["item"] == "http://www.wikidata.org/entity/Q185676" for row in rows)  # fado


def test_fetch_descriptions_returns_description_and_enwiki_article_from_the_live_api() -> None:
    rock = wikidata_client.fetch_descriptions(["Q11399"])["Q11399"]

    assert rock.description
    assert rock.wikipedia_title == "Rock music"
    assert rock.wikipedia_url == "https://en.wikipedia.org/wiki/Rock_music"
    assert rock.aliases


def test_tradition_label_query_binds_labels_on_the_live_endpoint() -> None:
    patterns = {"music_genre": "VALUES ?item { wd:Q11399 }"}
    rows = wikidata_client.run_query(
        wikidata_client.tradition_claim_query("music_genre", wikidata_client.LABEL_PID, patterns),
        variables=wikidata_client.CULTURAL_ENTITY_CLAIM_QUERY_VARIABLES,
        timeout=60.0,
    )

    assert [row["value"] for row in rows] == ["rock music"]


def test_ingest_artist_images_reads_every_partition_from_the_live_endpoint(tmp_path: Path) -> None:
    # Every partition, not a sample: a single bad P18 value (e.g. an "unknown value" skolem IRI) fails the whole run.
    df = pl.read_parquet(ingest.ingest_artist_images(tmp_path))

    assert df.height > 100_000
    assert set(df["musicbrainz_artist_id"].str.slice(0, 1).unique()) == set(wikidata_client.ARTIST_IMAGE_PARTITIONS)
