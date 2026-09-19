"""Redis helper unit tests: degradation, reconnect, Pub/Sub, and cleanup."""

import asyncio
import sys
from types import SimpleNamespace

import pytest

from app import cache


@pytest.fixture(autouse=True)
def reset_cache_state(monkeypatch):
    monkeypatch.setattr(cache.config, "REDIS_URL", "redis://user:secret@redis:6379/0")
    monkeypatch.setattr(cache.config, "REDIS_CONNECT_TIMEOUT", 0.1)
    monkeypatch.setattr(cache.config, "REDIS_SOCKET_TIMEOUT", 0.1)
    monkeypatch.setattr(cache, "_client", None)
    monkeypatch.setattr(cache, "_sync_next_retry", 0.0)
    monkeypatch.setattr(cache, "_sync_backoff", cache._RETRY_INITIAL_SECONDS)
    monkeypatch.setattr(cache, "_sync_ever_connected", False)
    monkeypatch.setattr(cache, "_sync_unavailable_logged", False)
    monkeypatch.setattr(cache, "_async_redis", None)
    monkeypatch.setattr(cache, "_async_lock", None)
    monkeypatch.setattr(cache, "_async_next_retry", 0.0)
    monkeypatch.setattr(cache, "_async_backoff", cache._RETRY_INITIAL_SECONDS)
    monkeypatch.setattr(cache, "_async_ever_connected", False)
    monkeypatch.setattr(cache, "_async_unavailable_logged", False)


class SyncClient:
    def __init__(self, *, ping_error=None, get_error=None):
        self.ping_error = ping_error
        self.get_error = get_error
        self.closed = False
        self.values = {}

    def ping(self):
        if self.ping_error:
            raise self.ping_error
        return True

    def get(self, key):
        if self.get_error:
            raise self.get_error
        return self.values.get(key)

    def set(self, key, value, px):
        self.values[key] = value
        self.last_px = px

    def delete(self, key):
        self.values.pop(key, None)

    def publish(self, channel, data):
        self.published = (channel, data)

    def close(self):
        self.closed = True


def _install_sync_factory(monkeypatch, clients):
    calls = []

    def from_url(url, **kwargs):
        calls.append((url, kwargs))
        return clients.pop(0)

    fake = SimpleNamespace(Redis=SimpleNamespace(from_url=from_url))
    monkeypatch.setitem(sys.modules, "redis", fake)
    return calls


def test_transient_connect_failure_backs_off_then_reconnects(monkeypatch):
    now = [100.0]
    monkeypatch.setattr(cache.time, "monotonic", lambda: now[0])
    good = SyncClient()
    calls = _install_sync_factory(
        monkeypatch, [SyncClient(ping_error=ConnectionError("down")), good]
    )

    assert cache.get_bytes("k") is None
    assert len(calls) == 1
    assert cache.get_bytes("k") is None  # shared backoff: no retry storm
    assert len(calls) == 1

    now[0] += cache._RETRY_INITIAL_SECONDS
    good.values["k"] = b"value"
    assert cache.get_bytes("k") == b"value"
    assert len(calls) == 2
    assert calls[0][1]["socket_connect_timeout"] == 0.1


def test_operation_failure_discards_stale_client(monkeypatch):
    broken = SyncClient(get_error=ConnectionError("restart"))
    monkeypatch.setattr(cache, "_client", broken)
    assert cache.get_bytes("k") is None
    assert broken.closed is True
    assert cache._client is None
    assert cache._sync_next_retry > 0


def test_set_requires_ttl_and_publish_failure_is_best_effort(monkeypatch):
    with pytest.raises(ValueError):
        cache.set_bytes("permanent", b"x", 0)

    class BrokenPublisher(SyncClient):
        def publish(self, channel, data):
            raise ConnectionError("down")

    broken = BrokenPublisher()
    monkeypatch.setattr(cache, "_client", broken)
    cache.publish_event("ws:one:events", b"event")
    assert broken.closed is True
    assert cache._client is None


def test_log_endpoint_redacts_userinfo():
    endpoint = cache._safe_endpoint()
    assert endpoint == "redis://redis:6379/0"
    assert "secret" not in endpoint
    assert "user" not in endpoint


class PubSub:
    def __init__(self, messages):
        self.messages = list(messages)
        self.unsubscribed = False
        self.closed = False

    async def subscribe(self, channel):
        self.channel = channel

    async def get_message(self, **kwargs):
        value = self.messages.pop(0)
        if isinstance(value, Exception):
            raise value
        return value

    async def unsubscribe(self, channel):
        self.unsubscribed = True

    async def aclose(self):
        self.closed = True


class AsyncClient:
    def __init__(self, pubsub, ping_error=None):
        self._pubsub = pubsub
        self.ping_error = ping_error
        self.closed = False

    async def ping(self):
        if self.ping_error:
            raise self.ping_error
        return True

    def pubsub(self):
        return self._pubsub

    async def aclose(self):
        self.closed = True


def test_subscribe_yields_message_and_idle_tick_and_cleans_up(monkeypatch):
    pubsub = PubSub([
        None,
        {"type": "message", "data": b'{"id":"event-1"}'},
    ])
    client = AsyncClient(pubsub)

    async def fake_client():
        return client

    monkeypatch.setattr(cache, "_lazy_async_client", fake_client)

    async def run():
        stream = cache.subscribe_events("ws:one:events")
        assert await anext(stream) is None
        assert await anext(stream) == b'{"id":"event-1"}'
        await stream.aclose()

    asyncio.run(run())
    assert pubsub.unsubscribed is True
    assert pubsub.closed is True


def test_subscribe_connection_error_discards_client_and_closes_pubsub(monkeypatch):
    pubsub = PubSub([ConnectionError("redis restarted")])
    client = AsyncClient(pubsub)
    monkeypatch.setattr(cache, "_async_redis", client)

    async def fake_client():
        return client

    monkeypatch.setattr(cache, "_lazy_async_client", fake_client)

    async def run():
        assert [item async for item in cache.subscribe_events("ws:one:events")] == []

    asyncio.run(run())
    assert client.closed is True
    assert pubsub.unsubscribed is True
    assert pubsub.closed is True
    assert cache._async_redis is None


def test_async_connect_failure_backs_off_then_reconnects(monkeypatch):
    import redis.asyncio as aioredis

    now = [200.0]
    monkeypatch.setattr(cache.time, "monotonic", lambda: now[0])
    clients = [
        AsyncClient(PubSub([]), ping_error=ConnectionError("down")),
        AsyncClient(PubSub([])),
    ]
    calls = []

    def from_url(url, **kwargs):
        calls.append((url, kwargs))
        return clients.pop(0)

    monkeypatch.setattr(aioredis, "from_url", from_url)

    async def run():
        assert await cache._lazy_async_client() is None
        assert await cache._lazy_async_client() is None
        now[0] += cache._RETRY_INITIAL_SECONDS
        assert await cache._lazy_async_client() is not None
        await cache.close_redis()

    asyncio.run(run())
    assert len(calls) == 2


def test_shutdown_closes_sync_and_async_clients(monkeypatch):
    sync_client = SyncClient()
    async_client = AsyncClient(PubSub([]))
    monkeypatch.setattr(cache, "_client", sync_client)
    monkeypatch.setattr(cache, "_async_redis", async_client)
    asyncio.run(cache.close_redis())
    assert sync_client.closed is True
    assert async_client.closed is True
    assert cache._client is None
    assert cache._async_redis is None


def test_integration_display_name_cache_keys_are_binding_scoped(monkeypatch):
    from app.services import integrations

    requested = []
    monkeypatch.setattr(cache, "get_bytes", lambda key: requested.append(key) or b"Cached")

    assert integrations.slack_user_display_name("token", "U1", "binding-a") == "Cached"
    assert integrations.lark_user_display_name(SimpleNamespace(id="binding-b"), "OU1") == "Cached"
    assert requested == [
        "integr:slackuser:binding-a:U1",
        "integr:larkuser:binding-b:OU1",
    ]
