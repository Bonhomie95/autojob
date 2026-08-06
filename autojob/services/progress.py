"""
Live run-progress channel.

A pipeline run streams progress lines to a web process serving Server-Sent
Events. Two transports are supported, chosen automatically:

* **In-process bus (default)** — the run executes in a background thread inside
  the same web process that serves the SSE stream, so a plain in-memory queue
  bridges them. This is what lets AutoJob deploy as a single web service with no
  Redis and no separate worker.
* **Redis pub/sub** — used only when ``REDIS_URL`` points at a reachable Redis
  AND the run executes in a *separate* process (a real Celery worker). The
  worker PUBLISHes to a per-run channel; the web process SUBSCRIBEs and relays.

The transport is decided per call by whether Redis is reachable; if it isn't,
everything falls back to the in-process bus, which is always available.
"""

from __future__ import annotations

import json
import logging
import queue
import threading
from collections.abc import Iterator

from flask import current_app

logger = logging.getLogger(__name__)

_DONE = "__DONE__"

# ── In-process bus ────────────────────────────────────────────────
# One queue per active run, shared between the background run thread (publisher)
# and the SSE request handler (subscriber) within a single process.
_local_bus: dict[str, queue.Queue] = {}
_local_lock = threading.Lock()

# Cache the "is Redis usable" decision so we don't attempt a connection on every
# publish. None = not yet decided.
_redis_ok: bool | None = None


def _channel(run_id: str) -> str:
    return f"autojob:run:{run_id}"


def _local_queue(run_id: str) -> queue.Queue:
    with _local_lock:
        q = _local_bus.get(run_id)
        if q is None:
            q = queue.Queue()
            _local_bus[run_id] = q
        return q


def _drop_local_queue(run_id: str) -> None:
    with _local_lock:
        _local_bus.pop(run_id, None)


def _redis():
    """Return a reachable Redis client, or None. The reachability check runs
    once per process and is cached — most deployments have no Redis at all."""
    global _redis_ok
    if _redis_ok is False:
        return None
    try:
        import redis

        client = redis.Redis.from_url(current_app.config["REDIS_URL"])
        if _redis_ok is None:
            client.ping()  # decide reachability exactly once
            _redis_ok = True
        return client
    except Exception as exc:  # noqa: BLE001
        if _redis_ok is None:
            logger.info("Redis unavailable — using in-process progress bus (%s)", exc)
        _redis_ok = False
        return None


def publish(run_id: str, message: str) -> None:
    r = _redis()
    if r is not None:
        try:
            r.publish(_channel(run_id), json.dumps({"message": message}))
            return
        except Exception as exc:  # noqa: BLE001
            logger.debug("progress publish failed, falling back: %s", exc)
    _local_queue(run_id).put({"message": message})


def publish_done(run_id: str) -> None:
    r = _redis()
    if r is not None:
        try:
            r.publish(_channel(run_id), json.dumps({"done": True}))
            return
        except Exception:  # noqa: BLE001
            pass
    _local_queue(run_id).put({"done": True})


def subscribe(run_id: str, timeout: int = 300) -> Iterator[dict]:
    """Yield progress dicts for a run until a done marker or timeout."""
    r = _redis()
    if r is not None:
        yield from _subscribe_redis(r, run_id)
        return
    yield from _subscribe_local(run_id, timeout)


def _subscribe_local(run_id: str, timeout: int) -> Iterator[dict]:
    q = _local_queue(run_id)
    try:
        while True:
            try:
                payload = q.get(timeout=timeout)
            except queue.Empty:
                yield {"done": True, "message": "Timed out waiting for progress."}
                return
            yield payload
            if payload.get("done"):
                return
    finally:
        _drop_local_queue(run_id)


def _subscribe_redis(r, run_id: str) -> Iterator[dict]:
    pubsub = r.pubsub()
    pubsub.subscribe(_channel(run_id))
    try:
        for item in pubsub.listen():
            if item.get("type") != "message":
                continue
            payload = json.loads(item["data"])
            yield payload
            if payload.get("done"):
                break
    finally:
        pubsub.close()
