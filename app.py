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
    save_api_keys_to_redis, load_api_keys_from_redis,
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
  <title>Contribot — Admin Portal</title>
  <link rel="preconnect" href="https://fonts.googleapis.com">
  <link rel="preconnect" href="https://fonts.gstatic.com" crossorigin>
  <link href="https://fonts.googleapis.com/css2?family=Inter:wght@400;500;600;700;800&display=swap" rel="stylesheet">
  <style>
    *, *::before, *::after { box-sizing: border-box; margin: 0; padding: 0; }

    :root {
      --bg:        #f0f2f5;
      --card:      #ffffff;
      --border:    #e5e7eb;
      --border-2:  #d1d5db;
      --navy:      #0d1b2a;
      --navy-2:    #152336;
      --blue:      #2563eb;
      --blue-lt:   #eff6ff;
      --blue-mid:  #dbeafe;
      --text:      #111827;
      --text-2:    #374151;
      --text-3:    #6b7280;
      --text-4:    #9ca3af;
      --green:     #059669;
      --green-lt:  #ecfdf5;
      --yellow:    #d97706;
      --yellow-lt: #fffbeb;
      --red:       #dc2626;
      --red-lt:    #fef2f2;
      --purple:    #7c3aed;
      --purple-lt: #f5f3ff;
      --radius:    10px;
      --shadow:    0 1px 3px rgba(0,0,0,0.08), 0 1px 2px rgba(0,0,0,0.05);
      --shadow-md: 0 4px 12px rgba(0,0,0,0.08), 0 2px 4px rgba(0,0,0,0.05);
    }

    body {
      font-family: 'Inter', -apple-system, BlinkMacSystemFont, 'Segoe UI', sans-serif;
      background: var(--bg);
      color: var(--text);
      min-height: 100vh;
      font-size: 14px;
      line-height: 1.5;
    }

    /* ── Login overlay ── */
    #loginOverlay {
      position: fixed; inset: 0; z-index: 9999;
      background: rgba(13,27,42,0.9);
      backdrop-filter: blur(6px);
      display: flex; align-items: center; justify-content: center;
    }
    .login-card {
      background: #fff; border-radius: 16px; padding: 2.75rem 2.25rem;
      width: 100%; max-width: 400px;
      box-shadow: 0 24px 64px rgba(0,0,0,0.4);
      text-align: center;
    }
    .login-logo {
      width: 56px; height: 56px; border-radius: 14px;
      background: linear-gradient(135deg, #0d1b2a 0%, #2563eb 100%);
      display: inline-flex; align-items: center; justify-content: center;
      font-size: 1.6rem; margin-bottom: 1.25rem;
      box-shadow: 0 4px 14px rgba(37,99,235,0.4);
    }
    .login-title { font-size: 1.3rem; font-weight: 700; color: var(--text); margin-bottom: 0.3rem; }
    .login-sub   { font-size: 0.82rem; color: var(--text-3); margin-bottom: 2rem; }
    .login-input {
      width: 100%; padding: 0.72rem 1rem; border: 1.5px solid var(--border-2);
      border-radius: 9px; font-size: 0.9rem; font-family: inherit;
      color: var(--text); outline: none;
      transition: border-color 0.15s, box-shadow 0.15s;
      margin-bottom: 0.85rem;
    }
    .login-input:focus {
      border-color: var(--blue);
      box-shadow: 0 0 0 3px rgba(37,99,235,0.12);
    }
    .login-btn {
      width: 100%; padding: 0.78rem;
      background: linear-gradient(135deg, #0d1b2a 0%, #2563eb 100%);
      color: #fff; border: none; border-radius: 9px;
      font-size: 0.9rem; font-weight: 600; font-family: inherit;
      cursor: pointer; transition: opacity 0.15s; letter-spacing: 0.01em;
    }
    .login-btn:hover { opacity: 0.87; }
    .login-error { margin-top: 0.75rem; font-size: 0.8rem; color: var(--red); display: none; }

    /* ── Topbar ── */
    .topbar {
      position: sticky; top: 0; z-index: 100;
      background: var(--navy);
      height: 60px;
      display: flex; align-items: center; justify-content: space-between;
      padding: 0 2rem;
      border-bottom: 1px solid rgba(255,255,255,0.07);
      box-shadow: 0 1px 12px rgba(0,0,0,0.3);
    }
    .topbar-left { display: flex; align-items: center; gap: 0.9rem; }
    .topbar-logo {
      width: 36px; height: 36px; border-radius: 9px;
      background: linear-gradient(135deg, #1d4ed8, #3b82f6);
      display: flex; align-items: center; justify-content: center;
      font-size: 1.1rem; flex-shrink: 0;
      box-shadow: 0 2px 8px rgba(37,99,235,0.4);
    }
    .topbar-brand { display: flex; flex-direction: column; line-height: 1.2; }
    .topbar-name  { font-size: 0.92rem; font-weight: 700; color: #f1f5f9; }
    .topbar-sub   { font-size: 0.68rem; color: #64748b; }
    .topbar-right { display: flex; align-items: center; gap: 0.75rem; }
    .topbar-badge {
      background: rgba(37,99,235,0.18); color: #93c5fd;
      border: 1px solid rgba(37,99,235,0.35);
      font-size: 0.65rem; font-weight: 700; letter-spacing: 0.1em;
      text-transform: uppercase; padding: 4px 11px; border-radius: 5px;
    }

    /* ── Page wrapper ── */
    .page {
      max-width: 1100px;
      margin: 0 auto;
      padding: 2rem 1.5rem 4rem;
      display: flex; flex-direction: column; gap: 2.25rem;
    }

    /* ── Section title ── */
    .section-title {
      margin-bottom: 0.9rem;
    }
    .section-title h2  { font-size: 0.95rem; font-weight: 700; color: var(--text); }
    .section-title p   { font-size: 0.78rem; color: var(--text-3); margin-top: 3px; }

    /* ── Cards ── */
    .card {
      background: var(--card); border: 1px solid var(--border);
      border-radius: var(--radius); box-shadow: var(--shadow);
    }
    .card-body { padding: 1.5rem; }
    .card-body + .card-body { border-top: 1px solid var(--border); }

    /* ── Stats row ── */
    .stats-grid {
      display: grid; grid-template-columns: repeat(3, 1fr); gap: 1rem;
    }
    .stat-card {
      background: var(--card); border: 1px solid var(--border);
      border-radius: var(--radius); padding: 1.5rem 1.6rem 1.4rem;
      box-shadow: var(--shadow);
      border-top: 3px solid var(--border);
    }
    .stat-card-blue   { border-top-color: var(--blue); }
    .stat-card-green  { border-top-color: var(--green); }
    .stat-card-yellow { border-top-color: var(--yellow); }
    .stat-label { font-size: 0.68rem; font-weight: 700; color: var(--text-3); text-transform: uppercase; letter-spacing: 0.09em; margin-bottom: 0.5rem; }
    .stat-value { font-size: 1.85rem; font-weight: 800; color: var(--text); line-height: 1; margin-bottom: 0.35rem; }
    .stat-meta  { font-size: 0.7rem; color: var(--text-4); }

    /* ── Upload ── */
    .drop-zone {
      border: 2px dashed var(--border-2); border-radius: 9px;
      padding: 2.5rem 2rem; text-align: center; cursor: pointer;
      transition: border-color 0.15s, background 0.15s;
      position: relative; background: #fafafa;
    }
    .drop-zone:hover, .drop-zone.dragover {
      border-color: var(--blue); background: var(--blue-lt);
    }
    .drop-zone input[type=file] {
      position: absolute; inset: 0; opacity: 0;
      cursor: pointer; width: 100%; height: 100%;
    }
    .drop-icon { font-size: 2.2rem; margin-bottom: 0.6rem; }
    .drop-text { font-size: 0.87rem; color: var(--text-3); line-height: 1.65; }
    .drop-text strong { color: var(--blue); font-weight: 600; }
    .drop-text small  { font-size: 0.78rem; }
    .file-selected { margin-top: 0.7rem; font-size: 0.82rem; color: var(--green); font-weight: 600; display: none; }
    .btn-upload {
      width: 100%; padding: 0.78rem 1rem; margin-top: 1.1rem;
      background: linear-gradient(135deg, #0d1b2a 0%, #2563eb 100%);
      color: #fff; border: none; border-radius: 9px;
      font-size: 0.88rem; font-weight: 600; font-family: inherit;
      cursor: pointer; transition: opacity 0.15s; letter-spacing: 0.01em;
    }
    .btn-upload:hover    { opacity: 0.87; }
    .btn-upload:disabled { background: #9ca3af; cursor: not-allowed; opacity: 1; }
    .result {
      margin-top: 1rem; border-radius: 9px;
      padding: 1rem 1.2rem; font-size: 0.82rem; display: none;
    }
    .result.success { background: var(--green-lt); border: 1px solid #6ee7b7; color: #064e3b; }
    .result.error   { background: var(--red-lt);   border: 1px solid #fca5a5; color: #7f1d1d; }
    .result-title   { font-weight: 700; margin-bottom: 0.45rem; font-size: 0.86rem; }
    .preview-table  { width: 100%; border-collapse: collapse; margin-top: 0.75rem; font-size: 0.78rem; }
    .preview-table thead tr { background: #d1fae5; }
    .preview-table th {
      text-align: left; padding: 0.35rem 0.65rem;
      font-weight: 700; color: #064e3b; font-size: 0.7rem;
      text-transform: uppercase; letter-spacing: 0.04em;
      border-bottom: 2px solid #6ee7b7;
    }
    .preview-table td { padding: 0.38rem 0.65rem; border-bottom: 1px solid #d1fae5; }
    .preview-table tbody tr:last-child td { border-bottom: none; }

    /* ── Grid layouts ── */
    .grid-2 { display: grid; grid-template-columns: 1fr 1fr; gap: 1rem; }
    .grid-3 { display: grid; grid-template-columns: repeat(3,1fr); gap: 1rem; }

    /* ── Info list ── */
    .info-list { display: flex; flex-direction: column; }
    .info-row {
      display: flex; justify-content: space-between; align-items: center;
      padding: 0.58rem 0; border-bottom: 1px solid var(--border); font-size: 0.82rem;
    }
    .info-row:last-child { border-bottom: none; padding-bottom: 0; }
    .info-row .ik { color: var(--text-3); font-weight: 500; }
    .info-row .iv { color: var(--text-2); font-weight: 600; }

    /* ── Form elements ── */
    .field-label {
      display: block; font-size: 0.75rem; font-weight: 600;
      color: var(--text-2); margin-bottom: 0.4rem; letter-spacing: 0.01em;
    }
    .field-input {
      width: 100%; padding: 0.62rem 0.9rem;
      border: 1.5px solid var(--border-2); border-radius: 9px;
      font-size: 0.875rem; font-family: inherit; color: var(--text); outline: none;
      transition: border-color 0.15s, box-shadow 0.15s; background: #fff;
    }
    .field-input:focus {
      border-color: var(--blue);
      box-shadow: 0 0 0 3px rgba(37,99,235,0.1);
    }

    /* ── Buttons ── */
    .btn-primary {
      padding: 0.62rem 1.1rem;
      background: linear-gradient(135deg, #0d1b2a 0%, #2563eb 100%);
      color: #fff; border: none; border-radius: 9px;
      font-size: 0.84rem; font-weight: 600; font-family: inherit;
      cursor: pointer; transition: opacity 0.15s; white-space: nowrap;
      letter-spacing: 0.01em;
    }
    .btn-primary:hover    { opacity: 0.87; }
    .btn-primary:disabled { background: #9ca3af; cursor: not-allowed; opacity: 1; }
    .btn-outline {
      padding: 0.55rem 1rem;
      background: #fff; color: var(--text-2);
      border: 1.5px solid var(--border-2); border-radius: 9px;
      font-size: 0.82rem; font-weight: 600; font-family: inherit;
      cursor: pointer; transition: all 0.15s; white-space: nowrap;
    }
    .btn-outline:hover    { background: #f9fafb; border-color: #9ca3af; }
    .btn-outline:disabled { opacity: 0.5; cursor: not-allowed; }

    /* ── Feedback messages ── */
    .msg { font-size: 0.78rem; margin-top: 0.5rem; font-weight: 500; min-height: 1.1rem; }
    .msg.ok  { color: var(--green); }
    .msg.err { color: var(--red); }
    /* legacy support */
    .admin-msg        { font-size: 0.78rem; margin-top: 0.5rem; font-weight: 500; display: none; }
    .admin-msg.ok     { color: var(--green); }
    .admin-msg.err    { color: var(--red); }

    /* ── Admin chips ── */
    .admin-list  { display: flex; flex-wrap: wrap; gap: 0.45rem; }
    .admin-chip {
      display: inline-flex; align-items: center; gap: 0.45rem;
      background: var(--blue-lt); border: 1px solid var(--blue-mid);
      color: #1e40af; border-radius: 7px;
      padding: 0.3rem 0.75rem; font-size: 0.8rem; font-weight: 500;
    }
    .admin-chip .remove-btn {
      background: none; border: none; cursor: pointer;
      color: #93c5fd; font-size: 0.85rem; padding: 0;
      line-height: 1; transition: color 0.15s;
    }
    .admin-chip .remove-btn:hover { color: var(--red); }
    .admin-chip.default-admin .remove-btn { display: none; }

    /* ── Data table ── */
    .data-table { width: 100%; border-collapse: collapse; font-size: 0.82rem; }
    .data-table thead th {
      text-align: left; padding: 0.6rem 0.9rem;
      font-size: 0.68rem; font-weight: 700; text-transform: uppercase;
      letter-spacing: 0.07em; color: var(--text-3);
      background: #f9fafb; border-bottom: 1px solid var(--border);
    }
    .data-table tbody td {
      padding: 0.72rem 0.9rem; border-bottom: 1px solid var(--border);
      color: var(--text-2);
    }
    .data-table tbody tr:last-child td { border-bottom: none; }
    .data-table tbody tr:hover         { background: #fafbfc; }

    /* ── Badges ── */
    .badge {
      display: inline-flex; align-items: center;
      padding: 2px 8px; border-radius: 20px;
      font-size: 0.67rem; font-weight: 700;
      letter-spacing: 0.05em; text-transform: uppercase;
    }
    .badge-active       { background: var(--green-lt); color: #065f46; border: 1px solid #6ee7b7; }
    .badge-cooling      { background: var(--yellow-lt); color: #78350f; border: 1px solid #fcd34d; }
    .badge-exhausted    { background: var(--red-lt); color: #7f1d1d; border: 1px solid #fca5a5; }
    .badge-valid        { background: var(--green-lt); color: #065f46; border: 1px solid #6ee7b7; }
    .badge-invalid      { background: var(--red-lt); color: #7f1d1d; border: 1px solid #fca5a5; }
    .badge-rate-limited { background: var(--yellow-lt); color: #78350f; border: 1px solid #fcd34d; }
    .badge-server-error { background: var(--purple-lt); color: #4c1d95; border: 1px solid #c4b5fd; }
    .badge-validating   { background: #f1f5f9; color: var(--text-3); border: 1px solid var(--border-2); }
    .badge-error        { background: var(--red-lt); color: #7f1d1d; border: 1px solid #fca5a5; }

    /* ── Key monospace ── */
    .key-mono { font-family: 'SF Mono', 'Fira Code', 'Consolas', monospace; font-size: 0.78rem; color: var(--text-3); letter-spacing: 0.02em; }

    /* ── Inline action buttons ── */
    .btn-xs {
      border: 1.5px solid; cursor: pointer; border-radius: 6px;
      font-size: 0.68rem; font-weight: 600; padding: 3px 8px;
      font-family: inherit; transition: background 0.12s; background: #fff;
    }
    .btn-xs-blue { border-color: #bfdbfe; color: #1d4ed8; }
    .btn-xs-blue:hover { background: var(--blue-lt); }
    .btn-xs-red  { border-color: #fecaca; color: var(--red); }
    .btn-xs-red:hover  { background: var(--red-lt); }

    /* ── Table toolbar ── */
    .table-toolbar {
      display: flex; justify-content: space-between; align-items: center;
      margin-bottom: 0.65rem;
    }
    .table-meta { font-size: 0.74rem; color: var(--text-4); }

    /* ── Add key area ── */
    .add-key-wrap {
      border-top: 1px solid var(--border); padding-top: 1.1rem; margin-top: 1rem;
    }
    .add-key-label {
      font-size: 0.7rem; font-weight: 700; text-transform: uppercase;
      letter-spacing: 0.08em; color: var(--text-3); margin-bottom: 0.55rem;
    }
    .add-key-row { display: flex; gap: 0.6rem; align-items: center; }
    .key-input {
      flex: 1; padding: 0.62rem 0.9rem;
      border: 1.5px solid var(--border-2); border-radius: 9px;
      font-size: 0.82rem; font-family: 'SF Mono', 'Fira Code', 'Consolas', monospace;
      color: var(--text); outline: none;
      transition: border-color 0.15s, box-shadow 0.15s;
    }
    .key-input:focus {
      border-color: var(--blue);
      box-shadow: 0 0 0 3px rgba(37,99,235,0.1);
    }

    /* ── Spinner ── */
    .spinner {
      display: inline-block; width: 13px; height: 13px;
      border: 2px solid rgba(255,255,255,0.3); border-top-color: #fff;
      border-radius: 50%; animation: spin 0.6s linear infinite;
      margin-right: 0.35rem; vertical-align: middle;
    }
    .spinner-sm {
      display: inline-block; width: 12px; height: 12px;
      border: 2px solid rgba(37,99,235,0.2); border-top-color: var(--blue);
      border-radius: 50%; animation: spin 0.6s linear infinite;
      margin-right: 0.3rem; vertical-align: middle;
    }
    @keyframes spin { to { transform: rotate(360deg); } }

    /* ── Sub-section label ── */
    .sub-label {
      font-size: 0.7rem; font-weight: 700; text-transform: uppercase;
      letter-spacing: 0.08em; color: var(--text-3); margin-bottom: 0.75rem;
    }

    /* ── Divider ── */
    hr.divider { border: none; border-top: 1px solid var(--border); margin: 1.25rem 0; }

    /* ── Footer ── */
    footer {
      text-align: center; padding: 1.5rem;
      font-size: 0.72rem; color: var(--text-4);
      border-top: 1px solid var(--border);
    }

    /* ── Responsive ── */
    @media (max-width: 800px) {
      .stats-grid { grid-template-columns: 1fr 1fr; }
      .grid-2     { grid-template-columns: 1fr; }
    }
    @media (max-width: 500px) {
      .stats-grid  { grid-template-columns: 1fr; }
      .topbar      { padding: 0 1rem; }
      .page        { padding: 1.25rem 0.9rem 3rem; gap: 1.75rem; }
    }
  </style>
</head>
<body>

<!-- ── Login overlay ── -->
<div id="loginOverlay" style="display:none">
  <div class="login-card">
    <div class="login-logo">🎓</div>
    <div class="login-title">Admin Portal</div>
    <div class="login-sub">EPAM Campus Junior Training &mdash; Internal Access Only</div>
    <input type="password" id="secretInput" class="login-input"
           placeholder="Enter admin secret" autocomplete="off">
    <button class="login-btn" id="loginBtn" onclick="doLogin()">Sign In &rarr;</button>
    <div class="login-error" id="loginError">Incorrect secret. Please try again.</div>
  </div>
</div>

<!-- ── Topbar ── -->
<header class="topbar">
  <div class="topbar-left">
    <div class="topbar-logo">🎓</div>
    <div class="topbar-brand">
      <div class="topbar-name">CONTRIBOT ADMIN</div>
      <div class="topbar-sub">EPAM Campus Junior Training</div>
    </div>
  </div>
  <div class="topbar-right">
    <div class="topbar-badge">Admin Portal</div>
  </div>
</header>

<!-- ── Main ── -->
<main>
<div class="page">

  <!-- ── Stat cards ── -->
  <div class="stats-grid">
    <div class="stat-card stat-card-blue">
      <div class="stat-label">Contributors</div>
      <div class="stat-value" id="stat-count">—</div>
      <div class="stat-meta">Eligible records</div>
    </div>
    <div class="stat-card stat-card-green">
      <div class="stat-label">Total Bonus Pool</div>
      <div class="stat-value" id="stat-bonus">—</div>
      <div class="stat-meta">At $15 / point</div>
    </div>
    <div class="stat-card stat-card-yellow">
      <div class="stat-label">Last Synced</div>
      <div class="stat-value" id="stat-updated" style="font-size:1.05rem;margin-bottom:0.35rem;">—</div>
      <div class="stat-meta">Learn export</div>
    </div>
  </div>

  <!-- ── Upload ── -->
  <div>
    <div class="section-title">
      <h2>Upload Learn Export</h2>
      <p>Upload the latest .xlsx export from learn.epam.com to refresh all contribution data</p>
    </div>
    <div class="card">
      <div class="card-body">
        <form id="uploadForm">
          <div class="drop-zone" id="dropZone">
            <input type="file" id="fileInput" name="file" accept=".xlsx">
            <div class="drop-icon">📊</div>
            <div class="drop-text">
              <strong>Click to select a file</strong> or drag &amp; drop here<br>
              <small>Accepts .xlsx exports from learn.epam.com · Recalculates all contribution data on upload</small>
            </div>
            <div class="file-selected" id="fileName"></div>
          </div>
          <button type="submit" class="btn-upload" id="uploadBtn" disabled>
            Upload &amp; Regenerate Data
          </button>
        </form>
        <div class="result" id="result"></div>
      </div>
    </div>
  </div>

  <!-- ── Dataset info + Admin management ── -->
  <div class="grid-2">

    <!-- Dataset config -->
    <div>
      <div class="section-title">
        <h2>Dataset Configuration</h2>
        <p>Rules applied when processing the Learn export</p>
      </div>
      <div class="card">
        <div class="card-body">
          <div class="info-list">
            <div class="info-row"><span class="ik">Data Source</span><span class="iv">Learn Export (.xlsx)</span></div>
            <div class="info-row"><span class="ik">Eligible Format</span><span class="iv">Group Meeting w/ Contributor</span></div>
            <div class="info-row"><span class="ik">Eligible Statuses</span><span class="iv">Submitted &amp; Approved</span></div>
            <div class="info-row"><span class="ik">Point Value</span><span class="iv">$15 per point</span></div>
          </div>
        </div>
      </div>
    </div>

    <!-- Add admin -->
    <div>
      <div class="section-title">
        <h2>Add Administrator</h2>
        <p>Grant admin access to an EPAM email address</p>
      </div>
      <div class="card">
        <div class="card-body">
          <label class="field-label" for="adminEmailInput">EPAM Email Address</label>
          <input type="email" id="adminEmailInput" class="field-input"
                 placeholder="firstname_lastname@epam.com" autocomplete="off"
                 style="margin-bottom:0.85rem;">
          <button class="btn-primary" style="width:100%;" onclick="addAdmin()">Add Administrator</button>
          <div class="admin-msg" id="addAdminMsg"></div>
        </div>
      </div>
    </div>

  </div>

  <!-- ── Current admins ── -->
  <div>
    <div class="section-title">
      <h2>Current Administrators</h2>
      <p>All accounts with access to this admin portal</p>
    </div>
    <div class="card">
      <div class="card-body">
        <div class="admin-list" id="adminList">
          <span style="color:var(--text-4)">Loading...</span>
        </div>
      </div>
    </div>
  </div>

  <!-- ── API Key Pool ── -->
  <div>
    <div class="section-title">
      <h2>Gemini API Key Pool</h2>
      <p>Manage API keys for Gemini model access &mdash; changes persist to Redis across redeployments</p>
    </div>
    <div class="card">
      <div class="card-body">
        <div class="table-toolbar">
          <div class="table-meta" id="poolRefreshTime">Loading…</div>
          <button class="btn-outline" id="validateBtn" onclick="validateKeys()"
                  style="font-size:0.78rem;padding:0.42rem 0.9rem;">
            ✓&nbsp; Validate All Keys
          </button>
        </div>

        <table class="data-table">
          <thead>
            <tr>
              <th>#</th>
              <th>Key Preview</th>
              <th>State</th>
              <th>Failures</th>
              <th>Available In</th>
              <th>Live Check</th>
              <th>Actions</th>
            </tr>
          </thead>
          <tbody id="keyPoolBody">
            <tr><td colspan="7" style="padding:1.5rem 0.9rem;color:var(--text-4);">
              <span class="spinner-sm"></span>Loading key pool…
            </td></tr>
          </tbody>
        </table>

        <div class="add-key-wrap">
          <div class="add-key-label">Add New Key</div>
          <div class="add-key-row">
            <input type="password" id="newKeyInput" class="key-input"
                   placeholder="Paste Gemini API key here" autocomplete="off">
            <button class="btn-primary" onclick="addKey()" style="padding:0.62rem 1rem;">
              Add Key
            </button>
          </div>
          <div class="msg" id="keyActionMsg"></div>
        </div>
      </div>
    </div>
  </div>

</div><!-- /page -->
</main>

<footer>
  EPAM Systems &mdash; Campus Junior Training &mdash; Internal Use Only
</footer>

<script>
  // ── Auth ──────────────────────────────────────────────────────────────────
  const SECRET_KEY = 'contribot_admin_token';
  function getToken()    { return localStorage.getItem(SECRET_KEY) || ''; }
  function authHeaders() { const t = getToken(); return t ? { 'X-Admin-Token': t } : {}; }

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
      const d = await fetch('/api/stats', { headers: authHeaders() }).then(r => r.json());
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
      const d   = await fetch('/api/admin/list', { headers: authHeaders() }).then(r => r.json());
      const el  = document.getElementById('adminList');
      if (!d.admins || !d.admins.length) {
        el.innerHTML = '<span style="color:var(--text-4);font-size:0.82rem;">No admins configured.</span>';
        return;
      }
      const defaultAdmins = new Set(['vishal_bhandari@epam.com']);
      el.innerHTML = d.admins.map(email => {
        const isDef = defaultAdmins.has(email.toLowerCase());
        return `<span class="admin-chip ${isDef ? 'default-admin' : ''}">
          ${email}
          <button class="remove-btn" onclick="removeAdmin('${email}')" title="Remove">&#x2715;</button>
        </span>`;
      }).join('');
    } catch(e) {
      document.getElementById('adminList').innerHTML =
        '<span style="color:var(--text-4)">Could not load administrators.</span>';
    }
  }

  async function addAdmin() {
    const email = document.getElementById('adminEmailInput').value.trim().toLowerCase();
    const msgEl = document.getElementById('addAdminMsg');
    msgEl.style.display = 'none';
    if (!email || !email.includes('@')) {
      msgEl.textContent = 'Please enter a valid EPAM email address.';
      msgEl.className = 'admin-msg err'; msgEl.style.display = 'block'; return;
    }
    try {
      const d = await fetch('/api/admin/add', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json', ...authHeaders() },
        body: JSON.stringify({ email })
      }).then(r => r.json());
      if (d.success) {
        msgEl.textContent = `${email} added as administrator.`;
        msgEl.className = 'admin-msg ok';
        document.getElementById('adminEmailInput').value = '';
        loadAdmins();
      } else {
        msgEl.textContent = d.error || 'Failed to add administrator.';
        msgEl.className = 'admin-msg err';
      }
    } catch(e) {
      msgEl.textContent = 'Network error.';
      msgEl.className = 'admin-msg err';
    }
    msgEl.style.display = 'block';
  }

  async function removeAdmin(email) {
    if (!confirm(`Remove ${email} from administrators?`)) return;
    try {
      const d = await fetch('/api/admin/remove', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json', ...authHeaders() },
        body: JSON.stringify({ email })
      }).then(r => r.json());
      if (d.success) loadAdmins();
      else alert(d.message || 'Could not remove administrator.');
    } catch(e) { alert('Network error.'); }
  }

  // ── Key pool ──────────────────────────────────────────────────────────────
  let _lastValidation = {};

  function _keyMsg(msg, ok) {
    const el = document.getElementById('keyActionMsg');
    el.textContent = msg; el.className = 'msg ' + (ok ? 'ok' : 'err');
  }

  async function loadKeyPool() {
    try {
      const d     = await fetch('/api/keys/status', { headers: authHeaders() }).then(r => r.json());
      const tbody = document.getElementById('keyPoolBody');
      if (!d.keys || !d.keys.length) {
        tbody.innerHTML = '<tr><td colspan="7" style="color:var(--text-4);padding:1.25rem 0.9rem;">No keys configured.</td></tr>';
        return;
      }
      tbody.innerHTML = d.keys.map(k => {
        const cls   = k.state === 'active'  ? 'badge-active'
                    : k.state === 'cooling' ? 'badge-cooling' : 'badge-exhausted';
        const avail = k.available_in_seconds != null ? fmtDur(k.available_in_seconds) : '—';
        const vr    = _lastValidation[k.key_index];
        const live  = vr
          ? `<span class="badge badge-${vr.vstatus.replace(/_/g,'-')}">${vr.vstatus.replace(/_/g,' ')}</span>`
            + (vr.detail ? `<br><span style="color:var(--text-4);font-size:0.68rem;line-height:1.4;display:block;margin-top:2px;">${vr.detail}</span>` : '')
          : '<span style="color:var(--text-4);">—</span>';
        return `<tr>
          <td style="font-weight:600;color:var(--text);">${k.key_index}</td>
          <td class="key-mono">${k.key_preview || '—'}</td>
          <td><span class="badge ${cls}">${k.state}</span></td>
          <td>${k.fail_count}</td>
          <td style="color:var(--text-3);">${avail}</td>
          <td>${live}</td>
          <td>
            <button class="btn-xs btn-xs-blue" onclick="replaceKey(${k.key_index})">Replace</button>
            <button class="btn-xs btn-xs-red" style="margin-left:4px;" onclick="removeKey(${k.key_index})">Remove</button>
          </td>
        </tr>`;
      }).join('');
      document.getElementById('poolRefreshTime').textContent =
        d.keys.length + ' key' + (d.keys.length === 1 ? '' : 's') +
        ' · refreshed ' + new Date().toLocaleTimeString('en-US');
    } catch(e) {
      document.getElementById('keyPoolBody').innerHTML =
        '<tr><td colspan="7" style="color:var(--text-4);padding:1rem 0.9rem;">Could not load pool status.</td></tr>';
    }
  }

  async function validateKeys() {
    const btn = document.getElementById('validateBtn');
    btn.disabled = true;
    btn.innerHTML = '<span class="spinner-sm"></span>Validating&hellip;';
    _lastValidation = {};
    document.querySelectorAll('#keyPoolBody tr').forEach(tr => {
      const cells = tr.querySelectorAll('td');
      if (cells.length >= 6) cells[5].innerHTML = '<span class="badge badge-validating">checking</span>';
    });
    try {
      const d = await fetch('/api/keys/validate', { method: 'POST', headers: authHeaders() }).then(r => r.json());
      if (!d.keys) throw new Error(d.error || 'Validation failed');
      d.keys.forEach(k => {
        _lastValidation[k.key_index] = { vstatus: k.validation_status, detail: k.detail || '' };
      });
      await loadKeyPool();
      document.getElementById('poolRefreshTime').textContent =
        d.keys.length + ' key' + (d.keys.length === 1 ? '' : 's') +
        ' · validated ' + new Date().toLocaleTimeString('en-US');
    } catch(e) {
      document.getElementById('poolRefreshTime').textContent = 'Validation error: ' + e.message;
    } finally {
      btn.disabled = false;
      btn.innerHTML = '&#x2713;&nbsp; Validate All Keys';
    }
  }

  async function addKey() {
    const inp = document.getElementById('newKeyInput');
    const key = inp.value.trim();
    if (!key) { _keyMsg('Paste a key first.', false); return; }
    try {
      const d = await fetch('/api/keys', {
        method: 'POST',
        headers: { ...authHeaders(), 'Content-Type': 'application/json' },
        body: JSON.stringify({ key })
      }).then(r => r.json());
      if (!d.success) throw new Error(d.error || 'Failed');
      inp.value = '';
      _keyMsg(`Key added as slot ${d.key_index} (${d.total} total).`, true);
      await loadKeyPool();
    } catch(e) { _keyMsg('Error: ' + e.message, false); }
  }

  async function replaceKey(idx) {
    const nk = window.prompt(`Paste the replacement key for slot ${idx}:`);
    if (!nk || !nk.trim()) return;
    try {
      const d = await fetch(`/api/keys/${idx}`, {
        method: 'PUT',
        headers: { ...authHeaders(), 'Content-Type': 'application/json' },
        body: JSON.stringify({ key: nk.trim() })
      }).then(r => r.json());
      if (!d.success) throw new Error(d.error || 'Failed');
      delete _lastValidation[idx];
      _keyMsg(`Key ${idx} replaced successfully.`, true);
      await loadKeyPool();
    } catch(e) { _keyMsg('Error: ' + e.message, false); }
  }

  async function removeKey(idx) {
    if (!confirm(`Remove Key ${idx} from the pool? This takes effect immediately.`)) return;
    try {
      const d = await fetch(`/api/keys/${idx}`, { method: 'DELETE', headers: authHeaders() }).then(r => r.json());
      if (!d.success) throw new Error(d.error || 'Failed');
      delete _lastValidation[idx];
      _keyMsg(`Key ${idx} removed.`, true);
      await loadKeyPool();
    } catch(e) { _keyMsg('Error: ' + e.message, false); }
  }

  function fmtDur(sec) {
    if (sec < 60)   return Math.round(sec) + 's';
    if (sec < 3600) return Math.round(sec / 60) + 'm ' + (Math.round(sec % 60)) + 's';
    return Math.floor(sec/3600) + 'h ' + Math.round((sec%3600)/60) + 'm';
  }

  // ── Upload ────────────────────────────────────────────────────────────────
  const input  = document.getElementById('fileInput');
  const btn    = document.getElementById('uploadBtn');
  const nameEl = document.getElementById('fileName');
  const zone   = document.getElementById('dropZone');

  function setFile(file) {
    nameEl.textContent = '✔ ' + file.name;
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
      input.files = dt.files; setFile(file);
    }
  });

  document.getElementById('uploadForm').addEventListener('submit', async (e) => {
    e.preventDefault();
    const resultEl = document.getElementById('result');
    resultEl.style.display = 'none';
    btn.disabled = true;
    btn.innerHTML = '<span class="spinner"></span>Processing&hellip;';
    const fd = new FormData();
    fd.append('file', input.files[0]);
    try {
      const d = await fetch('/upload', { method: 'POST', body: fd, headers: authHeaders() }).then(r => r.json());
      resultEl.style.display = 'block';
      if (d.success) {
        resultEl.className = 'result success';
        let html = `<div class="result-title">✔ Data updated &mdash; ${d.people_count} contributor(s) found</div>`;
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
        resultEl.innerHTML = `<div class="result-title">Upload Failed</div>${d.error ?? 'Unknown error.'}`;
      }
    } catch(err) {
      resultEl.style.display = 'block';
      resultEl.className = 'result error';
      resultEl.innerHTML = `<div class="result-title">Network Error</div>${err.message}`;
    }
    btn.innerHTML = 'Upload &amp; Regenerate Data';
    btn.disabled = false;
  });

  // ── Init ──────────────────────────────────────────────────────────────────
  function loadAll() { loadStats(); loadAdmins(); loadKeyPool(); }

  checkAuth().then(() => {
    if (document.getElementById('loginOverlay').style.display !== 'flex') loadAll();
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

    # Load API keys from Redis (overrides env vars if admin saved keys there)
    try:
        from agent.key_pool import GeminiKeyPool, set_pool, get_pool
        redis_keys = await load_api_keys_from_redis()
        if redis_keys:
            set_pool(GeminiKeyPool(redis_keys))
            print(f"[startup] Loaded {len(redis_keys)} API key(s) from Redis.", flush=True)
        else:
            get_pool()  # initialize from env vars so errors surface at startup
            print("[startup] API keys loaded from environment variables.", flush=True)
    except Exception as e:
        print(f"[startup] Key pool init failed: {e}", flush=True)

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
    from agent.agent import MODEL
    try:
        results = await get_pool().validate_all_keys(model=MODEL)
        return json_response({"keys": results})
    except Exception as e:
        return json_response({"error": str(e)}, status=500)


async def route_keys_add(req: Request) -> Response:
    if not _check_admin_auth(req):
        return json_response({"error": "Unauthorized"}, status=401)
    from agent.key_pool import get_pool
    try:
        body = await req.json()
        key = (body.get("key") or "").strip()
        if not key:
            return json_response({"error": "key is required"}, status=400)
        pool = get_pool()
        new_idx = pool.add_key(key)
        await save_api_keys_to_redis(pool.get_raw_keys())
        return json_response({"success": True, "key_index": new_idx, "total": len(pool._keys)})
    except Exception as e:
        return json_response({"error": str(e)}, status=500)


async def route_keys_replace(req: Request) -> Response:
    if not _check_admin_auth(req):
        return json_response({"error": "Unauthorized"}, status=401)
    from agent.key_pool import get_pool
    try:
        idx = int(req.match_info["index"])
        body = await req.json()
        key = (body.get("key") or "").strip()
        if not key:
            return json_response({"error": "key is required"}, status=400)
        pool = get_pool()
        pool.replace_key(idx, key)
        await save_api_keys_to_redis(pool.get_raw_keys())
        return json_response({"success": True, "key_index": idx})
    except (IndexError, ValueError) as e:
        return json_response({"error": str(e)}, status=400)
    except Exception as e:
        return json_response({"error": str(e)}, status=500)


async def route_keys_remove(req: Request) -> Response:
    if not _check_admin_auth(req):
        return json_response({"error": "Unauthorized"}, status=401)
    from agent.key_pool import get_pool
    try:
        idx = int(req.match_info["index"])
        pool = get_pool()
        pool.remove_key(idx)
        await save_api_keys_to_redis(pool.get_raw_keys())
        return json_response({"success": True})
    except (IndexError, ValueError) as e:
        return json_response({"error": str(e)}, status=400)
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
app.router.add_get ("/api/keys/status",         route_keys_status)
app.router.add_post("/api/keys/validate",       route_keys_validate)
app.router.add_post("/api/keys",                route_keys_add)
app.router.add_put ("/api/keys/{index}",        route_keys_replace)
app.router.add_delete("/api/keys/{index}",      route_keys_remove)
app.router.add_get ("/api/health",              route_health)

if __name__ == "__main__":
    port = int(os.environ.get("PORT", 8000))
    print(f"Starting on http://0.0.0.0:{port}", flush=True)
    web.run_app(app, host="0.0.0.0", port=port)
