import json
import re
from collections.abc import Mapping, Sequence
from dataclasses import dataclass

import httpx
import tenacity

SPARQL_ENDPOINT = "https://query.wikidata.org/sparql"
API_ENDPOINT = "https://www.wikidata.org/w/api.php"
WBGETENTITIES_MAX_IDS = 50
USER_AGENT = "the-music-tree-pipelines (https://github.com/BehindTheMusicTree/the-music-tree-pipelines)"

MUSIC_GENRE_QID = "Q188451"
INDIGENOUS_TO_PID = "P2341"
COUNTRY_OF_ORIGIN_PID = "P495"

# Label service fallback chain: tries each language in order, falling back to the
# next when an item has no label in the previous one, before finally falling back
# to printing the QID (see silver/item_links.py's has_item_label/has_parent_label).
# "mul" = Wikidata's language-independent label (e.g. band/artist names); the rest
# are the languages most likely to carry a label for regional/national genres that
# lack an English one.
LABEL_LANGUAGES = "en,mul,es,fr,de,pt,it,nl,ru,ja,zh"

GENRE_TREE_QUERY_VARIABLES = ("item", "itemLabel", "parent", "parentLabel", "relation")
INDIGENOUS_TO_QUERY_VARIABLES = ("item", "indigenousTo", "indigenousToLabel")
COUNTRY_OF_ORIGIN_QUERY_VARIABLES = ("item", "countryOfOrigin", "countryOfOriginLabel")

# P31 = "instance of": class membership, identifies what an item *is*
# (e.g. "rock music" P31 "music genre" — this is how we find the full set of genre items).
#
# P279 = "subclass of": links a class to a more general class, building a taxonomy
# (e.g. "heavy metal" is a P279 subclass of "metal music", which is itself a P279
# subclass of "rock music" — a genre can chain through several P279 hops).
#
# P361 = "part of": a part-whole (not is-a) edge, used inconsistently in place of
# or alongside P279 for what is, in practice, still subgenre-of-genre information.
#
# P31 and P279/P361 are never interchangeable: P31 tells us an item IS a music
# genre, P279/P361 tell us HOW two genres relate to each other. We therefore query
# P31 to find every genre item, and separately query each genre's own direct
# P279/P361 edges to find its parent(s) — never P31 for the parent edges, and never
# a P279 walk to find the genre set (a direct P279 query against "music genre"
# itself finds only 12 items, mostly meta-categories like "rock genre" rather
# than real genres — see DESIGN.md#1-bronze).
#
# For each genre, we ingest its direct P279 ("subclass of") and P361 ("part of")
# parent(s), tagging each edge with ?relation so the two relationship types
# remain distinguishable downstream.
#
# Parents are not restricted to items that are themselves music genres:
# Wikidata's raw P279/P361 relationships can point to non-genre classes
# (e.g. "opera" P279 "composed musical work"). Filtering parents to
# genre-only items is therefore a Silver-layer responsibility.
#
# Genres with neither a P279 nor a P361 parent still produce exactly one row,
# with ?parent and ?relation unbound, preserving the pre-P361 root-item shape.
GENRE_TREE_QUERY = f"""
SELECT ?item ?itemLabel ?parent ?parentLabel ?relation WHERE {{
  ?item wdt:P31 wd:{MUSIC_GENRE_QID}.
  {{
    ?item wdt:P279 ?parent .
    BIND("P279" AS ?relation)
  }}
  UNION
  {{
    ?item wdt:P361 ?parent .
    BIND("P361" AS ?relation)
  }}
  UNION
  {{
    FILTER NOT EXISTS {{ ?item wdt:P279 ?p279Parent }}
    FILTER NOT EXISTS {{ ?item wdt:P361 ?p361Parent }}
  }}
  SERVICE wikibase:label {{ bd:serviceParam wikibase:language "{LABEL_LANGUAGES}". }}
}}
"""

# P2341 = "indigenous to": links an item to the people/ethnic group it originates from
# (e.g. "Han Chinese music" P2341 "Han Chinese people"). This is a per-item ethnographic
# attribute, not a genre-to-genre taxonomy edge like P279/P361, and its cardinality is
# independent of a genre's parent count — an item can have any number of P279/P361 parents
# and, separately, any number of P2341 values. Querying both in a single row (as
# GENRE_TREE_QUERY does for P279/P361, which share the same "parent edge" semantics) would
# cross-multiply the two OPTIONALs into spurious combinations, so this is a separate query
# producing a separate (item, indigenous_to) table — see ingest.ingest_indigenous_to.
#
# Only items missing a P2341 value are absent from the result; an item with several values
# produces one row per value.
INDIGENOUS_TO_QUERY = f"""
SELECT ?item ?indigenousTo ?indigenousToLabel WHERE {{
  ?item wdt:P31 wd:{MUSIC_GENRE_QID} ;
        wdt:{INDIGENOUS_TO_PID} ?indigenousTo .
  SERVICE wikibase:label {{ bd:serviceParam wikibase:language "{LABEL_LANGUAGES}". }}
}}
"""

# P495 = "country of origin": links a creative work/genre to the country it originated in
# (e.g. "fado" P495 "Portugal"). Like P2341, this is a per-item attribute, not a genre-to-genre
# taxonomy edge, and its cardinality is independent of an item's P279/P361 parent count, so it's
# queried and ingested separately from GENRE_TREE_QUERY for the same cross-multiplication reason
# documented above INDIGENOUS_TO_QUERY — see ingest.ingest_country_of_origin.
#
# Only items missing a P495 value are absent from the result; an item with several values
# produces one row per value.
COUNTRY_OF_ORIGIN_QUERY = f"""
SELECT ?item ?countryOfOrigin ?countryOfOriginLabel WHERE {{
  ?item wdt:P31 wd:{MUSIC_GENRE_QID} ;
        wdt:{COUNTRY_OF_ORIGIN_PID} ?countryOfOrigin .
  SERVICE wikibase:label {{ bd:serviceParam wikibase:language "{LABEL_LANGUAGES}". }}
}}
"""


ARCHAEOLOGICAL_CULTURE_QID = "Q465299"
HISTORICAL_COUNTRY_QID = "Q3024240"
COUNTRY_QID = "Q6256"
GREAT_HOMELAND_QID = "Q3115110"
CULTURAL_REGION_QID = "Q3502482"
CIVILIZATION_QID = "Q8432"
ETHNIC_GROUP_QID = "Q41710"
HISTORICAL_REGION_QID = "Q1620908"
NOTABLE_SITELINKS = 30
MACRO_REGION_MIN_MEMBERS = 2
CULTURAL_ENTITY_QUERY_TIMEOUT = 180.0

CULTURAL_ENTITY_QUERY_VARIABLES = ("item", "itemLabel", "glottocode")
CULTURAL_ENTITY_CLAIM_QUERY_VARIABLES = ("item", "value", "precision")

# Which items belong to each cultural-entity kind. An item can match several kinds (a historical
# country with a Glottocode, say) and carry several Glottocodes (Limburgish maps to two languoids),
# so ingestion keeps one row per (item, kind, glottocode). time_period items are the P2348 targets of the
# archaeological cultures, civilizations, historical and modern countries and cultural regions: they are no
# entities, only a date fallback, joined by UNION since a VALUES class list makes WDQS time out.
# Great homelands (irredentist projects such as Greater Serbia) are a subclass of country in Wikidata but no countries, so they are filtered out of country.
# Ethnic groups (~80k items) and historical regions (~113k) are too broad to pull wholesale, so only those with at least NOTABLE_SITELINKS sitelinks are kept. A macro region has no class of its
# own: it is any item that at least MACRO_REGION_MIN_MEMBERS countries or historical countries are part of
# (P361), located in (P706) or on the continent of (P30); members are matched on a direct P31 and the
# optimizer is switched off, as WDQS otherwise starts from the P361 triples and exceeds its timeout.
CULTURAL_ENTITY_KIND_PATTERNS = {
    "languoid": "?item wdt:P1394 ?glottocode .",
    "archaeological_culture": f"?item wdt:P31/wdt:P279* wd:{ARCHAEOLOGICAL_CULTURE_QID} .",
    "civilization": f"?item wdt:P31/wdt:P279* wd:{CIVILIZATION_QID} .",
    "historical_country": f"?item wdt:P31/wdt:P279* wd:{HISTORICAL_COUNTRY_QID} .",
    "country": (
        f"?item wdt:P31/wdt:P279* wd:{COUNTRY_QID} . "
        f"FILTER NOT EXISTS {{ ?item wdt:P31/wdt:P279* wd:{GREAT_HOMELAND_QID} . }}"
    ),
    "historical_region": (
        f"?item wdt:P31/wdt:P279* wd:{HISTORICAL_REGION_QID} ; wikibase:sitelinks ?sitelinks . "
        f"FILTER(?sitelinks >= {NOTABLE_SITELINKS})"
    ),
    "ethnic_group": (
        f"?item wdt:P31/wdt:P279* wd:{ETHNIC_GROUP_QID} ; wikibase:sitelinks ?sitelinks . "
        f"FILTER(?sitelinks >= {NOTABLE_SITELINKS})"
    ),
    "cultural_region": f"?item wdt:P31/wdt:P279* wd:{CULTURAL_REGION_QID} .",
    "macro_region": (
        "{ SELECT ?item WHERE { hint:Query hint:optimizer 'None' . "
        f"VALUES ?memberClass {{ wd:{COUNTRY_QID} wd:{HISTORICAL_COUNTRY_QID} }} "
        "?member wdt:P31 ?memberClass ; wdt:P361|wdt:P706|wdt:P30 ?item . } "
        f"GROUP BY ?item HAVING (COUNT(DISTINCT ?member) >= {MACRO_REGION_MIN_MEMBERS}) }}"
    ),
    "time_period": " UNION ".join(
        f"{{ ?source wdt:P31/wdt:P279* wd:{qid} ; wdt:P2348 ?item . }}"
        for qid in (
            ARCHAEOLOGICAL_CULTURE_QID,
            CIVILIZATION_QID,
            HISTORICAL_COUNTRY_QID,
            COUNTRY_QID,
            CULTURAL_REGION_QID,
        )
    ),
}

# Claim properties that date, locate, or relate a cultural entity. P571/P576 = inception/dissolution,
# P580/P582 = start/end time (the ones archaeological cultures actually carry), P2348 = time period,
# P1365/P1366 = replaces/replaced by, P361 = part of, P625 = coordinate location.
CULTURAL_CLAIM_DATE_PIDS = ("P571", "P576", "P580", "P582")
CULTURAL_CLAIM_PIDS = (*CULTURAL_CLAIM_DATE_PIDS, "P2348", "P1365", "P1366", "P361", "P625")


CURATED_KIND = "curated"
QID_PATTERN = re.compile(r"Q[0-9]+")


def curated_entity_patterns(qids: Sequence[str]) -> dict[str, str]:
    """The bulk kind patterns plus, when `qids` is not empty, a `curated` kind selecting exactly those items."""
    invalid = [qid for qid in qids if not QID_PATTERN.fullmatch(qid)]
    if invalid:
        raise ValueError(f"entity additions must be Wikidata QIDs, got {invalid[:5]}")
    if len(set(qids)) != len(qids):
        raise ValueError("entity additions contain duplicate QIDs")
    if not qids:
        return CULTURAL_ENTITY_KIND_PATTERNS
    return {
        **CULTURAL_ENTITY_KIND_PATTERNS,
        CURATED_KIND: f"VALUES ?item {{ {' '.join(f'wd:{qid}' for qid in qids)} }}",
    }


def cultural_entity_query(kind: str, patterns: Mapping[str, str] = CULTURAL_ENTITY_KIND_PATTERNS) -> str:
    return f"""
SELECT DISTINCT ?item ?itemLabel ?glottocode WHERE {{
  {patterns[kind]}
  {"" if kind == "languoid" else "OPTIONAL { ?item wdt:P1394 ?glottocode . }"}
  SERVICE wikibase:label {{ bd:serviceParam wikibase:language "{LABEL_LANGUAGES}". }}
}}
"""


# Claims are queried per (kind, property) rather than in one wide row for the same
# cross-multiplication reason documented above INDIGENOUS_TO_QUERY, and to keep each request
# short enough for WDQS's timeout. Dates come from the full-value node, so the raw literal (BCE
# years such as "-2600-01-01T00:00:00Z" included) and its precision (century, millennium...) are
# kept; deprecated statements are dropped, every other rank is kept for Silver to arbitrate.
def cultural_entity_claim_query(
    kind: str, pid: str, patterns: Mapping[str, str] = CULTURAL_ENTITY_KIND_PATTERNS
) -> str:
    if pid in CULTURAL_CLAIM_DATE_PIDS:
        value_pattern = f"""
  ?item p:{pid} ?statement .
  ?statement psv:{pid} ?node ; wikibase:rank ?rank .
  ?node wikibase:timeValue ?value ; wikibase:timePrecision ?precision .
  FILTER(?rank != wikibase:DeprecatedRank)"""
    else:
        value_pattern = f"\n  ?item wdt:{pid} ?value ."
    return f"""
SELECT DISTINCT ?item ?value ?precision WHERE {{
  {patterns[kind]}{value_pattern}
}}
"""


@tenacity.retry(
    retry=tenacity.retry_if_exception_type((httpx.TransportError, httpx.HTTPStatusError, json.JSONDecodeError)),
    wait=tenacity.wait_exponential(multiplier=1, max=30),
    stop=tenacity.stop_after_attempt(8),
    reraise=True,
)
def run_query(
    query: str, variables: Sequence[str] = GENRE_TREE_QUERY_VARIABLES, timeout: float = 60.0
) -> list[dict[str, str | None]]:
    # WDQS (or a caching layer in front of it) has been observed serving a truncated,
    # non-JSON-parseable body under load; Cache-Control: no-cache keeps a retry after
    # such a truncation from being served the same cached bad response again.
    response = httpx.get(
        SPARQL_ENDPOINT,
        params={"query": query},
        headers={
            "User-Agent": USER_AGENT,
            "Accept": "application/sparql-results+json",
            "Cache-Control": "no-cache",
        },
        timeout=timeout,
    )
    response.raise_for_status()
    bindings = response.json()["results"]["bindings"]
    return [{key: binding.get(key, {}).get("value") for key in variables} for binding in bindings]


@tenacity.retry(
    retry=tenacity.retry_if_exception_type((httpx.TransportError, httpx.HTTPStatusError, json.JSONDecodeError)),
    wait=tenacity.wait_exponential(multiplier=1, max=30),
    stop=tenacity.stop_after_attempt(8),
    reraise=True,
)
def _get_entities(ids: Sequence[str], props: str, timeout: float, sitefilter: str | None) -> dict[str, dict]:
    params = {
        "action": "wbgetentities",
        "ids": "|".join(ids),
        "props": props,
        "languages": LABEL_LANGUAGES.replace(",", "|"),
        "format": "json",
    }
    if sitefilter is not None:
        params["sitefilter"] = sitefilter
    response = httpx.get(
        API_ENDPOINT,
        params=params,
        headers={"User-Agent": USER_AGENT},
        timeout=timeout,
    )
    response.raise_for_status()
    body = response.json()
    if "error" in body:
        raise ValueError(f"wbgetentities failed for {list(ids)}: {body['error']}")
    return body["entities"]


def _get_batched_entities(
    ids: Sequence[str], props: str, timeout: float, sitefilter: str | None = None
) -> dict[str, dict]:
    entities: dict[str, dict] = {}
    for start in range(0, len(ids), WBGETENTITIES_MAX_IDS):
        batch = ids[start : start + WBGETENTITIES_MAX_IDS]
        batch_entities = _get_entities(batch, props, timeout, sitefilter)
        absent = [qid for qid in batch if qid not in batch_entities]
        if absent:
            raise ValueError(f"wbgetentities returned no entity for {absent}")
        entities.update({qid: batch_entities[qid] for qid in batch})
    return entities


def _label(entity: dict) -> str | None:
    labels = entity.get("labels", {})
    return next((labels[lang]["value"] for lang in LABEL_LANGUAGES.split(",") if lang in labels), None)


def _claim_ids(entity: dict, pid: str) -> list[str]:
    """Truthy values, as SPARQL `wdt:` sees them: preferred-rank statements if any, else normal ones."""
    statements = [s for s in entity.get("claims", {}).get(pid, []) if "datavalue" in s["mainsnak"]]
    rank = "preferred" if any(s.get("rank") == "preferred" for s in statements) else "normal"
    return [s["mainsnak"]["datavalue"]["value"]["id"] for s in statements if s.get("rank", "normal") == rank]


@dataclass(frozen=True)
class EntityInfo:
    """A QID's resolved entity: for a merged item, the redirect target's data."""

    id: str
    label: str | None
    modified: str | None
    instance_of: list[str]
    subclass_of: list[str]
    has_indigenous_to: bool


def fetch_entities(ids: Sequence[str], timeout: float = 60.0) -> dict[str, EntityInfo | None]:
    """Maps each QID to its current entity: itself, its redirect target if merged, or None if deleted."""
    result: dict[str, EntityInfo | None] = {}
    for qid, entity in _get_batched_entities(ids, "info|labels|claims", timeout).items():
        if "missing" in entity:
            result[qid] = None
            continue
        result[qid] = EntityInfo(
            id=entity.get("redirects", {}).get("to", qid),
            label=_label(entity),
            modified=entity.get("modified"),
            instance_of=_claim_ids(entity, "P31"),
            subclass_of=_claim_ids(entity, "P279"),
            has_indigenous_to=bool(_claim_ids(entity, INDIGENOUS_TO_PID)),
        )
    return result


def fetch_labels(ids: Sequence[str], timeout: float = 60.0) -> dict[str, str | None]:
    return {qid: _label(entity) for qid, entity in _get_batched_entities(ids, "labels", timeout).items()}


@dataclass(frozen=True)
class Description:
    description: str | None
    wikipedia_title: str | None
    wikipedia_url: str | None


def fetch_descriptions(ids: Sequence[str], timeout: float = 60.0) -> dict[str, Description]:
    """English short description (CC0) and English Wikipedia article of each QID, all None for a deleted item."""
    result: dict[str, Description] = {}
    for qid, entity in _get_batched_entities(ids, "descriptions|sitelinks/urls", timeout, sitefilter="enwiki").items():
        sitelink = entity.get("sitelinks", {}).get("enwiki", {})
        result[qid] = Description(
            description=entity.get("descriptions", {}).get("en", {}).get("value"),
            wikipedia_title=sitelink.get("title"),
            wikipedia_url=sitelink.get("url"),
        )
    return result
