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


def test_artist_image_query_returns_well_formed_rows_from_the_live_endpoint() -> None:
    # The response shape is the contract; whether the full extract is available is the nightly run's concern.
    rows = wikidata_client.run_query(
        wikidata_client.artist_image_query("0") + "\nLIMIT 500",
        variables=wikidata_client.ARTIST_IMAGE_QUERY_VARIABLES,
        timeout=60.0,
    )

    assert rows
    assert all(set(row) == set(wikidata_client.ARTIST_IMAGE_QUERY_VARIABLES) for row in rows)
    rejected = [row for row in rows if not ingest._commons_file(row["image"])]
    assert len(rejected) <= len(rows) * ingest.ARTIST_IMAGE_MAX_REJECT_RATIO
