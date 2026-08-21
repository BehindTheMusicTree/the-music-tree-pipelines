import json

import httpx
import tenacity

SPARQL_ENDPOINT = "https://query.wikidata.org/sparql"
USER_AGENT = "the-music-tree-pipelines (https://github.com/BehindTheMusicTree/the-music-tree-pipelines)"

MUSIC_GENRE_QID = "Q188451"

# P31 = "instance of": identifies what an item is
# (e.g. a specific item is an instance of "music genre").
#
# P279 = "subclass of": links a class to a more general class
# (e.g. "heavy metal" is a subclass of "rock music").
#
# P361 = "part of": links an item or class to a broader whole
# (e.g. a musical style can be part of a broader musical movement).
#
# Every item classified as a P31 ("instance of") music genre is ingested.
# We use P31 rather than P279 chains because subclass paths from a genre
# do not reliably converge on Q188451 ("music genre") itself
# (e.g. "rock music" is a P279 subclass of "popular music", not "music genre").
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
