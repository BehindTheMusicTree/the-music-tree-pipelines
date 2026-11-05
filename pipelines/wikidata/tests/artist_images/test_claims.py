import pytest

from wikidata.artist_images.claims import claims_query, parse_claims

_MBID = "01234567-89ab-cdef-0123-456789abcdef"


def row(qid: str, mbid: str | None = _MBID, image: str | None = "a.jpg") -> dict:
    result = {"item": {"type": "uri", "value": f"http://www.wikidata.org/entity/{qid}"}}
    if mbid is not None:
        result["mbid"] = {"type": "literal", "value": mbid}
    if image is not None:
        result["image"] = {"type": "uri", "value": f"http://commons.wikimedia.org/wiki/Special:FilePath/{image}"}
    return result


def payload(rows: list[dict]) -> dict:
    return {"results": {"bindings": rows}}


def test_optional_query_retains_outer_qid_and_best_rank_properties() -> None:
    query = claims_query(["Q1", "Q2"])
    assert "VALUES ?item { wd:Q1 wd:Q2 }" in query
    assert "OPTIONAL { ?item wdt:P434 ?mbid . }" in query
    assert "OPTIONAL { ?item wdt:P18 ?image . }" in query


def test_multiple_associations_are_retained_and_sorted() -> None:
    batch = parse_claims(["Q1"], payload([row("Q1", image="z.jpg"), row("Q1", image="a%20b.jpg"), row("Q1")]))
    assert batch.observations[0].associations == ((_MBID, "a b.jpg"), (_MBID, "a.jpg"), (_MBID, "z.jpg"))
    assert batch.observations[0].status == "has_image"


def test_valid_negative_and_unconfirmed_identity_are_distinct() -> None:
    batch = parse_claims(["Q1", "Q2"], payload([row("Q1", image=None), row("Q2", mbid=None, image=None)]))
    assert [observation.status for observation in batch.observations] == ["no_image", "needs_confirmation"]
    assert all(not observation.associations for observation in batch.observations)


def test_one_invalid_row_quarantines_the_whole_qid() -> None:
    batch = parse_claims(["Q1", "Q2"], payload([row("Q1"), row("Q1", mbid="invalid"), row("Q2")]))
    assert batch.rejected_qids == ("Q1",)
    assert [observation.item_id for observation in batch.observations] == ["Q2"]


def test_missing_qid_rejects_whole_batch() -> None:
    with pytest.raises(ValueError, match="Incomplete"):
        parse_claims(["Q1", "Q2"], payload([row("Q1")]))


def test_unrequested_qid_rejects_whole_batch() -> None:
    with pytest.raises(ValueError, match="unrequested"):
        parse_claims(["Q1"], payload([row("Q2")]))


def test_unknown_value_is_not_a_filename() -> None:
    binding = row("Q1")
    binding["image"]["value"] = "http://www.wikidata.org/.well-known/genid/unknown"
    batch = parse_claims(["Q1"], payload([binding]))
    assert batch.observations[0].status == "no_image"


@pytest.mark.parametrize("qids", [[], ["Q1", "Q1"], ["Q1 . ?a ?b ?c"], [f"Q{i}" for i in range(1, 1002)]])
def test_query_rejects_invalid_or_unbounded_input(qids: list[str]) -> None:
    with pytest.raises(ValueError):
        claims_query(qids)
