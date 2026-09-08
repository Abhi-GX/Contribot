"""
teams_bot.py

Microsoft Teams bot for the Campus Junior Training bonus program.

Any Mentor / SME / Scrum Master / Product Owner can message this bot
(1:1 or by @-mentioning it in a channel) and ask things like:
    "how many points do I have"
    "what's my bonus"
    "my contribution"
and it replies with their eligible points and bonus $, read from the
totals.json cache that refresh_export.py keeps up to date.

IDENTITY: the bot does NOT trust anything the user types as their name
or email. It resolves the sender's real email via Microsoft Graph
(TeamsInfo.get_member), using the Teams identity Azure AD already
verified when they logged into Teams. This is the whole reason totals.json
is keyed by email -- so a user can only ever see their own numbers.

Built on the Bot Framework SDK for Python (same framework used for any
Teams/Bot Framework channel bot).

REQUIRES (set as environment variables, see README.md):
    MicrosoftAppId
    MicrosoftAppPassword
    TOTALS_JSON_PATH   (optional, defaults to ./totals.json)

RUN LOCALLY (for testing with Bot Framework Emulator):
    pip install botbuilder-core botbuilder-schema aiohttp
    python teams_bot.py
    -> connect Bot Framework Emulator to http://localhost:3978/api/messages

DEPLOY: package this as an Azure App Service / Azure Function and wire
it to an Azure Bot Service registration (see README.md) with a Teams
channel enabled.
"""

from __future__ import annotations

import json
import os
import traceback
from datetime import datetime
from http import HTTPStatus
from pathlib import Path

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

APP_ID = os.environ.get("MicrosoftAppId", "")
APP_PASSWORD = os.environ.get("MicrosoftAppPassword", "")
TOTALS_PATH = Path(os.environ.get("TOTALS_JSON_PATH", "totals.json"))

TRIGGER_KEYWORDS = ("point", "bonus", "contribut", "hour")


def load_totals() -> dict:
    if not TOTALS_PATH.exists():
        return {}
    with open(TOTALS_PATH, "r", encoding="utf-8") as f:
        return json.load(f)


def format_reply(entry: dict) -> str:
    points = entry["eligible_points"]
    bonus = entry["bonus_usd"]
    rows = entry["row_count"]
    breakdown = entry.get("included_statuses", {})
    breakdown_str = ", ".join(f"{v} {k}" for k, v in breakdown.items())

    return (
        f"Here's your Campus Junior contribution total, **{entry['name']}**:\n\n"
        f"- Eligible points (Group Meeting sessions): **{points:g}**\n"
        f"- Sessions counted: {rows} ({breakdown_str})\n"
        f"- Bonus at $15/point: **${bonus:,.2f}**\n\n"
        f"_This reflects the last Learn export refresh. If you completed a "
        f"session very recently, it may not show up yet._"
    )


class ContributionBot(ActivityHandler):
    async def on_message_activity(self, turn_context: TurnContext):
        text = (turn_context.activity.text or "").strip()
        lower = text.lower()

        # Resolve the sender's real, verified identity via Teams/Graph --
        # never trust free text typed by the user for this.
        try:
            member = await TeamsInfo.get_member(
                turn_context, turn_context.activity.from_property.id
            )
            email = (member.email or member.user_principal_name or "").strip().lower()
        except Exception:
            email = ""

        if not email:
            await turn_context.send_activity(
                MessageFactory.text(
                    "I couldn't verify your identity through Teams, so I can't "
                    "safely look up your contribution total. Please try again, "
                    "and if this keeps happening, contact the program admin."
                )
            )
            return

        totals = load_totals()
        entry = totals.get(email)

        if entry is None:
            await turn_context.send_activity(
                MessageFactory.text(
                    f"I don't see any eligible contribution records yet for "
                    f"**{email}** in the latest Learn export "
                    f"(refreshed as of the data currently cached). This can mean:\n\n"
                    f"1. You haven't logged any Group Meeting sessions with a "
                    f"Submitted/Verified/Approved status yet, or\n"
                    f"2. Your Learn account email doesn't match your Teams email, or\n"
                    f"3. The data just hasn't refreshed since your latest session.\n\n"
                    f"If you believe this is wrong, contact the program admin."
                )
            )
            return

        await turn_context.send_activity(MessageFactory.text(format_reply(entry)))

    async def on_members_added_activity(self, members_added, turn_context: TurnContext):
        for member in members_added:
            if member.id != turn_context.activity.recipient.id:
                await turn_context.send_activity(
                    MessageFactory.text(
                        "Hi! I'm the Campus Junior Training contribution bot. "
                        "Message me anytime (e.g. \"what's my bonus?\") and I'll "
                        "look up your eligible points and bonus amount."
                    )
                )


SETTINGS = BotFrameworkAdapterSettings(APP_ID, APP_PASSWORD)
ADAPTER = BotFrameworkAdapter(SETTINGS)
BOT = ContributionBot()


async def on_error(context: TurnContext, error: Exception):
    print(f"\n [on_turn_error] unhandled error: {error}", flush=True)
    traceback.print_exc()
    await context.send_activity("Sorry, something went wrong on my end.")


ADAPTER.on_turn_error = on_error


async def messages(req: Request) -> Response:
    if "application/json" in req.headers.get("Content-Type", ""):
        body = await req.json()
    else:
        return Response(status=HTTPStatus.UNSUPPORTED_MEDIA_TYPE)

    activity = Activity().deserialize(body)
    auth_header = req.headers.get("Authorization", "")

    response = await ADAPTER.process_activity(activity, auth_header, BOT.on_turn)
    if response:
        return json_response(data=response.body, status=response.status)
    return Response(status=HTTPStatus.OK)


async def health(req: Request) -> Response:
    totals = load_totals()
    return json_response(
        {
            "status": "ok",
            "people_cached": len(totals),
            "totals_file": str(TOTALS_PATH),
            "checked_at": datetime.utcnow().isoformat(),
        }
    )


APP = web.Application()
APP.router.add_post("/api/messages", messages)
APP.router.add_get("/health", health)

if __name__ == "__main__":
    port = int(os.environ.get("PORT", 3978))
    web.run_app(APP, host="0.0.0.0", port=port)
