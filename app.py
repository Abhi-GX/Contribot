"""
app.py

Production server for Render Web Service.

Routes:
  GET  /                    → Admin UI  (login-protected if ADMIN_SECRET set)
  POST /admin/verify        → Verify admin secret, returns token
  POST /upload              → Accept .xlsx, regenerate data (admin auth required)
  GET  /api/stats           → Current CSV stats
  GET  /api/admin/list      → List visible admins
  POST /api/admin/add       → Add an admin email (admin auth required)
  POST /api/admin/remove    → Remove an admin email (admin auth required)
  POST /api/messages        → Teams bot webhook
  GET  /api/keys/status     → Key pool status (admin auth required)
  GET  /api/health          → Health check

Start:
    python app.py

Render config:
    Build:  pip install -r requirements.txt && python generate_csv.py
    Start:  python app.py
    Port:   auto-detected from PORT env var (Render sets it)
"""

from __future__ import annotations

import asyncio
import csv
import json
import os
import sys
import traceback
from datetime import datetime, timezone
from pathlib import Path

# ---------------------------------------------------------------------------
# Load .env file before anything else
# ---------------------------------------------------------------------------
def _load_env():
    env_file = Path(__file__).parent / ".env"
    if not env_file.exists():
        return
    for line in env_file.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        k, v = line.split("=", 1)
        os.environ.setdefault(k.strip(), v.strip())

_load_env()
from http import HTTPStatus

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
from generate_csv import generate_from_excel, CSV_OUT, SESSIONS_OUT
from session_store import (
    get_session, save_session, create_session,
    get_admins, add_admin, remove_admin, is_admin,
    save_data_to_redis, load_data_from_redis,
    save_excel_to_redis, load_excel_from_redis,
)

# ---------------------------------------------------------------------------
# Config
# ---------------------------------------------------------------------------
APP_ID       = os.environ.get("MicrosoftAppId",       "400dfdef-d35e-4561-95c1-57d11495c1de")
APP_PASSWORD = os.environ.get("MicrosoftAppPassword", "")
APP_TENANT   = os.environ.get("MicrosoftAppTenantId", "b41b72d0-4e9f-4c26-8a69-f949f367c91d")
ADMIN_SECRET = os.environ.get("ADMIN_SECRET", "")
UPLOAD_DIR   = ROOT / "data"


# ---------------------------------------------------------------------------
# Admin auth helper
# ---------------------------------------------------------------------------
def _check_admin_auth(req: Request) -> bool:
    if not ADMIN_SECRET:
        return True  # No secret configured — open access (dev mode)
    token = req.headers.get("X-Admin-Token", "")
    return token == ADMIN_SECRET


# ---------------------------------------------------------------------------
# Admin UI HTML
# ---------------------------------------------------------------------------
ADMIN_HTML = r"""<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="UTF-8">
  <meta name="viewport" content="width=device-width, initial-scale=1.0">
  <title>EPAM — Contribution Admin Portal</title>
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
      height: 64px;
      display: flex;
      align-items: center;
      justify-content: space-between;
      border-bottom: 3px solid #1565c0;
      flex-shrink: 0;
    }
    .header-left { display: flex; align-items: center; gap: 0.9rem; }
    .header-icon { font-size: 1.4rem; }
    .header-title { font-size: 1.05rem; font-weight: 700; letter-spacing: 0.01em; }
    .header-sub   { font-size: 0.75rem; color: #90a8d4; margin-top: 2px; }
    .header-badge {
      font-size: 0.68rem; font-weight: 700; letter-spacing: 0.08em;
      text-transform: uppercase; background: #1565c0;
      color: #cfe2ff; padding: 4px 12px; border-radius: 3px;
    }

    /* ── Login overlay ── */
    #loginOverlay {
      position: fixed; inset: 0; z-index: 1000;
      background: rgba(15, 25, 55, 0.88);
      display: flex; align-items: center; justify-content: center;
    }
    .login-card {
      background: #fff; border-radius: 6px; padding: 2.5rem 2rem;
      width: 100%; max-width: 380px;
      box-shadow: 0 8px 40px rgba(0,0,0,0.3);
      text-align: center;
    }
    .login-icon  { font-size: 2.5rem; margin-bottom: 0.75rem; }
    .login-title { font-size: 1.1rem; font-weight: 700; color: #1a2d5a; margin-bottom: 0.4rem; }
    .login-sub   { font-size: 0.82rem; color: #6b7a99; margin-bottom: 1.5rem; }
    .login-input {
      width: 100%; padding: 0.7rem 1rem; border: 1px solid #c5cfe0;
      border-radius: 4px; font-size: 0.9rem; color: #1a2540;
      outline: none; margin-bottom: 0.75rem;
      transition: border-color 0.15s;
    }
    .login-input:focus { border-color: #1565c0; }
    .btn-login {
      width: 100%; padding: 0.7rem 1rem;
      background: #1a2d5a; color: #fff;
      border: none; border-radius: 4px;
      font-size: 0.9rem; font-weight: 700;
      cursor: pointer; transition: background 0.15s;
    }
    .btn-login:hover { background: #1565c0; }
    .login-error {
      margin-top: 0.6rem; font-size: 0.8rem; color: #c0392b;
      display: none;
    }

    /* ── Main ── */
    main {
      flex: 1;
      display: flex;
      flex-direction: column;
      align-items: center;
      padding: 2.5rem 1.5rem;
      gap: 1.5rem;
    }

    /* ── Cards ── */
    .panel {
      background: #fff;
      border: 1px solid #d0d8e8;
      border-radius: 6px;
      box-shadow: 0 1px 6px rgba(0,0,0,0.07);
      width: 100%;
    }
    .panel-header {
      padding: 0.9rem 1.5rem;
      border-bottom: 1px solid #d0d8e8;
      background: #f5f7fb;
      border-radius: 6px 6px 0 0;
      display: flex;
      align-items: center;
      gap: 0.5rem;
    }
    .panel-header h2 {
      font-size: 0.78rem; font-weight: 700; letter-spacing: 0.07em;
      text-transform: uppercase; color: #3a4d78;
    }
    .panel-body { padding: 1.5rem; }

    /* ── Upload panel (big/prominent) ── */
    .upload-panel { max-width: 780px; }
    .upload-panel .panel-body { padding: 2rem 2rem; }

    .drop-zone {
      border: 2px dashed #b0bdd6;
      border-radius: 6px;
      padding: 3rem 2rem;
      text-align: center;
      cursor: pointer;
      transition: border-color 0.15s, background 0.15s;
      position: relative;
      background: #f9fafd;
      margin-bottom: 1.25rem;
    }
    .drop-zone:hover, .drop-zone.dragover {
      border-color: #1565c0;
      background: #eff5ff;
    }
    .drop-zone input[type=file] {
      position: absolute; inset: 0; opacity: 0;
      cursor: pointer; width: 100%; height: 100%;
    }
    .drop-icon { font-size: 2.5rem; margin-bottom: 0.6rem; }
    .drop-text { font-size: 0.9rem; color: #6b7a99; line-height: 1.6; }
    .drop-text strong { color: #1565c0; font-size: 1rem; }
    .file-selected {
      margin-top: 0.6rem; font-size: 0.85rem; color: #1a5c2e;
      font-weight: 600; display: none;
    }
    .btn-upload {
      width: 100%; padding: 0.85rem 1rem;
      background: #1a2d5a; color: #fff;
      border: none; border-radius: 4px;
      font-size: 0.95rem; font-weight: 700;
      letter-spacing: 0.02em; cursor: pointer;
      transition: background 0.15s;
    }
    .btn-upload:hover    { background: #1565c0; }
    .btn-upload:disabled { background: #9aaabf; cursor: not-allowed; }

    /* ── Upload result ── */
    .result {
      margin-top: 1.25rem; border-radius: 4px;
      padding: 0.9rem 1.1rem; font-size: 0.83rem; display: none;
    }
    .result.success { background: #f0faf4; border: 1px solid #4caf82; color: #1a5c2e; }
    .result.error   { background: #fff5f5; border: 1px solid #e07575; color: #7a1c1c; }
    .result-title { font-weight: 700; margin-bottom: 0.5rem; font-size: 0.88rem; }
    .preview-table { width: 100%; border-collapse: collapse; margin-top: 0.75rem; font-size: 0.8rem; }
    .preview-table thead tr { background: #e8f0e8; }
    .preview-table th {
      text-align: left; padding: 0.4rem 0.6rem;
      font-weight: 700; color: #1a5c2e; font-size: 0.72rem;
      text-transform: uppercase; letter-spacing: 0.04em;
      border-bottom: 2px solid #4caf82;
    }
    .preview-table td { padding: 0.4rem 0.6rem; border-bottom: 1px solid #d4ead8; }
    .preview-table tbody tr:last-child td { border-bottom: none; }

    /* ── Stats + Admin row ── */
    .two-col {
      max-width: 780px;
      display: grid;
      grid-template-columns: 1fr 1fr;
      gap: 1.25rem;
      width: 100%;
    }

    /* ── Stats ── */
    .stat-row { display: flex; gap: 0.75rem; margin-bottom: 1rem; }
    .stat-box {
      flex: 1; background: #f5f7fb; border: 1px solid #d0d8e8;
      border-radius: 4px; padding: 0.75rem 1rem;
    }
    .stat-label { font-size: 0.68rem; color: #6b7a99; text-transform: uppercase;
                  letter-spacing: 0.06em; font-weight: 600; }
    .stat-value { font-size: 1.4rem; font-weight: 700; color: #1a2d5a;
                  margin-top: 0.2rem; line-height: 1; }
    .stat-meta  { font-size: 0.7rem; color: #8896b3; margin-top: 0.3rem; }
    .info-row { display: flex; justify-content: space-between;
                font-size: 0.78rem; padding: 0.4rem 0;
                border-bottom: 1px solid #edf0f7; color: #3a4d78; }
    .info-row:last-child { border-bottom: none; }
    .info-row span:first-child { color: #6b7a99; font-weight: 500; }
    .info-row span:last-child  { font-weight: 600; }

    /* ── Admin management ── */
    .field-label {
      display: block; font-size: 0.78rem; font-weight: 600;
      color: #3a4d78; margin-bottom: 0.4rem; letter-spacing: 0.01em;
    }
    .field-input {
      width: 100%; padding: 0.55rem 0.8rem; border: 1px solid #c5cfe0;
      border-radius: 4px; font-size: 0.85rem; color: #1a2540;
      outline: none; margin-bottom: 0.65rem;
      transition: border-color 0.15s;
    }
    .field-input:focus { border-color: #1565c0; }
    .btn-primary {
      width: 100%; padding: 0.6rem 1rem;
      background: #1a2d5a; color: #fff;
      border: none; border-radius: 4px;
      font-size: 0.85rem; font-weight: 700;
      cursor: pointer; transition: background 0.15s;
    }
    .btn-primary:hover { background: #1565c0; }
    .admin-msg {
      margin-top: 0.5rem; font-size: 0.78rem; display: none; font-weight: 600;
    }
    .admin-msg.ok  { color: #1a5c2e; }
    .admin-msg.err { color: #7a1c1c; }

    /* ── Full-width section ── */
    .full-width { max-width: 780px; width: 100%; }

    /* ── Admins list ── */
    .admin-list { margin-top: 0.75rem; }
    .admin-chip {
      display: inline-flex; align-items: center; gap: 0.4rem;
      background: #eef5ff; border: 1px solid #b0c8f0;
      color: #1a2d5a; border-radius: 4px;
      padding: 0.3rem 0.7rem; font-size: 0.8rem; font-weight: 600;
      margin: 0.25rem 0.25rem 0 0;
    }
    .admin-chip .remove-btn {
      background: none; border: none; cursor: pointer;
      color: #6b7a99; font-size: 0.85rem; padding: 0;
      line-height: 1; transition: color 0.15s;
    }
    .admin-chip .remove-btn:hover { color: #c0392b; }
    .admin-chip.default-admin .remove-btn { display: none; }

    /* ── Key pool ── */
    .key-table { width: 100%; border-collapse: collapse; font-size: 0.82rem; }
    .key-table th {
      text-align: left; padding: 0.4rem 0.75rem;
      font-size: 0.7rem; font-weight: 700; letter-spacing: 0.05em;
      text-transform: uppercase; color: #3a4d78;
      background: #f5f7fb; border-bottom: 2px solid #d0d8e8;
    }
    .key-table td { padding: 0.5rem 0.75rem; border-bottom: 1px solid #edf0f7; }
    .key-table tbody tr:last-child td { border-bottom: none; }
    .badge {
      display: inline-block; padding: 2px 9px; border-radius: 10px;
      font-size: 0.7rem; font-weight: 700; letter-spacing: 0.04em;
      text-transform: uppercase;
    }
    .badge-active       { background: #e6f4ec; color: #1a6b3a; border: 1px solid #9dd4b2; }
    .badge-cooling      { background: #fff8e6; color: #7a5c00; border: 1px solid #f0c96a; }
    .badge-exhausted    { background: #fef0f0; color: #8b2020; border: 1px solid #e8a5a5; }
    .badge-valid        { background: #e6f4ec; color: #1a6b3a; border: 1px solid #9dd4b2; }
    .badge-invalid      { background: #fef0f0; color: #8b2020; border: 1px solid #e8a5a5; }
    .badge-rate-limited { background: #fff8e6; color: #7a5c00; border: 1px solid #f0c96a; }
    .badge-server-error { background: #f0f0ff; color: #3a2080; border: 1px solid #a5a5e8; }
    .badge-validating   { background: #f5f7fb; color: #3a4d78; border: 1px solid #d0d8e8; }
    .pool-refresh       { font-size: 0.7rem; color: #8896b3; margin-top: 0.5rem; text-align: right; }

    /* ── Spinner ── */
    .spinner {
      display: inline-block; width: 14px; height: 14px;
      border: 2px solid rgba(255,255,255,0.35); border-top-color: #fff;
      border-radius: 50%; animation: spin 0.65s linear infinite;
      margin-right: 0.4rem; vertical-align: middle;
    }
    @keyframes spin { to { transform: rotate(360deg); } }

    /* ── Footer ── */
    footer {
      text-align: center; padding: 1rem;
      font-size: 0.72rem; color: #8896b3;
      border-top: 1px solid #d0d8e8;
      background: #f5f7fb; flex-shrink: 0;
    }

    @media (max-width: 600px) {
      .two-col { grid-template-columns: 1fr; }
    }
  </style>
</head>
<body>

<!-- Login overlay (shown if ADMIN_SECRET is required and not yet authenticated) -->
<div id="loginOverlay" style="display:none">
  <div class="login-card">
    <div class="login-icon">🔐</div>
    <div class="login-title">Admin Access Required</div>
    <div class="login-sub">Enter the admin secret to continue.</div>
    <input type="password" id="secretInput" class="login-input" placeholder="Admin secret" autocomplete="off">
    <button class="btn-login" id="loginBtn" onclick="doLogin()">Sign In</button>
    <div class="login-error" id="loginError">Incorrect secret. Please try again.</div>
  </div>
</div>

<header>
  <div class="header-left">
    <div class="header-icon">🎓</div>
    <div>
      <div class="header-title">EPAM — Contribution Assistant</div>
      <div class="header-sub">EPAM Systems / Internal Administration</div>
    </div>
  </div>
  <div class="header-badge">Admin Portal</div>
</header>

<main>

  <!-- Upload section — full width, prominent -->
  <div class="panel upload-panel">
    <div class="panel-header">
      <span>📤</span>
      <h2>Upload New Learn Export</h2>
    </div>
    <div class="panel-body">
      <form id="uploadForm">
        <div class="drop-zone" id="dropZone">
          <input type="file" id="fileInput" name="file" accept=".xlsx">
          <div class="drop-icon">📊</div>
          <div class="drop-text">
            <strong>Click to select</strong> or drag and drop<br>
            Download the latest export from <em>learn.epam.com</em> and upload here.<br>
            Contribution data will be recalculated and applied immediately.
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

  <!-- Stats + Admin management row -->
  <div class="two-col">

    <!-- Data stats -->
    <div class="panel">
      <div class="panel-header"><span>📈</span><h2>Current Dataset</h2></div>
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
          <span>Data Source</span><span>Learn Export (.xlsx)</span>
        </div>
        <div class="info-row">
          <span>Eligible Format</span><span>Group Meeting w/ Contributor</span>
        </div>
        <div class="info-row">
          <span>Eligible Statuses</span><span>Submitted, Approved</span>
        </div>
        <div class="info-row">
          <span>Last Refreshed</span>
          <span id="stat-updated">Loading...</span>
        </div>
      </div>
    </div>

    <!-- Add admin -->
    <div class="panel">
      <div class="panel-header"><span>👤</span><h2>Add Administrator</h2></div>
      <div class="panel-body">
        <label class="field-label" for="adminEmailInput">EPAM Email Address</label>
        <input type="email" id="adminEmailInput" class="field-input"
               placeholder="firstname_lastname@epam.com" autocomplete="off">
        <button class="btn-primary" onclick="addAdmin()">Add Admin</button>
        <div class="admin-msg" id="addAdminMsg"></div>
      </div>
    </div>

  </div>

  <!-- Admins list -->
  <div class="panel full-width">
    <div class="panel-header"><span>🛡️</span><h2>Current Administrators</h2></div>
    <div class="panel-body">
      <div class="admin-list" id="adminList">
        <span style="color:#8896b3;font-size:0.82rem;">Loading...</span>
      </div>
    </div>
  </div>

  <!-- API Key pool -->
  <div class="panel full-width">
    <div class="panel-header" style="justify-content:space-between;align-items:center;">
      <div style="display:flex;align-items:center;gap:0.5rem;"><span>🔑</span><h2>API Key Pool Status</h2></div>
      <button class="btn-primary" id="validateBtn"
              style="font-size:0.78rem;padding:0.3rem 0.9rem;margin:0;"
              onclick="validateKeys()">Validate All Keys</button>
    </div>
    <div class="panel-body">
      <table class="key-table">
        <thead>
          <tr>
            <th>Key Slot</th>
            <th>State</th>
            <th>Failures</th>
            <th>Available In</th>
            <th>Live Check</th>
          </tr>
        </thead>
        <tbody id="keyPoolBody">
          <tr><td colspan="5" style="color:#8896b3;font-size:0.8rem;">Loading...</td></tr>
        </tbody>
      </table>
      <p class="pool-refresh" id="poolRefreshTime"></p>
    </div>
  </div>

</main>

<footer>
  EPAM Systems &mdash; Campus Junior Training &mdash; Internal Use Only
</footer>

<script>
  // ── Auth ──────────────────────────────────────────────────────────────────
  const SECRET_KEY = 'contribot_admin_token';

  function getToken() { return localStorage.getItem(SECRET_KEY) || ''; }

  function authHeaders() {
    const t = getToken();
    return t ? { 'X-Admin-Token': t } : {};
  }

  async function checkAuth() {
    const needsAuth = await fetch('/admin/auth-required').then(r => r.json());
    if (!needsAuth.required) return;
    const token = getToken();
    if (!token) { showLogin(); return; }
    const ok = await fetch('/admin/verify', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ secret: token })
    }).then(r => r.ok);
    if (!ok) { localStorage.removeItem(SECRET_KEY); showLogin(); }
  }

  function showLogin() { document.getElementById('loginOverlay').style.display = 'flex'; }
  function hideLogin() { document.getElementById('loginOverlay').style.display = 'none'; }

  async function doLogin() {
    const secret = document.getElementById('secretInput').value.trim();
    const errEl  = document.getElementById('loginError');
    errEl.style.display = 'none';
    const ok = await fetch('/admin/verify', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ secret })
    }).then(r => r.ok);
    if (ok) {
      localStorage.setItem(SECRET_KEY, secret);
      hideLogin();
      loadAll();
    } else {
      errEl.style.display = 'block';
    }
  }

  document.getElementById('secretInput').addEventListener('keydown', e => {
    if (e.key === 'Enter') doLogin();
  });

  // ── Stats ─────────────────────────────────────────────────────────────────
  async function loadStats() {
    try {
      const r = await fetch('/api/stats', { headers: authHeaders() });
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

  // ── Admins ────────────────────────────────────────────────────────────────
  async function loadAdmins() {
    try {
      const d = await fetch('/api/admin/list', { headers: authHeaders() }).then(r => r.json());
      const el = document.getElementById('adminList');
      if (!d.admins || !d.admins.length) {
        el.innerHTML = '<span style="color:#8896b3;font-size:0.82rem;">No admins configured.</span>';
        return;
      }
      const defaultAdmins = new Set(['vishal_bhandari@epam.com']);
      el.innerHTML = d.admins.map(email => {
        const isDefault = defaultAdmins.has(email.toLowerCase());
        return `<span class="admin-chip ${isDefault ? 'default-admin' : ''}">
          ${email}
          <button class="remove-btn" onclick="removeAdmin('${email}')" title="Remove">✕</button>
        </span>`;
      }).join('');
    } catch(e) {}
  }

  async function addAdmin() {
    const email  = document.getElementById('adminEmailInput').value.trim().toLowerCase();
    const msgEl  = document.getElementById('addAdminMsg');
    msgEl.style.display = 'none';
    if (!email || !email.includes('@')) {
      msgEl.textContent = 'Please enter a valid email address.';
      msgEl.className = 'admin-msg err';
      msgEl.style.display = 'block';
      return;
    }
    try {
      const d = await fetch('/api/admin/add', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json', ...authHeaders() },
        body: JSON.stringify({ email })
      }).then(r => r.json());
      if (d.success) {
        msgEl.textContent = `${email} added as admin.`;
        msgEl.className = 'admin-msg ok';
        document.getElementById('adminEmailInput').value = '';
        loadAdmins();
      } else {
        msgEl.textContent = d.error || 'Failed to add admin.';
        msgEl.className = 'admin-msg err';
      }
    } catch(e) {
      msgEl.textContent = 'Network error.';
      msgEl.className = 'admin-msg err';
    }
    msgEl.style.display = 'block';
  }

  async function removeAdmin(email) {
    if (!confirm(`Remove ${email} from admins?`)) return;
    try {
      const d = await fetch('/api/admin/remove', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json', ...authHeaders() },
        body: JSON.stringify({ email })
      }).then(r => r.json());
      if (d.success) loadAdmins();
      else alert(d.message || 'Could not remove admin.');
    } catch(e) { alert('Network error.'); }
  }

  // ── Key pool ──────────────────────────────────────────────────────────────
  // Stores the last validation results by key_index so loadKeyPool() can
  // preserve them across the 30s auto-refresh.
  let _lastValidation = {};

  async function loadKeyPool() {
    try {
      const r = await fetch('/api/keys/status', { headers: authHeaders() });
      const d = await r.json();
      const tbody = document.getElementById('keyPoolBody');
      if (!d.keys || !d.keys.length) {
        tbody.innerHTML = '<tr><td colspan="5" style="color:#8896b3">No key pool configured</td></tr>';
        return;
      }
      tbody.innerHTML = d.keys.map(k => {
        const cls   = k.state === 'active' ? 'badge-active'
                    : k.state === 'cooling' ? 'badge-cooling' : 'badge-exhausted';
        const avail = k.available_in_seconds != null ? fmtDur(k.available_in_seconds) : '—';
        const vr    = _lastValidation[k.key_index];
        const liveCell = vr
          ? `<span class="badge badge-${vr.vstatus.replace('_','-')}" title="${vr.detail || ''}">${vr.vstatus.replace('_',' ')}</span>`
          : '<span style="color:#8896b3;font-size:0.75rem;">—</span>';
        return `<tr>
          <td><strong>Key ${k.key_index}</strong></td>
          <td><span class="badge ${cls}">${k.state}</span></td>
          <td>${k.fail_count}</td>
          <td>${avail}</td>
          <td>${liveCell}</td>
        </tr>`;
      }).join('');
      document.getElementById('poolRefreshTime').textContent =
        'Last refreshed: ' + new Date().toLocaleTimeString('en-US');
    } catch(e) {
      document.getElementById('keyPoolBody').innerHTML =
        '<tr><td colspan="5" style="color:#8896b3">Could not load pool status</td></tr>';
    }
  }

  async function validateKeys() {
    const btn = document.getElementById('validateBtn');
    btn.disabled = true;
    btn.innerHTML = '<span class="spinner"></span>Validating...';
    _lastValidation = {};
    // Show "checking..." for every row while waiting
    document.querySelectorAll('#keyPoolBody tr').forEach(tr => {
      const cells = tr.querySelectorAll('td');
      if (cells.length >= 5)
        cells[4].innerHTML = '<span class="badge badge-validating">checking</span>';
    });
    try {
      const r = await fetch('/api/keys/validate', {
        method: 'POST', headers: authHeaders()
      });
      const d = await r.json();
      if (!d.keys) throw new Error(d.error || 'Validation failed');
      d.keys.forEach(k => {
        _lastValidation[k.key_index] = {
          vstatus: k.validation_status,
          detail:  k.detail || '',
          preview: k.key_preview || '',
        };
      });
      await loadKeyPool();
      document.getElementById('poolRefreshTime').textContent =
        'Validated at ' + new Date().toLocaleTimeString('en-US');
    } catch(e) {
      document.getElementById('poolRefreshTime').textContent =
        'Validation error: ' + e.message;
    } finally {
      btn.disabled = false;
      btn.innerHTML = 'Validate All Keys';
    }
  }

  function fmtDur(sec) {
    if (sec < 60)   return Math.round(sec) + 's';
    if (sec < 3600) return Math.round(sec / 60) + 'm ' + Math.round(sec % 60) + 's';
    const h = Math.floor(sec / 3600), m = Math.round((sec % 3600) / 60);
    return h + 'h ' + m + 'm';
  }

  // ── Upload form ───────────────────────────────────────────────────────────
  const input  = document.getElementById('fileInput');
  const btn    = document.getElementById('uploadBtn');
  const nameEl = document.getElementById('fileName');
  const zone   = document.getElementById('dropZone');

  function setFile(file) {
    nameEl.textContent = '✔ Selected: ' + file.name;
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
      const r = await fetch('/upload', { method: 'POST', body: fd, headers: authHeaders() });
      const d = await r.json();
      resultEl.style.display = 'block';
      if (d.success) {
        resultEl.className = 'result success';
        let html = `<div class="result-title">✔ Data updated — ${d.people_count} contributor(s) found</div>`;
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

  // ── Init ──────────────────────────────────────────────────────────────────
  function loadAll() {
    loadStats();
    loadAdmins();
    loadKeyPool();
  }

  checkAuth().then(() => {
    const overlay = document.getElementById('loginOverlay');
    if (overlay.style.display !== 'flex') loadAll();
  });

  setInterval(loadKeyPool, 30000);
</script>
</body>
</html>"""


# ---------------------------------------------------------------------------
# Startup — always restore from Redis when data exists there.
# This overwrites whatever the build step generated from the repo's Excel,
# ensuring the admin's last upload survives redeployments.
# ---------------------------------------------------------------------------
async def on_startup(app_instance):
    sessions_path = ROOT / "data" / "sessions.json"
    print("[startup] Checking Redis for persisted data...", flush=True)
    try:
        totals_csv, sessions_json = await load_data_from_redis()
        if totals_csv:
            CSV_OUT.parent.mkdir(exist_ok=True)
            CSV_OUT.write_text(totals_csv, encoding="utf-8")
            print(f"[startup] Restored totals.csv from Redis ({len(totals_csv)} bytes) — overrides build", flush=True)
        if sessions_json:
            sessions_path.write_text(sessions_json, encoding="utf-8")
            print(f"[startup] Restored sessions.json from Redis ({len(sessions_json)} bytes) — overrides build", flush=True)
        if not totals_csv and not sessions_json:
            print("[startup] No data in Redis yet — using file from build step.", flush=True)

        # Also restore the original Excel so it's available if admin needs to re-run generate
        excel_path = UPLOAD_DIR / "latest_export.xlsx"
        if not excel_path.exists():
            excel_bytes = await load_excel_from_redis()
            if excel_bytes:
                UPLOAD_DIR.mkdir(exist_ok=True)
                excel_path.write_bytes(excel_bytes)
                print(f"[startup] Restored latest_export.xlsx from Redis ({len(excel_bytes):,} bytes)", flush=True)
    except Exception as e:
        print(f"[startup] Redis restore failed (non-fatal): {e}", flush=True)

    # Seed default admin list
    try:
        admins = await get_admins()
        print(f"[startup] Admins: {admins}", flush=True)
    except Exception as e:
        print(f"[startup] Could not seed admins: {e}", flush=True)


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
        user_is_admin = await is_admin(user_email) if user_email else False

        try:
            reply, updated_history = await run_agent_async(
                user_message=user_message,
                user_email=user_email,
                history=history,
                prefetched_context=prefetched_context,
                is_admin=user_is_admin,
            )
        except Exception:
            traceback.print_exc()
            reply = "Sorry, I ran into an issue. Please try again in a moment."
            updated_history = history

        session["history"] = updated_history
        asyncio.ensure_future(save_session(teams_user_id, session))
        await turn_context.send_activity(MessageFactory.text(reply))

    async def on_members_added_activity(self, members_added, turn_context: TurnContext):
        for member in members_added:
            if member.id != turn_context.activity.recipient.id:
                await turn_context.send_activity(
                    MessageFactory.text(
                        "👋 Hi! I'm **Kitrak Campus — Contribution Assistant**.\n\n"
                        "Ask me things like:\n"
                        "- `what's my bonus?`\n"
                        "- `how many points do I have?`\n"
                        "- `what learning paths did I contribute to?`\n"
                        "- `which categories did I work in?`\n"
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


async def route_auth_required(req: Request) -> Response:
    return json_response({"required": bool(ADMIN_SECRET)})


async def route_admin_verify(req: Request) -> Response:
    if not ADMIN_SECRET:
        return json_response({"ok": True})
    try:
        data = await req.json()
        secret = data.get("secret", "")
    except Exception:
        return json_response({"ok": False}, status=400)
    if secret == ADMIN_SECRET:
        return json_response({"ok": True})
    return json_response({"ok": False}, status=401)


async def route_upload(req: Request) -> Response:
    if not _check_admin_auth(req):
        return json_response({"success": False, "error": "Unauthorized"}, status=401)

    reader = await req.multipart()
    field  = await reader.next()
    if field is None or field.name != "file":
        return json_response({"success": False, "error": "No file field in form"}, status=400)

    filename = field.filename or ""
    if not filename.lower().endswith(".xlsx"):
        return json_response({"success": False, "error": "Only .xlsx files are accepted"}, status=400)

    save_path = UPLOAD_DIR / "latest_export.xlsx"
    UPLOAD_DIR.mkdir(exist_ok=True)
    with open(save_path, "wb") as f:
        while True:
            chunk = await field.read_chunk(65536)
            if not chunk:
                break
            f.write(chunk)

    result = generate_from_excel(save_path)

    if result["success"]:
        # Persist everything to Redis — survives redeployments (single latest version)
        try:
            totals_csv    = CSV_OUT.read_text(encoding="utf-8") if CSV_OUT.exists() else ""
            sessions_path = ROOT / "data" / "sessions.json"
            sessions_json = sessions_path.read_text(encoding="utf-8") if sessions_path.exists() else "{}"
            await save_data_to_redis(totals_csv, sessions_json)
            await save_excel_to_redis(save_path.read_bytes())
        except Exception as e:
            print(f"[upload] Redis persist failed (non-fatal): {e}", flush=True)

    return json_response(result)


async def route_stats(req: Request) -> Response:
    if not CSV_OUT.exists():
        return json_response({"people_count": 0, "total_bonus_usd": 0, "last_modified": None})

    people_count = 0
    total_bonus  = 0.0
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
        "people_count":    people_count,
        "total_bonus_usd": round(total_bonus, 2),
        "last_modified":   last_modified,
    })


async def route_admin_list(req: Request) -> Response:
    admins = await get_admins()
    return json_response({"admins": admins})


async def route_admin_add(req: Request) -> Response:
    if not _check_admin_auth(req):
        return json_response({"success": False, "error": "Unauthorized"}, status=401)
    try:
        data  = await req.json()
        email = data.get("email", "").strip().lower()
    except Exception:
        return json_response({"success": False, "error": "Invalid request"}, status=400)

    if not email or "@" not in email:
        return json_response({"success": False, "error": "Invalid email address"}, status=400)

    success = await add_admin(email)
    admins  = await get_admins()
    return json_response({"success": success, "admins": admins})


async def route_admin_remove(req: Request) -> Response:
    if not _check_admin_auth(req):
        return json_response({"success": False, "error": "Unauthorized"}, status=401)
    try:
        data  = await req.json()
        email = data.get("email", "").strip().lower()
    except Exception:
        return json_response({"success": False, "error": "Invalid request"}, status=400)

    success, message = await remove_admin(email)
    admins = await get_admins()
    return json_response({"success": success, "message": message, "admins": admins})


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


async def route_keys_status(req: Request) -> Response:
    if not _check_admin_auth(req):
        return json_response({"error": "Unauthorized"}, status=401)
    from agent.key_pool import get_pool
    try:
        return json_response({"keys": get_pool().status()})
    except Exception as e:
        return json_response({"error": str(e)}, status=500)


async def route_keys_validate(req: Request) -> Response:
    if not _check_admin_auth(req):
        return json_response({"error": "Unauthorized"}, status=401)
    from agent.key_pool import get_pool
    try:
        results = await get_pool().validate_all_keys()
        return json_response({"keys": results})
    except Exception as e:
        return json_response({"error": str(e)}, status=500)


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
app = web.Application(client_max_size=50 * 1024 * 1024)
app.on_startup.append(on_startup)

app.router.add_get ("/",                    route_index)
app.router.add_get ("/admin/auth-required", route_auth_required)
app.router.add_post("/admin/verify",        route_admin_verify)
app.router.add_post("/upload",              route_upload)
app.router.add_get ("/api/stats",           route_stats)
app.router.add_get ("/api/admin/list",      route_admin_list)
app.router.add_post("/api/admin/add",       route_admin_add)
app.router.add_post("/api/admin/remove",    route_admin_remove)
app.router.add_post("/api/messages",        route_messages)
app.router.add_get ("/api/keys/status",     route_keys_status)
app.router.add_post("/api/keys/validate",   route_keys_validate)
app.router.add_get ("/api/health",          route_health)

if __name__ == "__main__":
    port = int(os.environ.get("PORT", 8000))
    print(f"Starting on http://0.0.0.0:{port}", flush=True)
    web.run_app(app, host="0.0.0.0", port=port)
