"""
test_agent.py — local smoke test for the Gemini ADK agent with session history.

Usage:
    python generate_csv.py        # generate CSV from Excel first
    set GOOGLE_API_KEY=your_key
    python test_agent.py
"""

import sys, os
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent))

from agent import run_agent

def test_conversation():
    """Simulate a multi-turn conversation with one user."""
    print("\n" + "="*60)
    print("SIMULATING MULTI-TURN CONVERSATION")
    print("User: saikrishna_tammi@epam.com")
    print("="*60)

    email   = "saikrishna_tammi@epam.com"
    history = []

    turns = [
        "what's my bonus?",
        "how many sessions is that?",
        "what statuses do those sessions have?",
    ]

    for message in turns:
        print(f"\nUSER: {message}")
        print("-"*40)
        reply, history = run_agent(message, email, history)
        print(f"BOT:  {reply}")

    print("\n" + "="*60)
    print(f"Final history length: {len(history)} messages")


def test_unknown_user():
    """User not in the CSV."""
    print("\n" + "="*60)
    print("TEST: Unknown user")
    print("="*60)
    reply, _ = run_agent("what's my bonus?", "unknown@epam.com", [])
    print(f"BOT: {reply}")


if __name__ == "__main__":
    if not os.environ.get("GOOGLE_API_KEY"):
        print("ERROR: GOOGLE_API_KEY not set.")
        sys.exit(1)

    test_conversation()
    test_unknown_user()
