"""
agent/agent.py

Gemini ADK powered Contribution Bot agent.

This module defines the LlmAgent and exposes run_agent() which is the single
entry point called from api/messages.py for every incoming Teams message.

Architecture:
    Teams message
        └── api/messages.py calls run_agent(user_message, user_email)
                └── ADK Runner executes LlmAgent with tools
                        └── Gemini decides which tools to call
                                └── tools.py reads from data/totals.csv
                        └── Gemini returns natural language reply
                └── run_agent returns reply string
        └── api/messages.py sends reply back to Teams

Vercel constraint:
    Vercel is stateless — each request is a fresh process.
    We use InMemorySessionService with a fixed session_id derived from
    the user's email so within a single request the agent has context,
    but there is no cross-request memory (acceptable for now).

Environment variables:
    GOOGLE_API_KEY  — Gemini API key (set in Vercel env vars)
"""

from __future__ import annotations

import asyncio
import os
from pathlib import Path

from google.adk.agents import LlmAgent
from google.adk.runners import Runner
from google.adk.sessions import InMemorySessionService
from google.genai import types as genai_types

from .tools import get_contribution, get_program_info

# ---------------------------------------------------------------------------
# Model
# ---------------------------------------------------------------------------
MODEL = os.environ.get("GEMINI_MODEL", "gemini-3.6-flash")

# ---------------------------------------------------------------------------
# System prompt
# ---------------------------------------------------------------------------
SYSTEM_PROMPT = """
You are the Campus Junior Training Contribution Bot for EPAM Systems.
You help Mentors, SMEs, Scrum Masters, and Product Owners check their
eligible contribution points and bonus amount from the Campus Junior Training program.

Your personality:
- Friendly, concise, and professional
- Always address the user by name when you know it
- Format numbers clearly (e.g. "15 points", "$225.00 bonus")

What you can do:
- Look up a user's contribution points and bonus using get_contribution(email)
- Explain how the program works using get_program_info()

Rules:
- ALWAYS use get_contribution(email) to look up data — never make up numbers
- If the user's email is provided in the context, use it directly
- If the user is not found, explain possible reasons clearly and suggest contacting the admin
- Keep replies short and focused — this is a chat bot, not a report

When showing contribution results, format like this:
  Hi [Name]! Here's your Campus Junior Training summary:
  • Eligible points: [X]
  • Sessions counted: [N] ([breakdown])
  • Bonus at $15/point: $[amount]
  Data reflects the latest export refresh. Recent sessions may not appear yet.
"""

# ---------------------------------------------------------------------------
# Agent definition
# ---------------------------------------------------------------------------
def _make_agent() -> LlmAgent:
    return LlmAgent(
        model=MODEL,
        name="contribution_bot",
        description="Looks up Campus Junior Training contribution points and bonus for EPAM employees.",
        instruction=SYSTEM_PROMPT,
        tools=[get_contribution, get_program_info],
    )


# ---------------------------------------------------------------------------
# Main entry point
# ---------------------------------------------------------------------------
def run_agent(user_message: str, user_email: str = "") -> str:
    """
    Run the Gemini ADK agent for a single Teams message and return the reply.

    Args:
        user_message: The text the user sent in Teams.
        user_email:   The verified Teams email of the sender (resolved via
                      TeamsInfo.get_member in api/messages.py).
                      Empty string if identity resolution failed.

    Returns:
        The agent's reply as a plain string (may contain markdown).
    """
    # Build message — include email as context so the agent can call
    # get_contribution() without asking the user to type their email.
    if user_email:
        full_message = (
            f"[Context: the user's EPAM email is {user_email}]\n\n"
            f"{user_message}"
        )
    else:
        full_message = user_message

    # Run async agent in a new event loop (Vercel is sync per request)
    return asyncio.run(_run_async(full_message, user_email))


async def _run_async(message: str, session_id_key: str) -> str:
    """Async execution of the ADK runner."""
    session_service = InMemorySessionService()
    session_id = session_id_key or "anonymous"
    app_name = "contribution_bot"

    # Create a fresh session for this request
    session = await session_service.create_session(
        app_name=app_name,
        user_id=session_id,
        session_id=session_id,
    )

    agent = _make_agent()

    runner = Runner(
        agent=agent,
        app_name=app_name,
        session_service=session_service,
    )

    # Send message and collect the final text response
    content = genai_types.Content(
        role="user",
        parts=[genai_types.Part(text=message)]
    )

    final_reply = ""
    async for event in runner.run_async(
        user_id=session_id,
        session_id=session_id,
        new_message=content,
    ):
        # Capture the last text response from the agent
        if event.is_final_response():
            if event.content and event.content.parts:
                final_reply = "".join(
                    part.text for part in event.content.parts if hasattr(part, "text")
                )

    return final_reply or "I'm sorry, I couldn't generate a response. Please try again."
