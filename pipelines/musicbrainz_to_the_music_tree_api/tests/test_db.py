from unittest.mock import MagicMock

import pytest

from musicbrainz_to_the_music_tree_api import db


def test_connect_builds_dsn_from_env_and_registers_uuid_loaders(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("MB_HOST", "localhost")
    monkeypatch.setenv("MB_PORT", "5432")
    monkeypatch.setenv("MB_DB", "musicbrainz_db")
    monkeypatch.setenv("MB_USER", "musicbrainz")

    conn = MagicMock()
    psycopg_connect = MagicMock(return_value=conn)
    monkeypatch.setattr(db.psycopg, "connect", psycopg_connect)

    result = db.connect()

    psycopg_connect.assert_called_once_with("postgresql://musicbrainz@localhost:5432/musicbrainz_db", connect_timeout=3)
    assert conn.adapters.register_loader.call_count == 2
    assert result is conn


def test_connect_raises_when_required_env_missing(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("MB_HOST", raising=False)

    with pytest.raises(RuntimeError, match="MB_HOST is required"):
        db.connect()
