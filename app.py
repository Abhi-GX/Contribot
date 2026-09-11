"""
app.py

Production server for Render Web Service.

Routes:
  GET  /              → Admin UI  (Excel upload page)
  POST /upload        → Accept .xlsx, regenerate data/totals.csv
  GET  /api/stats     → Current CSV stats (used by admin UI)
  POST /api/messages  → Teams bot webhook
  GET  /api/health    → Health check

Start:
    python app.py

Render config:
    Build:  pip install -r requirements.txt && python generate_csv.py
    Start:  python app.py
    Port:   auto-detected from PORT env var (Render sets it)
"""

from __future__ import annotations

import csv
import json
import os
import sys
import tempfile
import traceback
from datetime import datetime, timezone
from http import HTTPStatus
from pathlib import Path

ROOT = Path(__file__).parent
sys.path.insert(0, str(ROOT))

from aiohttp import web
from aiohttp.web import Request, Response, json_response

from botbuilder.core import (
    ActivityHandler,
    BotFrameworkAdapter,
    BotFrameworkAdapterSettings,
    MessageFactory,
    TurnContext,
)
from botbuilder.core.teams import TeamsInfo
from botbuilder.schema import Activity

from agent import run_agent_async
from agent.tools import get_contribution
from generate_csv import generate_from_excel, CSV_OUT
from session_store import get_session, save_session, create_session

# ---------------------------------------------------------------------------
# Config
# ---------------------------------------------------------------------------
APP_ID       = os.environ.get("MicrosoftAppId",       "400dfdef-d35e-4561-95c1-57d11495c1de")
APP_PASSWORD = os.environ.get("MicrosoftAppPassword", "")
APP_TENANT   = os.environ.get("MicrosoftAppTenantId", "b41b72d0-4e9f-4c26-8a69-f949f367c91d")
UPLOAD_DIR   = ROOT / "data"

# ---------------------------------------------------------------------------
# Admin UI HTML
# ---------------------------------------------------------------------------
ADMIN_HTML = """<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="UTF-8">
  <meta name="viewport" content="width=device-width, initial-scale=1.0">
  <title>Contribution Bot — Admin</title>
  <style>
    *, *::before, *::after { box-sizing: border-box; margin: 0; padding: 0; }

    body {
      font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, sans-serif;
      background: #0f1117;
      color: #e2e8f0;
      min-height: 100vh;
      display: flex;
      align-items: center;
      justify-content: center;
      padding: 2rem;
    }

    .card {
      background: #1a1d27;
      border: 1px solid #2d3148;
      border-radius: 12px;
      padding: 2.5rem;
      width: 100%;
      max-width: 560px;
      box-shadow: 0 4px 32px rgba(0,0,0,0.4);
    }

    .logo { font-size: 1.5rem; margin-bottom: 0.25rem; }
    h1 { font-size: 1.1rem; font-weight: 600; color: #94a3b8; margin-bottom: 2rem; }

    .stats {
      background: #12151e;
      border: 1px solid #2d3148;
      border-radius: 8px;
      padding: 1rem 1.25rem;
      margin-bottom: 2rem;
      display: grid;
      grid-template-columns: 1fr 1fr;
      gap: 0.75rem;
    }
    .stat-label { font-size: 0.75rem; color: #64748b; text-transform: uppercase; letter-spacing: 0.05em; }
    .stat-value { font-size: 1.25rem; font-weight: 700; color: #e2e8f0; margin-top: 0.15rem; }
    .stat-sub   { font-size: 0.75rem; color: #64748b; margin-top: 0.1rem; }

    .drop-zone {
      border: 2px dashed #2d3148;
      border-radius: 8px;
      padding: 2.5rem 1.5rem;
      text-align: center;
      cursor: pointer;
      transition: border-color 0.2s, background 0.2s;
      position: relative;
      margin-bottom: 1.25rem;
    }
    .drop-zone:hover, .drop-zone.dragover {
      border-color: #6366f1;
      background: rgba(99,102,241,0.05);
    }
    .drop-zone input[type=file] {
      position: absolute; inset: 0; opacity: 0; cursor: pointer; width: 100%; height: 100%;
    }
    .drop-icon { font-size: 2rem; margin-bottom: 0.5rem; }
    .drop-text { color: #94a3b8; font-size: 0.9rem; }
    .drop-text strong { color: #6366f1; }
    .file-name { margin-top: 0.5rem; font-size: 0.85rem; color: #6366f1; font-weight: 500; }

    button {
      width: 100%;
      padding: 0.75rem;
      background: #6366f1;
      color: white;
      border: none;
      border-radius: 8px;
      font-size: 1rem;
      font-weight: 600;
      cursor: pointer;
      transition: background 0.2s, opacity 0.2s;
    }
    button:hover  { background: #4f46e5; }
    button:disabled { opacity: 0.5; cursor: not-allowed; }

    .result {
      margin-top: 1.5rem;
      border-radius: 8px;
      padding: 1rem 1.25rem;
      font-size: 0.9rem;
      display: none;
    }
    .result.success { background: rgba(16,185,129,0.1); border: 1px solid #10b981; color: #6ee7b7; }
    .result.error   { background: rgba(239,68,68,0.1);  border: 1px solid #ef4444; color: #fca5a5; }

    .result-title { font-weight: 700; margin-bottom: 0.5rem; }
    .preview-table {
      width: 100%; border-collapse: collapse; margin-top: 0.75rem;
      font-size: 0.8rem;
    }
    .preview-table th { text-align: left; padding: 0.3rem 0.5rem; color: #94a3b8; border-bottom: 1px solid #2d3148; }
    .preview-table td { padding: 0.3rem 0.5rem; border-bottom: 1px solid #1e2235; }

    .spinner {
      display: inline-block; width: 16px; height: 16px;
      border: 2px solid rgba(255,255,255,0.3);
      border-top-color: white;
      border-radius: 50%;
      animation: spin 0.7s linear infinite;
      margin-right: 0.5rem; vertical-align: middle;
    }
    @keyframes spin { to { transform: rotate(360deg); } }
  </style>
</head>
<body>
<div class="card">
  <div class="logo">🤖</div>
  <h1>Campus Junior Training — Contribution Bot Admin</h1>

  <div class="stats" id="stats">
    <div>
      <div class="stat-label">Contributors</div>
      <div class="stat-value" id="stat-count">—</div>
    </div>
    <div>
      <div class="stat-label">Total Bonus Pool</div>
      <div class="stat-value" id="stat-bonus">—</div>
    </div>
    <div style="grid-column:1/-1">
      <div class="stat-label">Last Updated</div>
      <div class="stat-sub" id="stat-updated">Loading…</div>
    </div>
  </div>

  <form id="uploadForm">
    <div class="drop-zone" id="dropZone">
      <input type="file" id="fileInput" name="file" accept=".xlsx">
      <div class="drop-icon">📂</div>
      <div class="drop-text">
        <strong>Click to browse</strong> or drag &amp; drop<br>
        Learn export (.xlsx)
      </div>
      <div class="file-name" id="fileName"></div>
    </div>
    <button type="submit" id="uploadBtn" disabled>Upload &amp; Regenerate Data</button>
  </form>

  <div class="result" id="result"></div>
</div>

<script>
  // Load stats
  async function loadStats() {
    try {
      const r = await fetch('/api/stats');
      const d = await r.json();
      document.getElementById('stat-count').textContent = d.people_count ?? '0';
      document.getElementById('stat-bonus').textContent = d.total_bonus_usd != null
        ? '$' + d.total_bonus_usd.toLocaleString('en-US', {minimumFractionDigits: 2})
        : '$0.00';
      document.getElementById('stat-updated').textContent = d.last_modified
        ? new Date(d.last_modified).toLocaleString()
        : 'Never';
    } catch(e) {
      document.getElementById('stat-updated').textContent = 'Could not load stats';
    }
  }
  loadStats();

  // File input
  const input = document.getElementById('fileInput');
  const btn   = document.getElementById('uploadBtn');
  const nameEl = document.getElementById('fileName');
  const zone  = document.getElementById('dropZone');

  input.addEventListener('change', () => {
    if (input.files[0]) {
      nameEl.textContent = input.files[0].name;
      btn.disabled = false;
    }
  });
  zone.addEventListener('dragover',  e => { e.preventDefault(); zone.classList.add('dragover'); });
  zone.addEventListener('dragleave', () => zone.classList.remove('dragover'));
  zone.addEventListener('drop', e => {
    e.preventDefault(); zone.classList.remove('dragover');
    const file = e.dataTransfer.files[0];
    if (file && file.name.endsWith('.xlsx')) {
      const dt = new DataTransfer(); dt.items.add(file);
      input.files = dt.files;
      nameEl.textContent = file.name;
      btn.disabled = false;
    }
  });

  // Upload
  document.getElementById('uploadForm').addEventListener('submit', async (e) => {
    e.preventDefault();
    const resultEl = document.getElementById('result');
    resultEl.style.display = 'none';
    btn.disabled = true;
    btn.innerHTML = '<span class="spinner"></span>Processing…';

    const fd = new FormData();
    fd.append('file', input.files[0]);

    try {
      const r = await fetch('/upload', { method: 'POST', body: fd });
      const d = await r.json();

      resultEl.style.display = 'block';
      if (d.success) {
        resultEl.className = 'result success';
        let html = `<div class="result-title">✅ Updated — ${d.people_count} contributor(s) found</div>`;
        if (d.people && d.people.length) {
          html += '<table class="preview-table"><thead><tr><th>Name</th><th>Points</th><th>Bonus</th><th>Sessions</th></tr></thead><tbody>';
          d.people.forEach(p => {
            html += `<tr><td>${p.name}</td><td>${p.eligible_points}</td><td>$${p.bonus_usd}</td><td>${p.row_count}</td></tr>`;
          });
          html += '</tbody></table>';
        }
        resultEl.innerHTML = html;
        loadStats();
      } else {
        resultEl.className = 'result error';
        resultEl.innerHTML = `<div class="result-title">❌ Upload failed</div>${d.error ?? 'Unknown error'}`;
      }
    } catch(err) {
      resultEl.style.display = 'block';
      resultEl.className = 'result error';
      resultEl.innerHTML = `<div class="result-title">❌ Network error</div>${err.message}`;
    }

    btn.innerHTML = 'Upload &amp; Regenerate Data';
    btn.disabled = false;
  });
</script>
</body>
</html>"""


# ---------------------------------------------------------------------------
# Bot
# ---------------------------------------------------------------------------
async def _resolve_email(turn_context: TurnContext, teams_user_id: str) -> str:
    try:
        member = await TeamsInfo.get_member(turn_context, teams_user_id)
        return (member.email or member.user_principal_name or "").strip().lower()
    except Exception as e:
        print(f"[app] TeamsInfo.get_member failed: {e}", flush=True)
        return ""


class ContributionBot(ActivityHandler):

    async def on_message_activity(self, turn_context: TurnContext):
        user_message = (turn_context.activity.text or "").strip()
        if not user_message:
            return

        teams_user_id = turn_context.activity.from_property.id

        session = await get_session(teams_user_id)
        if session is None:
            user_email = await _resolve_email(turn_context, teams_user_id)
            session = await create_session(teams_user_id, user_email)
        else:
            user_email = session.get("email", "")
            if not user_email:
                user_email = await _resolve_email(turn_context, teams_user_id)
                if user_email:
                    session["email"] = user_email
                    await save_session(teams_user_id, session)

        history = session.get("history", [])

        await turn_context.send_activity(Activity(type="typing"))

        prefetched_context = get_contribution(user_email) if user_email else None

        try:
            reply, updated_history = await run_agent_async(
                user_message=user_message,
                user_email=user_email,
                history=history,
                prefetched_context=prefetched_context,
            )
        except Exception:
            traceback.print_exc()
            reply = "Sorry, I ran into an issue. Please try again in a moment."
            updated_history = history

        session["history"] = updated_history
        await save_session(teams_user_id, session)
        await turn_context.send_activity(MessageFactory.text(reply))

    async def on_members_added_activity(self, members_added, turn_context: TurnContext):
        for member in members_added:
            if member.id != turn_context.activity.recipient.id:
                await turn_context.send_activity(
                    MessageFactory.text(
                        "👋 Hi! I'm the **Campus Junior Training Contribution Bot**.\n\n"
                        "Ask me things like:\n"
                        "- `what's my bonus?`\n"
                        "- `how many points do I have?`\n"
                        "- `who has the most contributions?`\n"
                        "- `how does the program work?`"
                    )
                )


SETTINGS = BotFrameworkAdapterSettings(APP_ID, APP_PASSWORD, channel_auth_tenant=APP_TENANT)
ADAPTER  = BotFrameworkAdapter(SETTINGS)
BOT      = ContributionBot()


async def _on_error(context: TurnContext, error: Exception):
    print(f"[on_turn_error] {error}", flush=True)
    await context.send_activity("Sorry, something went wrong on my end.")


ADAPTER.on_turn_error = _on_error


# ---------------------------------------------------------------------------
# Route handlers
# ---------------------------------------------------------------------------
async def route_index(req: Request) -> Response:
    return Response(text=ADMIN_HTML, content_type="text/html")


async def route_upload(req: Request) -> Response:
    reader = await req.multipart()
    field  = await reader.next()

    if field is None or field.name != "file":
        return json_response({"success": False, "error": "No file field in form"}, status=400)

    filename = field.filename or ""
    if not filename.lower().endswith(".xlsx"):
        return json_response({"success": False, "error": "Only .xlsx files are accepted"}, status=400)

    # Save to data/latest_export.xlsx (fixed path so generate always finds it)
    save_path = UPLOAD_DIR / "latest_export.xlsx"
    UPLOAD_DIR.mkdir(exist_ok=True)

    with open(save_path, "wb") as f:
        while True:
            chunk = await field.read_chunk(65536)
            if not chunk:
                break
            f.write(chunk)

    result = generate_from_excel(save_path)
    return json_response(result)


async def route_stats(req: Request) -> Response:
    if not CSV_OUT.exists():
        return json_response({"people_count": 0, "total_bonus_usd": 0, "last_modified": None})

    people_count   = 0
    total_bonus    = 0.0
    with open(CSV_OUT, newline="", encoding="utf-8") as f:
        for row in csv.DictReader(f):
            people_count += 1
            try:
                total_bonus += float(row.get("bonus_usd", 0))
            except ValueError:
                pass

    mtime = CSV_OUT.stat().st_mtime
    last_modified = datetime.fromtimestamp(mtime, tz=timezone.utc).isoformat()

    return json_response({
        "people_count":   people_count,
        "total_bonus_usd": round(total_bonus, 2),
        "last_modified":  last_modified,
    })


async def route_messages(req: Request) -> Response:
    if "application/json" not in req.headers.get("Content-Type", ""):
        return Response(status=HTTPStatus.UNSUPPORTED_MEDIA_TYPE)

    body        = await req.json()
    activity    = Activity().deserialize(body)
    auth_header = req.headers.get("Authorization", "")

    response = await ADAPTER.process_activity(activity, auth_header, BOT.on_turn)
    if response:
        return json_response(data=response.body, status=response.status)
    return Response(status=HTTPStatus.OK)


async def route_health(req: Request) -> Response:
    people_count = 0
    if CSV_OUT.exists():
        with open(CSV_OUT, newline="", encoding="utf-8") as f:
            people_count = sum(1 for _ in csv.DictReader(f))
    return json_response({
        "status":        "ok",
        "people_cached": people_count,
        "checked_at":    datetime.now(tz=timezone.utc).isoformat(),
    })


# ---------------------------------------------------------------------------
# App
# ---------------------------------------------------------------------------
app = web.Application(client_max_size=50 * 1024 * 1024)   # 50 MB upload limit
app.router.add_get ("/",             route_index)
app.router.add_post("/upload",       route_upload)
app.router.add_get ("/api/stats",    route_stats)
app.router.add_post("/api/messages", route_messages)
app.router.add_get ("/api/health",   route_health)

if __name__ == "__main__":
    port = int(os.environ.get("PORT", 8000))
    print(f"Starting on http://0.0.0.0:{port}", flush=True)
    web.run_app(app, host="0.0.0.0", port=port)
