"""
generate_csv.py

Reads a Learn export Excel file and writes data/totals.csv.

Can be run directly:
    python generate_csv.py                          # uses default Excel path
    python generate_csv.py path/to/export.xlsx      # explicit path

Or imported:
    from generate_csv import generate_from_excel
    result = generate_from_excel(Path("export.xlsx"))
"""

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

# Canonical output path
CSV_OUT = ROOT / "data" / "totals.csv"

# Default Excel search: explicit env var → latest_export.xlsx → dated file
def _find_default_excel() -> Path:
    candidates = [
        ROOT / "data" / "latest_export.xlsx",
        ROOT / "Contribution 07-september-2026.xlsx",
    ]
    for p in candidates:
        if p.exists():
            return p
    # Last resort: any .xlsx with "Contribution" in root
    for p in ROOT.glob("Contribution*.xlsx"):
        return p
    return candidates[0]  # will fail with a clear message


ELIGIBLE_STATUSES = {"submitted", "approved"}
ELIGIBLE_FORMAT   = "group meeting with contributor"
POINT_VALUE_USD   = 15.0


def normalize(val):
    return str(val).strip().lower() if val is not None else ""


def generate_from_excel(excel_path: Path) -> dict:
    """
    Parse the Learn export at `excel_path` and write data/totals.csv.

    Returns:
        {
            "success":      bool,
            "people_count": int,
            "people":       [{"name", "email", "eligible_points", "bonus_usd", "row_count", "included_statuses"}, ...],
            "error":        str | None,
        }
    """
    if not excel_path.exists():
        return {"success": False, "people_count": 0, "people": [], "error": f"File not found: {excel_path}"}

    try:
        wb = openpyxl.load_workbook(excel_path, read_only=True, data_only=True)
        ws = wb.active
        rows = list(ws.iter_rows(values_only=True))
        wb.close()
    except Exception as e:
        return {"success": False, "people_count": 0, "people": [], "error": f"Could not read Excel: {e}"}

    if not rows:
        return {"success": False, "people_count": 0, "people": [], "error": "Excel file is empty"}

    headers = [normalize(h) for h in rows[0]]

    def col(*aliases):
        for a in aliases:
            if a in headers:
                return headers.index(a)
        return None

    idx_name     = col("name", "mentor", "mentor name")
    idx_email    = col("email", "e mail", "mentor email")
    idx_status   = col("contribution status", "status")
    idx_format   = col("format")
    idx_points   = col("points")
    idx_verified = col("verified points")

    missing = [n for n, i in [("name", idx_name), ("status", idx_status),
                               ("format", idx_format), ("points", idx_points)] if i is None]
    if missing:
        return {"success": False, "people_count": 0, "people": [],
                "error": f"Missing columns: {missing}. Found: {headers}"}

    people = {}
    for row in rows[1:]:
        if normalize(row[idx_format]) != ELIGIBLE_FORMAT:
            continue
        if normalize(row[idx_status]) not in ELIGIBLE_STATUSES:
            continue

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

        name  = str(row[idx_name]).strip() if row[idx_name] else ""
        email = str(row[idx_email]).strip().lower() if idx_email is not None and row[idx_email] else ""
        key   = email or name.lower()
        status = normalize(row[idx_status])

        if key not in people:
            people[key] = {"name": name, "email": email, "eligible_points": 0.0,
                           "row_count": 0, "statuses": {}}
        people[key]["eligible_points"] += pts
        people[key]["row_count"]       += 1
        people[key]["statuses"][status] = people[key]["statuses"].get(status, 0) + 1

    CSV_OUT.parent.mkdir(exist_ok=True)
    rows_out = []
    with open(CSV_OUT, "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=[
            "email", "name", "eligible_points", "bonus_usd", "row_count", "included_statuses"
        ])
        writer.writeheader()
        for key, p in sorted(people.items()):
            status_str = " / ".join(f"{v} {k}" for k, v in p["statuses"].items())
            row = {
                "email":             p["email"],
                "name":              p["name"],
                "eligible_points":   p["eligible_points"],
                "bonus_usd":         round(p["eligible_points"] * POINT_VALUE_USD, 2),
                "row_count":         p["row_count"],
                "included_statuses": status_str,
            }
            writer.writerow(row)
            rows_out.append(row)

    return {"success": True, "people_count": len(people), "people": rows_out, "error": None}


def main():
    excel_path = Path(sys.argv[1]) if len(sys.argv) > 1 else _find_default_excel()
    print(f"Reading: {excel_path}")
    result = generate_from_excel(excel_path)
    if not result["success"]:
        print(f"ERROR: {result['error']}")
        sys.exit(1)
    print(f"\nWrote {result['people_count']} people to {CSV_OUT}")
    for p in result["people"][:5]:
        print(f"  {p['name']} ({p['email']}) — {p['eligible_points']} pts, ${p['bonus_usd']:.2f}")


if __name__ == "__main__":
    main()
