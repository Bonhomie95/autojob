"""
Live run-progress channel.

A background pipeline task (in a Celery worker) needs to stream progress lines
to a web process serving Server-Sent Events — a different process. Redis pub/sub
bridges them: the worker PUBLISHes to a per-run channel, the web process
SUBSCRIBEs and relays to the browser. This is what makes SSE work once there is
more than one process (the legacy in-memory ``queue.Queue`` could not).

For dev/tests without Redis, a no-op in-memory fallback keeps everything
importable and lets eager task runs work; SSE just won't stream cross-process.
"""

from __future__ import annotations

import json
import logging
from collections.abc import Iterator

from flask import current_app

logger = logging.getLogger(__name__)

_DONE = "__DONE__"


def _channel(run_id: str) -> str:
    return f"autojob:run:{run_id}"


def _redis():
    try:
        import redis

        return redis.Redis.from_url(current_app.config["REDIS_URL"])
    except Exception as exc:  # noqa: BLE001
        logger.debug("Redis unavailable for progress (%s)", exc)
        return None


def publish(run_id: str, message: str) -> None:
    r = _redis()
    if r is None:
        return
    try:
        r.publish(_channel(run_id), json.dumps({"message": message}))
    except Exception as exc:  # noqa: BLE001
        logger.debug("progress publish failed: %s", exc)


def publish_done(run_id: str) -> None:
    r = _redis()
    if r is None:
        return
    try:
        r.publish(_channel(run_id), json.dumps({"done": True}))
    except Exception:  # noqa: BLE001
        pass


def subscribe(run_id: str, timeout: int = 300) -> Iterator[dict]:
    """Yield progress dicts for a run until a done marker or timeout."""
    r = _redis()
    if r is None:
        yield {"message": "Live streaming unavailable (Redis not configured)."}
        yield {"done": True}
        return

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
