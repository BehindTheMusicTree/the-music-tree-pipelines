"""Validate complete bounded best-rank P434/P18 observations before replacement."""

import re
from dataclasses import dataclass
from urllib.parse import unquote

_QID = re.compile(r"Q[1-9][0-9]*\Z")
_MBID = re.compile(r"[0-9a-f]{8}(?:-[0-9a-f]{4}){3}-[0-9a-f]{12}\Z")
_ENTITY_PREFIX = "http://www.wikidata.org/entity/"
_FILE_PREFIXES = (
    "http://commons.wikimedia.org/wiki/Special:FilePath/",
    "https://commons.wikimedia.org/wiki/Special:FilePath/",
)


@dataclass(frozen=True)
class ClaimObservation:
    item_id: str
    status: str
    associations: tuple[tuple[str, str], ...]


@dataclass(frozen=True)
class ClaimBatch:
    observations: tuple[ClaimObservation, ...]
    rejected_qids: tuple[str, ...]


def claims_query(qids: list[str]) -> str:
    if not qids or len(qids) > 1000 or len(set(qids)) != len(qids) or any(not _QID.fullmatch(q) for q in qids):
        raise ValueError("Claims requests require 1–1000 distinct QIDs")
    values = " ".join(f"wd:{qid}" for qid in qids)
    return f"""PREFIX wd: <http://www.wikidata.org/entity/>
PREFIX wdt: <http://www.wikidata.org/prop/direct/>
SELECT ?item ?mbid ?image WHERE {{
  VALUES ?item {{ {values} }}
  OPTIONAL {{ ?item wdt:P434 ?mbid . }}
  OPTIONAL {{ ?item wdt:P18 ?image . }}
}}"""


def _term(binding: dict, key: str) -> tuple[str, str] | None:
    if key not in binding:
        return None
    term = binding[key]
    if not isinstance(term, dict) or not isinstance(term.get("value"), str):
        raise ValueError("Malformed RDF term")
    kind = term.get("type")
    if kind not in ("uri", "literal", "bnode", "typed-literal"):
        raise ValueError("Unsupported RDF term")
    return kind, term["value"]


def _mbid(term: tuple[str, str] | None) -> str | None:
    if (
        term is None
        or term[0] == "bnode"
        or (term[0] == "uri" and term[1].startswith("http://www.wikidata.org/.well-known/genid/"))
    ):
        return None
    if term[0] not in ("literal", "typed-literal"):
        raise ValueError("P434 must be a literal")
    value = term[1].strip().lower()
    if not _MBID.fullmatch(value):
        raise ValueError("Invalid MusicBrainz artist identifier")
    return value


def _file(term: tuple[str, str] | None) -> str | None:
    if (
        term is None
        or term[0] == "bnode"
        or (term[0] == "uri" and term[1].startswith("http://www.wikidata.org/.well-known/genid/"))
    ):
        return None
    if term[0] != "uri":
        raise ValueError("P18 must be a Commons file URI")
    for prefix in _FILE_PREFIXES:
        if term[1].startswith(prefix):
            value = unquote(term[1][len(prefix) :])
            if value and not any(ord(char) < 32 for char in value):
                return value
    raise ValueError("Invalid Commons file URI")


def parse_claims(qids: list[str], payload: object) -> ClaimBatch:
    """Quarantine an entire malformed QID; missing coverage rejects the whole batch."""
    claims_query(qids)
    if not isinstance(payload, dict) or not isinstance(payload.get("results"), dict):
        raise ValueError("Invalid SPARQL response")
    bindings = payload["results"].get("bindings")
    if not isinstance(bindings, list):
        raise ValueError("Invalid SPARQL bindings")
    expected = set(qids)
    seen = set()
    rejected = set()
    mbids: dict[str, set[str]] = {qid: set() for qid in qids}
    associations: dict[str, set[tuple[str, str]]] = {qid: set() for qid in qids}
    for binding in bindings:
        if not isinstance(binding, dict):
            raise ValueError("Invalid SPARQL binding")
        item = _term(binding, "item")
        if item is None or item[0] != "uri" or not item[1].startswith(_ENTITY_PREFIX):
            raise ValueError("Invalid SPARQL item identity")
        qid = item[1][len(_ENTITY_PREFIX) :]
        if qid not in expected:
            raise ValueError("SPARQL response contains an unrequested QID")
        seen.add(qid)
        try:
            mbid = _mbid(_term(binding, "mbid"))
            image = _file(_term(binding, "image"))
            if mbid is not None:
                mbids[qid].add(mbid)
                if image is not None:
                    associations[qid].add((mbid, image))
        except ValueError:
            rejected.add(qid)
    if seen != expected:
        raise ValueError("Incomplete SPARQL QID coverage")
    observations = []
    for qid in qids:
        if qid in rejected:
            continue
        pairs = tuple(sorted(associations[qid]))
        status = "has_image" if pairs else "no_image" if mbids[qid] else "needs_confirmation"
        observations.append(ClaimObservation(qid, status, pairs))
    return ClaimBatch(tuple(observations), tuple(sorted(rejected)))
