"""Inspect actual values in the Excel file to find correct filter values."""
import openpyxl
from pathlib import Path
from collections import Counter

ROOT = Path(__file__).parent
wb = openpyxl.load_workbook(ROOT / "Contribution 07-september-2026.xlsx", read_only=True, data_only=True)
ws = wb.active
rows = list(ws.iter_rows(values_only=True))
headers = [str(h).strip().lower() for h in rows[0]]

def col(name):
    return headers.index(name) if name in headers else None

idx_format = col("format")
idx_status = col("contribution status")
idx_email  = col("email")
idx_name   = col("name")
idx_points = col("points")

print("=== FORMAT values ===")
formats = Counter(str(rows[i][idx_format]).strip() for i in range(1, len(rows)))
for v, c in formats.most_common():
    print(f"  {c:4d}x  '{v}'")

print("\n=== CONTRIBUTION STATUS values ===")
statuses = Counter(str(rows[i][idx_status]).strip() for i in range(1, len(rows)))
for v, c in statuses.most_common():
    print(f"  {c:4d}x  '{v}'")

print("\n=== First 5 data rows (name, email, status, format, points) ===")
for row in rows[1:6]:
    print(f"  name={row[idx_name]}, email={row[idx_email]}, status={row[idx_status]}, format={row[idx_format]}, points={row[idx_points]}")

wb.close()
