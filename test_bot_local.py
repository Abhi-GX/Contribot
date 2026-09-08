"""
test_bot_local.py

Simulates the bot conversation locally — no Azure, no Teams needed.
Just runs the same lookup logic the real bot uses and prints the reply.

Usage:
    python test_bot_local.py
"""

import json
from pathlib import Path

TOTALS_PATH = Path(__file__).parent / "totals.json"
POINT_VALUE_USD = 15.0


def load_totals():
    if not TOTALS_PATH.exists():
        print(f"ERROR: {TOTALS_PATH} not found. Run contribution_calc.py first.")
        return {}
    with open(TOTALS_PATH, "r", encoding="utf-8") as f:
        return json.load(f)


def format_reply(entry):
    points = entry["eligible_points"]
    bonus = entry["bonus_usd"]
    rows = entry["row_count"]
    breakdown = entry.get("included_statuses", {})
    breakdown_str = ", ".join(f"{v} {k}" for k, v in breakdown.items())
    return (
        f"\nHere's your Campus Junior contribution total, {entry['name']}:\n"
        f"  Eligible points (Group Meeting sessions): {points:g}\n"
        f"  Sessions counted: {rows} ({breakdown_str})\n"
        f"  Bonus at $15/point: ${bonus:,.2f}\n"
    )


def main():
    totals = load_totals()
    if not totals:
        return

    print("=" * 50)
    print("  Contribution Bot — Local Test")
    print("=" * 50)
    print(f"Loaded {len(totals)} people from totals.json\n")
    print("People in the data:")
    for key, val in totals.items():
        print(f"  {val['name']} ({key})")

    print("\nType an email or name to look up (or 'quit' to exit).")
    print("-" * 50)

    while True:
        query = input("\nEmail/name: ").strip().lower()
        if query in ("quit", "exit", "q"):
            break
        if not query:
            continue

        # exact email match first
        entry = totals.get(query)

        # fallback: name substring
        if not entry:
            entry = next(
                (v for v in totals.values() if query in v["name"].lower()), None
            )

        if entry:
            print(format_reply(entry))
        else:
            print(f"\n  No record found for '{query}'.")
            print("  This would mean either no eligible sessions yet,")
            print("  or the email doesn't match what's in Learn.")


if __name__ == "__main__":
    main()
