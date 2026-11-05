"""Explicit mode-specific image worker configuration."""

from dataclasses import dataclass
from pathlib import Path

from common.env import require_env


def _path(name: str) -> Path:
    value = require_env(name)
    if not value.strip() or not Path(value).is_absolute():
        raise ValueError(f"{name} must be an absolute path")
    return Path(value)


def _integer(name: str, maximum: int) -> int:
    value = require_env(name)
    if not value.isascii() or not value.isdigit() or not 1 <= int(value) <= maximum:
        raise ValueError(f"{name} must be an integer between 1 and {maximum}")
    return int(value)


@dataclass(frozen=True)
class WorkerConfig:
    state_root: Path
    published_root: Path
    writer_lock: Path
    background_lock: Path
    catalogue_lock: Path
    wdqs_attempts: int
    entity_attempts: int
    commons_attempts: int
    run_seconds: int
    discovery_days: int
    positive_days: int
    negative_days: int
    producer_commit: str
    metadata_days: int = 30
    missing_metadata_days: int = 7

    @classmethod
    def from_env(cls) -> "WorkerConfig":
        config = cls(
            state_root=_path("ARTIST_IMAGES_STATE_DIR"),
            published_root=_path("ARTIST_IMAGES_PUBLISHED_DIR"),
            writer_lock=_path("ARTIST_IMAGES_WRITER_LOCK_FILE"),
            background_lock=_path("ARTIST_IMAGES_BACKGROUND_LOCK_FILE"),
            catalogue_lock=_path("MUSIC_TREE_PIPELINES_LOCK_FILE"),
            wdqs_attempts=_integer("ARTIST_IMAGES_MAX_WDQS_ATTEMPTS", 64),
            entity_attempts=_integer("ARTIST_IMAGES_MAX_ENTITY_API_ATTEMPTS", 64),
            commons_attempts=_integer("ARTIST_IMAGES_MAX_COMMONS_HTTP_ATTEMPTS", 400),
            run_seconds=_integer("ARTIST_IMAGES_RUN_BUDGET_SECONDS", 2700),
            discovery_days=_integer("ARTIST_IMAGES_DISCOVERY_INTERVAL_DAYS", 365),
            positive_days=_integer("ARTIST_IMAGES_CLAIMS_TTL_DAYS", 365),
            negative_days=_integer("ARTIST_IMAGES_NEGATIVE_TTL_DAYS", 365),
            metadata_days=_integer("ARTIST_IMAGES_METADATA_TTL_DAYS", 365),
            missing_metadata_days=_integer("ARTIST_IMAGES_MISSING_METADATA_TTL_DAYS", 365),
            producer_commit=require_env("ARTIST_IMAGES_PRODUCER_COMMIT"),
        )
        state, published = config.state_root.resolve(), config.published_root.resolve()
        if state == published or state in published.parents or published in state.parents:
            raise ValueError("Image state and publication roots must be separate directories")
        locks = [config.writer_lock.resolve(), config.background_lock.resolve(), config.catalogue_lock.resolve()]
        if len(set(locks)) != 3:
            raise ValueError("Writer, background and catalogue locks must be distinct")
        if not config.producer_commit.strip():
            raise ValueError("ARTIST_IMAGES_PRODUCER_COMMIT is required")
        for lock in locks:
            if not lock.parent.is_dir():
                raise ValueError(f"Lock parent does not exist: {lock.parent}")
        if not config.catalogue_lock.is_file():
            raise ValueError("Catalogue lock must already exist")
        return config
