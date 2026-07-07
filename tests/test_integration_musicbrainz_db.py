import psycopg
import pytest


@pytest.mark.integration
def test_artist_table_has_sample_rows(mb_conn: psycopg.Connection) -> None:
    with mb_conn.cursor() as cur:
        cur.execute("select count(*) from musicbrainz.artist")
        (count,) = cur.fetchone()
    assert count > 0
