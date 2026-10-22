import os
from collections.abc import Iterator

import adbc_driver_postgresql.dbapi as adbc
import pytest

from musicbrainz.db import connect


@pytest.fixture(scope="session")
def mb_conn() -> Iterator[adbc.Connection]:
    try:
        conn = connect()
    except RuntimeError as exc:
        if os.environ.get("MB_TEST_REQUIRE_DB"):
            raise
        pytest.skip(f"MusicBrainz test config missing: {exc}")
    except adbc.OperationalError as exc:
        if os.environ.get("MB_TEST_REQUIRE_DB"):
            raise
        pytest.skip(f"MusicBrainz sample database not reachable: {exc}")
    yield conn
    conn.close()
