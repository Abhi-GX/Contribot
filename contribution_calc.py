"""
contribution_calc.py

Core calculation engine for the Campus Junior Training bonus bot.

Reads a Learn contribution export (the .xlsx you get from the "Export"
button on https://learn.epam.com/admin/contribution) and computes, per
stakeholder (mentor / SME / Scrum Master / Product Owner), how many
bonus-eligible points they've earned and what that's worth in dollars.

BUSINESS RULES (confirmed with Vaibhav):
  - Only rows where FORMAT == "Group Meeting" count. "Individual" format
    rows are excluded even if their status looks eligible.
  - Only rows where CONTRIBUTION STATUS is one of:
        Submitted, Verified, Approved
    count. Anything else (e.g. "Not Eligible", "Rejected", "Draft") is
    excluded.
  - Point value used per row: VERIFIED POINTS if present/non-null,
    otherwise POINTS. (Verified points are the reconciled number once
    an admin has checked the session; before that, the submitted POINTS
    value is the best available number.)
  - 1 point = $15 bonus.

Expected columns in the export (case-insensitive, spaces/underscores
normalized): NAME, EMAIL, MENTEE, CONTRIBUTION STATUS, FORMAT,
POINTS, VERIFIED POINTS. Extra columns are ignored. If EMAIL is
missing, NAME is used as the lookup key instead (less reliable for
matching Teams identities, so EMAIL should always be included in the
export when possible).

USAGE:
    python contribution_calc.py <export.xlsx> [--out totals.json]

    from contribution_calc import compute_totals
    totals = compute_totals("export.xlsx")
    totals["someone@epam.com"]
    # -> {"name": ..., "email": ..., "eligible_points": 15,
    #     "bonus_usd": 225.0, "row_count": 15}
"""

from __future__ import annotations

import argparse
import json
import sys
from dataclasses import dataclass, asdict
from pathlib import Path
from typing import Optional

import pandas as pd

POINT_VALUE_USD = 15.0
ELIGIBLE_FORMAT = "group meeting"
ELIGIBLE_STATUSES = {"submitted", "verified", "approved"}


def _normalize_col(col: str) -> str:
    return str(col).strip().lower().replace("_", " ").replace("-", " ")


def _find_column(columns, *candidates: str) -> Optional[str]:
    """Find the original column name matching any of the normalized candidates."""
    norm_map = {_normalize_col(c): c for c in columns}
    for candidate in candidates:
        if candidate in norm_map:
            return norm_map[candidate]
    return None


@dataclass
class PersonTotal:
    name: str
    email: str
    eligible_points: float
    bonus_usd: float
    row_count: int
    included_statuses: dict  # status -> count, for transparency/debugging


def load_export(path: str) -> pd.DataFrame:
    """Load the Learn export (.xlsx or .csv) into a DataFrame."""
    p = Path(path)
    if not p.exists():
        raise FileNotFoundError(f"Export file not found: {path}")

    if p.suffix.lower() == ".csv":
        df = pd.read_csv(p)
    else:
        df = pd.read_excel(p)

    if df.empty:
        raise ValueError("Export file has no rows.")

    return df


def compute_totals(path: str) -> dict:
    """
    Compute per-person eligible points and bonus $ from a Learn export file.

    Returns a dict keyed by lookup key (email if available, else name),
    each value a dict matching PersonTotal fields.
    """
    df = load_export(path)
    columns = list(df.columns)

    name_col = _find_column(columns, "name", "mentor", "mentor name")
    email_col = _find_column(columns, "email", "e mail", "mentor email")
    status_col = _find_column(columns, "contribution status", "status")
    format_col = _find_column(columns, "format")
    points_col = _find_column(columns, "points")
    verified_points_col = _find_column(columns, "verified points")

    missing = [
        label
        for label, col in [
            ("NAME", name_col),
            ("CONTRIBUTION STATUS", status_col),
            ("FORMAT", format_col),
            ("POINTS", points_col),
        ]
        if col is None
    ]
    if missing:
        raise ValueError(
            f"Export is missing required column(s): {', '.join(missing)}. "
            f"Found columns: {columns}"
        )

    if email_col is None:
        print(
            "WARNING: no EMAIL column found in export. Falling back to NAME "
            "as the lookup key — this is fragile for matching Teams users "
            "and should be fixed by including email in the Learn export.",
            file=sys.stderr,
        )

    # Effective point value per row: verified points if present, else points
    def effective_points(row) -> float:
        if verified_points_col is not None:
            v = row[verified_points_col]
            if pd.notna(v):
                try:
                    return float(v)
                except (TypeError, ValueError):
                    pass
        p = row[points_col]
        try:
            return float(p) if pd.notna(p) else 0.0
        except (TypeError, ValueError):
            return 0.0

    df["_format_norm"] = df[format_col].astype(str).str.strip().str.lower()
    df["_status_norm"] = df[status_col].astype(str).str.strip().str.lower()

    eligible = df[
        (df["_format_norm"] == ELIGIBLE_FORMAT)
        & (df["_status_norm"].isin(ELIGIBLE_STATUSES))
    ].copy()

    eligible["_effective_points"] = eligible.apply(effective_points, axis=1)

    results: dict[str, dict] = {}

    if email_col is not None:
        group_cols = [email_col, name_col]
    else:
        group_cols = [name_col]

    for keys, group in eligible.groupby(group_cols, dropna=False):
        if email_col is not None:
            email_val, name_val = keys
            key = str(email_val).strip().lower() if pd.notna(email_val) else str(name_val).strip().lower()
            email_out = str(email_val).strip() if pd.notna(email_val) else ""
        else:
            (name_val,) = (keys,) if not isinstance(keys, tuple) else keys
            key = str(name_val).strip().lower()
            email_out = ""

        status_breakdown = group["_status_norm"].value_counts().to_dict()
        total_points = float(group["_effective_points"].sum())

        results[key] = asdict(
            PersonTotal(
                name=str(name_val).strip(),
                email=email_out,
                eligible_points=total_points,
                bonus_usd=round(total_points * POINT_VALUE_USD, 2),
                row_count=int(len(group)),
                included_statuses=status_breakdown,
            )
        )

    return results


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("export_path", help="Path to the Learn export (.xlsx or .csv)")
    parser.add_argument(
        "--out", default="totals.json", help="Output JSON path (default: totals.json)"
    )
    parser.add_argument(
        "--person",
        help="Optional: print only this person's total (matches name or email, case-insensitive)",
    )
    args = parser.parse_args()

    totals = compute_totals(args.export_path)

    with open(args.out, "w", encoding="utf-8") as f:
        json.dump(totals, f, indent=2)

    print(f"Wrote {len(totals)} people to {args.out}")

    if args.person:
        needle = args.person.strip().lower()
        match = totals.get(needle)
        if not match:
            # try matching by name substring
            match = next(
                (v for v in totals.values() if needle in v["name"].lower()), None
            )
        if match:
            print(json.dumps(match, indent=2))
        else:
            print(f"No match found for '{args.person}'")


if __name__ == "__main__":
    main()
