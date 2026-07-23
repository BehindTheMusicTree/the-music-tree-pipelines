import os
from collections.abc import Iterator

import psycopg
import pytest

from musicbrainz_to_the_music_tree_api.db import connect


@pytest.fixture(scope="session")
def mb_conn() -> Iterator[psycopg.Connection]:
    try:
        conn = connect()
    except (psycopg.OperationalError, RuntimeError) as exc:
        if os.environ.get("MB_TEST_REQUIRE_DB"):
            raise
        pytest.skip(f"MusicBrainz sample database not reachable: {exc}")
    yield conn
    conn.close()
