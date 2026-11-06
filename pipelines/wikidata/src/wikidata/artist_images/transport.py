"""Run-wide attempt budgets and cancellable provider request deadlines."""

import asyncio
import json
import random
import time
from contextlib import nullcontext
from datetime import UTC, datetime
from email.utils import parsedate_to_datetime
from pathlib import Path

import httpx

from wikidata.wikidata_client import USER_AGENT
from wikidata.artist_images.locks import exclusive_lock


class BudgetExhausted(RuntimeError):
    """No further request or wait can fit the configured run budget."""


class ProviderUnavailable(RuntimeError):
    """Exhausted provider retries suppress publication for the run."""


class QueryTimeout(ProviderUnavailable):
    """A bounded WDQS query family may split after exhausting one retry."""


class ProviderCircuitOpen(ProviderUnavailable):
    """A run has stopped issuing requests to this provider."""


class RequestBudget:
    def __init__(self, attempts: dict[str, int], seconds: float, *, catalogue_lock: Path | None = None):
        if set(attempts) != {"wdqs", "entities", "commons"}:
            raise ValueError("Explicit budgets required for every image provider")
        if any(type(value) is not int or not 1 <= value <= 10000 for value in attempts.values()):
            raise ValueError("Provider attempt budgets must be positive bounded integers")
        if not 0 < seconds <= 2700:
            raise ValueError("Image run budget must be at most 2700 seconds")
        self.limits = dict(attempts)
        self.attempts = dict.fromkeys(attempts, 0)
        self.failures = dict.fromkeys(attempts, 0)
        self.deadline = time.monotonic() + seconds
        self.catalogue_lock = catalogue_lock
        self.work: dict[str, dict[str, set[str]]] = {
            kind: {result: set() for result in ("attempted", "succeeded", "failed")} for kind in ("claims", "metadata")
        }
        self.next_wikidata_attempt = 0.0
        self.metrics = {
            provider: {"retries": 0, "throttles": 0, "timeouts": 0, "bytes": 0, "seconds": 0.0, "validationFailures": 0}
            for provider in attempts
        }

    def remaining(self, family_deadline: float) -> float:
        remaining = min(self.deadline, family_deadline) - time.monotonic()
        if remaining <= 0:
            raise BudgetExhausted("Image request deadline exhausted")
        return remaining

    def reserve(self, provider: str, family_deadline: float) -> float:
        remaining = self.remaining(family_deadline)
        if self.failures[provider] >= 3:
            raise ProviderCircuitOpen(f"{provider} circuit open after three consecutive failures")
        if self.attempts[provider] >= self.limits[provider]:
            raise BudgetExhausted(f"{provider} HTTP attempt budget exhausted")
        self.attempts[provider] += 1
        return remaining


def retry_after_seconds(value: str | None) -> float:
    if value is None:
        return 1.0
    try:
        if value.strip().isdigit():
            return float(value)
        timestamp = parsedate_to_datetime(value)
        if timestamp.tzinfo is None:
            raise ValueError("Retry-After date has no timezone")
        return max(0.0, (timestamp - datetime.now(UTC)).total_seconds())
    except (ValueError, TypeError, OverflowError) as exc:
        raise ProviderUnavailable("Malformed provider Retry-After") from exc


async def request_json(
    client: httpx.AsyncClient,
    budget: RequestBudget,
    provider: str,
    method: str,
    url: str,
    *,
    family_deadline: float,
    params: dict | None = None,
    data: dict | None = None,
) -> dict:
    """At most two attempts, charged before I/O, sharing family and run deadlines."""
    last_error: Exception | None = None
    timed_out = False
    for attempt in range(2):
        delay = random.uniform(1.0, 2.0)
        wikidata_request = provider in ("wdqs", "entities")
        if wikidata_request:
            pause = budget.next_wikidata_attempt - time.monotonic()
            if pause > 0:
                if pause >= budget.remaining(family_deadline):
                    raise BudgetExhausted("Wikidata pacing exceeds remaining deadline")
                await asyncio.sleep(pause)
        request_started = None
        try:
            guard = (
                exclusive_lock(budget.catalogue_lock, existing=True, shared=True)
                if wikidata_request and budget.catalogue_lock
                else nullcontext()
            )
            with guard:
                remaining = budget.reserve(provider, family_deadline)
                request_started = time.monotonic()
                if attempt:
                    budget.metrics[provider]["retries"] += 1
                async with asyncio.timeout(min(30.0, remaining)):
                    response = await client.request(
                        method,
                        url,
                        params=params,
                        data=data,
                        headers={"User-Agent": USER_AGENT, "Accept": "application/json", "Cache-Control": "no-cache"},
                        timeout=min(30.0, remaining),
                    )
            budget.metrics[provider]["bytes"] += len(response.content)
            if response.status_code == 429:
                budget.metrics[provider]["throttles"] += 1
                delay = retry_after_seconds(response.headers.get("Retry-After"))
                raise ProviderUnavailable(f"{provider} throttled")
            response.raise_for_status()
            try:
                body = response.json()
            except json.JSONDecodeError as exc:
                budget.metrics[provider]["validationFailures"] += 1
                raise ValueError(f"{provider} returned invalid JSON") from exc
            if not isinstance(body, dict):
                budget.metrics[provider]["validationFailures"] += 1
                raise ValueError(f"{provider} returned a non-object JSON response")
            budget.failures[provider] = 0
            return body
        except ProviderCircuitOpen:
            raise
        except httpx.HTTPStatusError as exc:
            if exc.response.status_code < 500:
                raise
            last_error = exc
            timed_out = False
        except (TimeoutError, httpx.TimeoutException, QueryTimeout) as exc:
            last_error = exc
            timed_out = True
            budget.metrics[provider]["timeouts"] += 1
        except (httpx.TransportError, ProviderUnavailable) as exc:
            last_error = exc
            timed_out = False
        finally:
            if request_started is not None:
                elapsed = time.monotonic() - request_started
                budget.metrics[provider]["seconds"] += elapsed
                if wikidata_request:
                    budget.next_wikidata_attempt = time.monotonic() + elapsed
        budget.failures[provider] += 1
        if attempt == 0:
            if delay >= budget.remaining(family_deadline):
                raise BudgetExhausted(f"{provider} retry delay exceeds remaining deadline") from last_error
            await asyncio.sleep(delay)
    error = QueryTimeout if timed_out and provider == "wdqs" else ProviderUnavailable
    raise error(f"{provider} retries exhausted") from last_error
