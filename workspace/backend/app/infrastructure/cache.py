# -*- coding: utf-8 -*-
"""Best-effort Redis cache and Pub/Sub helpers.

Redis stores only disposable, reconstructible data: bounded-TTL caches,
composing indicators, webhook dedupe markers, and Pub/Sub messages. PostgreSQL
remains authoritative. Every failure therefore degrades to a cache miss or a
closed SSE stream, never an authorization success.
"""

import asyncio
import json
import logging
import threading
import time
from typing import Any, AsyncGenerator, Callable, Optional

from app.config import config

logger = logging.getLogger(__name__)

_RETRY_INITIAL_SECONDS = 0.25
_RETRY_MAX_SECONDS = 5.0

_client = None
_sync_lock = threading.Lock()
_sync_next_retry = 0.0
_sync_backoff = _RETRY_INITIAL_SECONDS
_sync_ever_connected = False
_sync_unavailable_logged = False

_async_redis = None
_async_lock = None
_async_next_retry = 0.0
_async_backoff = _RETRY_INITIAL_SECONDS
_async_ever_connected = False
_async_unavailable_logged = False


def _safe_endpoint() -> str:
    """Return a credential-free endpoint suitable for logs."""
    if not config.REDIS_URL:
        return "disabled"
    try:
        from urllib.parse import urlsplit

        parsed = urlsplit(config.REDIS_URL)
        host = parsed.hostname or "unknown"
        port = f":{parsed.port}" if parsed.port else ""
        return f"{parsed.scheme}://{host}{port}{parsed.path or ''}"
    except Exception:
        return "configured endpoint"


def _close_sync_client(client) -> None:
    if client is None:
        return
    try:
        client.close()
    except Exception:
        try:
            client.connection_pool.disconnect()
        except Exception:
            pass


def _sync_failed(exc: Exception) -> None:
    """Discard a broken sync client and schedule a bounded reconnect."""
    global _client, _sync_next_retry, _sync_backoff, _sync_unavailable_logged
    with _sync_lock:
        broken, _client = _client, None
        _close_sync_client(broken)
        _sync_next_retry = time.monotonic() + _sync_backoff
        _sync_backoff = min(_sync_backoff * 2, _RETRY_MAX_SECONDS)
        if not _sync_unavailable_logged:
            logger.warning("Redis temporarily unavailable at %s: %s", _safe_endpoint(), exc)
            _sync_unavailable_logged = True


def _lazy_client():
    """Return a healthy sync client, retrying after bounded shared backoff."""
    global _client, _sync_next_retry, _sync_backoff
    global _sync_ever_connected, _sync_unavailable_logged
    if not config.REDIS_URL:
        return None
    if _client is not None:
        return _client
    if time.monotonic() < _sync_next_retry:
        return None

    with _sync_lock:
        if _client is not None:
            return _client
        if time.monotonic() < _sync_next_retry:
            return None
        candidate = None
        try:
            import redis

            candidate = redis.Redis.from_url(
                config.REDIS_URL,
                socket_timeout=config.REDIS_SOCKET_TIMEOUT,
                socket_connect_timeout=config.REDIS_CONNECT_TIMEOUT,
                retry_on_timeout=False,
                decode_responses=False,
                health_check_interval=30,
            )
            candidate.ping()
            _client = candidate
            _sync_next_retry = 0.0
            _sync_backoff = _RETRY_INITIAL_SECONDS
            state = "reconnected" if _sync_ever_connected else "connected"
            logger.info("Redis cache %s to %s", state, _safe_endpoint())
            _sync_ever_connected = True
            _sync_unavailable_logged = False
            return _client
        except Exception as exc:
            _close_sync_client(candidate)
            _sync_next_retry = time.monotonic() + _sync_backoff
            _sync_backoff = min(_sync_backoff * 2, _RETRY_MAX_SECONDS)
            if not _sync_unavailable_logged:
                logger.warning("Redis temporarily unavailable at %s: %s", _safe_endpoint(), exc)
                _sync_unavailable_logged = True
            return None


def get_bytes(key: str) -> Optional[bytes]:
    """Return cached bytes, or ``None`` on miss/unavailable Redis."""
    client = _lazy_client()
    if client is None:
        return None
    try:
        return client.get(key)
    except Exception as exc:
        _sync_failed(exc)
        return None


def set_bytes(key: str, value: bytes, ttl_seconds: float) -> None:
    """Store bytes with a mandatory bounded TTL; silently degrade on failure."""
    if ttl_seconds <= 0:
        raise ValueError("Redis cache entries require a positive TTL")
    client = _lazy_client()
    if client is None:
        return
    try:
        client.set(key, value, px=max(1, int(round(ttl_seconds * 1000))))
    except Exception as exc:
        _sync_failed(exc)


def delete_key(key: str) -> None:
    """Delete one targeted cache key; silently degrade on failure."""
    client = _lazy_client()
    if client is None:
        return
    try:
        client.delete(key)
    except Exception as exc:
        _sync_failed(exc)


def json_read_through(key: str, ttl_seconds: float, compute: Callable[[], Any]) -> Any:
    """Read JSON through Redis, falling back to the authoritative computation."""
    raw = get_bytes(key)
    if raw is not None:
        try:
            return json.loads(raw)
        except Exception:
            delete_key(key)

    value = compute()
    try:
        set_bytes(key, json.dumps(value, separators=(",", ":")).encode(), ttl_seconds)
    except (TypeError, ValueError) as exc:
        logger.debug("Skip cache for %s: %s", key, exc)
    return value


def publish_event(channel: str, data: bytes) -> None:
    """Publish after database commit; Redis failure never fails the event."""
    client = _lazy_client()
    if client is None:
        return
    try:
        client.publish(channel, data)
    except Exception as exc:
        _sync_failed(exc)


def _get_async_lock():
    global _async_lock
    if _async_lock is None:
        _async_lock = asyncio.Lock()
    return _async_lock


async def _close_async_client(client) -> None:
    if client is None:
        return
    try:
        await client.aclose()
    except AttributeError:  # redis-py 4 compatibility
        await client.close()
    except Exception:
        pass


async def _async_failed(exc: Exception) -> None:
    """Discard a broken async client so a later SSE request can reconnect."""
    global _async_redis, _async_next_retry, _async_backoff, _async_unavailable_logged
    lock = _get_async_lock()
    async with lock:
        broken, _async_redis = _async_redis, None
        await _close_async_client(broken)
        _async_next_retry = time.monotonic() + _async_backoff
        _async_backoff = min(_async_backoff * 2, _RETRY_MAX_SECONDS)
        if not _async_unavailable_logged:
            logger.warning("Redis Pub/Sub temporarily unavailable at %s: %s", _safe_endpoint(), exc)
            _async_unavailable_logged = True


async def _lazy_async_client():
    """Return a healthy async client using the same bounded retry policy."""
    global _async_redis, _async_next_retry, _async_backoff
    global _async_ever_connected, _async_unavailable_logged
    if not config.REDIS_URL:
        return None
    if _async_redis is not None:
        return _async_redis
    if time.monotonic() < _async_next_retry:
        return None

    lock = _get_async_lock()
    async with lock:
        if _async_redis is not None:
            return _async_redis
        if time.monotonic() < _async_next_retry:
            return None
        candidate = None
        try:
            import redis.asyncio as aioredis

            candidate = aioredis.from_url(
                config.REDIS_URL,
                socket_timeout=config.REDIS_SOCKET_TIMEOUT,
                socket_connect_timeout=config.REDIS_CONNECT_TIMEOUT,
                retry_on_timeout=False,
                decode_responses=False,
                health_check_interval=30,
            )
            await candidate.ping()
            _async_redis = candidate
            _async_next_retry = 0.0
            _async_backoff = _RETRY_INITIAL_SECONDS
            state = "reconnected" if _async_ever_connected else "connected"
            logger.info("Redis Pub/Sub %s to %s", state, _safe_endpoint())
            _async_ever_connected = True
            _async_unavailable_logged = False
            return _async_redis
        except Exception as exc:
            await _close_async_client(candidate)
            _async_next_retry = time.monotonic() + _async_backoff
            _async_backoff = min(_async_backoff * 2, _RETRY_MAX_SECONDS)
            if not _async_unavailable_logged:
                logger.warning("Redis Pub/Sub temporarily unavailable at %s: %s", _safe_endpoint(), exc)
                _async_unavailable_logged = True
            return None


async def subscribe_events(channel: str) -> AsyncGenerator[Optional[bytes], None]:
    """Yield Pub/Sub messages and idle ticks; close cleanly on Redis failure."""
    client = await _lazy_async_client()
    if client is None:
        return
    pubsub = client.pubsub()
    try:
        await pubsub.subscribe(channel)
        while True:
            message = await pubsub.get_message(ignore_subscribe_messages=True, timeout=1.0)
            if message and message["type"] == "message":
                yield message["data"]
            else:
                yield None
                await asyncio.sleep(0.05)
    except asyncio.CancelledError:
        raise
    except Exception as exc:
        await _async_failed(exc)
    finally:
        try:
            await pubsub.unsubscribe(channel)
        except Exception:
            pass
        try:
            await pubsub.aclose()
        except AttributeError:
            await pubsub.close()
        except Exception:
            pass


async def close_redis() -> None:
    """Close sync/async pools during application shutdown."""
    global _client, _async_redis, _async_lock
    with _sync_lock:
        client, _client = _client, None
        _close_sync_client(client)
    async_client, _async_redis = _async_redis, None
    await _close_async_client(async_client)
    _async_lock = None
