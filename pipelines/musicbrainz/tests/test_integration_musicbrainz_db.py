import psycopg
import pytest


@pytest.mark.integration
def test_artist_table_has_sample_rows(mb_conn: psycopg.Connection) -> None:
    with mb_conn.cursor() as cur:
        cur.execute("select 1 from musicbrainz.artist limit 1")
        assert cur.fetchone() is not None


@pytest.mark.integration
def test_artist_credit_table_has_sample_rows(mb_conn: psycopg.Connection) -> None:
    with mb_conn.cursor() as cur:
        cur.execute("select 1 from musicbrainz.artist_credit limit 1")
        assert cur.fetchone() is not None


@pytest.mark.integration
def test_artist_credit_name_table_has_sample_rows(mb_conn: psycopg.Connection) -> None:
    with mb_conn.cursor() as cur:
        cur.execute("select 1 from musicbrainz.artist_credit_name limit 1")
        assert cur.fetchone() is not None
