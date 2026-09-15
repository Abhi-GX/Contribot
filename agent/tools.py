"""
agent/tools.py

Tools available to the Contribution Bot agent.

DATA SOURCES:
  data/totals.csv     — summary index, O(1) email lookup
  data/sessions.json  — full per-session detail including learning paths, categories, programs

When the CSV grows large or moves to a database/API, only this file changes.
"""

from __future__ import annotations

import csv
import json
import os
from pathlib import Path
from typing import Dict, List

ROOT = Path(__file__).parent.parent
_CSV_PATH          = Path(os.environ.get("TOTALS_CSV_PATH",   str(ROOT / "data" / "totals.csv")))
_SESSIONS_PATH     = Path(os.environ.get("SESSIONS_JSON_PATH", str(ROOT / "data" / "sessions.json")))
_CATEGORY_IDX_PATH = ROOT / "data" / "index_by_category.json"
_LEARNING_IDX_PATH = ROOT / "data" / "index_by_learning.json"
POINT_VALUE_USD = 15.0

# ---------------------------------------------------------------------------
# In-memory indexes — loaded once at import, reloaded if files change on disk
# ---------------------------------------------------------------------------
_index: Dict[str, dict] = {}       # email → summary record
_csv_mtime: float = 0.0

_sessions_index: Dict[str, dict] = {}  # email → full session record
_name_to_email:  Dict[str, str]  = {}  # lowercase_name → email
_sessions_mtime: float = 0.0


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


def _load_sessions() -> None:
    global _sessions_index, _name_to_email, _sessions_mtime
    if not _SESSIONS_PATH.exists():
        return
    mtime = _SESSIONS_PATH.stat().st_mtime
    if mtime == _sessions_mtime:
        return
    try:
        with open(_SESSIONS_PATH, encoding="utf-8") as f:
            data = json.load(f)
        _name_to_email  = data.pop("_name_to_email", {})
        _sessions_index = data
        _sessions_mtime = mtime
    except Exception as e:
        print(f"[tools] Failed to load sessions.json: {e}", flush=True)


# Load on import
_load_index()
_load_sessions()


# ---------------------------------------------------------------------------
# Tools
# ---------------------------------------------------------------------------

def get_contribution(email: str) -> dict:
    """
    Look up Campus Junior Training contribution data for one person by EPAM email.

    Returns: found, name, email, eligible_points, bonus_usd, row_count,
             included_statuses, learning_categories, learning_paths, programs.

    Call this when the user asks about their own points, bonus, contribution, or earnings.
    Use the email already in session context — do NOT ask the user for their email.
    """
    _load_index()
    _load_sessions()
    record = _index.get(email.strip().lower())
    if record:
        sess = _sessions_index.get(email.strip().lower(), {})
        return {
            "found": True,
            **record,
            "learning_categories": sess.get("learning_categories", []),
            "learning_paths":      sess.get("learning_paths", []),
            "programs":            sess.get("programs", []),
        }
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


def get_session_details(email_or_name: str) -> dict:
    """
    Get detailed session data for a contributor by EPAM email OR full name.

    Returns all eligible sessions with: learning path name, learning category
    (e.g. JavaScript, Java), program name, contributor role, block name,
    activity start/end dates, and points per session.

    Use this tool when the user asks:
      - "what learning paths did I contribute to?"
      - "which learning categories did [name] work on?"
      - "what programs am I part of?"
      - "show my session history"
      - "what did Saikrishna Tammi contribute to?"

    Accepts: EPAM email (john_doe@epam.com) OR full/partial name (Saikrishna Tammi).
    """
    _load_index()
    _load_sessions()

    key = email_or_name.strip().lower()

    # 1. Try exact email lookup
    record = _sessions_index.get(key)

    # 2. Try exact name lookup
    if record is None:
        email_found = _name_to_email.get(key)
        if email_found:
            record = _sessions_index.get(email_found)

    # 3. Fuzzy partial name match
    if record is None:
        for name_lower, email in _name_to_email.items():
            if key in name_lower or name_lower in key:
                record = _sessions_index.get(email)
                if record:
                    break

    if record is None:
        return {
            "found": False,
            "identifier": email_or_name,
            "message": (
                f"No contributor found for '{email_or_name}'. "
                "Try their full name (e.g. 'Saikrishna Tammi') or EPAM email."
            ),
        }

    return {"found": True, **record}


def get_all_contributors() -> dict:
    """
    Return ALL contributors with eligible data.

    Use for: 'list everyone', 'how many people have points', 'total program stats'.
    ADMIN ONLY — do not call for regular (non-admin) users.
    """
    _load_index()
    contributors = sorted(
        _index.values(),
        key=lambda r: r["eligible_points"],
        reverse=True,
    )
    return {
        "total_contributors":   len(contributors),
        "contributors":         contributors,
        "total_eligible_points": sum(r["eligible_points"] for r in contributors),
        "total_bonus_usd":      sum(r["bonus_usd"] for r in contributors),
    }


def get_top_contributors(n: int = 5) -> dict:
    """
    Return the top N contributors ranked by eligible points (highest first).

    Use for: 'who has the most points?', 'leaderboard', 'top contributors'.
    ADMIN ONLY — do not call for regular (non-admin) users.

    Args:
        n: number of top contributors to return (default 5, max 20)
    """
    _load_index()
    n = max(1, min(int(n), 20))
    ranked = sorted(_index.values(), key=lambda r: r["eligible_points"], reverse=True)[:n]
    return {"top_n": n, "contributors": ranked}


def get_contributions_in_period(
    email_or_name: str,
    start_date: str = "",
    end_date: str = "",
) -> dict:
    """Shortcut — delegates to query_contributions with only date filters."""
    return query_contributions(
        email_or_name=email_or_name,
        start_date=start_date,
        end_date=end_date,
    )


_GROUP_BY_FIELD = {
    "learning_category": "learning_category",
    "category":          "learning_category",
    "learning_path":     "learning",
    "path":              "learning",
    "program":           "program_name",
    "role":              "role",
    "status":            "status",
    "learning_status":   "learning_status",
    "block":             "block_name",
    "month":             "__month__",
    "year":              "__year__",
}


def _apply_group_by(sessions: list, group_by: str) -> list:
    field = _GROUP_BY_FIELD.get(group_by.strip().lower(), "")
    if not field:
        return []
    groups: dict = {}
    for s in sessions:
        if field == "__month__":
            key = (s.get("activity_start", "") or "")[:7] or "unknown"
        elif field == "__year__":
            key = (s.get("activity_start", "") or "")[:4] or "unknown"
        else:
            key = (s.get(field, "") or "").strip() or "unknown"
        if key not in groups:
            groups[key] = {"group": key, "session_count": 0, "points": 0.0,
                           "total_mentees": 0, "total_duration_min": 0}
        g = groups[key]
        g["session_count"]     += 1
        g["points"]            += s.get("points", 0)
        g["total_mentees"]     += s.get("total_mentees", 0)
        g["total_duration_min"] += s.get("duration_min", 0)
    result = sorted(groups.values(), key=lambda x: x["points"], reverse=True)
    for g in result:
        g["bonus_usd"] = round(g["points"] * POINT_VALUE_USD, 2)
    return result


def query_contributions(
    email_or_name: str,
    learning_category: str = "",
    learning_path: str = "",
    program: str = "",
    role: str = "",
    status: str = "",
    learning_status: str = "",
    start_date: str = "",
    end_date: str = "",
    group_by: str = "",
) -> dict:
    """
    Generic flexible query — filter a person's sessions by ANY combination of fields.

    ALL text filters are case-insensitive partial matches:
      learning_category="java"     matches "JavaScript", "Java", "Core Java"
      learning_path="fullstack"    matches any path containing "fullstack"
      role="mentor"                matches "Mentor", "Senior Mentor"
      learning_status="delivered"  matches "Delivered"

    group_by: aggregate instead of listing raw sessions — ALWAYS use this when the
      user asks for a summary, breakdown, or "how much per X". Valid values:
        "learning_category", "learning_path", "program", "role",
        "status", "learning_status", "month", "year"
      Returns grouped rows: {group, session_count, points, bonus_usd,
                              total_mentees, total_duration_min}
      This keeps the response tiny regardless of session count.

    Examples:
      "my Java contributions last two months"
          → learning_category="java", start_date=..., end_date=...
      "points by learning category"
          → group_by="learning_category"
      "how many sessions per month this year"
          → start_date="YYYY-01-01", group_by="month"
      "approved sessions in Explora"
          → program="explora", status="approved"
      "breakdown by role as mentor"
          → role="mentor", group_by="role"

    All params optional except email_or_name. Dates are YYYY-MM-DD.
    """
    _load_sessions()

    key = email_or_name.strip().lower()
    record = _sessions_index.get(key)

    if record is None:
        email_found = _name_to_email.get(key)
        if email_found:
            record = _sessions_index.get(email_found)

    if record is None:
        for name_lower, email in _name_to_email.items():
            if key in name_lower or name_lower in key:
                record = _sessions_index.get(email)
                if record:
                    break

    if record is None:
        return {
            "found": False,
            "identifier": email_or_name,
            "message": f"No contributor found for '{email_or_name}'. Try full name or EPAM email.",
        }

    lc   = learning_category.strip().lower()
    lp   = learning_path.strip().lower()
    prog = program.strip().lower()
    r    = role.strip().lower()
    st   = status.strip().lower()
    ls   = learning_status.strip().lower()
    start = start_date.strip()
    end   = end_date.strip()

    filtered = []
    for s in record.get("sessions", []):
        if lc   and lc   not in s.get("learning_category", "").lower(): continue
        if lp   and lp   not in s.get("learning", "").lower():          continue
        if prog and prog not in s.get("program_name", "").lower():      continue
        if r    and r    not in s.get("role", "").lower():              continue
        if st   and st   not in s.get("status", "").lower():            continue
        if ls   and ls   not in s.get("learning_status", "").lower():   continue
        s_date = s.get("activity_start", "") or s.get("activity_end", "")
        if s_date:
            if start and s_date < start: continue
            if end   and s_date > end:   continue
        elif start or end:
            continue
        filtered.append(s)

    period_points  = sum(s.get("points", 0) for s in filtered)
    total_mentees  = sum(s.get("total_mentees", 0) for s in filtered)
    total_duration = sum(s.get("duration_min", 0) for s in filtered)

    active_filters = {k: v for k, v in {
        "learning_category": learning_category,
        "learning_path":     learning_path,
        "program":           program,
        "role":              role,
        "status":            status,
        "learning_status":   learning_status,
        "start_date":        start_date,
        "end_date":          end_date,
    }.items() if v}

    base = {
        "found":               True,
        "name":                record["name"],
        "email":               record["email"],
        "filters_applied":     active_filters,
        "period_points":       period_points,
        "period_bonus_usd":    round(period_points * POINT_VALUE_USD, 2),
        "session_count":       len(filtered),
        "total_mentees":       total_mentees,
        "total_duration_min":  total_duration,
    }

    if group_by:
        grouped = _apply_group_by(filtered, group_by)
        base["grouped_by"] = group_by
        base["groups"] = grouped
    else:
        base["learning_paths"]      = list(dict.fromkeys(s["learning"]          for s in filtered if s.get("learning")))
        base["learning_categories"] = list(dict.fromkeys(s["learning_category"] for s in filtered if s.get("learning_category")))
        base["programs"]            = list(dict.fromkeys(s["program_name"]      for s in filtered if s.get("program_name")))
        base["roles"]               = list(dict.fromkeys(s["role"]              for s in filtered if s.get("role")))
        base["sessions"]            = filtered

    return base
