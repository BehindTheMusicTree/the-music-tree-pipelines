from pathlib import Path
from unittest.mock import patch

import polars as pl
import pytest
from common.artist_image_snapshot import resolve_artist_image_snapshot

from wikidata.artist_images.publish import publish_snapshot


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


def frames() -> tuple[pl.DataFrame, pl.DataFrame]:
    images = pl.DataFrame(
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
    )
    return images, lineage_frame("artist")


def test_publish_pins_versions(tmp_path: Path) -> None:
    images, lineage = frames()
    old = publish_snapshot(tmp_path, images, lineage, provenance())
    new = publish_snapshot(tmp_path, images, lineage, {**provenance(), "producerCommit": "def"})
    assert old.snapshot_id != new.snapshot_id
    assert resolve_artist_image_snapshot(tmp_path).snapshot_id == new.snapshot_id
    assert resolve_artist_image_snapshot(tmp_path, old.snapshot_id).manifest["producerCommit"] == "abc"


def test_invalid_candidate_preserves_pointer(tmp_path: Path) -> None:
    images, lineage = frames()
    previous = publish_snapshot(tmp_path, images, lineage, provenance())
    with pytest.raises(ValueError, match="lineage"):
        publish_snapshot(tmp_path, images, lineage_frame("other"), provenance())
    assert resolve_artist_image_snapshot(tmp_path).snapshot_id == previous.snapshot_id


def test_pointer_failure_preserves_previous_snapshot(tmp_path: Path) -> None:
    images, lineage = frames()
    previous = publish_snapshot(tmp_path, images, lineage, provenance())
    with patch("wikidata.artist_images.publish.os.replace", side_effect=OSError("simulated failure")):
        with pytest.raises(OSError, match="simulated"):
            publish_snapshot(tmp_path, images, lineage, provenance())
    assert resolve_artist_image_snapshot(tmp_path).snapshot_id == previous.snapshot_id


def test_incomplete_provenance_does_not_publish(tmp_path: Path) -> None:
    images, lineage = frames()
    previous = publish_snapshot(tmp_path, images, lineage, provenance())
    with pytest.raises(ValueError, match="producerCommit"):
        publish_snapshot(tmp_path, images, lineage, {})
    assert resolve_artist_image_snapshot(tmp_path).snapshot_id == previous.snapshot_id


@pytest.mark.parametrize(
    ("validation", "rejects", "message"),
    [
        ({"inputRows": 1, "rejectedRows": 0, "failed": True}, {}, "Source validation failure"),
        ({"inputRows": 2, "rejectedRows": 1, "failed": False}, {"invalid_url": 1}, "reject rate"),
    ],
)
def test_source_gate_preserves_pointer(tmp_path: Path, validation: dict, rejects: dict, message: str) -> None:
    images, lineage = frames()
    previous = publish_snapshot(tmp_path, images, lineage, provenance())
    candidate = {**provenance(), "sourceValidation": validation, "rejectsByReason": rejects}
    with pytest.raises(ValueError, match=message):
        publish_snapshot(tmp_path, images, lineage, candidate)
    assert resolve_artist_image_snapshot(tmp_path).snapshot_id == previous.snapshot_id


def test_large_loss_preserves_pointer(tmp_path: Path) -> None:
    images, lineage = frames()
    previous = publish_snapshot(tmp_path, images, lineage, provenance())
    candidate = {
        **provenance(),
        "selectedImageCount": 0,
        "sourceQidCount": 0,
        "sourceFileCount": 0,
        "sourceValidation": {"inputRows": 0, "rejectedRows": 0, "failed": False},
    }
    with pytest.raises(ValueError, match="loss exceeds 5%"):
        publish_snapshot(tmp_path, images.head(0), lineage.head(0), candidate)
    assert resolve_artist_image_snapshot(tmp_path).snapshot_id == previous.snapshot_id


def test_small_loss_requires_newer_matching_source_evidence(tmp_path):
    images, lineage = frames()
    ids = [f"artist-{index}" for index in range(20)]
    images = pl.concat([images.with_columns(pl.lit(value).alias("musicbrainz_artist_id")) for value in ids])
    lineage = pl.concat([lineage.with_columns(pl.lit(value).alias("musicbrainz_artist_id")) for value in ids])
    initial = {
        **provenance(),
        "selectedImageCount": 20,
        "sourceValidation": {"inputRows": 20, "rejectedRows": 0, "failed": False},
    }
    previous = publish_snapshot(tmp_path, images, lineage, initial)
    candidate = {
        **initial,
        "selectedImageCount": 19,
        "sourceValidation": {"inputRows": 19, "rejectedRows": 0, "failed": False},
    }
    with pytest.raises(ValueError, match="requires source observation"):
        publish_snapshot(tmp_path, images.slice(1), lineage.slice(1), candidate)
    assert resolve_artist_image_snapshot(tmp_path).snapshot_id == previous.snapshot_id
    candidate["removalEvidence"] = [
        {
            "musicbrainzArtistId": ids[0],
            "itemId": "Q42",
            "imageFile": "image.jpg",
            "reason": "claims_changed",
            "checkedAt": "2026-11-05T01:00:00+00:00",
        }
    ]
    candidate["removalsByReason"] = {"claims_changed": 1}
    current = publish_snapshot(tmp_path, images.slice(1), lineage.slice(1), candidate)
    assert current.snapshot_id != previous.snapshot_id
