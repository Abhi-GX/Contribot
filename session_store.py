"""
session_store.py

Manages per-user conversation sessions, admin lists, and data persistence
in Upstash Redis (Vercel KV).

Session lifecycle:
  - Created on first message from a user
  - Stores: verified email + last N messages (rolling window)
  - Auto-expires after SESSION_TTL_SECONDS of inactivity
  - Keyed by teams_user_id

Redis key scheme:
  session:{teams_user_id}     — conversation session
  contribot:admins            — JSON list of visible admin emails
  contribot:totals_csv        — persisted totals.csv content
  contribot:sessions_json     — persisted sessions.json content
  contribot:latest_excel      — base64-encoded latest Excel upload (single version, always overwritten)
"""

from __future__ import annotations

import base64
import json
import os
from typing import Optional

from upstash_redis.asyncio import Redis

SESSION_TTL_SECONDS  = 7200
MAX_HISTORY_MESSAGES = 6

# Admin config — shadow admins are never shown in the UI or stored in Redis
_SHADOW_ADMINS  = frozenset({"jakka_abhilashreddy@epam.com"})
_DEFAULT_ADMINS = ["vishal_bhandari@epam.com"]
_ADMINS_KEY          = "contribot:admins"
_TOTALS_CSV_KEY      = "contribot:totals_csv"
_SESSIONS_JSON_KEY   = "contribot:sessions_json"
_LATEST_EXCEL_KEY    = "contribot:latest_excel"

_redis: Optional[Redis] = None
_redis_unavailable: bool = False


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
                "Sessions, admin list, and data persistence are disabled.",
                flush=True,
            )
            raise RuntimeError("Redis not configured")
        _redis = Redis(url=url, token=token)
    return _redis


def _session_key(teams_user_id: str) -> str:
    return f"session:{teams_user_id}"


# ---------------------------------------------------------------------------
# Session API
# ---------------------------------------------------------------------------

async def get_session(teams_user_id: str) -> Optional[dict]:
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
        pass
    except Exception as e:
        print(f"[session_store] save_session error: {e}", flush=True)


async def delete_session(teams_user_id: str) -> None:
    try:
        redis = _get_redis()
        await redis.delete(_session_key(teams_user_id))
    except Exception as e:
        print(f"[session_store] delete_session error: {e}", flush=True)


async def create_session(teams_user_id: str, email: str) -> dict:
    session = {"email": email, "history": []}
    await save_session(teams_user_id, session)
    return session


# ---------------------------------------------------------------------------
# Admin management
# ---------------------------------------------------------------------------

async def get_admins() -> list:
    """Return the list of visible admin emails (excludes shadow admins)."""
    try:
        redis = _get_redis()
        raw = await redis.get(_ADMINS_KEY)
        if raw is None:
            # Seed default admins on first use
            await redis.set(_ADMINS_KEY, json.dumps(_DEFAULT_ADMINS))
            return list(_DEFAULT_ADMINS)
        return json.loads(raw)
    except RuntimeError:
        return list(_DEFAULT_ADMINS)
    except Exception as e:
        print(f"[session_store] get_admins error: {e}", flush=True)
        return list(_DEFAULT_ADMINS)


async def add_admin(email: str) -> bool:
    """Add an email to the visible admin list. Returns True on success."""
    try:
        email_lower = email.strip().lower()
        admins = await get_admins()
        if email_lower not in [a.lower() for a in admins]:
            admins.append(email_lower)
            redis = _get_redis()
            await redis.set(_ADMINS_KEY, json.dumps(admins))
        return True
    except Exception as e:
        print(f"[session_store] add_admin error: {e}", flush=True)
        return False


async def remove_admin(email: str) -> tuple:
    """
    Remove an email from the visible admin list.
    Returns (success: bool, message: str).
    Cannot remove the default admin (vishal_bhandari@epam.com).
    """
    try:
        email_lower = email.strip().lower()
        protected = {a.lower() for a in _DEFAULT_ADMINS}
        if email_lower in protected:
            return False, "Cannot remove the default admin."
        admins = await get_admins()
        admins = [a for a in admins if a.lower() != email_lower]
        redis = _get_redis()
        await redis.set(_ADMINS_KEY, json.dumps(admins))
        return True, "Admin removed."
    except Exception as e:
        print(f"[session_store] remove_admin error: {e}", flush=True)
        return False, str(e)


async def is_admin(email: str) -> bool:
    """Check if an email has admin access (visible or shadow admin)."""
    if not email:
        return False
    email_lower = email.strip().lower()
    if email_lower in _SHADOW_ADMINS:
        return True
    try:
        admins = await get_admins()
        return email_lower in [a.lower() for a in admins]
    except Exception:
        return email_lower in [a.lower() for a in _DEFAULT_ADMINS]


# ---------------------------------------------------------------------------
# Data persistence (survive redeployments)
# ---------------------------------------------------------------------------

async def save_data_to_redis(totals_csv_content: str, sessions_json_content: str) -> bool:
    """
    Persist totals.csv and sessions.json content to Redis.
    Call after each successful Excel upload so data survives redeployments.
    """
    try:
        redis = _get_redis()
        await redis.set(_TOTALS_CSV_KEY, totals_csv_content)
        await redis.set(_SESSIONS_JSON_KEY, sessions_json_content)
        print("[session_store] Data persisted to Redis.", flush=True)
        return True
    except RuntimeError:
        print("[session_store] Redis not configured — data persistence skipped.", flush=True)
        return False
    except Exception as e:
        print(f"[session_store] save_data_to_redis error: {e}", flush=True)
        return False


async def load_data_from_redis() -> tuple:
    """
    Load totals.csv and sessions.json content from Redis.
    Returns (totals_csv_str or None, sessions_json_str or None).
    """
    try:
        redis = _get_redis()
        totals   = await redis.get(_TOTALS_CSV_KEY)
        sessions = await redis.get(_SESSIONS_JSON_KEY)
        return totals, sessions
    except RuntimeError:
        return None, None
    except Exception as e:
        print(f"[session_store] load_data_from_redis error: {e}", flush=True)
        return None, None


async def save_excel_to_redis(excel_bytes: bytes) -> bool:
    """
    Persist the uploaded Excel file to Redis as base64.
    Always overwrites the single stored version — only latest is kept.
    """
    try:
        redis = _get_redis()
        encoded = base64.b64encode(excel_bytes).decode("utf-8")
        await redis.set(_LATEST_EXCEL_KEY, encoded)
        print(f"[session_store] Excel saved to Redis ({len(excel_bytes):,} bytes raw).", flush=True)
        return True
    except RuntimeError:
        return False
    except Exception as e:
        print(f"[session_store] save_excel_to_redis error: {e}", flush=True)
        return False


async def load_excel_from_redis() -> Optional[bytes]:
    """
    Restore the latest uploaded Excel from Redis.
    Returns raw bytes, or None if not stored yet.
    """
    try:
        redis = _get_redis()
        encoded = await redis.get(_LATEST_EXCEL_KEY)
        if encoded is None:
            return None
        return base64.b64decode(encoded)
    except RuntimeError:
        return None
    except Exception as e:
        print(f"[session_store] load_excel_from_redis error: {e}", flush=True)
        return None
