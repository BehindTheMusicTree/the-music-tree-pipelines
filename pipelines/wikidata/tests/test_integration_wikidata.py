import pytest

from wikidata import wikidata_client


@pytest.mark.integration
def test_genre_tree_query_returns_rows_from_the_live_endpoint() -> None:
    rows = wikidata_client.run_query(wikidata_client.GENRE_TREE_QUERY, timeout=60.0)

    assert len(rows) > 1000
    assert any(row["item"] == "http://www.wikidata.org/entity/Q11399" for row in rows)  # rock music
