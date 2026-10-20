import json
from collections.abc import Sequence

import httpx
import tenacity

from wikidata.wikidata_client import USER_AGENT

API_ENDPOINT = "https://en.wikipedia.org/w/api.php"
EXTRACTS_MAX_TITLES = 20


@tenacity.retry(
    retry=tenacity.retry_if_exception_type((httpx.TransportError, httpx.HTTPStatusError, json.JSONDecodeError)),
    wait=tenacity.wait_exponential(multiplier=1, max=30),
    stop=tenacity.stop_after_attempt(8),
    reraise=True,
)
def _get_extracts_page(params: dict, timeout: float) -> dict:
    response = httpx.get(API_ENDPOINT, params=params, headers={"User-Agent": USER_AGENT}, timeout=timeout)
    response.raise_for_status()
    body = response.json()
    if "error" in body:
        raise ValueError(f"Wikipedia extracts query failed for {params['titles']}: {body['error']}")
    return body


def _get_extracts(titles: Sequence[str], timeout: float) -> dict:
    """Follows `continue` until done: one response carries only as many extracts as fit, the rest come empty."""
    base_params = {
        "action": "query",
        "prop": "extracts",
        "exintro": 1,
        "explaintext": 1,
        "exlimit": "max",
        "redirects": 1,
        "titles": "|".join(titles),
        "format": "json",
        "formatversion": 2,
    }
    renames: dict[str, list] = {"normalized": [], "redirects": []}
    pages: dict[str, dict] = {}
    continue_params: dict = {}
    while True:
        body = _get_extracts_page({**base_params, **continue_params}, timeout)
        for key in renames:
            renames[key] = body["query"].get(key, renames[key])
        for page in body["query"]["pages"]:
            if page.get("extract") or page["title"] not in pages:
                pages[page["title"]] = page
        if "continue" not in body:
            return {**renames, "pages": list(pages.values())}
        continue_params = body["continue"]


def fetch_intros(titles: Sequence[str], timeout: float = 60.0) -> dict[str, str | None]:
    """Plain-text intro of each English Wikipedia article, None for a missing page or an empty intro."""
    unique_titles = list(dict.fromkeys(titles))
    intros: dict[str, str | None] = {}
    for start in range(0, len(unique_titles), EXTRACTS_MAX_TITLES):
        batch = unique_titles[start : start + EXTRACTS_MAX_TITLES]
        query = _get_extracts(batch, timeout)
        renamed = {change["from"]: change["to"] for change in query.get("normalized", []) + query.get("redirects", [])}
        extract_by_title = {page["title"]: (page.get("extract") or "").strip() or None for page in query["pages"]}
        for title in batch:
            resolved = title
            # Normalization maps the requested title to its canonical form, then a redirect maps that on.
            for _ in range(2):
                resolved = renamed.get(resolved, resolved)
            intros[title] = extract_by_title.get(resolved)
    return intros
