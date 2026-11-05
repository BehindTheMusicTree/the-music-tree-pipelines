import pytest

from wikidata.artist_images.entities import parse_confirmations


def test_missing_and_removed_mbid_are_explicit_negatives() -> None:
    result = parse_confirmations(
        ["Q1", "Q2"],
        {
            "entities": {
                "Q1": {"id": "Q1", "missing": ""},
                "Q2": {"id": "Q2", "claims": {}},
            }
        },
    )
    assert [(row.item_id, row.status) for row in result.observations] == [("Q1", "missing"), ("Q2", "no_mbid")]


def test_redirect_preserves_source_until_registry_merge() -> None:
    result = parse_confirmations(
        ["Q1"],
        {
            "redirects": [{"from": "Q1", "to": "Q2"}],
            "entities": {"Q2": {"id": "Q2", "claims": {}}},
        },
    )
    assert result.observations == ()
    assert result.redirects == (("Q1", "Q2"),)
    assert result.requires_refresh == ("Q2",)


def statement(rank: str, snaktype: str) -> dict:
    snak = {"snaktype": snaktype}
    if snaktype == "value":
        snak["datavalue"] = {"value": "01234567-89ab-cdef-0123-456789abcdef"}
    return {"rank": rank, "mainsnak": snak}


def test_preferred_novalue_suppresses_normal_value() -> None:
    result = parse_confirmations(
        ["Q1"],
        {
            "entities": {
                "Q1": {
                    "id": "Q1",
                    "claims": {
                        "P434": [
                            statement("normal", "value"),
                            statement("preferred", "novalue"),
                        ]
                    },
                }
            }
        },
    )
    assert result.observations[0].status == "no_mbid"


def test_existing_mbid_requires_fresh_claims_instead_of_deletion() -> None:
    result = parse_confirmations(
        ["Q1"],
        {
            "entities": {
                "Q1": {
                    "id": "Q1",
                    "claims": {
                        "P434": [
                            statement("normal", "value"),
                        ]
                    },
                }
            }
        },
    )
    assert result.observations == ()
    assert result.requires_refresh == ("Q1",)


@pytest.mark.parametrize(
    "body",
    [
        {"entities": {}},
        {"error": {"code": "maxlag"}, "entities": {}},
        {"entities": {"Q1": {"id": "Q1"}}},
        {"entities": {"Q1": {"id": "Q1", "claims": {"P434": [{}]}}}},
    ],
)
def test_incomplete_or_malformed_response_is_not_deletion(body: dict) -> None:
    with pytest.raises(ValueError):
        parse_confirmations(["Q1"], body)


def test_redirect_merge_is_atomic_and_preserves_alias_history(tmp_path) -> None:
    from datetime import UTC, datetime

    import polars as pl

    from wikidata.artist_images.bootstrap import seed_registry
    from wikidata.artist_images.claims import ClaimObservation
    from wikidata.artist_images.entities import merge_redirect
    from wikidata.artist_images.state import ImageState, compact_state, load_state

    now = datetime(2026, 11, 5, tzinfo=UTC)
    claims = pl.DataFrame(
        {
            "item_id": ["Q1", "Q2"],
            "musicbrainz_artist_id": ["old", "also-old"],
            "image_file": ["a.jpg", "b.jpg"],
            "claims_checked_at": [now, now],
        }
    )
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
            {"redirects": {"Q3": "Q1"}},
        ),
    )
    merge_redirect(
        tmp_path,
        state,
        "Q1",
        ClaimObservation("Q2", "has_image", (("new", "c.jpg"),)),
        now,
        positive_ttl_days=7,
        negative_ttl_days=30,
    )
    recovered = load_state(tmp_path)
    assert recovered.tables["registry"]["item_id"].to_list() == ["Q2"]
    assert recovered.tables["claims"]["image_file"].to_list() == ["c.jpg"]
    assert recovered.provenance["redirects"] == {"Q1": "Q2", "Q3": "Q2"}
