from datetime import UTC, datetime
from pathlib import Path

import polars as pl
import pytest

from wikidata.artist_images.bootstrap import seed_registry
from wikidata.artist_images.discovery import commit_discovery, parse_discovery
from wikidata.artist_images.state import ImageState, compact_state, load_state


def test_discovery_adds_imageless_entities_without_removing_omitted_qids(tmp_path: Path) -> None:
    now = datetime(2026, 11, 5, tzinfo=UTC)
    claims = pl.DataFrame({"item_id": ["Q1"], "image_file": ["a.jpg"]})
    state = compact_state(
        tmp_path,
        ImageState(
            "",
            0,
            {
                "registry": seed_registry(claims),
                "claims": claims,
                "commons": pl.DataFrame({"image_file": ["a.jpg"]}),
            },
            {"discovery": {"coverage": "bootstrap_seed", "completedAt": None}},
        ),
    )
    commit_discovery(tmp_path, state, ["Q2"], now)
    recovered = load_state(tmp_path)
    assert recovered.tables["claims"].equals(claims)
    registry = recovered.tables["registry"]
    assert registry["item_id"].to_list() == ["Q1", "Q2"]
    assert registry["last_seen_in_discovery_at"].to_list() == [None, now]
    assert recovered.provenance["discovery"]["completedAt"] == now.isoformat()


def test_discovery_validates_and_deduplicates_complete_payload() -> None:
    row = {"item": {"type": "uri", "value": "http://www.wikidata.org/entity/Q1"}}
    assert parse_discovery({"results": {"bindings": [row, row]}}) == ["Q1"]
    with pytest.raises(ValueError):
        parse_discovery({"results": {"bindings": [row, {"item": {"type": "literal", "value": "Q2"}}]}})
    with pytest.raises(ValueError):
        parse_discovery({"results": {"bindings": []}})
