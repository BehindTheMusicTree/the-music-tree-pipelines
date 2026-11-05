from pathlib import Path

import pytest

from wikidata.artist_images.config import WorkerConfig


def configure(monkeypatch, root: Path) -> None:
    paths = {
        "ARTIST_IMAGES_STATE_DIR": root / "state",
        "ARTIST_IMAGES_PUBLISHED_DIR": root / "published",
        "ARTIST_IMAGES_WRITER_LOCK_FILE": root / "writer.lock",
        "ARTIST_IMAGES_BACKGROUND_LOCK_FILE": root / "background.lock",
        "MUSIC_TREE_PIPELINES_LOCK_FILE": root / "catalogue.lock",
    }
    paths["MUSIC_TREE_PIPELINES_LOCK_FILE"].touch()
    for key, value in paths.items():
        monkeypatch.setenv(key, str(value))
    for key, value in {
        "ARTIST_IMAGES_MAX_WDQS_ATTEMPTS": "64",
        "ARTIST_IMAGES_MAX_ENTITY_API_ATTEMPTS": "64",
        "ARTIST_IMAGES_MAX_COMMONS_HTTP_ATTEMPTS": "400",
        "ARTIST_IMAGES_RUN_BUDGET_SECONDS": "2700",
        "ARTIST_IMAGES_DISCOVERY_INTERVAL_DAYS": "7",
        "ARTIST_IMAGES_CLAIMS_TTL_DAYS": "7",
        "ARTIST_IMAGES_NEGATIVE_TTL_DAYS": "30",
        "ARTIST_IMAGES_PRODUCER_COMMIT": "commit",
        "ARTIST_IMAGES_METADATA_TTL_DAYS": "30",
        "ARTIST_IMAGES_MISSING_METADATA_TTL_DAYS": "7",
    }.items():
        monkeypatch.setenv(key, value)


def test_explicit_configuration_accepts_documented_budgets(monkeypatch, tmp_path: Path) -> None:
    configure(monkeypatch, tmp_path)
    config = WorkerConfig.from_env()
    assert config.run_seconds == 2700
    assert config.state_root == tmp_path / "state"


@pytest.mark.parametrize("value", ["0", "65", "abc", "-1", "1.5", ""])
def test_invalid_or_oversized_budget_fails_fast(monkeypatch, tmp_path: Path, value: str) -> None:
    configure(monkeypatch, tmp_path)
    monkeypatch.setenv("ARTIST_IMAGES_MAX_WDQS_ATTEMPTS", value)
    with pytest.raises(ValueError, match="ARTIST_IMAGES_MAX_WDQS_ATTEMPTS"):
        WorkerConfig.from_env()


def test_nested_roots_and_shared_lock_identity_are_rejected(monkeypatch, tmp_path: Path) -> None:
    configure(monkeypatch, tmp_path)
    monkeypatch.setenv("ARTIST_IMAGES_PUBLISHED_DIR", str(tmp_path / "state" / "publication"))
    with pytest.raises(ValueError, match="separate directories"):
        WorkerConfig.from_env()
    monkeypatch.setenv("ARTIST_IMAGES_PUBLISHED_DIR", str(tmp_path / "published"))
    monkeypatch.setenv("ARTIST_IMAGES_BACKGROUND_LOCK_FILE", str(tmp_path / "writer.lock"))
    with pytest.raises(ValueError, match="distinct"):
        WorkerConfig.from_env()
