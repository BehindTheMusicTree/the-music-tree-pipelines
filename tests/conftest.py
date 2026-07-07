import os
from collections.abc import Iterator

import psycopg
import pytest

MB_HOST = os.environ.get("MB_HOST", "127.0.0.1")
MB_PORT = os.environ.get("MB_PORT", "5432")
MB_DB = os.environ.get("MB_DB", "musicbrainz_db")
MB_USER = os.environ.get("MB_USER", "musicbrainz")
MB_DSN = f"postgresql://{MB_USER}@{MB_HOST}:{MB_PORT}/{MB_DB}"


@pytest.fixture(scope="session")
def mb_conn() -> Iterator[psycopg.Connection]:
    try:
        conn = psycopg.connect(MB_DSN, connect_timeout=3)
    except psycopg.OperationalError as exc:
        if os.environ.get("MB_TEST_REQUIRE_DB"):
            raise
        pytest.skip(f"MusicBrainz sample database not reachable: {exc}")
    yield conn
    conn.close()
