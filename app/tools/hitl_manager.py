"""
app/tools/hitl_manager.py
Human-in-the-Loop session manager backed by Redis.
All HITL sessions are stored under keys:
  hitl:{session_id}          → pending session data  (TTL 1800s)
  hitl_resolved:{session_id} → resolution from CA    (TTL 3600s)
"""
from __future__ import annotations

import json
import logging
import time
import uuid
from typing import Any

import redis.asyncio as aioredis

from app.config import settings

logger = logging.getLogger(__name__)

HITL_TTL = 1800        # 30 minutes for pending sessions
RESOLVED_TTL = 3600    # 1 hour for resolved sessions


# ─── Redis connection (lazy singleton) ───────────────────────────────────────

_redis_client: aioredis.Redis | None = None


async def _get_redis() -> aioredis.Redis:
    global _redis_client
    if _redis_client is None:
        _redis_client = aioredis.from_url(
            settings.REDIS_URL,
            encoding="utf-8",
            decode_responses=True,
        )
    return _redis_client


# ─── Core HITL functions ─────────────────────────────────────────────────────

async def pause_for_hitl(reason: str, session_data: dict) -> str:
    """
    Save a browser session to Redis and return a session_id for the CA to resume.

    Parameters
    ----------
    reason       : Human-readable explanation of why HITL is needed.
    session_data : Dict containing cookies, current_url, screenshot_b64,
                   actions_remaining, and any other context.

    Returns
    -------
    str — UUID session_id the CA uses to resume via the API.
    """
    session_id = str(uuid.uuid4())
    r = await _get_redis()

    payload = {
        **session_data,
        "reason": reason,
        "created_at": time.time(),
        "session_id": session_id,
        "status": "pending",
    }

    await r.set(
        f"hitl:{session_id}",
        json.dumps(payload),
        ex=HITL_TTL,
    )
    logger.info("HITL session created: %s — reason: %s", session_id, reason)
    return session_id


async def resume_hitl(session_id: str, resolution: dict) -> dict:
    """
    Merge a CA-provided resolution into the saved session data and store it.

    Parameters
    ----------
    session_id : The UUID from pause_for_hitl.
    resolution : Dict that can contain:
                   captcha_token : str  — solved CAPTCHA token
                   cookies       : dict — manually injected cookies
                   manual_input  : dict — any free-form data from the CA

    Returns
    -------
    dict — merged session data + resolution (ready to pass back to run_browser).

    Raises
    ------
    KeyError : Session not found or already expired.
    """
    r = await _get_redis()
    raw = await r.get(f"hitl:{session_id}")
    if not raw:
        raise KeyError(f"HITL session {session_id!r} not found or expired")

    session_data: dict = json.loads(raw)
    merged = {**session_data, **resolution, "status": "resolved"}

    # Store resolution separately so the agent can pick it up
    await r.set(
        f"hitl_resolved:{session_id}",
        json.dumps(merged),
        ex=RESOLVED_TTL,
    )

    # Mark original session as resolved (keep for audit, reduce TTL)
    session_data["status"] = "resolved"
    await r.set(f"hitl:{session_id}", json.dumps(session_data), ex=300)

    logger.info("HITL session resolved: %s", session_id)
    return merged


async def get_hitl_status(session_id: str) -> dict:
    """
    Return the current status of a HITL session.

    Returns
    -------
    {found: bool, data: dict, age_seconds: int}
    """
    r = await _get_redis()
    raw = await r.get(f"hitl:{session_id}")

    if not raw:
        return {"found": False, "data": {}, "age_seconds": 0}

    data: dict = json.loads(raw)
    created_at = data.get("created_at", time.time())
    age_seconds = int(time.time() - created_at)

    # Also check if a resolution exists
    resolved_raw = await r.get(f"hitl_resolved:{session_id}")
    if resolved_raw:
        data["resolution"] = json.loads(resolved_raw)

    return {"found": True, "data": data, "age_seconds": age_seconds}


async def list_pending_hitl() -> list[dict]:
    """
    Scan Redis for all pending hitl:* keys and return a summary list.

    Returns
    -------
    List of dicts: [{session_id, reason, created_at, age_seconds,
                     current_url, screenshot_b64, status}]
    """
    r = await _get_redis()
    sessions: list[dict] = []

    try:
        keys = await r.keys("hitl:*")
        for key in keys:
            raw = await r.get(key)
            if not raw:
                continue
            data: dict = json.loads(raw)

            # Skip already-resolved sessions in the pending list
            if data.get("status") == "resolved":
                continue

            created_at = data.get("created_at", time.time())
            ttl = await r.ttl(key)

            sessions.append({
                "session_id": data.get("session_id", key.replace("hitl:", "")),
                "reason": data.get("reason", ""),
                "created_at": created_at,
                "age_seconds": int(time.time() - created_at),
                "current_url": data.get("current_url", ""),
                "screenshot_b64": data.get("screenshot_b64", ""),
                "status": data.get("status", "pending"),
                "ttl_seconds": ttl,
            })

        # Sort newest first
        sessions.sort(key=lambda x: x["created_at"], reverse=True)

    except Exception as exc:
        logger.error("list_pending_hitl error: %s", exc)

    return sessions


async def cancel_hitl(session_id: str) -> bool:
    """
    Delete a HITL session from Redis (CA discards it).

    Returns True if deleted, False if not found.
    """
    r = await _get_redis()
    deleted = await r.delete(f"hitl:{session_id}", f"hitl_resolved:{session_id}")
    logger.info("HITL session cancelled: %s (deleted=%d)", session_id, deleted)
    return deleted > 0