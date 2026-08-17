import httpx

SPARQL_ENDPOINT = "https://query.wikidata.org/sparql"
USER_AGENT = "the-music-tree-pipelines (https://github.com/BehindTheMusicTree/the-music-tree-pipelines)"

MUSIC_GENRE_QID = "Q188451"

# Every item classified P31 "instance of" music genre (the class extension — P279 chains from a
# genre don't reliably converge back to Q188451 itself, e.g. "rock music" P279 "popular music",
# not "music genre"), plus each genre's direct P279 "subclass of" parent(s). Parents are not
# restricted to also being a music genre instance — Wikidata's own P279 edges for a genre
# routinely point at non-genre classes too (e.g. "opera" P279 "composed musical work"), and
# Bronze ingests that raw, unfiltered; pruning to genre-only parents is Silver-layer work.
GENRE_TREE_QUERY = f"""
SELECT ?item ?itemLabel ?parent ?parentLabel WHERE {{
  ?item wdt:P31 wd:{MUSIC_GENRE_QID}.
  OPTIONAL {{ ?item wdt:P279 ?parent. }}
  SERVICE wikibase:label {{ bd:serviceParam wikibase:language "en". }}
}}
"""


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
        {key: binding.get(key, {}).get("value") for key in ("item", "itemLabel", "parent", "parentLabel")}
        for binding in bindings
    ]
