"""
api/health.py

Vercel serverless function — GET /api/health

Returns a JSON payload confirming the service is up and how many people
are currently in the totals cache. Useful for:
  - Vercel deployment health checks
  - Quick smoke-test after deploying a new version
  - Confirming totals.json was bundled correctly
"""

from __future__ import annotations

import json
import os
import sys
from datetime import datetime
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler
from pathlib import Path

ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(ROOT))

TOTALS_PATH = Path(os.environ.get("TOTALS_JSON_PATH", str(ROOT / "totals.json")))


def _load_totals() -> dict:
    if not TOTALS_PATH.exists():
        return {}
    with open(TOTALS_PATH, "r", encoding="utf-8") as f:
        return json.load(f)


class handler(BaseHTTPRequestHandler):
    """Vercel Python serverless entry point."""

    def do_GET(self):
        totals = _load_totals()
        payload = {
            "status": "ok",
            "people_cached": len(totals),
            "totals_file": str(TOTALS_PATH),
            "checked_at": datetime.utcnow().isoformat() + "Z",
        }
        body = json.dumps(payload, indent=2).encode("utf-8")
        self.send_response(HTTPStatus.OK)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)
