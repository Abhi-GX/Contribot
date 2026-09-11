# Campus Junior Training — Contribution Bot

A Microsoft Teams chatbot that lets Mentors, SMEs, Scrum Masters, and Product Owners
check their eligible contribution points and bonus amount by messaging the bot directly.

---

## How it works

```
User messages bot in Teams
        │
        ▼
Teams sends POST to https://<your-vercel-app>.vercel.app/api/messages
        │
        ▼
Bot Framework SDK verifies JWT, routes activity to ContributionBot
        │
        ▼
Bot resolves sender email via Teams identity (Microsoft Graph)
        │
        ▼
Looks up email in totals.json  →  replies with points + bonus
```

---

## Project structure

```
Communication_Bot/
├── api/
│   ├── messages.py        ← Vercel serverless function: POST /api/messages
│   └── health.py          ← Vercel serverless function: GET /api/health
├── teams-app-package/
│   ├── manifest.json      ← Teams app manifest (edit before zipping)
│   ├── color.png          ← 192×192 app icon
│   └── outline.png        ← 32×32 outline icon
├── contribution_calc.py   ← Calculates points/bonus from Learn export
├── refresh_export.py      ← Automates downloading the Learn export
├── capture_login_session.py
├── teams_bot.py           ← Local aiohttp runner (for Bot Emulator testing)
├── test_bot_local.py      ← CLI test: look up anyone in totals.json
├── totals.json            ← Cached data the bot reads from
├── sample_export.xlsx     ← Sample Learn export for testing calc logic
├── requirements.txt
├── vercel.json
└── .env.example
```

---

## Prerequisites

- Python 3.11+
- A [Vercel](https://vercel.com) account (free tier works)
- A Microsoft 365 account with Teams (any org that allows custom app sideloading)
- An [Azure](https://portal.azure.com) free account (only needed for bot registration — no compute cost)

---

## Step 1 — Set up Azure Bot registration (one-time)

This is free. You're just registering the bot identity; no Azure compute is used.

1. Go to [portal.azure.com](https://portal.azure.com) → **Create a resource** → search **Azure Bot**
2. Fill in:
   - **Bot handle**: `contribution-bot` (any unique name)
   - **Subscription**: your subscription
   - **Resource group**: create new, e.g. `contribot-rg`
   - **Pricing tier**: F0 (free)
   - **Microsoft App ID**: choose **Create new Microsoft App ID**
3. Click **Review + create** → **Create**
4. Once created, go to the resource → **Configuration**:
   - Copy **Microsoft App ID** — you'll need this everywhere
   - Click **Manage Password** → **New client secret** → copy the secret value immediately (it's shown only once)
5. Go to **Channels** → add the **Microsoft Teams** channel → Save

Keep both values: `MicrosoftAppId` and `MicrosoftAppPassword`.

---

## Step 2 — Deploy to Vercel

### 2a. Push to GitHub

```bash
git init          # if not already a git repo
git add .
git commit -m "initial bot deployment"
git remote add origin https://github.com/YOUR_ORG/contribution-bot.git
git push -u origin main
```

### 2b. Import project in Vercel

1. Go to [vercel.com](https://vercel.com) → **Add New Project** → import your GitHub repo
2. Vercel auto-detects `vercel.json` — no framework preset needed
3. Before deploying, set **Environment Variables** (Settings → Environment Variables):

   | Name | Value |
   |------|-------|
   | `MicrosoftAppId` | your App ID from Step 1 |
   | `MicrosoftAppPassword` | your client secret from Step 1 |

4. Click **Deploy**

Your bot endpoint is now live at:
```
https://<your-project>.vercel.app/api/messages
```

Verify it's up:
```
https://<your-project>.vercel.app/api/health
```
Should return `{"status": "ok", "people_cached": 5, ...}`

### 2c. Wire the endpoint back into Azure Bot

1. Go back to your Azure Bot → **Configuration**
2. Set **Messaging endpoint** to:
   ```
   https://<your-project>.vercel.app/api/messages
   ```
3. Save

---

## Step 3 — Build the Teams app package

The Teams app package is a `.zip` containing exactly three files:
- `manifest.json`
- `color.png`
- `outline.png`

### 3a. Edit manifest.json

Open `teams-app-package/manifest.json` and replace:

| Placeholder | Replace with |
|-------------|-------------|
| `REPLACE_WITH_YOUR_AZURE_BOT_APP_ID` | your App ID from Step 1 (appears **twice**: `id` and `bots[0].botId`) |
| `REPLACE_WITH_YOUR_VERCEL_DOMAIN` | your Vercel domain, e.g. `contribution-bot-abc123.vercel.app` (no `https://`) |

Example after editing:
```json
{
  "id": "a1b2c3d4-1234-5678-abcd-ef1234567890",
  ...
  "bots": [{ "botId": "a1b2c3d4-1234-5678-abcd-ef1234567890", ... }],
  "validDomains": ["contribution-bot-abc123.vercel.app"]
}
```

### 3b. Zip the package

```powershell
# From inside the teams-app-package folder
Compress-Archive -Path color.png, outline.png, manifest.json -DestinationPath ..\ContributionBot.zip
```

Or on Mac/Linux:
```bash
cd teams-app-package
zip ../ContributionBot.zip color.png outline.png manifest.json
```

---

## Step 4 — Install in Microsoft Teams

### Option A: Upload directly (sideloading)

1. Open Microsoft Teams desktop app
2. Go to **Apps** (left sidebar) → **Manage your apps** → **Upload an app**
3. Choose **Upload a custom app**
4. Select `ContributionBot.zip`
5. Click **Add** in the dialog that appears

### Option B: Via Teams Admin Center (for org-wide rollout)

1. Go to [admin.teams.microsoft.com](https://admin.teams.microsoft.com)
2. **Teams apps** → **Manage apps** → **Upload new app**
3. Upload `ContributionBot.zip`
4. Then go to **Setup policies** to assign it to users/groups

> **Note:** Sideloading must be enabled in your org. Check with your Teams admin if Upload a custom app is grayed out.

---

## Step 5 — Test in Teams

1. In Teams, find **Contribution Bot** in your apps
2. Open a 1:1 chat with the bot
3. Type: `my bonus` or `my points` or `what's my contribution?`
4. The bot replies with your points and bonus from `totals.json`

---

## Local development

Run the bot locally with the [Bot Framework Emulator](https://github.com/microsoft/BotFramework-Emulator):

```bash
pip install -r requirements.txt
python teams_bot.py
```

Connect the emulator to `http://localhost:3978/api/messages`.

> Note: `TeamsInfo.get_member` won't work in the emulator (no real Teams identity).
> Use `test_bot_local.py` for a quick CLI lookup test instead:
> ```bash
> python test_bot_local.py
> ```

---

## Updating totals.json

`totals.json` is the data source the bot reads from. Currently it's bundled
with the deployment (static). Two ways to keep it fresh:

**For POC / demo:** Edit `totals.json` locally → redeploy to Vercel (automatic on `git push`).

**For production:** Run `refresh_export.py` on a schedule (cron / GitHub Actions / Azure Function timer).
It logs into Learn, downloads the export, and regenerates `totals.json`.
Then commit + push to trigger a Vercel redeploy, or host `totals.json` in a database/blob storage.

---

## Environment variables reference

| Variable | Required | Description |
|----------|----------|-------------|
| `MicrosoftAppId` | Yes | Azure Bot App ID |
| `MicrosoftAppPassword` | Yes | Azure Bot client secret |
| `TOTALS_JSON_PATH` | No | Path to totals.json (default: `./totals.json`) |
| `PORT` | No | Local dev port (default: `3978`) |

---

## Troubleshooting

| Symptom | Fix |
|---------|-----|
| Bot replies "couldn't verify your identity" | `TeamsInfo.get_member` needs a real Teams context — won't work in Bot Emulator |
| Bot replies "no records found" for a valid user | Check that the user's Teams email matches the key in `totals.json` |
| Vercel function returns 500 | Check Vercel function logs; likely missing `MicrosoftAppId`/`MicrosoftAppPassword` env vars |
| Teams says "app can't be added" | Sideloading is disabled — ask your Teams admin to enable it |
| Azure Bot shows "Bot unreachable" | Messaging endpoint URL in Azure Bot → Configuration doesn't match your Vercel URL |
