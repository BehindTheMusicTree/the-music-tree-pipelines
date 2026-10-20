import pytest

from wikidata import wikidata_client


@pytest.mark.integration
def test_genre_tree_query_returns_rows_from_the_live_endpoint() -> None:
    rows = wikidata_client.run_query(wikidata_client.GENRE_TREE_QUERY, timeout=60.0)

    assert len(rows) > 1000
    assert any(row["item"] == "http://www.wikidata.org/entity/Q11399" for row in rows)  # rock music


@pytest.mark.integration
def test_indigenous_to_query_returns_rows_from_the_live_endpoint() -> None:
    rows = wikidata_client.run_query(
        wikidata_client.INDIGENOUS_TO_QUERY, variables=wikidata_client.INDIGENOUS_TO_QUERY_VARIABLES, timeout=60.0
    )

    assert len(rows) > 0
    assert any(row["item"] == "http://www.wikidata.org/entity/Q10376827" for row in rows)  # Han Chinese music


@pytest.mark.integration
def test_country_of_origin_query_returns_rows_from_the_live_endpoint() -> None:
    rows = wikidata_client.run_query(
        wikidata_client.COUNTRY_OF_ORIGIN_QUERY,
        variables=wikidata_client.COUNTRY_OF_ORIGIN_QUERY_VARIABLES,
        timeout=60.0,
    )

    assert len(rows) > 0
    assert any(row["item"] == "http://www.wikidata.org/entity/Q185676" for row in rows)  # fado


@pytest.mark.integration
def test_fetch_descriptions_returns_description_and_enwiki_article_from_the_live_api() -> None:
    rock = wikidata_client.fetch_descriptions(["Q11399"])["Q11399"]

    assert rock.description
    assert rock.wikipedia_title == "Rock music"
    assert rock.wikipedia_url == "https://en.wikipedia.org/wiki/Rock_music"
