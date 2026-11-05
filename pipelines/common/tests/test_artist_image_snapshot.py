import hashlib
import json
from pathlib import Path

import polars as pl
import pytest

from common.artist_image_snapshot import resolve_artist_image_snapshot


def provenance() -> dict:
    return {
        "publishedAt": "2026-11-05T00:00:00Z",
        "producerCommit": "abc",
        "policyVersion": "1",
        "sourceStateGeneration": "generation-1",
        "discovery": {"coverage": "complete", "completedAt": "2026-11-05T00:00:00Z"},
        "selectedImageCount": 1,
        "sourceQidCount": 1,
        "sourceFileCount": 1,
        "sourceValidation": {"inputRows": 1, "rejectedRows": 0, "failed": False},
        "rejectsByReason": {},
        "removalsByReason": {},
        "ageSummary": {kind: {"overdueCount": 0, "oldestCheckedAt": None} for kind in ("claims", "metadata")},
        "refreshCoverage": {
            kind: {"attempted": 0, "succeeded": 0, "deferred": 0, "failed": 0} for kind in ("claims", "metadata")
        },
    }


def lineage_frame(artist: str) -> pl.DataFrame:
    return pl.DataFrame(
        {
            "musicbrainz_artist_id": [artist],
            "item_id": ["Q42"],
            "image_file": ["image.jpg"],
            "claims_checked_at": [None],
            "metadata_checked_at": [None],
            "attribution_required": [True],
        },
        schema={
            "musicbrainz_artist_id": pl.String,
            "item_id": pl.String,
            "image_file": pl.String,
            "claims_checked_at": pl.Datetime("us", "UTC"),
            "metadata_checked_at": pl.Datetime("us", "UTC"),
            "attribution_required": pl.Boolean,
        },
    )


def make_snapshot(root: Path) -> Path:
    directory = root / "snapshots" / "version-1"
    directory.mkdir(parents=True)
    pl.DataFrame(
        {
            "musicbrainz_artist_id": ["artist"],
            "thumb_url": ["https://upload.wikimedia.org/image"],
            "page_url": ["https://commons.wikimedia.org/image"],
            "author": ["author"],
            "license": ["CC BY"],
            "license_url": [None],
        },
        schema=dict.fromkeys(
            ("musicbrainz_artist_id", "thumb_url", "page_url", "author", "license", "license_url"), pl.String
        ),
    ).write_parquet(directory / "artist_images.parquet")
    lineage_frame("artist").write_parquet(directory / "lineage.parquet")
    artifacts = {}
    for name in ("artist_images.parquet", "lineage.parquet"):
        artifacts[name] = {"sha256": hashlib.sha256((directory / name).read_bytes()).hexdigest(), "rowCount": 1}
    (directory / "manifest.json").write_text(
        json.dumps({**provenance(), "schemaVersion": 1, "snapshotId": "version-1", "artifacts": artifacts})
    )
    (root / "current.json").write_text(json.dumps({"schemaVersion": 1, "snapshotId": "version-1"}))
    return directory


def test_resolves_pinned_snapshot(tmp_path: Path) -> None:
    directory = make_snapshot(tmp_path)
    snapshot = resolve_artist_image_snapshot(tmp_path)
    (tmp_path / "current.json").write_text("{}")
    assert snapshot.snapshot_id == "version-1"
    assert snapshot.images_path == directory / "artist_images.parquet"
    assert resolve_artist_image_snapshot(tmp_path, "version-1").lineage_path == directory / "lineage.parquet"


def test_rejects_corrupted_artifact(tmp_path: Path) -> None:
    directory = make_snapshot(tmp_path)
    (directory / "artist_images.parquet").write_bytes(b"corrupt")
    with pytest.raises(ValueError, match="checksum"):
        resolve_artist_image_snapshot(tmp_path)


def test_rejects_snapshot_path_traversal(tmp_path: Path) -> None:
    make_snapshot(tmp_path)
    with pytest.raises(ValueError, match="snapshot ID"):
        resolve_artist_image_snapshot(tmp_path, "../outside")


@pytest.mark.parametrize(
    ("change", "message"),
    [
        ({"producerCommit": ""}, "producerCommit"),
        ({"publishedAt": "2026-11-05T00:00:00"}, "UTC"),
        ({"selectedImageCount": 2}, "source validation counts"),
        ({"sourceQidCount": True}, "sourceQidCount"),
        ({"discovery": {"coverage": "complete", "completedAt": None}}, "completion time"),
        ({"discovery": {"coverage": "bootstrap_seed", "completedAt": None}}, "bootstrap provenance"),
        ({"refreshCoverage": {}}, "refresh coverage"),
    ],
)
def test_rejects_invalid_provenance(tmp_path: Path, change: dict, message: str) -> None:
    directory = make_snapshot(tmp_path)
    path = directory / "manifest.json"
    manifest = json.loads(path.read_text())
    manifest.update(change)
    path.write_text(json.dumps(manifest))
    with pytest.raises(ValueError, match=message):
        resolve_artist_image_snapshot(tmp_path)
