import json

import httpx
import tenacity

SPARQL_ENDPOINT = "https://query.wikidata.org/sparql"
USER_AGENT = "the-music-tree-pipelines (https://github.com/BehindTheMusicTree/the-music-tree-pipelines)"

MUSIC_GENRE_QID = "Q188451"

# Every item classified P31 "instance of" music genre (the class extension — P279 chains from a
# genre don't reliably converge back to Q188451 itself, e.g. "rock music" P279 "popular music",
# not "music genre"), plus each genre's direct P279 "subclass of" and P361 "part of" parent(s),
# tagged by ?relation so the two edge types stay distinguishable downstream. Parents are not
# restricted to also being a music genre instance — Wikidata's own P279/P361 edges for a genre
# routinely point at non-genre classes too (e.g. "opera" P279 "composed musical work"), and
# Bronze ingests that raw, unfiltered; pruning to genre-only parents is Silver-layer work. An
# item with neither a P279 nor a P361 parent still gets exactly one row, with ?parent/?relation
# unbound, preserving the pre-P361 "root item" row shape.
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
