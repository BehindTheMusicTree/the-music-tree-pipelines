"""Assemble publishable rows and honest freshness from committed image state."""

from datetime import datetime, timedelta

import polars as pl

from wikidata.artist_images.state import ImageState
from wikidata.silver.artist_images import OUTPUT_COLUMNS, select_artist_images_with_report


def assemble_candidate(
    state: ImageState,
    now: datetime,
    producer_commit: str,
    coverage: dict,
    *,
    claims_days: int = 7,
    metadata_days: int = 30,
    previous_snapshot=None,
):
    claims, metadata = state.tables["claims"], state.tables["commons"]
    images, input_rows, rejects = select_artist_images_with_report(claims, metadata)
    timestamp = pl.Datetime("us", "UTC")
    candidates = (
        claims.join(metadata, on="image_file")
        .join(images, on=list(OUTPUT_COLUMNS), how="inner", nulls_equal=True)
        .with_columns(pl.col("attribution_required").fill_null(True))
    )
    lineage = (
        candidates.group_by("musicbrainz_artist_id", "item_id", "image_file", "attribution_required")
        .agg(
            pl.when(pl.col("claims_checked_at").is_null().any())
            .then(pl.lit(None, dtype=timestamp))
            .otherwise(pl.col("claims_checked_at").min())
            .alias("claims_checked_at"),
            pl.when(pl.col("checked_at").is_null().any())
            .then(pl.lit(None, dtype=timestamp))
            .otherwise(pl.col("checked_at").min())
            .alias("metadata_checked_at"),
        )
        .sort("musicbrainz_artist_id", "image_file", "item_id")
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
    age_summary = summarize_age(lineage, now, claims_days, metadata_days)
    evidence = removal_evidence(state, previous_snapshot, images) if previous_snapshot is not None else []
    removals = {
        reason: sum(item["reason"] == reason for item in evidence)
        for reason in sorted({item["reason"] for item in evidence})
    }
    discovery = state.provenance["discovery"]
    provenance = {
        "publishedAt": now.isoformat(),
        "producerCommit": producer_commit,
        "policyVersion": "incremental-v1",
        "sourceStateGeneration": state.generation,
        "discovery": discovery,
        "selectedImageCount": images.height,
        "sourceQidCount": lineage["item_id"].n_unique(),
        "sourceFileCount": lineage["image_file"].n_unique(),
        "sourceValidation": {"inputRows": input_rows, "rejectedRows": rejects, "failed": False},
        "rejectsByReason": {"image_contract": rejects} if rejects else {},
        "removalsByReason": removals,
        "removalEvidence": evidence,
        "freshnessPolicy": {"claimsDays": claims_days, "metadataDays": metadata_days},
        "ageSummary": age_summary,
        "refreshCoverage": coverage,
    }
    if discovery["coverage"] == "bootstrap_seed":
        provenance["bootstrap"] = state.provenance["bootstrap"]
    return images, lineage, provenance


def summarize_age(lineage: pl.DataFrame, now: datetime, claims_days: int, metadata_days: int) -> dict:
    age_summary = {}
    for kind, column, days in (
        ("claims", "claims_checked_at", claims_days),
        ("metadata", "metadata_checked_at", metadata_days),
    ):
        oldest = lineage[column].min()
        if oldest is not None and not isinstance(oldest, datetime):
            raise ValueError("Invalid selected source observation time")
        age_summary[kind] = {
            "oldestCheckedAt": oldest.isoformat() if oldest is not None else None,
            "overdueCount": lineage.filter(
                pl.col(column).is_null() | (pl.col(column) < now - timedelta(days=days))
            ).height,
        }
    return age_summary


def removal_evidence(state: ImageState, previous, images: pl.DataFrame) -> list[dict]:
    """Explain every lost selected MBID using a newer complete source observation."""
    old = pl.read_parquet(previous.lineage_path)
    lost = old.filter(~pl.col("musicbrainz_artist_id").is_in(images["musicbrainz_artist_id"].implode()))
    evidence = []
    for row in lost.iter_rows(named=True):
        qid = state.provenance.get("redirects", {}).get(row["item_id"], row["item_id"])
        registry = state.tables["registry"].filter(pl.col("item_id") == qid)
        claims = state.tables["claims"].filter(
            (pl.col("item_id") == qid)
            & (pl.col("musicbrainz_artist_id") == row["musicbrainz_artist_id"])
            & (pl.col("image_file") == row["image_file"])
        )
        metadata = state.tables["commons"].filter(pl.col("image_file") == row["image_file"])
        claim_time = registry["claims_checked_at"][0] if registry.height else None
        metadata_time = metadata["checked_at"][0] if metadata.height else None
        reason, checked = None, None
        if (
            claims.is_empty()
            and claim_time is not None
            and (row["claims_checked_at"] is None or claim_time > row["claims_checked_at"])
        ):
            reason, checked = "claims_changed", claim_time
        elif metadata_time is not None and (
            row["metadata_checked_at"] is None or metadata_time > row["metadata_checked_at"]
        ):
            reason, checked = "metadata_changed", metadata_time
        if reason is None or checked is None:
            raise ValueError("Image removal lacks a newer successful source observation")
        evidence.append(
            {
                "musicbrainzArtistId": row["musicbrainz_artist_id"],
                "itemId": row["item_id"],
                "imageFile": row["image_file"],
                "reason": reason,
                "checkedAt": checked.isoformat(),
            }
        )
    return evidence
