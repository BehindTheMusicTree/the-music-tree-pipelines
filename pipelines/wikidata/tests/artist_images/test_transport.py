import asyncio
from unittest.mock import AsyncMock, patch

import httpx
import pytest

from wikidata.artist_images.transport import BudgetExhausted, ProviderUnavailable, RequestBudget, request_json


def budget(attempts: int = 8, seconds: float = 60) -> RequestBudget:
    return RequestBudget(dict.fromkeys(("wdqs", "entities", "commons"), attempts), seconds)


async def call(client: httpx.AsyncClient, limits: RequestBudget, provider: str = "wdqs") -> dict:
    return await request_json(client, limits, provider, "GET", "https://example.test/", family_deadline=limits.deadline)


def test_attempts_include_retries_and_success_resets_circuit() -> None:
    limits = budget()
    responses = [httpx.Response(503), httpx.Response(200, json={"ok": True})]

    async def run() -> None:
        async with httpx.AsyncClient(transport=httpx.MockTransport(lambda _: responses.pop(0))) as client:
            with patch("wikidata.artist_images.transport.asyncio.sleep", new=AsyncMock()):
                assert await call(client, limits) == {"ok": True}
        assert limits.attempts["wdqs"] == 2
        assert limits.failures["wdqs"] == 0

    asyncio.run(run())


def test_throttle_honors_retry_after() -> None:
    limits = budget()
    responses = [httpx.Response(429, headers={"Retry-After": "3"}), httpx.Response(200, json={})]

    async def run() -> None:
        async with httpx.AsyncClient(transport=httpx.MockTransport(lambda _: responses.pop(0))) as client:
            with patch("wikidata.artist_images.transport.asyncio.sleep", new=AsyncMock()) as sleep:
                await call(client, limits)
                assert sleep.await_args_list[0].args == (3.0,)

    asyncio.run(run())


def test_permanent_client_error_is_not_retried() -> None:
    limits = budget()

    async def run() -> None:
        async with httpx.AsyncClient(transport=httpx.MockTransport(lambda _: httpx.Response(400))) as client:
            with pytest.raises(httpx.HTTPStatusError):
                await call(client, limits)
        assert limits.attempts["wdqs"] == 1

    asyncio.run(run())


def test_provider_circuit_stops_request_creation() -> None:
    limits = budget()

    async def run() -> None:
        async with httpx.AsyncClient(transport=httpx.MockTransport(lambda _: httpx.Response(503))) as client:
            with patch("wikidata.artist_images.transport.asyncio.sleep", new=AsyncMock()):
                with pytest.raises(ProviderUnavailable, match="retries exhausted"):
                    await call(client, limits)
                with pytest.raises(ProviderUnavailable, match="circuit open"):
                    await call(client, limits)
        assert limits.attempts["wdqs"] == 3

    asyncio.run(run())


def test_attempt_budget_is_shared_across_calls() -> None:
    limits = budget(attempts=1)

    async def run() -> None:
        async with httpx.AsyncClient(transport=httpx.MockTransport(lambda _: httpx.Response(200, json={}))) as client:
            await call(client, limits)
            with pytest.raises(BudgetExhausted, match="attempt budget"):
                await call(client, limits)

    asyncio.run(run())


def test_wall_deadline_cancels_in_flight_request() -> None:
    limits = budget(seconds=0.01)
    cancelled = []

    async def handler(request: httpx.Request) -> httpx.Response:
        try:
            await asyncio.sleep(10)
        finally:
            cancelled.append(True)
        return httpx.Response(200, json={})

    async def run() -> None:
        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            with pytest.raises(BudgetExhausted):
                await call(client, limits)

    asyncio.run(run())
    assert cancelled == [True]


def test_catalogue_contention_defers_without_http_attempt(tmp_path) -> None:
    from wikidata.artist_images.locks import LockDeferred, exclusive_lock

    path = tmp_path / "catalogue.lock"
    path.touch()
    limits = RequestBudget({"wdqs": 8, "entities": 8, "commons": 8}, 60, catalogue_lock=path)
    calls = []

    async def run() -> None:
        async with httpx.AsyncClient(transport=httpx.MockTransport(lambda request: calls.append(request))) as client:
            with exclusive_lock(path):
                with pytest.raises(LockDeferred):
                    await call(client, limits)
        assert not calls
        assert limits.attempts["wdqs"] == 0

    asyncio.run(run())
