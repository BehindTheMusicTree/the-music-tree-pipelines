"""Resolve a pinned artist-image snapshot and validate its immutable artifacts."""

import hashlib
import json
import re
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

import polars as pl

_ARTIFACTS = ("artist_images.parquet", "lineage.parquet")
_IMAGE_COLUMNS = {
    "musicbrainz_artist_id": pl.String,
    "thumb_url": pl.String,
    "page_url": pl.String,
    "author": pl.String,
    "license": pl.String,
    "license_url": pl.String,
}
_ID = re.compile(r"[A-Za-z0-9][A-Za-z0-9_-]{0,127}\Z")
_LINEAGE_COLUMNS = {
    "musicbrainz_artist_id": pl.String,
    "item_id": pl.String,
    "image_file": pl.String,
    "claims_checked_at": pl.Datetime("us", "UTC"),
    "metadata_checked_at": pl.Datetime("us", "UTC"),
    "attribution_required": pl.Boolean,
}


@dataclass(frozen=True)
class ArtistImageSnapshot:
    snapshot_id: str
    images_path: Path
    lineage_path: Path
    manifest: dict


def _read_json(path: Path) -> dict:
    try:
        value = json.loads(path.read_text())
    except (OSError, ValueError) as exc:
        raise ValueError(f"Invalid snapshot JSON: {path}") from exc
    if not isinstance(value, dict):
        raise ValueError(f"Expected JSON object: {path}")
    return value


def _validate_time(value: object, name: str, *, nullable: bool = False) -> None:
    if value is None and nullable:
        return
    if not isinstance(value, str):
        raise ValueError(f"Invalid snapshot timestamp: {name}")
    try:
        timestamp = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as exc:
        raise ValueError(f"Invalid snapshot timestamp: {name}") from exc
    if timestamp.tzinfo is None or timestamp.utcoffset() != timezone.utc.utcoffset(timestamp):
        raise ValueError(f"Snapshot timestamp must be UTC: {name}")


def _validate_count(value: object, name: str) -> None:
    if type(value) is not int or value < 0:
        raise ValueError(f"Invalid snapshot count: {name}")


def _validate_manifest(manifest: dict, snapshot_id: str) -> None:
    if (
        type(manifest.get("schemaVersion")) is not int
        or manifest["schemaVersion"] != 1
        or manifest.get("snapshotId") != snapshot_id
    ):
        raise ValueError("Unsupported or mismatched artist image manifest")
    for key in ("producerCommit", "policyVersion", "sourceStateGeneration"):
        if not isinstance(manifest.get(key), str) or not manifest[key].strip():
            raise ValueError(f"Invalid snapshot provenance: {key}")
    _validate_time(manifest.get("publishedAt"), "publishedAt")
    discovery = manifest.get("discovery")
    if not isinstance(discovery, dict) or discovery.get("coverage") not in ("bootstrap_seed", "complete"):
        raise ValueError("Invalid snapshot discovery provenance")
    _validate_time(discovery.get("completedAt"), "discovery.completedAt", nullable=True)
    if discovery["coverage"] == "complete" and discovery.get("completedAt") is None:
        raise ValueError("Complete discovery requires a completion time")
    for key in ("selectedImageCount", "sourceQidCount", "sourceFileCount"):
        _validate_count(manifest.get(key), key)
    source_validation = manifest.get("sourceValidation")
    if not isinstance(source_validation, dict) or type(source_validation.get("failed")) is not bool:
        raise ValueError("Invalid snapshot source validation")
    for key in ("inputRows", "rejectedRows"):
        _validate_count(source_validation.get(key), key)
    if source_validation["inputRows"] != manifest["selectedImageCount"] + source_validation["rejectedRows"]:
        raise ValueError("Inconsistent snapshot source validation counts")
    for key in ("rejectsByReason", "removalsByReason"):
        counts = manifest.get(key)
        if not isinstance(counts, dict):
            raise ValueError(f"Invalid snapshot provenance: {key}")
        for reason, count in counts.items():
            if not isinstance(reason, str) or not reason.strip():
                raise ValueError(f"Invalid snapshot reason: {key}")
            _validate_count(count, key)
    if sum(manifest["rejectsByReason"].values()) != source_validation["rejectedRows"]:
        raise ValueError("Snapshot reject reasons do not match rejected rows")
    for kind in ("claims", "metadata"):
        age_summary = manifest.get("ageSummary")
        ages = age_summary.get(kind) if isinstance(age_summary, dict) else None
        if not isinstance(ages, dict):
            raise ValueError(f"Invalid snapshot age summary: {kind}")
        _validate_count(ages.get("overdueCount"), "overdueCount")
        _validate_time(ages.get("oldestCheckedAt"), "oldestCheckedAt", nullable=True)
        refresh = manifest.get("refreshCoverage")
        coverage = refresh.get(kind) if isinstance(refresh, dict) else None
        if not isinstance(coverage, dict):
            raise ValueError(f"Invalid snapshot refresh coverage: {kind}")
        for key in ("attempted", "succeeded", "deferred", "failed"):
            _validate_count(coverage.get(key), key)
        if coverage["succeeded"] + coverage["failed"] > coverage["attempted"]:
            raise ValueError(f"Inconsistent snapshot refresh coverage: {kind}")
    if discovery["coverage"] == "bootstrap_seed":
        bootstrap = manifest.get("bootstrap")
        if not isinstance(bootstrap, dict) or set(bootstrap) != {
            "wikidata_artist_images.parquet",
            "10_commons_image_metadata.parquet",
            "11_artist_images.parquet",
        }:
            raise ValueError("Missing bootstrap provenance")
        for name, source in bootstrap.items():
            if not isinstance(name, str) or not isinstance(source, dict):
                raise ValueError("Invalid bootstrap provenance")
            if not isinstance(source.get("sha256"), str) or not re.fullmatch(r"[0-9a-f]{64}", source["sha256"]):
                raise ValueError("Invalid bootstrap source checksum")
            _validate_time(source.get("observedAt"), "bootstrap.observedAt", nullable=True)


def resolve_artist_image_snapshot(root: Path, snapshot_id: str | None = None) -> ArtistImageSnapshot:
    """Read the pointer once and reject unsupported or damaged snapshots before use."""
    root = root.resolve(strict=True)
    if snapshot_id is None:
        pointer = _read_json(root / "current.json")
        if (
            set(pointer) != {"schemaVersion", "snapshotId"}
            or type(pointer.get("schemaVersion")) is not int
            or pointer["schemaVersion"] != 1
        ):
            raise ValueError("Unsupported artist image pointer version")
        snapshot_id = pointer.get("snapshotId")
    if not isinstance(snapshot_id, str) or not _ID.fullmatch(snapshot_id):
        raise ValueError("Invalid artist image snapshot ID")
    snapshots = (root / "snapshots").resolve(strict=True)
    if snapshots.parent != root:
        raise ValueError("Snapshots directory escapes publication root")
    directory = (snapshots / snapshot_id).resolve(strict=True)
    if directory.parent != snapshots:
        raise ValueError("Snapshot path escapes publication root")
    manifest = _read_json(directory / "manifest.json")
    _validate_manifest(manifest, snapshot_id)
    artifacts = manifest.get("artifacts")
    if not isinstance(artifacts, dict) or set(artifacts) != set(_ARTIFACTS):
        raise ValueError("Invalid artist image artifact list")
    frames = {}
    for name in _ARTIFACTS:
        path = (directory / name).resolve(strict=True)
        if path.parent != directory:
            raise ValueError("Artist image artifact path escapes snapshot")
        metadata = artifacts[name]
        if not isinstance(metadata, dict) or type(metadata.get("rowCount")) is not int or metadata["rowCount"] < 0:
            raise ValueError(f"Invalid artifact metadata: {name}")
        with path.open("rb") as artifact:
            digest = hashlib.file_digest(artifact, "sha256").hexdigest()
        if metadata.get("sha256") != digest:
            raise ValueError(f"Artist image artifact checksum mismatch: {name}")
        frame = pl.read_parquet(path)
        if frame.height != metadata["rowCount"]:
            raise ValueError(f"Artist image artifact row count mismatch: {name}")
        frames[name] = frame
    images = frames[_ARTIFACTS[0]]
    lineage = frames[_ARTIFACTS[1]]
    if manifest["selectedImageCount"] != images.height:
        raise ValueError("Snapshot selected image count mismatch")
    if images.schema != _IMAGE_COLUMNS:
        raise ValueError("Invalid artist image schema")
    for column in ("musicbrainz_artist_id", "thumb_url", "page_url", "license"):
        if images[column].null_count() or images.filter(pl.col(column).str.strip_chars() == "").height:
            raise ValueError(f"Null or blank artist image field: {column}")
    ids = images["musicbrainz_artist_id"]
    if ids.n_unique() != images.height:
        raise ValueError("Duplicate artist image ID")
    valid_urls = (
        pl.col("thumb_url").str.contains(r"^https://(upload|thumb)\.wikimedia\.org/")
        & pl.col("page_url").str.starts_with("https://")
        & (pl.col("license_url").is_null() | pl.col("license_url").str.contains(r"^https?://"))
        & pl.all_horizontal(
            pl.col(column).is_null() | (pl.col(column).str.len_bytes() <= 2048)
            for column in ("thumb_url", "page_url", "license_url")
        )
    )
    if images.filter(~valid_urls).height:
        raise ValueError("Invalid artist image URLs")
    if lineage.schema != _LINEAGE_COLUMNS:
        raise ValueError("Invalid artist image lineage schema")
    lineage_ids = lineage["musicbrainz_artist_id"]
    if lineage_ids.null_count() or lineage_ids.n_unique() != lineage.height or set(lineage_ids) != set(ids):
        raise ValueError("Artist image lineage does not match images")
    if lineage.select(
        pl.any_horizontal(pl.col("item_id", "image_file", "attribution_required").is_null()).any()
    ).item():
        raise ValueError("Null artist image lineage field")
    if (
        lineage["item_id"].n_unique() != manifest["sourceQidCount"]
        or lineage["image_file"].n_unique() != manifest["sourceFileCount"]
    ):
        raise ValueError("Snapshot source counts do not match lineage")
    joined = images.join(lineage.select("musicbrainz_artist_id", "attribution_required"), on="musicbrainz_artist_id")
    if joined.filter(
        pl.col("attribution_required") & (pl.col("author").is_null() | (pl.col("author").str.strip_chars() == ""))
    ).height:
        raise ValueError("Required artist image attribution missing")
    return ArtistImageSnapshot(snapshot_id, directory / _ARTIFACTS[0], directory / _ARTIFACTS[1], manifest)
