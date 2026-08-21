import json

import httpx
import tenacity

SPARQL_ENDPOINT = "https://query.wikidata.org/sparql"
USER_AGENT = "the-music-tree-pipelines (https://github.com/BehindTheMusicTree/the-music-tree-pipelines)"

MUSIC_GENRE_QID = "Q188451"

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
# a P279 walk to find the genre set (a P279* walk from "music genre" itself finds
# only ~14 meta-category items like "rock genre", not real genres — see SCHEMA.md).
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
  SERVICE wikibase:label {{ bd:serviceParam wikibase:language "en". }}
}}
"""


@tenacity.retry(
    retry=tenacity.retry_if_exception_type((httpx.TransportError, httpx.HTTPStatusError, json.JSONDecodeError)),
    wait=tenacity.wait_exponential(multiplier=1, max=10),
    stop=tenacity.stop_after_attempt(3),
    reraise=True,
)
def run_query(query: str, timeout: float = 60.0) -> list[dict[str, str | None]]:
    response = httpx.get(
        SPARQL_ENDPOINT,
        params={"query": query},
        headers={"User-Agent": USER_AGENT, "Accept": "application/sparql-results+json"},
        timeout=timeout,
    )
    response.raise_for_status()
    bindings = response.json()["results"]["bindings"]
    return [
        {key: binding.get(key, {}).get("value") for key in ("item", "itemLabel", "parent", "parentLabel", "relation")}
        for binding in bindings
    ]
