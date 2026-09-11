"""
agent/tools.py

Tools available to the Contribution Bot agent.

CURRENT DATA SOURCE: CSV file at data/totals.csv
FUTURE DATA SOURCE:  learn.epam.com GraphQL/REST API (Phase 3)

Each function is a plain Python function — ADK wraps them as FunctionTools
automatically when passed to LlmAgent(tools=[...]).

The function docstrings are what Gemini reads to decide when and how to call
each tool, so keep them clear and accurate.
"""

from __future__ import annotations

import csv
import os
from pathlib import Path

# Path to the CSV data file — relative to project root
_ROOT = Path(__file__).parent.parent
_CSV_PATH = Path(os.environ.get("TOTALS_CSV_PATH", str(_ROOT / "data" / "totals.csv")))

POINT_VALUE_USD = 15.0


def get_contribution(email: str) -> dict:
    """
    Look up the Campus Junior Training contribution data for a specific person
    using their EPAM email address.

    Returns a dictionary with:
      - found (bool): whether the person was found in the data
      - name (str): person's full name
      - email (str): their email
      - eligible_points (float): total eligible contribution points
      - bonus_usd (float): bonus amount in USD at $15 per point
      - row_count (int): number of eligible sessions counted
      - included_statuses (str): breakdown of session statuses counted

    Call this tool whenever the user asks about their points, bonus, contribution,
    or earnings from the Campus Junior Training program.

    Args:
        email: The EPAM email address of the person to look up (e.g. john_doe@epam.com).
    """
    if not _CSV_PATH.exists():
        return {
            "found": False,
            "error": f"Data file not found at {_CSV_PATH}. Contact the program admin."
        }

    email = email.strip().lower()

    with open(_CSV_PATH, newline="", encoding="utf-8") as f:
        reader = csv.DictReader(f)
        for row in reader:
            if row.get("email", "").strip().lower() == email:
                statuses = row.get("included_statuses", "")
                return {
                    "found": True,
                    "name": row.get("name", ""),
                    "email": row.get("email", ""),
                    "eligible_points": float(row.get("eligible_points", 0)),
                    "bonus_usd": float(row.get("bonus_usd", 0)),
                    "row_count": int(row.get("row_count", 0)),
                    "included_statuses": statuses,
                }

    return {
        "found": False,
        "email": email,
        "message": (
            f"No eligible contribution records found for {email}. "
            "This could mean: (1) no Group Meeting sessions with "
            "Submitted/Verified/Approved status yet, (2) the Learn account "
            "email doesn't match the Teams email, or (3) data hasn't refreshed "
            "since the latest session."
        )
    }


def get_program_info() -> dict:
    """
    Returns information about the Campus Junior Training contribution program rules.

    Call this tool when the user asks how points are calculated, what counts as
    eligible, what the bonus rate is, or general questions about the program.
    """
    return {
        "program": "EPAM Campus Junior Training",
        "eligible_format": "Group Meeting sessions only",
        "eligible_statuses": ["Submitted", "Verified", "Approved"],
        "ineligible_statuses": ["Not Eligible", "Rejected", "Draft"],
        "point_value_usd": POINT_VALUE_USD,
        "how_points_work": (
            "Each eligible Group Meeting session earns points. "
            "Verified Points are used if available, otherwise Submitted Points. "
            f"Each point is worth ${POINT_VALUE_USD:.0f} USD as a bonus."
        ),
        "data_freshness": (
            "Data is refreshed periodically from the Learn export. "
            "Very recent sessions may not appear yet."
        ),
        "roles_eligible": ["Mentor", "SME", "Scrum Master", "Product Owner"],
    }
