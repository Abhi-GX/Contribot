"""
api/messages.py

Vercel serverless function — handles POST /api/messages from Teams.

Session management flow per message:
  1. Extract Teams user ID from activity (stable, unique per user)
  2. Load session from Redis KV
     - Hit:  use cached email + history (skip Graph API call)
     - Miss: call TeamsInfo.get_member ONCE to get email, create session
  3. Pass message + history to Gemini ADK agent
  4. Save updated session (email + last 6 messages) back to Redis
  5. Reply to Teams

This means TeamsInfo.get_member (slow Graph API) is called ONCE per session
(first message only), not on every message.

Environment variables (Vercel → Settings → Environment Variables):
    MicrosoftAppId         — Entra ID App ID
    MicrosoftAppPassword   — Entra ID client secret
    MicrosoftAppTenantId   — Entra ID tenant ID
    GOOGLE_API_KEY         — Gemini API key
    KV_REST_API_URL        — Upstash Redis URL (auto-set by Vercel KV)
    KV_REST_API_TOKEN      — Upstash Redis token (auto-set by Vercel KV)
"""

from __future__ import annotations

import json
import os
import sys
import traceback
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler
from pathlib import Path

ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(ROOT))

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
from session_store import get_session, save_session, create_session

# ---------------------------------------------------------------------------
# Config
# ---------------------------------------------------------------------------
APP_ID       = os.environ.get("MicrosoftAppId",       "400dfdef-d35e-4561-95c1-57d11495c1de")
APP_PASSWORD = os.environ.get("MicrosoftAppPassword", "")
APP_TENANT   = os.environ.get("MicrosoftAppTenantId", "b41b72d0-4e9f-4c26-8a69-f949f367c91d")


# ---------------------------------------------------------------------------
# Bot
# ---------------------------------------------------------------------------
class ContributionBot(ActivityHandler):

    async def on_message_activity(self, turn_context: TurnContext):
        user_message = (turn_context.activity.text or "").strip()
        if not user_message:
            return

        # Teams user ID — stable unique identifier per user, no API call needed
        teams_user_id = turn_context.activity.from_property.id

        # ── Session lookup ─────────────────────────────────────────────────
        session = await get_session(teams_user_id)

        if session is None:
            # First message from this user in this session window
            # Call TeamsInfo.get_member ONCE to get verified email
            try:
                member = await TeamsInfo.get_member(turn_context, teams_user_id)
                user_email = (
                    member.email or member.user_principal_name or ""
                ).strip().lower()
            except Exception:
                user_email = ""

            session = await create_session(teams_user_id, user_email)
        else:
            # Returning user — email already cached, skip Graph API call
            user_email = session.get("email", "")

        history = session.get("history", [])

        # ── Send typing indicator ──────────────────────────────────────────
        await turn_context.send_activity(Activity(type="typing"))

        # ── Run agent ─────────────────────────────────────────────────────
        try:
            reply, updated_history = await run_agent_async(
                user_message=user_message,
                user_email=user_email,
                history=history,
            )
        except Exception:
            traceback.print_exc()
            reply = "Sorry, I ran into an issue. Please try again in a moment."
            updated_history = history

        # ── Save updated session ───────────────────────────────────────────
        session["history"] = updated_history
        await save_session(teams_user_id, session)

        # ── Reply ──────────────────────────────────────────────────────────
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
                        "- `how does the program work?`"
                    )
                )


# ---------------------------------------------------------------------------
# Adapter
# ---------------------------------------------------------------------------
SETTINGS = BotFrameworkAdapterSettings(APP_ID, APP_PASSWORD, channel_auth_tenant=APP_TENANT)
ADAPTER  = BotFrameworkAdapter(SETTINGS)
BOT      = ContributionBot()


async def _on_error(context: TurnContext, error: Exception):
    print(f"[on_turn_error] {error}", flush=True)
    traceback.print_exc()
    await context.send_activity("Sorry, something went wrong on my end.")


ADAPTER.on_turn_error = _on_error


# ---------------------------------------------------------------------------
# Vercel handler
# ---------------------------------------------------------------------------
class handler(BaseHTTPRequestHandler):

    def do_POST(self):
        import asyncio
        content_length = int(self.headers.get("Content-Length", 0))
        raw_body       = self.rfile.read(content_length)

        if "application/json" not in self.headers.get("Content-Type", ""):
            self.send_response(HTTPStatus.UNSUPPORTED_MEDIA_TYPE)
            self.end_headers()
            return

        try:
            body = json.loads(raw_body)
        except json.JSONDecodeError:
            self.send_response(HTTPStatus.BAD_REQUEST)
            self.end_headers()
            return

        activity    = Activity().deserialize(body)
        auth_header = self.headers.get("Authorization", "")

        loop = asyncio.new_event_loop()
        try:
            invoke_response = loop.run_until_complete(
                ADAPTER.process_activity(activity, auth_header, BOT.on_turn)
            )
        finally:
            loop.close()

        if invoke_response:
            body_bytes = json.dumps(invoke_response.body).encode("utf-8")
            self.send_response(invoke_response.status)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body_bytes)))
            self.end_headers()
            self.wfile.write(body_bytes)
        else:
            self.send_response(HTTPStatus.OK)
            self.end_headers()

    def do_GET(self):
        body = json.dumps({
            "status": "ok",
            "endpoint": "/api/messages",
            "mode": "gemini-adk + session"
        }).encode()
        self.send_response(HTTPStatus.OK)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)
