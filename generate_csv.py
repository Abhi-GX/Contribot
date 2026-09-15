"""
generate_csv.py

Reads a Learn export Excel file and writes:
  data/totals.csv      — summary row per contributor (backward-compatible)
  data/sessions.json   — full per-session detail for flexible queries

Can be run directly:
    python generate_csv.py                          # uses default Excel path
    python generate_csv.py path/to/export.xlsx      # explicit path

Or imported:
    from generate_csv import generate_from_excel, CSV_OUT, SESSIONS_OUT
    result = generate_from_excel(Path("export.xlsx"))
"""

import json
import sys
import csv
from pathlib import Path

try:
    import openpyxl
except ImportError:
    import subprocess
    subprocess.run([sys.executable, "-m", "pip", "install", "openpyxl"], check=True)
    import openpyxl

ROOT = Path(__file__).parent

CSV_OUT          = ROOT / "data" / "totals.csv"
SESSIONS_OUT     = ROOT / "data" / "sessions.json"
CATEGORY_IDX_OUT = ROOT / "data" / "index_by_category.json"
LEARNING_IDX_OUT = ROOT / "data" / "index_by_learning.json"

def _find_default_excel() -> Path:
    candidates = [
        ROOT / "Contribution 07-september-2026.xlsx",
        ROOT / "data" / "latest_export.xlsx",
    ]
    for p in candidates:
        if p.exists():
            return p
    for p in ROOT.glob("Contribution*.xlsx"):
        return p
    return candidates[0]


ELIGIBLE_STATUSES = {"submitted", "approved"}
ELIGIBLE_FORMAT   = "group meeting with contributor"
POINT_VALUE_USD   = 15.0


def normalize(val):
    return str(val).strip().lower() if val is not None else ""


def _fmt_date(val) -> str:
    if val is None:
        return ""
    if hasattr(val, "strftime"):
        return val.strftime("%Y-%m-%d")
    s = str(val).strip()
    return s[:10] if len(s) >= 10 else s


def generate_from_excel(excel_path: Path) -> dict:
    """
    Parse the Learn export at `excel_path` and write:
      - data/totals.csv      (summary, one row per contributor)
      - data/sessions.json   (full session details per contributor)

    Returns:
        {
            "success":      bool,
            "people_count": int,
            "people":       [...],
            "error":        str | None,
        }
    """
    if not excel_path.exists():
        return {"success": False, "people_count": 0, "people": [],
                "error": f"File not found: {excel_path}"}

    try:
        wb = openpyxl.load_workbook(excel_path, read_only=True, data_only=True)
        ws = wb.active
        rows = list(ws.iter_rows(values_only=True))
        wb.close()
    except Exception as e:
        return {"success": False, "people_count": 0, "people": [],
                "error": f"Could not read Excel: {e}"}

    if not rows:
        return {"success": False, "people_count": 0, "people": [],
                "error": "Excel file is empty"}

    headers = [normalize(h) for h in rows[0]]

    def col(*aliases):
        for a in aliases:
            if a in headers:
                return headers.index(a)
        return None

    # Required columns
    idx_name     = col("name", "mentor", "mentor name")
    idx_email    = col("email", "e mail", "mentor email")
    idx_status   = col("contribution status", "status")
    idx_format   = col("format")
    idx_points   = col("points")
    idx_verified = col("verified points")

    # Rich columns for sessions.json
    idx_learning        = col("learning")
    idx_category        = col("learning category")
    idx_program         = col("program name")
    idx_role            = col("role")
    idx_block           = col("block name")
    idx_group           = col("group name")
    idx_start           = col("activity start date")
    idx_end             = col("activity end date")
    idx_learning_status = col("learning status")
    idx_total_mentees   = col("total mentees")
    idx_duration        = col("duration, min")
    idx_updated_pts     = col("updated points")

    missing = [n for n, i in [("name", idx_name), ("status", idx_status),
                               ("format", idx_format), ("points", idx_points)] if i is None]
    if missing:
        return {"success": False, "people_count": 0, "people": [],
                "error": f"Missing columns: {missing}. Found: {headers}"}

    # Build per-person data
    people: dict = {}

    for row in rows[1:]:
        if normalize(row[idx_format]) != ELIGIBLE_FORMAT:
            continue
        if normalize(row[idx_status]) not in ELIGIBLE_STATUSES:
            continue

        # Points — priority: Updated > Verified > Submitted
        pts = 0.0
        for idx in (idx_updated_pts, idx_verified, idx_points):
            if idx is not None and row[idx] not in (None, ""):
                try:
                    v = float(row[idx])
                    if v != 0.0:
                        pts = v
                        break
                except (ValueError, TypeError):
                    pass

        name   = str(row[idx_name]).strip()  if row[idx_name]  else ""
        email  = str(row[idx_email]).strip().lower() if idx_email is not None and row[idx_email] else ""
        key    = email or name.lower()
        status = normalize(row[idx_status])

        # Rich fields
        learning         = str(row[idx_learning]).strip()        if idx_learning        is not None and row[idx_learning]        not in (None, "") else ""
        category         = str(row[idx_category]).strip()        if idx_category        is not None and row[idx_category]        not in (None, "") else ""
        program          = str(row[idx_program]).strip()         if idx_program         is not None and row[idx_program]         not in (None, "") else ""
        role             = str(row[idx_role]).strip()            if idx_role            is not None and row[idx_role]            not in (None, "") else ""
        block            = str(row[idx_block]).strip()           if idx_block           is not None and row[idx_block]           not in (None, "") else ""
        group            = str(row[idx_group]).strip()           if idx_group           is not None and row[idx_group]           not in (None, "") else ""
        start_dt         = _fmt_date(row[idx_start])             if idx_start           is not None else ""
        end_dt           = _fmt_date(row[idx_end])               if idx_end             is not None else ""
        learning_status  = str(row[idx_learning_status]).strip() if idx_learning_status is not None and row[idx_learning_status] not in (None, "") else ""
        try:
            total_mentees = int(row[idx_total_mentees]) if idx_total_mentees is not None and row[idx_total_mentees] not in (None, "") else 0
        except (ValueError, TypeError):
            total_mentees = 0
        try:
            duration_min = int(row[idx_duration]) if idx_duration is not None and row[idx_duration] not in (None, "") else 0
        except (ValueError, TypeError):
            duration_min = 0

        if key not in people:
            people[key] = {
                "name":               name,
                "email":              email,
                "eligible_points":    0.0,
                "row_count":          0,
                "statuses":           {},
                "learning_categories": [],
                "learning_paths":     [],
                "programs":           [],
                "sessions":           [],
            }

        people[key]["eligible_points"] += pts
        people[key]["row_count"]       += 1
        people[key]["statuses"][status] = people[key]["statuses"].get(status, 0) + 1

        if category and category not in people[key]["learning_categories"]:
            people[key]["learning_categories"].append(category)
        if learning and learning not in people[key]["learning_paths"]:
            people[key]["learning_paths"].append(learning)
        if program and program not in people[key]["programs"]:
            people[key]["programs"].append(program)

        people[key]["sessions"].append({
            "learning":          learning,
            "learning_category": category,
            "program_name":      program,
            "role":              role,
            "block_name":        block,
            "group_name":        group,
            "status":            status,
            "learning_status":   learning_status,
            "points":            pts,
            "total_mentees":     total_mentees,
            "duration_min":      duration_min,
            "activity_start":    start_dt,
            "activity_end":      end_dt,
        })

    # --- Write data/totals.csv ---
    CSV_OUT.parent.mkdir(exist_ok=True)
    rows_out = []
    with open(CSV_OUT, "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=[
            "email", "name", "eligible_points", "bonus_usd", "row_count",
            "included_statuses", "total_mentees", "total_duration_min"
        ])
        writer.writeheader()
        for key, p in sorted(people.items()):
            status_str = " / ".join(f"{v} {k}" for k, v in p["statuses"].items())
            bonus = round(p["eligible_points"] * POINT_VALUE_USD, 2)
            row = {
                "email":               p["email"],
                "name":                p["name"],
                "eligible_points":     p["eligible_points"],
                "bonus_usd":           bonus,
                "row_count":           p["row_count"],
                "included_statuses":   status_str,
                "total_mentees":       sum(s["total_mentees"] for s in p["sessions"]),
                "total_duration_min":  sum(s["duration_min"]  for s in p["sessions"]),
            }
            writer.writerow(row)
            rows_out.append(row)

    # --- Write data/sessions.json ---
    sessions_export: dict = {"_name_to_email": {}}
    for key, p in people.items():
        email_key = p["email"] or key
        bonus = round(p["eligible_points"] * POINT_VALUE_USD, 2)
        status_str = " / ".join(f"{v} {k}" for k, v in p["statuses"].items())
        sessions_export[email_key] = {
            "name":               p["name"],
            "email":              p["email"],
            "eligible_points":    p["eligible_points"],
            "bonus_usd":          bonus,
            "row_count":          p["row_count"],
            "included_statuses":  status_str,
            "learning_categories": p["learning_categories"],
            "learning_paths":     p["learning_paths"],
            "programs":           p["programs"],
            "sessions":           p["sessions"],
        }
        if p["name"]:
            sessions_export["_name_to_email"][p["name"].lower()] = email_key

    with open(SESSIONS_OUT, "w", encoding="utf-8") as f:
        json.dump(sessions_export, f, indent=2, default=str)

    # --- Write data/index_by_category.json ---
    # { "JavaScript": [{"name": ..., "email": ..., "eligible_points": ..., "learning_paths": [...]}, ...] }
    cat_index: dict = {}
    for key, p in people.items():
        email_key = p["email"] or key
        stub = {
            "name":            p["name"],
            "email":           p["email"],
            "eligible_points": p["eligible_points"],
            "bonus_usd":       round(p["eligible_points"] * POINT_VALUE_USD, 2),
            "learning_paths":  p["learning_paths"],
        }
        for cat in p["learning_categories"]:
            cat_index.setdefault(cat, []).append(stub)

    with open(CATEGORY_IDX_OUT, "w", encoding="utf-8") as f:
        json.dump(cat_index, f, indent=2, default=str)

    # --- Write data/index_by_learning.json ---
    # { "JavaScript fullstack - Jan Batch 01 - 2026": [{"name": ..., "email": ..., "sessions": N}, ...] }
    learning_index: dict = {}
    for key, p in people.items():
        for lp in p["learning_paths"]:
            session_count = sum(1 for s in p["sessions"] if s["learning"] == lp)
            learning_index.setdefault(lp, []).append({
                "name":            p["name"],
                "email":           p["email"],
                "eligible_points": p["eligible_points"],
                "session_count":   session_count,
            })

    with open(LEARNING_IDX_OUT, "w", encoding="utf-8") as f:
        json.dump(learning_index, f, indent=2, default=str)

    return {"success": True, "people_count": len(people), "people": rows_out, "error": None}


def main():
    excel_path = Path(sys.argv[1]) if len(sys.argv) > 1 else _find_default_excel()
    print(f"Reading: {excel_path}")
    result = generate_from_excel(excel_path)
    if not result["success"]:
        print(f"ERROR: {result['error']}")
        sys.exit(1)
    print(f"\nWrote {result['people_count']} people to {CSV_OUT}")
    print(f"Wrote session details to {SESSIONS_OUT}")
    for p in result["people"][:5]:
        print(f"  {p['name']} ({p['email']}) — {p['eligible_points']} pts, ${p['bonus_usd']:.2f}")


if __name__ == "__main__":
    main()
