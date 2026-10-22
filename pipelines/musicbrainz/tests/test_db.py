from unittest.mock import MagicMock

import pytest

from musicbrainz import db


def test_connect_builds_uri_from_env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("MB_HOST", "localhost")
    monkeypatch.setenv("MB_PORT", "5432")
    monkeypatch.setenv("MB_DB", "musicbrainz_db")
    monkeypatch.setenv("MB_USER", "musicbrainz")

    conn = MagicMock()
    adbc_connect = MagicMock(return_value=conn)
    monkeypatch.setattr(db.adbc, "connect", adbc_connect)

    result = db.connect()

    adbc_connect.assert_called_once_with("postgresql://musicbrainz@localhost:5432/musicbrainz_db?connect_timeout=3")
    assert result is conn


def test_connect_raises_when_required_env_missing(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("MB_HOST", raising=False)

    with pytest.raises(RuntimeError, match="MB_HOST is required"):
        db.connect()
