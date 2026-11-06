from datetime import UTC, datetime, timedelta

import polars as pl
import pytest

from wikidata.artist_images.bootstrap import seed_registry
from wikidata.artist_images.progress import defer_failed, quarantine_summary
from wikidata.artist_images.state import ImageState, checkpoint_state, compact_state, load_state


def test_quarantine_backoff_survives_restarts_and_caps_at_one_day(tmp_path):
    now = datetime(2026, 11, 5, tzinfo=UTC)
    claims = pl.DataFrame({"item_id": ["Q1"]})
    state = compact_state(
        tmp_path,
        ImageState(
            "",
            0,
            {"registry": seed_registry(claims), "claims": claims, "commons": pl.DataFrame({"image_file": ["a.jpg"]})},
            {},
        ),
    )
    first = now
    for delay in [15, 30, 60, 120, 240, 480, 960, 1440, 1440]:
        state = defer_failed(
            tmp_path,
            load_state(tmp_path),
            "registry",
            {"Q1"},
            now,
            reasons={"Q1": "invalid_claims: Invalid MusicBrainz artist identifier"},
        )
        row = load_state(tmp_path).tables["registry"].row(0, named=True)
        assert row["retry_after_at"] == now + timedelta(minutes=delay)
        assert row["first_failure_at"] == first
        assert row["claims_checked_at"] is None
        assert load_state(tmp_path).tables["claims"].equals(claims)
        now = row["retry_after_at"]
    assert quarantine_summary(state)["count"] == 1


def test_legacy_registry_base_and_checkpoint_upgrade_without_rewriting_history(tmp_path):
    claims = pl.DataFrame({"item_id": ["Q1"]})
    legacy = seed_registry(claims).drop("failure_reason", "first_failure_at")
    state = compact_state(
        tmp_path,
        ImageState(
            "", 0, {"registry": legacy, "claims": claims, "commons": pl.DataFrame({"image_file": ["a.jpg"]})}, {}
        ),
    )
    checkpoint_state(
        tmp_path,
        state,
        {"registry": legacy.with_columns(pl.lit(1, dtype=pl.UInt32).alias("consecutive_failures"))},
        {"registry": ["Q1"]},
    )
    recovered = load_state(tmp_path)
    assert recovered.sequence == 1
    assert recovered.tables["registry"]["consecutive_failures"].to_list() == [1]
    assert recovered.tables["registry"]["failure_reason"].to_list() == [None]
    assert recovered.tables["registry"]["first_failure_at"].to_list() == [None]
    defer_failed(
        tmp_path,
        recovered,
        "registry",
        {"Q1"},
        datetime.now(UTC),
        reasons={"Q1": "invalid_claims: Invalid MusicBrainz artist identifier"},
    )
    assert quarantine_summary(load_state(tmp_path))["count"] == 1
    compact_state(tmp_path, load_state(tmp_path))
    assert quarantine_summary(load_state(tmp_path))["count"] == 1
    assert "failure_reason" not in pl.read_parquet(tmp_path / "base" / state.generation / "registry.parquet").columns


def test_partially_missing_diagnostic_schema_is_not_treated_as_legacy(tmp_path):
    claims = pl.DataFrame({"item_id": ["Q1"]})
    with pytest.raises(ValueError, match="Incomplete registry failure diagnostics"):
        compact_state(
            tmp_path,
            ImageState(
                "",
                0,
                {
                    "registry": seed_registry(claims).drop("failure_reason"),
                    "claims": claims,
                    "commons": pl.DataFrame({"image_file": ["a.jpg"]}),
                },
                {},
            ),
        )


def test_quarantine_summary_is_bounded_and_transport_failure_keeps_known_diagnostic(tmp_path):
    now = datetime(2026, 11, 5, tzinfo=UTC)
    claims = pl.DataFrame({"item_id": [f"Q{i}" for i in range(1, 26)]})
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
            {},
        ),
    )
    state = defer_failed(
        tmp_path,
        state,
        "registry",
        set(claims["item_id"]),
        now,
        reasons={qid: "invalid_claims: Invalid MusicBrainz artist identifier" for qid in claims["item_id"]},
    )
    state = defer_failed(tmp_path, state, "registry", {"Q1"}, now + timedelta(hours=1))
    summary = quarantine_summary(load_state(tmp_path))
    assert summary["count"] == 25
    assert len(summary["sample"]) == 20
    assert summary["oldestFailureAt"] == now.isoformat()
    assert summary["sample"][0]["itemId"] == "Q1"
    assert summary["sample"][0]["consecutiveFailures"] == 2
    assert summary["sample"][0]["firstFailureAt"] == now.isoformat()
