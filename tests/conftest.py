from collections.abc import Iterator

import psycopg
import pytest

MB_DSN = "postgresql://musicbrainz:musicbrainz@127.0.0.1:5432/musicbrainz_db"


@pytest.fixture(scope="session")
def mb_conn() -> Iterator[psycopg.Connection]:
    try:
        conn = psycopg.connect(MB_DSN, connect_timeout=3)
    except psycopg.OperationalError as exc:
        pytest.skip(f"MusicBrainz sample database not reachable: {exc}")
    yield conn
    conn.close()
