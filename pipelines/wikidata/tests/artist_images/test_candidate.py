from datetime import UTC, datetime

import polars as pl

from wikidata.artist_images.candidate import assemble_candidate
from wikidata.artist_images.state import ImageState
from wikidata.silver.commons_image_metadata import _SCHEMA


def test_candidate_keeps_selected_lineage_and_actual_freshness() -> None:
    now = datetime(2026, 11, 5, tzinfo=UTC)
    claims = pl.DataFrame(
        {
            "item_id": ["Q2", "Q1"],
            "musicbrainz_artist_id": ["artist", "artist"],
            "image_file": ["a.jpg", "a.jpg"],
            "claims_checked_at": [now, None],
        },
        schema_overrides={"claims_checked_at": pl.Datetime("us", "UTC")},
    )
    metadata = pl.DataFrame(
        [
            {
                "image_file": "a.jpg",
                "thumb_url": "https://upload.wikimedia.org/a.jpg",
                "page_url": "https://commons.wikimedia.org/a.jpg",
                "license": "CC0",
                "non_free": False,
                "attribution_required": False,
                "checked_at": now,
            }
        ],
        schema=_SCHEMA,
    )
    state = ImageState(
        "generation",
        0,
        {"claims": claims, "commons": metadata},
        {
            "discovery": {"coverage": "complete", "completedAt": now.isoformat()},
        },
    )
    coverage = {kind: {"attempted": 0, "succeeded": 0, "deferred": 0, "failed": 0} for kind in ("claims", "metadata")}
    images, lineage, provenance = assemble_candidate(state, now, "commit", coverage)
    assert images.height == 1
    assert lineage["item_id"].to_list() == ["Q1"]
    assert lineage["claims_checked_at"].to_list() == [None]
    assert provenance["ageSummary"]["claims"]["overdueCount"] == 1
    assert provenance["ageSummary"]["metadata"]["overdueCount"] == 0
