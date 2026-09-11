"""
agent/agent.py

Gemini ADK powered Contribution Bot agent.

Entry points:
    run_agent_async(user_message, user_email, history) -> (reply, updated_history)

    history format:
        [
            {"role": "user",      "text": "what's my bonus?"},
            {"role": "assistant", "text": "Your bonus is $225..."},
            ...
        ]

    The caller (api/messages.py) manages session storage.
    This module only handles the LLM interaction.

Environment variables:
    GOOGLE_API_KEY  — Gemini API key (set in Vercel env vars)
    GEMINI_MODEL    — optional, defaults to gemini-2.5-flash
"""

from __future__ import annotations

import asyncio
import os
from typing import List, Dict, Tuple

from google.adk.agents import LlmAgent
from google.adk.runners import Runner
from google.adk.sessions import InMemorySessionService
from google.genai import types as genai_types

from .tools import get_contribution, get_program_info

# ---------------------------------------------------------------------------
# Model
# ---------------------------------------------------------------------------
MODEL = os.environ.get("GEMINI_MODEL", "gemini-2.5-flash")

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
- The user's email is provided in the context at the start — use it directly
- If not found, explain possible reasons and suggest contacting the admin
- Keep replies short and focused
- Remember the conversation context from previous messages in this session

When showing contribution results, format like this:
  Hi [Name]! Here's your Campus Junior Training summary:
  • Eligible points: [X]
  • Sessions counted: [N] ([breakdown])
  • Bonus at $15/point: $[amount]
  Data reflects the latest export refresh. Recent sessions may not appear yet.
"""


# ---------------------------------------------------------------------------
# Agent factory
# ---------------------------------------------------------------------------
def _make_agent() -> LlmAgent:
    return LlmAgent(
        model=MODEL,
        name="contribution_bot",
        description="Looks up Campus Junior Training contribution points and bonus.",
        instruction=SYSTEM_PROMPT,
        tools=[get_contribution, get_program_info],
    )


# ---------------------------------------------------------------------------
# Main async entry point
# ---------------------------------------------------------------------------
async def run_agent_async(
    user_message: str,
    user_email: str = "",
    history: List[Dict] = None,
) -> Tuple[str, List[Dict]]:
    """
    Run the agent for one turn. Returns (reply, updated_history).

    Args:
        user_message: text the user sent
        user_email:   verified EPAM email from Teams identity
        history:      previous messages in this session (list of {role, text})

    Returns:
        (reply_text, updated_history)
        updated_history includes the new user message and bot reply appended.
    """
    if history is None:
        history = []

    # Build the full message with email context injected once (only if history is empty)
    if user_email and not history:
        # First message in session — inject email context
        full_message = (
            f"[Context: the user's verified EPAM email is {user_email}. "
            f"Use this email when calling get_contribution.]\n\n"
            f"{user_message}"
        )
    else:
        full_message = user_message

    reply = await _run_async(full_message, user_email, history)

    # Update history
    updated_history = history + [
        {"role": "user",      "text": user_message},
        {"role": "assistant", "text": reply},
    ]

    return reply, updated_history


# ---------------------------------------------------------------------------
# ADK runner
# ---------------------------------------------------------------------------
async def _run_async(
    message: str,
    session_id_key: str,
    history: List[Dict],
) -> str:
    """Execute the ADK agent with conversation history."""
    session_service = InMemorySessionService()
    session_id = session_id_key or "anonymous"
    app_name   = "contribution_bot"

    # Create session
    await session_service.create_session(
        app_name=app_name,
        user_id=session_id,
        session_id=session_id,
    )

    agent  = _make_agent()
    runner = Runner(
        agent=agent,
        app_name=app_name,
        session_service=session_service,
    )

    # Build contents list — inject history + new message
    # ADK accepts a list of Content objects for multi-turn context
    contents = []

    # Add previous turns from history
    for turn in history:
        role = "user" if turn["role"] == "user" else "model"
        contents.append(
            genai_types.Content(
                role=role,
                parts=[genai_types.Part(text=turn["text"])]
            )
        )

    # Add current message
    contents.append(
        genai_types.Content(
            role="user",
            parts=[genai_types.Part(text=message)]
        )
    )

    # Use last content as new_message, pass rest as prior context
    new_message = contents[-1]

    final_reply = ""
    async for event in runner.run_async(
        user_id=session_id,
        session_id=session_id,
        new_message=new_message,
    ):
        if event.is_final_response():
            if event.content and event.content.parts:
                final_reply = "".join(
                    part.text
                    for part in event.content.parts
                    if hasattr(part, "text") and part.text
                )

    return final_reply or "I'm sorry, I couldn't generate a response. Please try again."


# ---------------------------------------------------------------------------
# Sync wrapper (for test_agent.py)
# ---------------------------------------------------------------------------
def run_agent(
    user_message: str,
    user_email: str = "",
    history: List[Dict] = None,
) -> Tuple[str, List[Dict]]:
    """Synchronous wrapper for test_agent.py."""
    return asyncio.run(run_agent_async(user_message, user_email, history or []))
