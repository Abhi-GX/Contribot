"""
agent/tools.py

Tools available to the Contribution Bot agent.

DATA SOURCE: data/totals.csv — indexed into memory at import time for O(1) lookup.
Schema columns: email, name, eligible_points, bonus_usd, row_count, included_statuses

When the CSV grows large or moves to a database/API, only this file changes.
"""

from __future__ import annotations

import csv
import os
from pathlib import Path
from typing import Dict, List

ROOT = Path(__file__).parent.parent
_CSV_PATH = Path(os.environ.get("TOTALS_CSV_PATH", str(ROOT / "data" / "totals.csv")))
POINT_VALUE_USD = 15.0

# ---------------------------------------------------------------------------
# In-memory index — loaded once at import, reloaded if CSV changes on disk
# ---------------------------------------------------------------------------
_index: Dict[str, dict] = {}        # email → record
_csv_mtime: float = 0.0


def _load_index() -> None:
    global _index, _csv_mtime

    if not _CSV_PATH.exists():
        _index = {}
        return

    mtime = _CSV_PATH.stat().st_mtime
    if mtime == _csv_mtime:
        return

    new_index: Dict[str, dict] = {}
    with open(_CSV_PATH, newline="", encoding="utf-8") as f:
        for row in csv.DictReader(f):
            email = row.get("email", "").strip().lower()
            if email:
                new_index[email] = {
                    "name":              row.get("name", "").strip(),
                    "email":             email,
                    "eligible_points":   float(row.get("eligible_points", 0)),
                    "bonus_usd":         float(row.get("bonus_usd", 0)),
                    "row_count":         int(row.get("row_count", 0)),
                    "included_statuses": row.get("included_statuses", ""),
                }

    _index = new_index
    _csv_mtime = mtime


_load_index()


# ---------------------------------------------------------------------------
# Tools
# ---------------------------------------------------------------------------

def get_contribution(email: str) -> dict:
    """
    Look up Campus Junior Training contribution data for one person by EPAM email.

    CSV schema: email, name, eligible_points, bonus_usd, row_count, included_statuses
      - eligible_points: total points from Group Meeting sessions (Submitted + Approved)
      - bonus_usd: eligible_points × $15
      - row_count: number of eligible sessions counted
      - included_statuses: breakdown e.g. "6 approved / 9 submitted"

    Returns dict with: found, name, email, eligible_points, bonus_usd, row_count, included_statuses.
    Call this when the user asks about their own points, bonus, contribution, or earnings.
    Use the email already in session context — do NOT ask the user for their email.

    Args:
        email: EPAM email address, e.g. john_doe@epam.com
    """
    _load_index()
    record = _index.get(email.strip().lower())
    if record:
        return {"found": True, **record}
    return {
        "found": False,
        "email": email.strip().lower(),
        "message": (
            f"No eligible records found for {email}. Possible reasons: "
            "(1) no Group Meeting sessions with Submitted/Approved status yet, "
            "(2) Learn account email differs from Teams email, "
            "(3) data not yet refreshed since latest session."
        ),
    }


def get_all_contributors() -> dict:
    """
    Return the full list of all contributors with eligible data from the latest export.

    CSV schema: email, name, eligible_points, bonus_usd, row_count, included_statuses

    Use this when the user asks:
      - "who has contributions?", "list everyone", "show all mentors",
      - "how many people have points?", "total program stats"
    Do NOT use this for personal lookups — use get_contribution(email) instead.
    """
    _load_index()
    contributors = sorted(
        _index.values(),
        key=lambda r: r["eligible_points"],
        reverse=True,
    )
    return {
        "total_contributors": len(contributors),
        "contributors": contributors,
        "total_eligible_points": sum(r["eligible_points"] for r in contributors),
        "total_bonus_usd": sum(r["bonus_usd"] for r in contributors),
    }


def get_top_contributors(n: int = 5) -> dict:
    """
    Return the top N contributors ranked by eligible points (highest first).

    CSV schema: email, name, eligible_points, bonus_usd, row_count, included_statuses

    Use this when the user asks:
      - "who has the most points?", "top contributors", "leaderboard",
      - "who earned the most bonus?"

    Args:
        n: number of top contributors to return (default 5, max 20)
    """
    _load_index()
    n = max(1, min(int(n), 20))
    ranked = sorted(_index.values(), key=lambda r: r["eligible_points"], reverse=True)[:n]
    return {
        "top_n": n,
        "contributors": ranked,
    }


def get_program_info() -> dict:
    """
    Returns Campus Junior Training program rules — eligible formats, statuses, bonus rate.

    Call this for general program questions: how points are calculated, what counts
    as eligible, program structure. Do NOT call this for personal data lookups.
    """
    return {
        "program":             "EPAM Campus Junior Training",
        "eligible_format":     "Group Meeting with contributor sessions only",
        "eligible_statuses":   ["Submitted", "Approved"],
        "ineligible_statuses": ["Not Eligible", "Rejected", "Draft", "Individual"],
        "point_value_usd":     POINT_VALUE_USD,
        "bonus_formula":       f"bonus = eligible_points × ${POINT_VALUE_USD:.0f}",
        "point_value_note":    "Verified Points used if available, otherwise Submitted Points",
        "eligible_roles":      ["Mentor", "SME", "Scrum Master", "Product Owner"],
        "csv_schema": {
            "email":              "EPAM email address (unique key)",
            "name":               "Full name of contributor",
            "eligible_points":    "Total contribution points from eligible sessions",
            "bonus_usd":          "Monetary bonus = eligible_points × $15",
            "row_count":          "Number of eligible sessions counted",
            "included_statuses":  "Breakdown of session statuses e.g. '6 approved / 9 submitted'",
        },
        "data_freshness": "Refreshed from Learn export on each deployment. Recent sessions may lag.",
    }
