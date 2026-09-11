"""
teams_bot.py

Local aiohttp runner — use this for testing with the Bot Framework Emulator.
Connect the emulator to: http://localhost:3978/api/messages

Note: TeamsInfo.get_member won't work in the emulator (no real Teams context).
The agent will still run but without the user's email — it will respond to the
message without being able to look up contribution data unless you type your email.

REQUIRES:
    pip install -r requirements.txt
    Set GOOGLE_API_KEY in your environment or .env file.

    Environment variables:
        MicrosoftAppId
        MicrosoftAppPassword
        MicrosoftAppTenantId
        GOOGLE_API_KEY
"""

from __future__ import annotations

import os
import sys
import traceback
from http import HTTPStatus
from pathlib import Path

ROOT = Path(__file__).parent
sys.path.insert(0, str(ROOT))

from aiohttp import web
from aiohttp.web import Request, Response, json_response

from botbuilder.core import (
    BotFrameworkAdapter,
    BotFrameworkAdapterSettings,
    TurnContext,
    ActivityHandler,
    MessageFactory,
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

        # Try Teams identity resolution (works in Teams, not in Emulator)
        try:
            member = await TeamsInfo.get_member(
                turn_context, turn_context.activity.from_property.id
            )
            user_email = (member.email or member.user_principal_name or "").strip().lower()
        except Exception:
            user_email = ""

        try:
            reply = run_agent(user_message, user_email)
        except Exception as e:
            traceback.print_exc()
            reply = "Sorry, I ran into an issue. Please try again."

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


async def on_error(context: TurnContext, error: Exception):
    print(f"[on_turn_error] {error}", flush=True)
    traceback.print_exc()
    await context.send_activity("Sorry, something went wrong on my end.")


ADAPTER.on_turn_error = on_error


# ---------------------------------------------------------------------------
# aiohttp routes
# ---------------------------------------------------------------------------
async def messages(req: Request) -> Response:
    if "application/json" not in req.headers.get("Content-Type", ""):
        return Response(status=HTTPStatus.UNSUPPORTED_MEDIA_TYPE)

    body        = await req.json()
    activity    = Activity().deserialize(body)
    auth_header = req.headers.get("Authorization", "")

    response = await ADAPTER.process_activity(activity, auth_header, BOT.on_turn)
    if response:
        return json_response(data=response.body, status=response.status)
    return Response(status=HTTPStatus.OK)


async def health(req: Request) -> Response:
    return json_response({"status": "ok", "mode": "gemini-adk"})


APP = web.Application()
APP.router.add_post("/api/messages", messages)
APP.router.add_get("/health", health)

if __name__ == "__main__":
    port = int(os.environ.get("PORT", 3978))
    print(f"Bot running on http://localhost:{port}/api/messages")
    web.run_app(APP, host="0.0.0.0", port=port)
