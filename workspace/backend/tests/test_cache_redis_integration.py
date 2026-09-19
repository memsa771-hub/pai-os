"""Opt-in tests against a real Redis instance.

Run with REDIS_INTEGRATION_URL=redis://127.0.0.1:<port>/0. The normal unit
suite skips these so developers are not required to run Redis manually.
"""

import asyncio
import os
import time

import pytest

from app import cache


pytestmark = pytest.mark.skipif(
    not os.environ.get("REDIS_INTEGRATION_URL"),
    reason="set REDIS_INTEGRATION_URL to run real Redis integration tests",
)


@pytest.fixture(autouse=True)
def real_redis(monkeypatch):
    monkeypatch.setattr(cache.config, "REDIS_URL", os.environ.get("REDIS_INTEGRATION_URL", ""))
    monkeypatch.setattr(cache, "_client", None)
    monkeypatch.setattr(cache, "_sync_next_retry", 0.0)
    monkeypatch.setattr(cache, "_async_redis", None)
    monkeypatch.setattr(cache, "_async_lock", None)
    monkeypatch.setattr(cache, "_async_next_retry", 0.0)
    yield
    asyncio.run(cache.close_redis())


def test_set_get_ttl_delete_and_connection_recovery():
    key = "test:placement-ai:ttl"
    cache.set_bytes(key, b"value", ttl_seconds=2.0)
    assert cache.get_bytes(key) == b"value"
    assert 0 < cache._client.pttl(key) <= 2000

    # Drop every pooled socket. redis-py and the helper must recover without a
    # backend process restart.
    cache._client.connection_pool.disconnect()
    assert cache.get_bytes(key) == b"value"

    cache.delete_key(key)
    assert cache.get_bytes(key) is None


def test_real_pubsub_message_idle_tick_and_cleanup():
    async def run():
        channel = f"test:placement-ai:events:{time.time_ns()}"
        stream = cache.subscribe_events(channel)
        first = asyncio.create_task(anext(stream))
        await asyncio.sleep(0.1)

        import redis.asyncio as aioredis
        publisher = aioredis.from_url(cache.config.REDIS_URL)
        try:
            await publisher.publish(channel, b"event")
        finally:
            await publisher.aclose()

        # Depending on timing the first item may be an idle tick; the message
        # must follow and the generator must be explicitly closeable.
        item = await asyncio.wait_for(first, timeout=2)
        if item is None:
            item = await asyncio.wait_for(anext(stream), timeout=2)
        assert item == b"event"
        await stream.aclose()

    asyncio.run(run())
