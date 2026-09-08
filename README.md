# Campus Junior Training — Contribution Bonus Bot

Lets Mentors, SMEs, Scrum Masters, and Product Owners ask a Teams bot
"how many points/bonus $ do I have" and get an accurate answer, sourced
from EPAM Learn's contribution data.

## How it fits together

```
capture_login_session.py   (run once, interactively)
        |  saves learn_session.json (cookies)
        v
refresh_export.py          (run on a schedule, e.g. daily)
        |  logs in using the saved session, clicks Export on
        |  https://learn.epam.com/admin/contribution, downloads the .xlsx
        v
contribution_calc.py       (called automatically by refresh_export.py)
        |  filters to FORMAT=Group Meeting, STATUS in
        |  {Submitted, Verified, Approved}, sums points, x $15
        v
totals.json                 (cache: email -> points/bonus)
        ^
        |  reads on every question
teams_bot.py                (always running, listens for Teams messages)
        |  resolves the asker's real email via Microsoft Graph,
        |  looks them up in totals.json, replies
```

## Business rules baked in (confirm these are still correct before going live)

- Only **Group Meeting** format rows count. Individual sessions are excluded.
- Only rows with contribution status **Submitted, Verified, or Approved** count.
- Point value used: **Verified Points** if present, else raw **Points**.
- **1 point = $15.**

These live in `contribution_calc.py` (top of the file) — change them there if the program's rules change.

## Setup, step by step

### 1. Local environment

```bash
python3 -m venv venv
source venv/bin/activate        # Windows: venv\Scripts\activate
pip install pandas openpyxl playwright botbuilder-core botbuilder-schema aiohttp
playwright install chromium
```

### 2. Test the calculation engine on its own

Export a sample from Learn yourself (or use any .xlsx with the right columns) and run:

```bash
python contribution_calc.py your_export.xlsx --out totals.json --person "your_name_or_email"
```

Confirm the points/bonus $ match what you'd calculate by hand. This is the piece most worth double-checking — everything downstream trusts it.

### 3. Capture a logged-in session (one-time, interactive)

Run this **as the service/admin account** that should have standing access to the contribution page:

```bash
python capture_login_session.py
```

A real browser window opens. Log in through EPAM SSO as you normally would (MFA included). Once you can see the contribution table, go back to the terminal and press Enter. This saves `learn_session.json` — treat it as a secret (it's equivalent to a logged-in cookie, not a password, but still sensitive).

### 4. Test the automated refresh once, by hand

```bash
python refresh_export.py
```

This should download a fresh export and regenerate `totals.json` without opening a visible browser. If it fails:
- **"still looks like a login page"** → the session expired or never authenticated; re-run step 3.
- **"could not find the Export button"** → Learn's page structure differs from what's coded; open the page in a real browser, inspect the Export button's HTML, and add its selector to `EXPORT_BUTTON_SELECTORS` in `refresh_export.py`.

### 5. Schedule the refresh job

Pick a cadence (daily is a reasonable default). Options:
- **cron** (if running on a persistent Linux box): `0 6 * * * cd /path/to/bot && python refresh_export.py >> refresh.log 2>&1`
- **Windows Task Scheduler**: daily trigger running `python refresh_export.py`
- **Azure Function (Timer Trigger)** or **Azure Container Instance + cron**, if you want this cloud-hosted rather than on someone's machine

Whatever you pick, monitor for failures — a silently-expired session means the bot starts giving stale answers instead of erroring loudly.

### 6. Register the bot with Azure (needed for real Teams deployment)

This part needs someone with Azure admin rights in your tenant:

1. In the [Azure Portal](https://portal.azure.com), create an **Azure Bot** resource (Bot Framework).
2. Note the generated **App ID**; create a **client secret** (this is `MicrosoftAppPassword`).
3. Set the messaging endpoint to wherever `teams_bot.py` is hosted, e.g. `https://<your-app>.azurewebsites.net/api/messages`.
4. Under **Channels**, add the **Microsoft Teams** channel.
5. Grant the bot's app registration Graph API permission `User.Read.All` (or `TeamsAppInstallation.ReadForUser` + appropriate profile scopes) so `TeamsInfo.get_member` can resolve emails — an Azure AD admin needs to consent to this.

### 7. Host `teams_bot.py`

Any place that can run a small Python web service works — Azure App Service is the natural fit given step 6. Set these environment variables on the host:

```
MicrosoftAppId=<from step 6>
MicrosoftAppPassword=<from step 6>
TOTALS_JSON_PATH=/path/to/totals.json
```

Make sure the host running `teams_bot.py` can read the same `totals.json` that `refresh_export.py` writes (same machine, or a shared volume/blob).

### 8. Add the bot to Teams

Create a Teams app manifest (App Studio / Developer Portal in Teams) pointing at the App ID from step 6, package it, and upload it to your tenant's Teams app catalog (or sideload for testing). Once installed, anyone can message the bot directly.

## Verifying it's working end to end

1. Message the bot: "what's my bonus?"
2. It should resolve your email via Teams/Graph, look you up in `totals.json`, and reply with your points and $ — matching what you'd get running `contribution_calc.py` by hand on the latest export.
3. Check `/health` on the hosted bot (e.g. `https://<your-app>.azurewebsites.net/health`) — it reports how many people are currently cached and when.

## Known risks / things that can break this

- **Learn session expiry** — SSO sessions don't last forever. When `refresh_export.py` starts failing, re-run `capture_login_session.py`.
- **Learn UI changes** — the Export button's selector may need updating if Learn's frontend changes. `refresh_export.py` saves a screenshot on failure to make this diagnosable.
- **Email mismatches** — if someone's Learn account email differs from their Teams/Azure AD email, they won't be found. Worth a quick audit of a few stakeholders' emails across both systems before rollout.
- **Stale data window** — the bot is only as fresh as the last successful refresh. Consider having it state the last-refreshed time in replies if that matters to your stakeholders (the `/health` endpoint already exposes it for debugging).
- **This automates clicking a button an admin is already allowed to click** — it is not scraping a private API. Still, get sign-off from whoever owns Learn's terms of use for service-account automation, since automating any internal tool is worth a quick heads-up to its owner.

## Files

| File | Purpose | Run how |
|---|---|---|
| `contribution_calc.py` | Core points/bonus calculation | called by `refresh_export.py`, or standalone for testing |
| `capture_login_session.py` | One-time interactive Learn login | run by hand, occasionally |
| `refresh_export.py` | Recurring export + recalculation | scheduled (cron/Task Scheduler/Azure Function) |
| `teams_bot.py` | The Teams-facing bot | always running as a hosted service |
| `sample_export.xlsx` | Synthetic test fixture matching Learn's export schema | for testing `contribution_calc.py` only — not real data |
