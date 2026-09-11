"""
api/health.py — Vercel serverless GET /api/health
"""

from __future__ import annotations

import csv
import json
import os
import sys
from datetime import datetime, timezone
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler
from pathlib import Path

ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(ROOT))

CSV_PATH = Path(os.environ.get("TOTALS_CSV_PATH", str(ROOT / "data" / "totals.csv")))


class handler(BaseHTTPRequestHandler):

    def do_GET(self):
        people_count = 0
        if CSV_PATH.exists():
            with open(CSV_PATH, newline="", encoding="utf-8") as f:
                people_count = sum(1 for _ in csv.DictReader(f))

        payload = {
            "status":        "ok",
            "people_cached": people_count,
            "checked_at":    datetime.now(tz=timezone.utc).isoformat(),
        }
        body = json.dumps(payload).encode("utf-8")
        self.send_response(HTTPStatus.OK)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)
