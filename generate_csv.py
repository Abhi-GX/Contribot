"""
generate_csv.py

Reads the actual Learn export Excel file and generates data/totals.csv
using the same business logic as contribution_calc.py.

Usage:
    python generate_csv.py
"""

import sys
import csv
from pathlib import Path

# Install openpyxl if needed
try:
    import openpyxl
except ImportError:
    print("Installing openpyxl...")
    import subprocess
    subprocess.run([sys.executable, "-m", "pip", "install", "openpyxl"], check=True)
    import openpyxl

ROOT = Path(__file__).parent
EXCEL_PATH = ROOT / "Contribution 07-september-2026.xlsx"
CSV_OUT    = ROOT / "data" / "totals.csv"

ELIGIBLE_STATUSES = {"submitted", "approved"}
ELIGIBLE_FORMAT   = "group meeting with contributor"
POINT_VALUE_USD   = 15.0


def normalize(val):
    return str(val).strip().lower() if val is not None else ""


def main():
    if not EXCEL_PATH.exists():
        print(f"ERROR: {EXCEL_PATH} not found.")
        sys.exit(1)

    print(f"Reading: {EXCEL_PATH}")
    wb = openpyxl.load_workbook(EXCEL_PATH, read_only=True, data_only=True)
    ws = wb.active

    rows = list(ws.iter_rows(values_only=True))
    if not rows:
        print("ERROR: Excel file is empty.")
        sys.exit(1)

    # Find headers
    headers = [normalize(h) for h in rows[0]]
    print(f"Columns found: {headers}")

    def col(name, *aliases):
        for candidate in [name] + list(aliases):
            if candidate in headers:
                return headers.index(candidate)
        return None

    idx_name     = col("name", "mentor", "mentor name")
    idx_email    = col("email", "e mail", "mentor email")
    idx_status   = col("contribution status", "status")
    idx_format   = col("format")
    idx_points   = col("points")
    idx_verified = col("verified points")

    missing = [(n, i) for n, i in [
        ("name", idx_name), ("status", idx_status),
        ("format", idx_format), ("points", idx_points)
    ] if i is None]

    if missing:
        print(f"ERROR: Missing columns: {[n for n,_ in missing]}")
        print(f"Available: {headers}")
        sys.exit(1)

    # Aggregate per person
    people = {}

    for row in rows[1:]:
        fmt    = normalize(row[idx_format])
        status = normalize(row[idx_status])

        if fmt != ELIGIBLE_FORMAT:
            continue
        if status not in ELIGIBLE_STATUSES:
            continue

        # Effective points
        pts = 0.0
        if idx_verified is not None and row[idx_verified] not in (None, ""):
            try:
                pts = float(row[idx_verified])
            except (ValueError, TypeError):
                pass
        if pts == 0.0:
            try:
                pts = float(row[idx_points]) if row[idx_points] not in (None, "") else 0.0
            except (ValueError, TypeError):
                pts = 0.0

        name  = str(row[idx_name]).strip()  if row[idx_name]  else ""
        email = str(row[idx_email]).strip().lower() if idx_email is not None and row[idx_email] else ""
        key   = email or name.lower()

        if key not in people:
            people[key] = {
                "name": name,
                "email": email,
                "eligible_points": 0.0,
                "row_count": 0,
                "statuses": {}
            }

        people[key]["eligible_points"] += pts
        people[key]["row_count"]       += 1
        people[key]["statuses"][status] = people[key]["statuses"].get(status, 0) + 1

    wb.close()

    if not people:
        print("WARNING: No eligible rows found (Group Meeting + Submitted/Verified/Approved).")
        print("Writing empty CSV.")

    # Write CSV
    CSV_OUT.parent.mkdir(exist_ok=True)
    with open(CSV_OUT, "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=[
            "email", "name", "eligible_points", "bonus_usd", "row_count", "included_statuses"
        ])
        writer.writeheader()
        for key, p in sorted(people.items()):
            status_str = " / ".join(f"{v} {k}" for k, v in p["statuses"].items())
            writer.writerow({
                "email":             p["email"],
                "name":              p["name"],
                "eligible_points":   p["eligible_points"],
                "bonus_usd":         round(p["eligible_points"] * POINT_VALUE_USD, 2),
                "row_count":         p["row_count"],
                "included_statuses": status_str,
            })

    print(f"\nWrote {len(people)} people to {CSV_OUT}")
    print("\nSample (first 5):")
    for i, (k, p) in enumerate(list(people.items())[:5]):
        print(f"  {p['name']} ({p['email']}) — {p['eligible_points']} pts, ${p['eligible_points']*POINT_VALUE_USD:.2f}")


if __name__ == "__main__":
    main()
