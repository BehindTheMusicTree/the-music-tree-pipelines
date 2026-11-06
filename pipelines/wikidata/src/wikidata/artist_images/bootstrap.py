"""Capture legacy inputs and derive reproducible snapshot lineage."""

import fcntl
import hashlib
import json
import os
import shutil
import tempfile
from pathlib import Path

import polars as pl

from wikidata.silver.artist_images import OUTPUT_COLUMNS, select_artist_images

_BRONZE_FILE = "wikidata_artist_images.parquet"
_SILVER_FILES = ("10_commons_image_metadata.parquet", "11_artist_images.parquet")


def capture_inputs(
    bronze_dir: Path, silver_dir: Path, destination: Path, lock_file: Path, *, resume: bool = False
) -> dict[str, str]:
    """Capture the three legacy inputs while holding the catalogue writer's lock.

    Only a fully copied and validated capture is made visible. Explicit resume
    verifies the capture and its original source paths without re-reading sources.
    """
    if not all(path.is_absolute() for path in (bronze_dir, silver_dir, destination, lock_file)):
        raise ValueError("Bootstrap capture paths must be absolute")
    if not lock_file.is_file():
        raise ValueError("Catalogue lock file does not exist")
    destination.parent.mkdir(parents=True, exist_ok=True)
    sources = {_BRONZE_FILE: bronze_dir / _BRONZE_FILE, **{name: silver_dir / name for name in _SILVER_FILES}}
    source_paths = {name: str(path.resolve()) for name, path in sources.items()}
    with lock_file.open("a") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        try:
            if destination.exists():
                if not resume:
                    raise FileExistsError(f"Bootstrap capture already exists: {destination}")
                return _resume_capture(destination, source_paths)
            with tempfile.TemporaryDirectory(prefix=".bootstrap-", dir=destination.parent) as temporary:
                capture = Path(temporary) / "capture"
                capture.mkdir()
                hashes = {}
                for name, source in sources.items():
                    target = capture / name
                    with source.open("rb") as src, target.open("xb") as dst:
                        shutil.copyfileobj(src, dst)
                        dst.flush()
                        os.fsync(dst.fileno())
                    with target.open("rb") as artifact:
                        hashes[name] = hashlib.file_digest(artifact, "sha256").hexdigest()
                prepare_bootstrap(*(pl.read_parquet(capture / name) for name in sources))
                with (capture / "manifest.json").open("x", encoding="utf-8") as output:
                    json.dump({"schemaVersion": 1, "sha256": hashes, "sources": source_paths}, output, sort_keys=True)
                    output.write("\n")
                    output.flush()
                    os.fsync(output.fileno())
                descriptor = os.open(capture, os.O_RDONLY)
                try:
                    os.fsync(descriptor)
                finally:
                    os.close(descriptor)
                os.rename(capture, destination)
                descriptor = os.open(destination.parent, os.O_RDONLY)
                try:
                    os.fsync(descriptor)
                finally:
                    os.close(descriptor)
                return hashes
        finally:
            fcntl.flock(lock, fcntl.LOCK_UN)


def _resume_capture(destination: Path, source_paths: dict[str, str]) -> dict[str, str]:
    try:
        manifest = json.loads((destination / "manifest.json").read_text())
        if (
            not isinstance(manifest, dict)
            or manifest.get("schemaVersion") != 1
            or manifest.get("sources") != source_paths
            or not isinstance(manifest.get("sha256"), dict)
            or set(manifest["sha256"]) != set(source_paths)
        ):
            raise ValueError("Invalid or unrelated bootstrap capture")
        for name, expected in manifest["sha256"].items():
            with (destination / name).open("rb") as artifact:
                actual = hashlib.file_digest(artifact, "sha256").hexdigest()
            if actual != expected:
                raise ValueError(f"Bootstrap capture checksum mismatch: {name}")
        prepare_bootstrap(*(pl.read_parquet(destination / name) for name in source_paths))
        return manifest["sha256"]
    except (OSError, json.JSONDecodeError) as exc:
        raise ValueError("Invalid bootstrap capture") from exc


def prepare_bootstrap(
    claims: pl.DataFrame, metadata: pl.DataFrame, captured_images: pl.DataFrame
) -> tuple[pl.DataFrame, pl.DataFrame]:
    """Reproduce the captured selection before accepting its provenance.

    Inputs must have been captured together while the legacy writer was stopped.
    Missing source observation timestamps remain unknown, rather than using file times.
    """
    if claims.is_empty() or metadata.is_empty() or captured_images.is_empty():
        raise ValueError("Bootstrap inputs must not be empty")
    selected = select_artist_images(claims, metadata)
    if captured_images.schema != selected.schema or not selected.sort("musicbrainz_artist_id").equals(
        captured_images.sort("musicbrainz_artist_id")
    ):
        raise ValueError("Captured artist images do not match regenerated selection")
    timestamp = pl.Datetime("us", "UTC")
    if "fetched_at" not in claims.columns:
        claims = claims.with_columns(pl.lit(None, dtype=timestamp).alias("fetched_at"))
    if "checked_at" not in metadata.columns:
        metadata = metadata.with_columns(pl.lit(None, dtype=timestamp).alias("checked_at"))
    # Match all exported values, not just MBID, so lineage cannot describe a different candidate.
    candidates = (
        claims.join(metadata, on="image_file")
        .join(selected, on=list(OUTPUT_COLUMNS), how="inner", nulls_equal=True)
        .with_columns(pl.col("attribution_required").fill_null(True))
    )
    if candidates.filter(pl.col("item_id").is_null() | pl.col("image_file").is_null()).height:
        raise ValueError("Invalid bootstrap association")
    # Unknown observations remain unknown even if another duplicate has a timestamp.
    candidates = candidates.group_by("musicbrainz_artist_id", "item_id", "image_file", "attribution_required").agg(
        pl.when(pl.col("fetched_at").is_null().any())
        .then(pl.lit(None, dtype=timestamp))
        .otherwise(pl.col("fetched_at").min())
        .alias("claims_checked_at"),
        pl.when(pl.col("checked_at").is_null().any())
        .then(pl.lit(None, dtype=timestamp))
        .otherwise(pl.col("checked_at").min())
        .alias("metadata_checked_at"),
    )
    lineage = (
        candidates.sort("musicbrainz_artist_id", "item_id", "image_file")
        .unique("musicbrainz_artist_id", keep="first", maintain_order=True)
        .select(
            "musicbrainz_artist_id",
            "item_id",
            "image_file",
            "claims_checked_at",
            "metadata_checked_at",
            "attribution_required",
        )
    )
    if lineage.height != selected.height:
        raise ValueError("Bootstrap lineage does not cover selected images")
    return selected, lineage


def seed_registry(claims: pl.DataFrame) -> pl.DataFrame:
    """Seed every observed QID without claiming a complete P434 discovery."""
    if claims.is_empty() or "item_id" not in claims.columns:
        raise ValueError("Bootstrap claims must contain QIDs")
    if (
        claims.schema["item_id"] != pl.String
        or claims.filter(pl.col("item_id").is_null() | ~pl.col("item_id").str.contains(r"^Q[1-9][0-9]*$")).height
    ):
        raise ValueError("Invalid bootstrap QID")
    timestamp = pl.Datetime("us", "UTC")
    if "fetched_at" not in claims.columns:
        claims = claims.with_columns(pl.lit(None, dtype=timestamp).alias("fetched_at"))
    # Positive legacy observations do not establish complete per-QID claim coverage.
    return (
        claims.group_by("item_id")
        .agg(
            pl.col("fetched_at").min().alias("first_seen_at"),
            pl.lit(None, dtype=timestamp).alias("last_seen_in_discovery_at"),
        )
        .sort("item_id")
        .with_columns(
            pl.lit("unfetched").alias("claims_status"),
            pl.lit(None, dtype=timestamp).alias("claims_checked_at"),
            pl.lit(None, dtype=timestamp).alias("next_check_at"),
            pl.lit(None, dtype=timestamp).alias("last_attempt_at"),
            pl.lit(0, dtype=pl.UInt32).alias("consecutive_failures"),
            pl.lit(None, dtype=timestamp).alias("retry_after_at"),
            pl.lit(None, dtype=pl.String).alias("failure_reason"),
            pl.lit(None, dtype=timestamp).alias("first_failure_at"),
        )
    )


def bootstrap_snapshot(
    bronze_dir: Path,
    silver_dir: Path,
    state_root: Path,
    published_root: Path,
    catalogue_lock: Path,
    producer_commit: str,
    *,
    resume: bool = False,
):
    """Initialize durable seeded state and publication under a caller-held writer lock."""
    from datetime import UTC, datetime

    from common.artist_image_snapshot import resolve_artist_image_snapshot

    from wikidata.artist_images.publish import publish_snapshot
    from wikidata.artist_images.state import ImageState, compact_state, load_state

    if not producer_commit.strip():
        raise ValueError("Bootstrap requires a producer commit")
    for path in (state_root, published_root):
        if not path.is_absolute():
            raise ValueError("Bootstrap state and publication paths must be absolute")
    state_path, published_path = state_root.resolve(), published_root.resolve()
    if state_path == published_path or state_path in published_path.parents or published_path in state_path.parents:
        raise ValueError("State and publication roots must be distinct")
    if (published_root / "current.json").exists() and not (state_root / "current.json").exists():
        raise ValueError("Existing publication has no matching initialized state")
    if (state_root / "checkpoints").exists() and not (state_root / "current.json").exists():
        raise ValueError("Cannot bootstrap state with orphan checkpoints")
    capture = state_root / "bootstrap-capture"
    hashes = capture_inputs(bronze_dir, silver_dir, capture, catalogue_lock, resume=resume)
    claims = pl.read_parquet(capture / _BRONZE_FILE)
    metadata = pl.read_parquet(capture / _SILVER_FILES[0])
    selected, lineage = prepare_bootstrap(claims, metadata, pl.read_parquet(capture / _SILVER_FILES[1]))
    bootstrap = {}
    for name, digest in hashes.items():
        frame = pl.read_parquet(capture / name)
        column = "fetched_at" if name == _BRONZE_FILE else "checked_at"
        observed = frame[column].min() if column in frame.columns else None
        if observed is not None and not isinstance(observed, datetime):
            raise ValueError("Invalid bootstrap observation time")
        bootstrap[name] = {"sha256": digest, "observedAt": observed.isoformat() if observed is not None else None}
    if (state_root / "current.json").exists():
        if not resume:
            raise ValueError("Image state already initialized; explicit resume is required")
        state = load_state(state_root)
        if (
            state.provenance.get("bootstrapHashes") != hashes
            or state.sequence != 0
            or state.provenance.get("discovery", {}).get("coverage") != "bootstrap_seed"
        ):
            raise ValueError("Cannot bootstrap over unrelated or refreshed image state")
    else:
        timestamp = pl.Datetime("us", "UTC")
        if "fetched_at" not in claims.columns:
            claims = claims.with_columns(pl.lit(None, dtype=timestamp).alias("fetched_at"))
        if "checked_at" not in metadata.columns:
            metadata = metadata.with_columns(pl.lit(None, dtype=timestamp).alias("checked_at"))
        state = compact_state(
            state_root,
            ImageState(
                "",
                0,
                {
                    "registry": seed_registry(claims),
                    "claims": claims.rename({"fetched_at": "claims_checked_at"}),
                    "commons": metadata.with_columns(
                        pl.lit("legacy").alias("result_status"),
                        pl.lit(None, dtype=pl.String).alias("canonical_title"),
                        pl.lit(None, dtype=timestamp).alias("last_attempt_at"),
                        pl.lit(0, dtype=pl.UInt32).alias("consecutive_failures"),
                        pl.lit(None, dtype=timestamp).alias("retry_after_at"),
                    ),
                },
                {
                    "bootstrapHashes": hashes,
                    "bootstrap": bootstrap,
                    "discovery": {"coverage": "bootstrap_seed", "completedAt": None},
                },
            ),
        )
    if (published_root / "current.json").exists():
        snapshot = resolve_artist_image_snapshot(published_root)
        if not resume or snapshot.manifest.get("sourceStateGeneration") != state.generation:
            raise ValueError("Cannot bootstrap over an unrelated publication")
        return snapshot
    now = datetime.now(UTC)
    age_summary = {}
    for kind, column in (("claims", "claims_checked_at"), ("metadata", "metadata_checked_at")):
        oldest = lineage[column].min()
        if oldest is not None and not isinstance(oldest, datetime):
            raise ValueError("Invalid bootstrap selected source observation time")
        age_summary[kind] = {
            "overdueCount": lineage.height,
            "oldestCheckedAt": oldest.isoformat() if oldest is not None else None,
        }
    return publish_snapshot(
        published_root,
        selected,
        lineage,
        {
            "publishedAt": now.isoformat(),
            "producerCommit": producer_commit,
            "policyVersion": "bootstrap-v1",
            "sourceStateGeneration": state.generation,
            "discovery": {"coverage": "bootstrap_seed", "completedAt": None},
            "selectedImageCount": selected.height,
            "sourceQidCount": lineage["item_id"].n_unique(),
            "sourceFileCount": lineage["image_file"].n_unique(),
            "sourceValidation": {"inputRows": selected.height, "rejectedRows": 0, "failed": False},
            "rejectsByReason": {},
            "removalsByReason": {},
            "bootstrap": bootstrap,
            "ageSummary": age_summary,
            "refreshCoverage": {
                kind: {"attempted": 0, "succeeded": 0, "deferred": 0, "failed": 0} for kind in ("claims", "metadata")
            },
        },
    )
