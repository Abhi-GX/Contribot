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
  <title>Contribution Bot — Administration</title>
  <style>
    *, *::before, *::after { box-sizing: border-box; margin: 0; padding: 0; }

    body {
      font-family: "Segoe UI", -apple-system, BlinkMacSystemFont, Roboto, sans-serif;
      background: #eef1f6;
      color: #1a2540;
      min-height: 100vh;
      display: flex;
      flex-direction: column;
    }

    /* ── Header ── */
    header {
      background: #1a2d5a;
      color: #fff;
      padding: 0 2.5rem;
      height: 62px;
      display: flex;
      align-items: center;
      justify-content: space-between;
      border-bottom: 3px solid #1565c0;
      flex-shrink: 0;
    }
    .header-left  { display: flex; align-items: center; gap: 0.75rem; }
    .header-title { font-size: 1rem; font-weight: 600; letter-spacing: 0.01em; }
    .header-sub   { font-size: 0.78rem; color: #90a8d4; margin-top: 1px; }
    .header-badge {
      font-size: 0.7rem; font-weight: 600; letter-spacing: 0.06em;
      text-transform: uppercase; background: #1565c0;
      color: #cfe2ff; padding: 3px 10px; border-radius: 3px;
    }

    /* ── Main ── */
    main {
      flex: 1;
      display: flex;
      align-items: flex-start;
      justify-content: center;
      padding: 2.5rem 1.5rem;
    }

    .layout {
      width: 100%;
      max-width: 860px;
      display: grid;
      grid-template-columns: 1fr 1fr;
      gap: 1.5rem;
      align-items: start;
    }

    /* ── Cards ── */
    .panel {
      background: #fff;
      border: 1px solid #d0d8e8;
      border-radius: 4px;
      box-shadow: 0 1px 4px rgba(0,0,0,0.06);
    }
    .panel-header {
      padding: 0.85rem 1.25rem;
      border-bottom: 1px solid #d0d8e8;
      background: #f5f7fb;
      border-radius: 4px 4px 0 0;
    }
    .panel-header h2 {
      font-size: 0.8rem; font-weight: 700; letter-spacing: 0.07em;
      text-transform: uppercase; color: #3a4d78;
    }
    .panel-body { padding: 1.25rem; }

    /* ── Stats ── */
    .stat-row {
      display: flex; gap: 1rem; margin-bottom: 1rem;
    }
    .stat-box {
      flex: 1; background: #f5f7fb; border: 1px solid #d0d8e8;
      border-radius: 3px; padding: 0.75rem 1rem;
    }
    .stat-label { font-size: 0.7rem; color: #6b7a99; text-transform: uppercase;
                  letter-spacing: 0.06em; font-weight: 600; }
    .stat-value { font-size: 1.5rem; font-weight: 700; color: #1a2d5a;
                  margin-top: 0.2rem; line-height: 1; }
    .stat-meta  { font-size: 0.72rem; color: #8896b3; margin-top: 0.3rem; }

    .info-row { display: flex; justify-content: space-between;
                font-size: 0.8rem; padding: 0.45rem 0;
                border-bottom: 1px solid #edf0f7; color: #3a4d78; }
    .info-row:last-child { border-bottom: none; }
    .info-row span:first-child { color: #6b7a99; font-weight: 500; }
    .info-row span:last-child  { font-weight: 600; }

    /* ── Upload form ── */
    .field-label {
      display: block; font-size: 0.78rem; font-weight: 600;
      color: #3a4d78; margin-bottom: 0.45rem; letter-spacing: 0.01em;
    }
    .field-note {
      font-size: 0.72rem; color: #8896b3; margin-bottom: 1rem;
    }

    .drop-zone {
      border: 2px dashed #b0bdd6;
      border-radius: 3px;
      padding: 2rem 1.25rem;
      text-align: center;
      cursor: pointer;
      transition: border-color 0.15s, background 0.15s;
      position: relative;
      margin-bottom: 1rem;
      background: #f9fafd;
    }
    .drop-zone:hover, .drop-zone.dragover {
      border-color: #1565c0;
      background: #eff5ff;
    }
    .drop-zone input[type=file] {
      position: absolute; inset: 0; opacity: 0;
      cursor: pointer; width: 100%; height: 100%;
    }
    .drop-icon { font-size: 1.6rem; margin-bottom: 0.4rem; }
    .drop-text { font-size: 0.82rem; color: #6b7a99; line-height: 1.5; }
    .drop-text strong { color: #1565c0; }
    .file-selected {
      margin-top: 0.5rem; font-size: 0.8rem; color: #1a5c2e;
      font-weight: 600; display: none;
    }

    .btn-upload {
      width: 100%; padding: 0.65rem 1rem;
      background: #1a2d5a; color: #fff;
      border: none; border-radius: 3px;
      font-size: 0.875rem; font-weight: 600;
      letter-spacing: 0.02em; cursor: pointer;
      transition: background 0.15s;
    }
    .btn-upload:hover    { background: #1565c0; }
    .btn-upload:disabled { background: #9aaabf; cursor: not-allowed; }

    /* ── Result ── */
    .result {
      margin-top: 1rem; border-radius: 3px;
      padding: 0.85rem 1rem; font-size: 0.82rem; display: none;
    }
    .result.success {
      background: #f0faf4; border: 1px solid #4caf82; color: #1a5c2e;
    }
    .result.error {
      background: #fff5f5; border: 1px solid #e07575; color: #7a1c1c;
    }
    .result-title { font-weight: 700; margin-bottom: 0.5rem; font-size: 0.85rem; }

    .preview-table {
      width: 100%; border-collapse: collapse; margin-top: 0.75rem;
      font-size: 0.78rem;
    }
    .preview-table thead tr { background: #e8f0e8; }
    .preview-table th {
      text-align: left; padding: 0.4rem 0.6rem;
      font-weight: 700; color: #1a5c2e; font-size: 0.72rem;
      text-transform: uppercase; letter-spacing: 0.04em;
      border-bottom: 2px solid #4caf82;
    }
    .preview-table td {
      padding: 0.4rem 0.6rem; border-bottom: 1px solid #d4ead8; color: #1a2540;
    }
    .preview-table tbody tr:last-child td { border-bottom: none; }

    /* ── Spinner ── */
    .spinner {
      display: inline-block; width: 13px; height: 13px;
      border: 2px solid rgba(255,255,255,0.35);
      border-top-color: #fff;
      border-radius: 50%;
      animation: spin 0.65s linear infinite;
      margin-right: 0.4rem; vertical-align: middle;
    }
    @keyframes spin { to { transform: rotate(360deg); } }

    /* ── Footer ── */
    footer {
      text-align: center; padding: 1rem;
      font-size: 0.72rem; color: #8896b3;
      border-top: 1px solid #d0d8e8;
      background: #f5f7fb;
      flex-shrink: 0;
    }
  </style>
</head>
<body>

<header>
  <div class="header-left">
    <div>
      <div class="header-title">Campus Junior Training — Contribution Bot</div>
      <div class="header-sub">EPAM Systems / Internal Administration</div>
    </div>
  </div>
  <div class="header-badge">Admin Portal</div>
</header>

<main>
  <div class="layout">

    <!-- Left: Current Data -->
    <div>
      <div class="panel">
        <div class="panel-header"><h2>Current Dataset</h2></div>
        <div class="panel-body">
          <div class="stat-row">
            <div class="stat-box">
              <div class="stat-label">Contributors</div>
              <div class="stat-value" id="stat-count">—</div>
              <div class="stat-meta">Eligible records</div>
            </div>
            <div class="stat-box">
              <div class="stat-label">Total Bonus Pool</div>
              <div class="stat-value" id="stat-bonus">—</div>
              <div class="stat-meta">At $15 / point</div>
            </div>
          </div>
          <div class="info-row">
            <span>Data Source</span>
            <span>Learn Export (.xlsx)</span>
          </div>
          <div class="info-row">
            <span>Eligible Format</span>
            <span>Group Meeting with Contributor</span>
          </div>
          <div class="info-row">
            <span>Eligible Statuses</span>
            <span>Submitted, Approved</span>
          </div>
          <div class="info-row">
            <span>Last Refreshed</span>
            <span id="stat-updated">Loading...</span>
          </div>
        </div>
      </div>
    </div>

    <!-- Right: Upload -->
    <div>
      <div class="panel">
        <div class="panel-header"><h2>Upload New Export</h2></div>
        <div class="panel-body">
          <label class="field-label">Learn Export File</label>
          <p class="field-note">
            Download the latest export from learn.epam.com and upload it here.
            The contribution data will be recalculated and applied immediately.
          </p>

          <form id="uploadForm">
            <div class="drop-zone" id="dropZone">
              <input type="file" id="fileInput" name="file" accept=".xlsx">
              <div class="drop-icon">&#128196;</div>
              <div class="drop-text">
                <strong>Click to select</strong> or drag and drop<br>
                Microsoft Excel Workbook (.xlsx)
              </div>
              <div class="file-selected" id="fileName"></div>
            </div>
            <button type="submit" class="btn-upload" id="uploadBtn" disabled>
              Upload and Regenerate Data
            </button>
          </form>

          <div class="result" id="result"></div>
        </div>
      </div>
    </div>

  </div>
</main>

<footer>
  EPAM Systems &mdash; Campus Junior Training &mdash; Internal Use Only
</footer>

<script>
  async function loadStats() {
    try {
      const r = await fetch('/api/stats');
      const d = await r.json();
      document.getElementById('stat-count').textContent = d.people_count ?? '0';
      document.getElementById('stat-bonus').textContent = d.total_bonus_usd != null
        ? '$' + Number(d.total_bonus_usd).toLocaleString('en-US', {minimumFractionDigits: 2})
        : '$0.00';
      document.getElementById('stat-updated').textContent = d.last_modified
        ? new Date(d.last_modified).toLocaleString('en-US', {dateStyle:'medium', timeStyle:'short'})
        : 'Not available';
    } catch(e) {
      document.getElementById('stat-updated').textContent = 'Could not load';
    }
  }
  loadStats();

  const input  = document.getElementById('fileInput');
  const btn    = document.getElementById('uploadBtn');
  const nameEl = document.getElementById('fileName');
  const zone   = document.getElementById('dropZone');

  function setFile(file) {
    nameEl.textContent = 'Selected: ' + file.name;
    nameEl.style.display = 'block';
    btn.disabled = false;
  }

  input.addEventListener('change', () => { if (input.files[0]) setFile(input.files[0]); });

  zone.addEventListener('dragover',  e => { e.preventDefault(); zone.classList.add('dragover'); });
  zone.addEventListener('dragleave', () => zone.classList.remove('dragover'));
  zone.addEventListener('drop', e => {
    e.preventDefault(); zone.classList.remove('dragover');
    const file = e.dataTransfer.files[0];
    if (file && file.name.endsWith('.xlsx')) {
      const dt = new DataTransfer(); dt.items.add(file);
      input.files = dt.files;
      setFile(file);
    }
  });

  document.getElementById('uploadForm').addEventListener('submit', async (e) => {
    e.preventDefault();
    const resultEl = document.getElementById('result');
    resultEl.style.display = 'none';
    btn.disabled = true;
    btn.innerHTML = '<span class="spinner"></span>Processing...';

    const fd = new FormData();
    fd.append('file', input.files[0]);

    try {
      const r = await fetch('/upload', { method: 'POST', body: fd });
      const d = await r.json();
      resultEl.style.display = 'block';

      if (d.success) {
        resultEl.className = 'result success';
        let html = `<div class="result-title">Data updated successfully &mdash; ${d.people_count} contributor(s) found</div>`;
        if (d.people && d.people.length) {
          html += '<table class="preview-table"><thead><tr><th>Name</th><th>Points</th><th>Bonus</th><th>Sessions</th></tr></thead><tbody>';
          d.people.forEach(p => {
            html += `<tr><td>${p.name}</td><td>${p.eligible_points}</td><td>$${Number(p.bonus_usd).toFixed(2)}</td><td>${p.row_count}</td></tr>`;
          });
          html += '</tbody></table>';
        }
        resultEl.innerHTML = html;
        loadStats();
      } else {
        resultEl.className = 'result error';
        resultEl.innerHTML = `<div class="result-title">Upload Failed</div>${d.error ?? 'An unknown error occurred.'}`;
      }
    } catch(err) {
      resultEl.style.display = 'block';
      resultEl.className = 'result error';
      resultEl.innerHTML = `<div class="result-title">Network Error</div>${err.message}`;
    }

    btn.innerHTML = 'Upload and Regenerate Data';
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
