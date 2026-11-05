"""Complete Commons metadata batches with explicit negative observations."""

import time
from datetime import datetime
from pathlib import Path

import httpx

from wikidata.artist_images.transport import BudgetExhausted, RequestBudget, request_json
from wikidata.artist_images.locks import LockDeferred
from wikidata.artist_images.state import ImageState
from wikidata.silver.commons_image_metadata import COMMONS_API_ENDPOINT, THUMB_WIDTH, metadata_row


async def acquire_metadata(client: httpx.AsyncClient, budget: RequestBudget, files: list[str]) -> list[dict]:
    if not files or len(files) > 50 or len(set(files)) != len(files):
        raise ValueError("Commons requires 1–50 distinct filenames")
    if any(not name or "|" in name or any(ord(char) < 32 for char in name) for name in files):
        raise ValueError("Invalid Commons filename")
    deadline = min(budget.deadline, time.monotonic() + 90)
    base = {
        "action": "query",
        "prop": "imageinfo",
        "iiprop": "url|extmetadata",
        "iiurlwidth": THUMB_WIDTH,
        "iiextmetadatafilter": "Artist|Credit|LicenseShortName|LicenseUrl|AttributionRequired|NonFree",
        "redirects": 1,
        "titles": "|".join(f"File:{name}" for name in files),
        "format": "json",
        "formatversion": 2,
    }
    renamed, pages, continuation, seen = {}, {}, {}, set()
    while True:
        body = await request_json(
            client,
            budget,
            "commons",
            "POST",
            COMMONS_API_ENDPOINT,
            family_deadline=deadline,
            data={**base, **continuation},
        )
        query = body.get("query")
        if "error" in body or not isinstance(query, dict) or not isinstance(query.get("pages"), list):
            raise ValueError("Incomplete Commons query response")
        for kind in ("normalized", "redirects"):
            changes = query.get(kind, [])
            if not isinstance(changes, list):
                raise ValueError("Invalid Commons title mapping")
            for change in changes:
                if not isinstance(change, dict) or not all(isinstance(change.get(key), str) for key in ("from", "to")):
                    raise ValueError("Invalid Commons title mapping")
                if change["from"] in renamed and renamed[change["from"]] != change["to"]:
                    raise ValueError("Conflicting Commons title mapping")
                renamed[change["from"]] = change["to"]
        for page in query["pages"]:
            if not isinstance(page, dict) or not isinstance(page.get("title"), str):
                raise ValueError("Invalid Commons page")
            if not pages.get(page["title"], {}).get("imageinfo"):
                pages[page["title"]] = page
        if "continue" not in body:
            break
        continuation = body["continue"]
        if (
            not isinstance(continuation, dict)
            or not continuation
            or not set(continuation) <= {"continue", "iicontinue"}
        ):
            raise ValueError("Unsupported Commons continuation")
        token = repr(sorted(continuation.items()))
        if token in seen:
            raise ValueError("Repeated Commons continuation")
        seen.add(token)
    rows = []
    for name in files:
        title, visited = f"File:{name}", set()
        while title in renamed:
            if title in visited:
                raise ValueError("Commons redirect cycle")
            visited.add(title)
            title = renamed[title]
        page = pages.get(title)
        if page is None:
            raise ValueError("Commons omitted a requested file")
        missing = "missing" in page
        if not missing and (not isinstance(page.get("imageinfo"), list) or not page["imageinfo"]):
            raise ValueError("Commons page has no metadata and is not confirmed missing")
        if not missing:
            info = page["imageinfo"][0]
            if (
                not isinstance(info, dict)
                or "extmetadata" not in info
                or not isinstance(info["extmetadata"], (dict, list))
            ):
                raise ValueError("Malformed Commons metadata")
            extended = info["extmetadata"]
            if isinstance(extended, list) and extended:
                raise ValueError("Malformed Commons metadata list")
            if isinstance(extended, dict):
                for value in extended.values():
                    if not isinstance(value, dict) or not isinstance(value.get("value"), str):
                        raise ValueError("Malformed Commons metadata value")
        try:
            row = metadata_row(name, None if missing else page)
        except (AttributeError, IndexError, KeyError, TypeError) as exc:
            raise ValueError("Unparseable Commons metadata") from exc
        row["result_status"] = "missing" if missing else "observed"
        row["canonical_title"] = title
        rows.append(row)
    return rows


def due_files(state: ImageState, now: datetime, positive_days: int = 30, negative_days: int = 7):
    import polars as pl
    from datetime import timedelta

    referenced = state.tables["claims"].select("image_file").unique()
    return referenced.join(
        state.tables["commons"].select("image_file", "checked_at", "result_status", "retry_after_at"),
        on="image_file",
        how="left",
    ).filter(
        pl.col("checked_at").is_null()
        | (
            pl.col("checked_at")
            <= pl.lit(now)
            - pl.when(pl.col("result_status") == "missing")
            .then(pl.lit(timedelta(days=negative_days)))
            .otherwise(pl.lit(timedelta(days=positive_days)))
        )
    )


async def refresh_metadata_queue(
    root: Path,
    state: ImageState,
    client: httpx.AsyncClient,
    budget: RequestBudget,
    now: datetime,
    *,
    positive_days: int = 30,
    negative_days: int = 7,
) -> ImageState:
    """Refresh shared files once; preserve valid observations and defer failures."""
    import polars as pl

    from wikidata.artist_images.state import checkpoint_state, load_state
    from wikidata.artist_images.progress import defer_failed

    if now.tzinfo is None or min(positive_days, negative_days) <= 0:
        raise ValueError("Metadata refresh requires aware time and positive TTLs")
    attempted = set()
    while True:
        cached = state.tables["commons"]
        due = due_files(state, now, positive_days, negative_days).filter(
            ~pl.col("image_file").is_in(sorted(attempted)),
            pl.col("retry_after_at").is_null() | (pl.col("retry_after_at") <= now),
        )
        old = due.filter(pl.col("checked_at").is_not_null()).sort("checked_at", "image_file")["image_file"].to_list()
        new = due.filter(pl.col("checked_at").is_null()).sort("image_file")["image_file"].to_list()
        selected = old[:25]
        selected.extend(new[: 50 - len(selected)])
        selected.extend(old[25 : 25 + 50 - len(selected)])
        if not selected:
            return state
        attempted.update(selected)
        budget.work["metadata"]["attempted"].update(selected)
        try:
            rows = await acquire_metadata(client, budget, selected)
            fetched = pl.DataFrame(
                [
                    {
                        **row,
                        "checked_at": now,
                        "last_attempt_at": now,
                        "consecutive_failures": 0,
                        "retry_after_at": None,
                    }
                    for row in rows
                ],
                schema=cached.schema,
            )
            state = checkpoint_state(root, state, {"commons": fetched}, {"commons": selected})
            budget.work["metadata"]["succeeded"].update(selected)
        except (BudgetExhausted, LockDeferred):
            raise
        except Exception:
            budget.work["metadata"]["failed"].update(selected)
            defer_failed(root, load_state(root), "commons", set(selected), now)
            raise
