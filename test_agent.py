"""
test_agent.py — local smoke test for the Gemini ADK agent.

Uses real data from Contribution 07-september-2026.xlsx (via data/totals.csv).

Usage:
    python generate_csv.py        # generate CSV from Excel first
    set GOOGLE_API_KEY=your_key
    python test_agent.py
"""

import sys
import os
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))

from agent import run_agent

def test(label, message, email=""):
    print(f"\n{'='*60}")
    print(f"TEST: {label}")
    print(f"USER ({email or 'no email'}): {message}")
    print("-"*60)
    reply = run_agent(message, email)
    print(f"BOT: {reply}")

if __name__ == "__main__":
    if not os.environ.get("GOOGLE_API_KEY"):
        print("ERROR: GOOGLE_API_KEY not set.")
        print("Get one from https://aistudio.google.com/app/apikey")
        sys.exit(1)

    # Real user from Contribution 07-september-2026.xlsx
    test(
        "Real user — asking for bonus",
        "what's my bonus?",
        "saikrishna_tammi@epam.com"
    )

    # Real user — different phrasing
    test(
        "Real user — asking for points",
        "how many contribution points do I have?",
        "saikrishna_tammi@epam.com"
    )

    # User not in the data
    test(
        "Unknown user",
        "can you check my contribution?",
        "unknown_user@epam.com"
    )

    # General program question — no email needed
    test(
        "Program info question",
        "how does the bonus calculation work?"
    )
