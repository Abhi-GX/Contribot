"""
session_store.py

Manages per-user conversation sessions in Upstash Redis (Vercel KV).

Session lifecycle:
  - Created on first message from a user
  - Stores: verified email + last N messages (rolling window)
  - Auto-expires after SESSION_TTL_SECONDS of inactivity
  - Deleted explicitly when user says bye/exit (optional)

Why Redis/KV for this:
  - Vercel functions are stateless — no in-memory state between requests
  - Redis TTL handles cleanup automatically — no cron job needed
  - One read + one write per message — fast (<10ms from same region)

Session data structure stored in Redis:
{
  "email": "user@epam.com",           # verified Teams email (resolved once)
  "history": [                         # rolling window of last N messages
    {"role": "user", "text": "what's my bonus?"},
    {"role": "assistant", "text": "Your bonus is $225..."},
    ...
  ]
}

Key format: session:{teams_user_id}
  teams_user_id is the Teams activity.from_property.id — stable per user,
  unique across all users, never changes.
"""

from __future__ import annotations

import json
import os
from typing import Optional

from upstash_redis.asyncio import Redis

# ---------------------------------------------------------------------------
# Config
# ---------------------------------------------------------------------------
SESSION_TTL_SECONDS = 600        # 10 minutes of inactivity → session expires
MAX_HISTORY_MESSAGES = 6         # keep last 6 messages (3 user + 3 bot turns)

# ---------------------------------------------------------------------------
# Redis client — lazy init so import doesn't fail if env vars aren't set
# ---------------------------------------------------------------------------
_redis: Optional[Redis] = None


def _get_redis() -> Redis:
    global _redis
    if _redis is None:
        url   = os.environ.get("KV_REST_API_URL", "")
        token = os.environ.get("KV_REST_API_TOKEN", "")
        if not url or not token:
            raise RuntimeError(
                "KV_REST_API_URL and KV_REST_API_TOKEN must be set. "
                "Add them as Vercel env vars or in your local .env file."
            )
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
    Returns None if no session exists (first message from this user).
    """
    try:
        redis = _get_redis()
        raw = await redis.get(_session_key(teams_user_id))
        if raw is None:
            return None
        return json.loads(raw)
    except Exception as e:
        print(f"[session_store] get_session error: {e}")
        return None


async def save_session(teams_user_id: str, session: dict) -> None:
    """
    Save/update a session in Redis with a rolling TTL.
    Call this after every message to reset the inactivity timer.
    """
    try:
        redis = _get_redis()
        # Trim history to keep only the last MAX_HISTORY_MESSAGES
        if "history" in session:
            session["history"] = session["history"][-MAX_HISTORY_MESSAGES:]
        await redis.setex(
            _session_key(teams_user_id),
            SESSION_TTL_SECONDS,
            json.dumps(session)
        )
    except Exception as e:
        print(f"[session_store] save_session error: {e}")


async def delete_session(teams_user_id: str) -> None:
    """Explicitly delete a session (e.g. user says 'bye')."""
    try:
        redis = _get_redis()
        await redis.delete(_session_key(teams_user_id))
    except Exception as e:
        print(f"[session_store] delete_session error: {e}")


async def create_session(teams_user_id: str, email: str) -> dict:
    """
    Create a brand new session for a user.
    Call this on first message (when get_session returns None).
    """
    session = {"email": email, "history": []}
    await save_session(teams_user_id, session)
    return session
