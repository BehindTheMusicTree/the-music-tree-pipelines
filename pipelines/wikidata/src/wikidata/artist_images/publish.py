"""Publish validated, immutable artist image snapshots."""

import hashlib
import json
import os
import tempfile
import uuid
from pathlib import Path

import polars as pl
from common.artist_image_snapshot import ArtistImageSnapshot, resolve_artist_image_snapshot


def _sync_directory(path: Path) -> None:
    descriptor = os.open(path, os.O_RDONLY)
    try:
        os.fsync(descriptor)
    finally:
        os.close(descriptor)


def _write_json(path: Path, value: dict) -> None:
    with path.open("x", encoding="utf-8") as output:
        json.dump(value, output, sort_keys=True, allow_nan=False)
        output.write("\n")
        output.flush()
        os.fsync(output.fileno())


def publish_snapshot(root: Path, images: pl.DataFrame, lineage: pl.DataFrame, provenance: dict) -> ArtistImageSnapshot:
    """Validate a private candidate before atomically changing the public pointer.

    The caller must hold the environment's single-writer lock and provide the
    complete source and reject counts for the candidate.
    """
    root.mkdir(parents=True, exist_ok=True)
    root = root.resolve(strict=True)
    snapshots = root / "snapshots"
    snapshots.mkdir(exist_ok=True)
    snapshot_id = uuid.uuid4().hex
    with tempfile.TemporaryDirectory(prefix=".candidate-", dir=root) as temporary:
        candidate_root = Path(temporary)
        candidate = candidate_root / "snapshots" / snapshot_id
        candidate.mkdir(parents=True)
        artifacts = {}
        for name, frame in (("artist_images.parquet", images), ("lineage.parquet", lineage)):
            path = candidate / name
            frame.write_parquet(path)
            with path.open("rb") as source:
                os.fsync(source.fileno())
                digest = hashlib.file_digest(source, "sha256").hexdigest()
            artifacts[name] = {"sha256": digest, "rowCount": frame.height}
        manifest = {**provenance, "schemaVersion": 1, "snapshotId": snapshot_id, "artifacts": artifacts}
        _write_json(candidate / "manifest.json", manifest)
        _sync_directory(candidate)
        resolve_artist_image_snapshot(candidate_root, snapshot_id)
        source = manifest["sourceValidation"]
        if source["failed"]:
            raise ValueError("Source validation failure prevents publication")
        if source["rejectedRows"] * 100 > source["inputRows"]:
            raise ValueError("Artist image reject rate exceeds 1%")
        if (root / "current.json").exists():
            previous = resolve_artist_image_snapshot(root)
            old_ids = set(pl.read_parquet(previous.images_path, columns=["musicbrainz_artist_id"]).to_series())
            new_ids = set(images["musicbrainz_artist_id"])
            if len(old_ids - new_ids) * 100 > len(old_ids) * 5:
                raise ValueError("Artist image loss exceeds 5%; investigation required")
            lost = old_ids - new_ids
            evidence = manifest.get("removalEvidence", [])
            if not isinstance(evidence, list) or any(not isinstance(item, dict) for item in evidence):
                raise ValueError("Invalid image removal evidence")
            explained = [item.get("musicbrainzArtistId") for item in evidence]
            if len(explained) != len(set(explained)) or set(explained) != lost:
                raise ValueError("Every image removal requires source observation evidence")
            old_lineage = pl.read_parquet(previous.lineage_path)
            counts = {}
            for item in evidence:
                reason = item.get("reason")
                if reason not in ("claims_changed", "metadata_changed"):
                    raise ValueError("Invalid image removal reason")
                from datetime import datetime

                try:
                    checked = datetime.fromisoformat(item["checkedAt"])
                except (KeyError, TypeError, ValueError) as exc:
                    raise ValueError("Invalid image removal observation time") from exc
                row = old_lineage.filter(pl.col("musicbrainz_artist_id") == item["musicbrainzArtistId"]).row(
                    0, named=True
                )
                column = "claims_checked_at" if reason == "claims_changed" else "metadata_checked_at"
                if (
                    checked.utcoffset() is None
                    or item.get("itemId") != row["item_id"]
                    or item.get("imageFile") != row["image_file"]
                    or (row[column] is not None and checked <= row[column])
                ):
                    raise ValueError("Image removal requires a newer matching source observation")
                counts[reason] = counts.get(reason, 0) + 1
            if counts != manifest["removalsByReason"]:
                raise ValueError("Image removal reasons do not match source evidence")
        os.rename(candidate, snapshots / snapshot_id)
        _sync_directory(snapshots)
        pointer = root / f".current-{snapshot_id}.json"
        try:
            _write_json(pointer, {"schemaVersion": 1, "snapshotId": snapshot_id})
            os.replace(pointer, root / "current.json")
            _sync_directory(root)
        finally:
            pointer.unlink(missing_ok=True)
    return resolve_artist_image_snapshot(root, snapshot_id)
