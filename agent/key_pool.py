"""
agent/key_pool.py

Gemini API key pool with intelligent rotation.

IMPORTANT: Gemini quotas are per Google Cloud project, not per API key.
Keys from the same project share the same RPM/RPD pool. To multiply capacity,
each key must come from a different GCP project.

Key states:
  ACTIVE    — available for use
  COOLING   — hit per-minute rate limit; unusable until cooldown_until
  EXHAUSTED — hit daily quota; unusable until midnight Pacific Time

Config (checked in order):
  GOOGLE_API_KEYS=key1,key2,key3   (comma-separated, preferred)
  GOOGLE_API_KEY + GOOGLE_API_KEY_2 ... GOOGLE_API_KEY_5  (individual fallback)
"""

from __future__ import annotations

import os
import re
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone, timedelta
from enum import Enum
from typing import List, Optional, Tuple

import google.genai as genai


class KeyState(Enum):
    ACTIVE    = "active"
    COOLING   = "cooling"
    EXHAUSTED = "exhausted"


@dataclass
class ManagedKey:
    key:           str
    state:         KeyState = KeyState.ACTIVE
    cooldown_until: float   = 0.0
    fail_count:    int      = 0
    _client:       Optional[genai.Client] = field(default=None, repr=False, compare=False)

    @property
    def client(self) -> genai.Client:
        if self._client is None:
            self._client = genai.Client(api_key=self.key)
        return self._client

    def mark_cooling(self, seconds: float) -> None:
        self.state = KeyState.COOLING
        self.cooldown_until = time.monotonic() + max(seconds, 1.0)
        self.fail_count += 1

    def mark_exhausted(self) -> None:
        self.state = KeyState.EXHAUSTED
        self.cooldown_until = time.monotonic() + _seconds_to_midnight_pt()
        self.fail_count += 1

    def check_and_restore(self) -> bool:
        if self.state == KeyState.ACTIVE:
            return True
        if time.monotonic() >= self.cooldown_until:
            self.state = KeyState.ACTIVE
            return True
        return False


def _seconds_to_midnight_pt() -> float:
    """Seconds until the next midnight in Pacific Time (UTC-7 in summer / UTC-8 in winter)."""
    # Use UTC-7 as a conservative approximation (PDT); worst case is 1 hour off
    now_utc = datetime.now(timezone.utc)
    pt_offset = timedelta(hours=7)   # PDT; PST would be 8
    now_pt = now_utc - pt_offset
    midnight_pt = (now_pt + timedelta(days=1)).replace(
        hour=0, minute=0, second=5, microsecond=0
    )
    return max((midnight_pt - now_pt).total_seconds(), 60.0)


class AllKeysExhausted(Exception):
    pass


class GeminiKeyPool:

    def __init__(self, keys: List[str]) -> None:
        if not keys:
            raise ValueError("GeminiKeyPool requires at least one API key.")
        self._keys: List[ManagedKey] = [ManagedKey(key=k) for k in keys]
        self._current: int = 0
        print(
            f"[key_pool] Initialized with {len(self._keys)} key(s). "
            "Each key should be from a separate GCP project for true quota multiplication.",
            flush=True,
        )

    @classmethod
    def from_env(cls) -> "GeminiKeyPool":
        # Preferred: GOOGLE_API_KEYS=k1,k2,k3,...
        multi = os.environ.get("GOOGLE_API_KEYS", "").strip()
        if multi:
            keys = [k.strip() for k in multi.split(",") if k.strip()]
            if keys:
                return cls(keys)

        # Fallback: GOOGLE_API_KEY (single or comma-separated) + GOOGLE_API_KEY_2 ... GOOGLE_API_KEY_5
        keys = []
        primary = os.environ.get("GOOGLE_API_KEY", "").strip()
        if primary:
            if "," in primary:
                # Multiple keys packed into GOOGLE_API_KEY (comma-separated)
                keys.extend(k.strip() for k in primary.split(",") if k.strip())
            else:
                keys.append(primary)
        for i in range(2, 11):
            extra = os.environ.get(f"GOOGLE_API_KEY_{i}", "").strip()
            if extra:
                keys.append(extra)

        if not keys:
            raise RuntimeError(
                "No Gemini API keys found. "
                "Set GOOGLE_API_KEYS (comma-separated) or GOOGLE_API_KEY."
            )
        return cls(keys)

    def _restore_ready(self) -> None:
        for k in self._keys:
            k.check_and_restore()

    def get_active_client(self) -> Tuple[genai.Client, int]:
        """
        Return (client, key_index) for the best available key.
        Raises AllKeysExhausted if no key is usable right now.
        """
        self._restore_ready()

        n = len(self._keys)
        for i in range(n):
            idx = (self._current + i) % n
            if self._keys[idx].state == KeyState.ACTIVE:
                self._current = idx
                return self._keys[idx].client, idx

        # No ACTIVE key — check if any are just cooling
        cooling = [k for k in self._keys if k.state == KeyState.COOLING]
        if cooling:
            soonest = min(cooling, key=lambda k: k.cooldown_until)
            wait = max(0.0, soonest.cooldown_until - time.monotonic())
            raise AllKeysExhausted(
                f"All {n} keys are rate-limited. Soonest available in {wait:.0f}s."
            )

        raise AllKeysExhausted(
            f"All {n} API keys have exhausted their daily quota. "
            "Quotas reset at midnight Pacific Time."
        )

    def report_429(self, key_index: int, error_str: str) -> Tuple[genai.Client, int]:
        """
        Called when key at key_index receives a 429.
        Classifies it as cooling (per-minute) or exhausted (daily),
        rotates to the next key, and returns (new_client, new_index).
        """
        k = self._keys[key_index]

        # Parse retry delay from error message
        delay_match = re.search(r"retry[^\d]*?(\d+(?:\.\d+)?)\s*s", error_str, re.IGNORECASE)
        retry_delay = float(delay_match.group(1)) if delay_match else 60.0

        # Classify daily vs per-minute
        is_daily = (
            "PerDay" in error_str
            or "per_day" in error_str.lower()
            or "daily" in error_str.lower()
            or retry_delay > 55
        )

        if is_daily:
            k.mark_exhausted()
            print(
                f"[key_pool] Key {key_index + 1}/{len(self._keys)} "
                f"daily quota exhausted (retry={retry_delay:.0f}s). Rotating.",
                flush=True,
            )
        else:
            k.mark_cooling(retry_delay)
            print(
                f"[key_pool] Key {key_index + 1}/{len(self._keys)} "
                f"rate-limited for {retry_delay:.0f}s. Rotating.",
                flush=True,
            )

        self._current = (key_index + 1) % len(self._keys)
        return self.get_active_client()

    def status(self) -> List[dict]:
        """Return per-key status dicts for the admin UI."""
        self._restore_ready()
        now = time.monotonic()
        result = []
        for i, k in enumerate(self._keys):
            entry: dict = {
                "key_index": i + 1,
                "state":     k.state.value,
                "fail_count": k.fail_count,
            }
            if k.state != KeyState.ACTIVE:
                entry["available_in_seconds"] = max(0.0, round(k.cooldown_until - now, 1))
            result.append(entry)
        return result


# ---------------------------------------------------------------------------
# Module-level singleton
# ---------------------------------------------------------------------------
_pool: Optional[GeminiKeyPool] = None


def get_pool() -> GeminiKeyPool:
    global _pool
    if _pool is None:
        _pool = GeminiKeyPool.from_env()
    return _pool
