import psycopg
import pytest


@pytest.mark.integration
def test_artist_table_has_sample_rows(mb_conn: psycopg.Connection) -> None:
    with mb_conn.cursor() as cur:
        cur.execute("select 1 from musicbrainz.artist limit 1")
        assert cur.fetchone() is not None
