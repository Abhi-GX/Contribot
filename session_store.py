"""
session_store.py

Manages per-user conversation sessions in Upstash Redis (Vercel KV).

Session lifecycle:
  - Created on first message from a user
  - Stores: verified email + last N messages (rolling window)
  - Auto-expires after SESSION_TTL_SECONDS of inactivity
  - Keyed by teams_user_id — fully multi-user, each user has an isolated session

Session data structure:
{
  "email":   "user@epam.com",   # verified Teams email (resolved once, cached)
  "history": [                  # rolling window of last MAX_HISTORY_MESSAGES turns
    {"role": "user",      "text": "..."},
    {"role": "assistant", "text": "..."},
  ]
}

Key format: session:{teams_user_id}
"""

from __future__ import annotations

import json
import os
from typing import Optional

from upstash_redis.asyncio import Redis

SESSION_TTL_SECONDS  = 7200   # 2 hours of inactivity → session expires
MAX_HISTORY_MESSAGES = 6      # keep last 6 messages (3 user + 3 bot turns)

_redis: Optional[Redis] = None
_redis_unavailable: bool = False   # set True once we confirm Redis isn't configured


def _get_redis() -> Redis:
    global _redis, _redis_unavailable
    if _redis_unavailable:
        raise RuntimeError("Redis not configured — operating without session cache")
    if _redis is None:
        url   = os.environ.get("KV_REST_API_URL", "")
        token = os.environ.get("KV_REST_API_TOKEN", "")
        if not url or not token:
            _redis_unavailable = True
            print(
                "[session_store] WARNING: KV_REST_API_URL / KV_REST_API_TOKEN not set. "
                "Sessions disabled — TeamsInfo.get_member will be called on every message "
                "and conversation history will not persist across turns.",
                flush=True,
            )
            raise RuntimeError("Redis not configured — operating without session cache")
        _redis = Redis(url=url, token=token)
    return _redis


def _session_key(teams_user_id: str) -> str:
    return f"session:{teams_user_id}"


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

async def get_session(teams_user_id: str) -> Optional[dict]:
    """
    Load an existing session from Redis.
    Returns None if no session exists or Redis is unavailable.
    """
    try:
        redis = _get_redis()
        raw = await redis.get(_session_key(teams_user_id))
        if raw is None:
            return None
        return json.loads(raw)
    except RuntimeError:
        return None
    except Exception as e:
        print(f"[session_store] get_session error: {e}", flush=True)
        return None


async def save_session(teams_user_id: str, session: dict) -> None:
    """
    Save/update session in Redis with a rolling TTL.
    Call after every message to reset the inactivity timer.
    """
    try:
        redis = _get_redis()
        if "history" in session:
            session["history"] = session["history"][-MAX_HISTORY_MESSAGES:]
        await redis.setex(
            _session_key(teams_user_id),
            SESSION_TTL_SECONDS,
            json.dumps(session),
        )
    except RuntimeError:
        pass   # Redis not configured — silently skip, already warned at startup
    except Exception as e:
        print(f"[session_store] save_session error: {e}", flush=True)


async def delete_session(teams_user_id: str) -> None:
    """Explicitly delete a session (e.g. user says 'bye')."""
    try:
        redis = _get_redis()
        await redis.delete(_session_key(teams_user_id))
    except Exception as e:
        print(f"[session_store] delete_session error: {e}", flush=True)


async def create_session(teams_user_id: str, email: str) -> dict:
    """
    Create a brand new session. Call when get_session returns None.
    """
    session = {"email": email, "history": []}
    await save_session(teams_user_id, session)
    return session
