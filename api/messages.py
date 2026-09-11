"""
api/messages.py

Vercel serverless function — handles POST /api/messages from Teams.

Every Teams message hits this endpoint. The Bot Framework SDK verifies
the JWT, then we resolve the sender's real email via TeamsInfo.get_member,
and pass the message + email to the Gemini ADK agent for a real AI response.

Environment variables (set in Vercel → Settings → Environment Variables):
    MicrosoftAppId         — Application (client) ID from Entra ID
    MicrosoftAppPassword   — Client secret value
    MicrosoftAppTenantId   — Directory (tenant) ID from Entra ID
    GOOGLE_API_KEY         — Gemini API key from aistudio.google.com
    GEMINI_MODEL           — optional, defaults to gemini-2.0-flash
    TOTALS_CSV_PATH        — optional, defaults to ./data/totals.csv
"""

from __future__ import annotations

import asyncio
import json
import os
import sys
import traceback
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler
from pathlib import Path

# Make project root importable so agent/ and data/ are reachable
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

from agent import run_agent

# ---------------------------------------------------------------------------
# Config
# ---------------------------------------------------------------------------
APP_ID       = os.environ.get("MicrosoftAppId",       "400dfdef-d35e-4561-95c1-57d11495c1de")
APP_PASSWORD = os.environ.get("MicrosoftAppPassword", "")
APP_TENANT   = os.environ.get("MicrosoftAppTenantId", "b41b72d0-4e9f-4c26-8a69-f949f367c91d")


# ---------------------------------------------------------------------------
# Bot — powered by Gemini ADK agent
# ---------------------------------------------------------------------------
class ContributionBot(ActivityHandler):
    async def on_message_activity(self, turn_context: TurnContext):
        user_message = (turn_context.activity.text or "").strip()
        if not user_message:
            return

        # Resolve verified Teams identity — never trust user-typed email
        try:
            member = await TeamsInfo.get_member(
                turn_context, turn_context.activity.from_property.id
            )
            user_email = (member.email or member.user_principal_name or "").strip().lower()
        except Exception:
            # TeamsInfo fails in Bot Emulator — gracefully degrade
            user_email = ""

        # Send typing indicator while agent thinks
        await turn_context.send_activity(Activity(type="typing"))

        try:
            # Run the Gemini ADK agent
            reply = run_agent(user_message, user_email)
        except Exception as e:
            traceback.print_exc()
            reply = (
                "Sorry, I ran into an issue processing your request. "
                "Please try again in a moment."
            )

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
# Adapter — Single Tenant (EPAM Entra ID)
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
    """Vercel Python serverless entry point."""

    def do_POST(self):
        content_length = int(self.headers.get("Content-Length", 0))
        raw_body = self.rfile.read(content_length)

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
            "mode": "gemini-adk"
        }).encode()
        self.send_response(HTTPStatus.OK)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)
