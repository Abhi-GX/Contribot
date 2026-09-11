"""
test_agent.py — smoke tests for the Contribution Bot.

Two modes:
  1. Local agent test  — calls run_agent() directly (needs GOOGLE_API_KEY)
  2. Deployed service  — hits live Render endpoints (no API key needed)

Usage:
    python test_agent.py            # runs both
    python test_agent.py --local    # local agent only
    python test_agent.py --deployed # deployed service only
"""

import sys
import os
import urllib.request
import json
from pathlib import Path

RENDER_URL = "https://contribot.onrender.com"

# ---------------------------------------------------------------------------
# Auto-load .env
# ---------------------------------------------------------------------------
ROOT = Path(__file__).parent
env_file = ROOT / ".env"
if env_file.exists():
    for line in env_file.read_text().splitlines():
        line = line.strip()
        if line and not line.startswith("#") and "=" in line:
            k, v = line.split("=", 1)
            os.environ.setdefault(k.strip(), v.strip())

sys.path.insert(0, str(ROOT))


# ---------------------------------------------------------------------------
# Deployed service tests
# ---------------------------------------------------------------------------
def test_deployed():
    print("\n" + "="*60)
    print(f"DEPLOYED SERVICE: {RENDER_URL}")
    print("="*60)

    # Health check
    print("\n[1] GET /api/health")
    try:
        with urllib.request.urlopen(f"{RENDER_URL}/api/health", timeout=30) as r:
            data = json.loads(r.read())
        print(f"    status:        {data.get('status')}")
        print(f"    people_cached: {data.get('people_cached')}")
        print(f"    checked_at:    {data.get('checked_at')}")
        assert data.get("status") == "ok", "health check failed"
        print("    PASS")
    except Exception as e:
        print(f"    FAIL: {e}")

    # Stats
    print("\n[2] GET /api/stats")
    try:
        with urllib.request.urlopen(f"{RENDER_URL}/api/stats", timeout=30) as r:
            data = json.loads(r.read())
        print(f"    people_count:   {data.get('people_count')}")
        print(f"    total_bonus:    ${data.get('total_bonus_usd')}")
        print(f"    last_modified:  {data.get('last_modified')}")
        print("    PASS")
    except Exception as e:
        print(f"    FAIL: {e}")

    # Admin UI
    print("\n[3] GET / (admin UI)")
    try:
        with urllib.request.urlopen(f"{RENDER_URL}/", timeout=30) as r:
            body = r.read().decode()
        assert "Contribution Bot" in body, "admin UI not found"
        print(f"    HTTP {r.status} - page loaded")
        print("    PASS")
    except Exception as e:
        print(f"    FAIL: {e}")


# ---------------------------------------------------------------------------
# Local agent tests
# ---------------------------------------------------------------------------
def test_local():
    from agent import run_agent
    from agent.tools import get_contribution

    print("\n" + "="*60)
    print("LOCAL AGENT TEST")
    print("="*60)

    email = "saikrishna_tammi@epam.com"

    # Pre-fetch as messages.py does in production
    prefetched = get_contribution(email)

    print("\n[1] Multi-turn conversation — saikrishna_tammi@epam.com")
    history = []
    turns = [
        "what's my bonus?",
        "how many sessions is that?",
        "what statuses do those sessions have?",
    ]
    for msg in turns:
        print(f"\n  USER: {msg}")
        reply, history = run_agent(msg, email, history, prefetched)
        print(f"  BOT:  {reply}")
    print(f"\n  History length: {len(history)} messages - PASS")

    print("\n[2] Unknown user")
    unknown_prefetched = get_contribution("unknown@epam.com")
    reply, _ = run_agent("what's my bonus?", "unknown@epam.com", [], unknown_prefetched)
    print(f"  BOT: {reply}")
    print("  PASS")

    print("\n[3] Program info (no pre-fetch needed)")
    reply, _ = run_agent("how does the program work?", email, [])
    print(f"  BOT: {reply}")
    print("  PASS")


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------
if __name__ == "__main__":
    mode = sys.argv[1] if len(sys.argv) > 1 else "--both"

    if mode in ("--deployed", "--both"):
        test_deployed()

    if mode in ("--local", "--both"):
        if not os.environ.get("GOOGLE_API_KEY"):
            print("\nSkipping local tests — GOOGLE_API_KEY not set.")
            print("Run:  set GOOGLE_API_KEY=your_key  then retry with --local")
        else:
            test_local()

    print("\nDone.")
