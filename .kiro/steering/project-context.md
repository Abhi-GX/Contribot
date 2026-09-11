---
inclusion: always
---

# Contribot — Project Context & Live Status

## What this project is

An internal Microsoft Teams chatbot for EPAM's Campus Junior Training program.
Mentors, SMEs, Scrum Masters, and Product Owners message the bot in Teams to
check their eligible contribution points and bonus amount.

---

## Current architecture (as built)

```
User (Teams)
    │  natural language message
    ▼
Microsoft Teams
    │  POST /api/messages  (Bot Framework JWT-signed)
    ▼
https://communicationbot.vercel.app/api/messages
    │  Vercel serverless Python function
    ▼
Bot Framework SDK
    │  verifies JWT, resolves sender email via TeamsInfo.get_member
    ▼
run_agent(user_message, user_email)   ← agent/__init__.py
    │
    ▼
Google ADK — LlmAgent (gemini-3.6-flash)
    │  tool calls decided by Gemini
    ├── get_contribution(email)   → reads data/totals.csv
    └── get_program_info()        → returns static program rules
    │
    ▼
Natural language reply → Teams
```

---

## Infrastructure — all confirmed working ✅

### Microsoft Entra ID (Azure AD) — EPAM tenant
- **App name:** Contribute-Bot
- **Application (client) ID:** `400dfdef-d35e-4561-95c1-57d11495c1de`
- **Directory (tenant) ID:** `b41b72d0-4e9f-4c26-8a69-f949f367c91d`
- **Tenant:** EPAM Systems — Single Tenant
- **Client secret:** in Vercel env var `MicrosoftAppPassword` (expires Sep 2027)

### Azure Bot resource
- **Handle:** `contribution-bot`
- **Resource group:** `contribot-rg` — South India region — F0 Free
- **Messaging endpoint:** `https://communicationbot.vercel.app/api/messages` ✅
- **Teams channel:** Microsoft Teams Commercial ✅ enabled
- **Type:** Single Tenant — linked to Entra app above

### Vercel deployment ✅
- **URL:** `https://communicationbot.vercel.app`
- **Endpoints:**
  - `POST /api/messages` → bot logic ✅
  - `GET /api/health` → health check ✅
- **Runtime:** Python 3.12 serverless
- **Env vars set:** `MicrosoftAppId`, `MicrosoftAppPassword`, `MicrosoftAppTenantId`
- **Env vars needed:** `GOOGLE_API_KEY` — not yet added (needed for Gemini ADK to work)
- **Deploy command:** `vercel --force` from project root

### Teams app package ✅
- **Zip:** `ContributionBot.zip`
- **Status:** personal sideload — confirmed working in Teams desktop app
- **Bot ID in manifest:** `400dfdef-d35e-4561-95c1-57d11495c1de`
- **Valid domain:** `communicationbot.vercel.app`

---

## Project file structure (actual current state)

```
Communication_Bot/
├── api/
│   ├── messages.py          ← Vercel serverless POST /api/messages
│   │                          calls run_agent() — Gemini ADK powered
│   └── health.py            ← Vercel serverless GET /api/health
│
├── agent/
│   ├── __init__.py          ← exposes run_agent(user_message, user_email)
│   ├── agent.py             ← LlmAgent(gemini-3.6-flash) + Runner + InMemorySessionService
│   └── tools.py             ← get_contribution(email) + get_program_info()
│
├── data/
│   ├── totals.csv           ← real data generated from Excel export
│   │                          currently: Saikrishna Tammi — 15pts / $225
│   └── sample.txt           ← ADK/Antigravity tutorial reference
│
├── teams-app-package/
│   ├── manifest.json        ← Teams app manifest (schema + bot ID + validDomains)
│   ├── color.png            ← 192×192 app icon
│   └── outline.png          ← 32×32 outline icon
│
├── contribution_calc.py     ← calculates points/bonus from Learn export (.xlsx)
├── generate_csv.py          ← generates data/totals.csv from Excel export
├── inspect_excel.py         ← inspects Excel column values (dev utility)
├── refresh_export.py        ← automates Learn export download via Playwright
├── capture_login_session.py ← saves browser session for refresh_export.py
├── teams_bot.py             ← local aiohttp runner (Bot Emulator testing)
├── test_agent.py            ← local smoke test for Gemini ADK agent
├── test_bot_local.py        ← CLI test: lookup anyone in totals.csv
├── totals.json              ← old sample data (superseded by totals.csv)
├── requirements.txt         ← botbuilder-core, botframework-connector, aiohttp,
│                              google-adk, google-genai
├── vercel.json              ← routes /api/messages and /api/health
├── .env.example             ← documents all required env vars
└── Contribution 07-september-2026.xlsx  ← real Learn export (source of truth)
```

---

## Excel data — what's in the real export

File: `Contribution 07-september-2026.xlsx`

**Eligible filter rules (discovered from real data):**
- Format must be: `"Group meeting with contributor"` (NOT "Group Meeting")
- Status must be: `"Submitted"` or `"Approved"` (no "Verified" in this export)
- `"NotEligible"` and `"Individual"` format rows are excluded

**Current eligible people in totals.csv:**
| Name | Email | Points | Bonus |
|------|-------|--------|-------|
| Saikrishna Tammi | saikrishna_tammi@epam.com | 15.0 | $225.00 |

Only 1 person has eligible data in this export. Others have NotEligible/Individual rows only.

**To regenerate totals.csv from a new Excel export:**
```cmd
python generate_csv.py
```

---

## Agent — how it works

**Model:** `gemini-3.6-flash` (gemini-2.0-flash is deprecated as of Sep 2026)

**Tools:**
```python
get_contribution(email: str) -> dict
    # reads data/totals.csv
    # returns: found, name, email, eligible_points, bonus_usd, row_count, included_statuses

get_program_info() -> dict
    # returns static program rules:
    # eligible format, eligible statuses, $15/point rate, eligible roles
```

**Entry point:**
```python
from agent import run_agent
reply = run_agent(user_message="what's my bonus?", user_email="user@epam.com")
```

**Vercel constraint:** stateless per request — InMemorySessionService scoped to
single request. No cross-request memory. Acceptable for single-turn lookups.

**TeamsInfo.get_member:** resolves real Teams email server-side. Works in
Teams, NOT in Bot Framework Emulator (emulator has no Teams identity context).

---

## Current status by phase  

### Phase 1 — Teams pipeline ✅ COMPLETE (POC)
- Bot deployed on Vercel
- Azure Bot registered + Teams channel enabled
- Teams app package sideloaded and working personally
- Static responses confirmed end-to-end

### Phase 2 — Gemini ADK agent 🔄 IN PROGRESS
- Agent code built: `agent/agent.py`, `agent/tools.py`
- CSV data source wired in: `data/totals.csv`
- Tools reading real Excel data via `generate_csv.py`
- `api/messages.py` updated to call `run_agent()`
- ⚠️ **Pending:** `GOOGLE_API_KEY` not yet added to Vercel env vars
- ⚠️ **Pending:** redeploy to Vercel after adding API key
- ⚠️ **Pending:** test in Teams with real Gemini responses

### Phase 3 — Real data from learn.epam.com ⏳ NOT STARTED
Replace CSV with live API:
1. **Auth Manager** — EPAM SSO/OAuth2, replace Playwright cookie approach
2. **Token cache** — Redis or Vercel KV, handle refresh
3. **Learn API calls** — GraphQL/REST endpoints (needs reverse engineering)
4. **Email mapping** — Teams email → Learn account
5. **New tools:** `get_my_contributions(email)`, `get_my_bonus(email)`

### Phase 4 — Org-wide Teams rollout ⏳ NEEDS ADMIN
- EPAM Teams admin uploads `ContributionBot.zip` to org app catalog
- admin.teams.microsoft.com → Teams apps → Manage apps → Upload
- Then assign via Setup Policy to target users/groups
- No code changes needed — purely admin action

---

## Key decisions

| Decision | Choice | Reason |
|----------|--------|--------|
| Hosting | Vercel | Free, simple, no Azure compute |
| Auth type | Single Tenant (EPAM) | Internal tool only |
| Azure usage | Identity layer only | No compute, no Azure AI |
| Bot Framework | Python 4.15.0 | Teams integration protocol |
| AI model | gemini-3.6-flash via ADK | Preferred framework, good tool-calling |
| Data source now | CSV from Excel export | Simple stand-in, easy to regenerate |
| Data source later | learn.epam.com API | Real source of truth |
| Excel format field | "Group meeting with contributor" | Discovered from real data |
| Excel status fields | "Submitted", "Approved" | Discovered from real data |

---

## Environment variables

| Var | Where | Status |
|-----|-------|--------|
| `MicrosoftAppId` | Vercel env vars | ✅ set |
| `MicrosoftAppTenantId` | Vercel env vars | ✅ set |
| `MicrosoftAppPassword` | Vercel env vars | ✅ set |
| `GOOGLE_API_KEY` | Vercel env vars | ❌ needs to be added |
| `GEMINI_MODEL` | Vercel env vars | optional, defaults to `gemini-3.6-flash` |
| `TOTALS_CSV_PATH` | Vercel env vars | optional, defaults to `./data/totals.csv` |

Get `GOOGLE_API_KEY` at: https://aistudio.google.com/app/apikey

---

## Common commands

```cmd
# Regenerate CSV from latest Excel export
python generate_csv.py

# Test agent locally
set GOOGLE_API_KEY=your_key
python test_agent.py

# Run bot locally (Bot Framework Emulator)
set GOOGLE_API_KEY=your_key
python teams_bot.py

# Deploy to Vercel
vercel --force

# Rebuild Teams zip after manifest changes
Compress-Archive -Path "teams-app-package\manifest.json","teams-app-package\color.png","teams-app-package\outline.png" -DestinationPath "ContributionBot.zip" -Force
```
