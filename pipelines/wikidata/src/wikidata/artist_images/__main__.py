"""Explicit bootstrap and integrity inspection for artist image publications."""

import asyncio
import argparse
import fcntl
import json
import logging
import os
from pathlib import Path

from common.artist_image_snapshot import resolve_artist_image_snapshot
from common.env import load_pipeline_env, require_env

import wikidata
from wikidata.artist_images.bootstrap import bootstrap_snapshot

logger = logging.getLogger(__name__)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    bootstrap = commands.add_parser("bootstrap")
    bootstrap.add_argument("--source-bronze", type=Path, required=True)
    bootstrap.add_argument("--source-silver", type=Path, required=True)
    bootstrap.add_argument("--producer-commit", required=True)
    bootstrap.add_argument("--resume", action="store_true")
    commands.add_parser("status")
    for name in ("refresh", "discover"):
        command = commands.add_parser(name)
        command.add_argument("--writer-lock-fd", type=int)
        command.add_argument("--background-lock-fd", type=int)
    args = parser.parse_args()
    load_pipeline_env(wikidata.__file__)
    published = Path(require_env("ARTIST_IMAGES_PUBLISHED_DIR"))
    if not published.is_absolute():
        raise ValueError("ARTIST_IMAGES_PUBLISHED_DIR must be absolute")
    if args.command == "status":
        snapshot = resolve_artist_image_snapshot(published)
        logger.info("Validated snapshot %s: %s", snapshot.snapshot_id, json.dumps(snapshot.manifest, sort_keys=True))
        return
    if args.command in ("refresh", "discover"):
        from wikidata.artist_images.config import WorkerConfig
        from wikidata.artist_images.worker import run_refresh
        from wikidata.artist_images.locks import LockDeferred

        try:
            status = asyncio.run(
                run_refresh(
                    WorkerConfig.from_env(),
                    discover_only=args.command == "discover",
                    writer_fd=args.writer_lock_fd,
                    background_fd=args.background_lock_fd,
                )
            )
        except LockDeferred:
            logger.warning("Image writer busy; deferred without changing active writer status")
            return
        logger.info("Image worker status: %s", json.dumps(status, sort_keys=True))
        return
    state = Path(require_env("ARTIST_IMAGES_STATE_DIR"))
    writer_lock = Path(require_env("ARTIST_IMAGES_WRITER_LOCK_FILE"))
    catalogue_lock = Path(require_env("MUSIC_TREE_PIPELINES_LOCK_FILE"))
    if not writer_lock.is_absolute() or not writer_lock.parent.is_dir():
        raise ValueError("Image writer lock requires an absolute path with an existing parent")
    # Keep the lock inode in place so all later invocations contend on the same file.
    with writer_lock.open("a") as lock:
        os.set_inheritable(lock.fileno(), False)
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        snapshot = bootstrap_snapshot(
            args.source_bronze,
            args.source_silver,
            state,
            published,
            catalogue_lock,
            args.producer_commit,
            resume=args.resume,
        )
        logger.info("Bootstrap publication validated: %s", snapshot.snapshot_id)


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    main()
