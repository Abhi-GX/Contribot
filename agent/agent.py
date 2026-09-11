"""
agent/agent.py

Contribution Bot — Gemini-powered conversational agent.

Architecture: direct Gemini API calls with tool use.
- Uses google-genai client directly (no ADK overhead)
- Conversation history passed explicitly as Content list each turn
- prefetched_context: contribution data pre-fetched in messages.py and injected
  into the system prompt so Gemini can answer in ONE round trip for most queries

Entry points:
    run_agent_async(user_message, user_email, history, prefetched_context)
    run_agent(...)  [sync wrapper]
"""

from __future__ import annotations

import asyncio
import json
import os
import re
from typing import List, Dict, Tuple, Optional

import google.genai as genai
from google.genai import types

from .tools import get_contribution, get_all_contributors, get_top_contributors, get_program_info

MODEL = os.environ.get("GEMINI_MODEL", "gemini-3.6-flash")
_client: genai.Client | None = None


def _get_client() -> genai.Client:
    global _client
    if _client is None:
        api_key = os.environ.get("GOOGLE_API_KEY", "")
        if not api_key:
            raise RuntimeError("GOOGLE_API_KEY environment variable not set.")
        _client = genai.Client(api_key=api_key)
    return _client


# ---------------------------------------------------------------------------
# Tool registry
# ---------------------------------------------------------------------------
_TOOLS = {
    "get_contribution":     get_contribution,
    "get_all_contributors": get_all_contributors,
    "get_top_contributors": get_top_contributors,
    "get_program_info":     get_program_info,
}

_TOOL_CONFIG = types.Tool(function_declarations=[
    types.FunctionDeclaration(
        name="get_contribution",
        description=(
            "Look up contribution data for ONE person by EPAM email. "
            "Returns eligible_points, bonus_usd, row_count, included_statuses. "
            "Call for personal data questions. Email is already in session context."
        ),
        parameters=types.Schema(
            type=types.Type.OBJECT,
            properties={
                "email": types.Schema(
                    type=types.Type.STRING,
                    description="EPAM email address, e.g. john_doe@epam.com"
                )
            },
            required=["email"]
        )
    ),
    types.FunctionDeclaration(
        name="get_all_contributors",
        description=(
            "Return ALL contributors with eligible data. "
            "Use for 'list everyone', 'how many people have points', 'total program stats'."
        ),
        parameters=types.Schema(type=types.Type.OBJECT, properties={})
    ),
    types.FunctionDeclaration(
        name="get_top_contributors",
        description=(
            "Return top N contributors ranked by eligible points (highest first). "
            "Use for 'who has the most points', 'leaderboard', 'top contributors'."
        ),
        parameters=types.Schema(
            type=types.Type.OBJECT,
            properties={
                "n": types.Schema(
                    type=types.Type.INTEGER,
                    description="Number of top contributors to return (default 5)"
                )
            }
        )
    ),
    types.FunctionDeclaration(
        name="get_program_info",
        description=(
            "Returns program rules: eligible formats, statuses, bonus rate, CSV schema. "
            "Call for general program questions, NOT personal data."
        ),
        parameters=types.Schema(type=types.Type.OBJECT, properties={})
    ),
])

# ---------------------------------------------------------------------------
# System prompt
# ---------------------------------------------------------------------------
_BASE_SYSTEM_PROMPT = """You are the Campus Junior Training Contribution Bot for EPAM Systems.
You help Mentors, SMEs, Scrum Masters, and Product Owners check their eligible
contribution points and bonus amount.

Rules:
- Be friendly, concise, and professional
- Address the user by name when known
- For personal data: use get_contribution(email) — the email is in session context
- For all contributors: use get_all_contributors()
- For leaderboard/rankings: use get_top_contributors(n)
- For program rules: use get_program_info()
- NEVER ask the user for their email — it is already provided in session context
- Remember context from earlier in this conversation for follow-up questions
- Keep replies short and focused

CSV data schema (what's available per person):
  email, name, eligible_points, bonus_usd, row_count, included_statuses

Format for personal contribution results:
  Hi [Name]! Here's your Campus Junior Training summary:
  • Eligible points: [X]
  • Sessions counted: [N] ([breakdown])
  • Bonus at $15/point: $[amount]
  Data reflects the latest export. Recent sessions may not appear yet."""


def _build_system_prompt(prefetched_context: Optional[dict]) -> str:
    if not prefetched_context:
        return _BASE_SYSTEM_PROMPT
    return (
        _BASE_SYSTEM_PROMPT
        + f"\n\n[PRE-FETCHED: User's contribution data is already loaded — "
        f"use it directly without calling get_contribution unless the user asks about someone else]\n"
        + json.dumps(prefetched_context, indent=2)
    )


# ---------------------------------------------------------------------------
# Core async function
# ---------------------------------------------------------------------------
async def run_agent_async(
    user_message: str,
    user_email: str = "",
    history: List[Dict] = None,
    prefetched_context: Optional[dict] = None,
) -> Tuple[str, List[Dict]]:
    """
    Run one conversation turn. Returns (reply_text, updated_history).

    Args:
        user_message:       text from the user
        user_email:         verified Teams email
        history:            previous turns [{role, text}, ...]
        prefetched_context: get_contribution result already fetched in messages.py
    """
    if history is None:
        history = []

    client = _get_client()
    system_prompt = _build_system_prompt(prefetched_context)

    # Build Gemini contents list from history
    contents: List[types.Content] = []

    if not history and user_email:
        contents.append(types.Content(
            role="user",
            parts=[types.Part(text=(
                f"[SESSION: my EPAM email is {user_email}. "
                f"Do not ask for my email.]\n\n{user_message}"
            ))]
        ))
    else:
        for turn in history:
            role = "user" if turn["role"] == "user" else "model"
            contents.append(types.Content(
                role=role,
                parts=[types.Part(text=turn["text"])]
            ))
        contents.append(types.Content(
            role="user",
            parts=[types.Part(text=user_message)]
        ))

    final_reply = ""
    max_iterations = 5

    for _ in range(max_iterations):
        for attempt in range(3):
            try:
                response = await client.aio.models.generate_content(
                    model=MODEL,
                    contents=contents,
                    config=types.GenerateContentConfig(
                        system_instruction=system_prompt,
                        tools=[_TOOL_CONFIG],
                        temperature=0.2,
                    )
                )
                break
            except Exception as e:
                err_str = str(e)
                if "503" in err_str or "UNAVAILABLE" in err_str:
                    if attempt < 2:
                        await asyncio.sleep(2 ** attempt)
                        continue
                elif "429" in err_str or "RESOURCE_EXHAUSTED" in err_str:
                    delay_match = re.search(r"retry in (\d+)", err_str, re.IGNORECASE)
                    delay = int(delay_match.group(1)) if delay_match else 3
                    if attempt < 2:
                        await asyncio.sleep(delay)
                        continue
                raise

        candidate = response.candidates[0]
        parts = candidate.content.parts

        tool_calls = [p for p in parts if p.function_call is not None]

        if not tool_calls:
            final_reply = "".join(
                p.text for p in parts if hasattr(p, "text") and p.text
            )
            break

        contents.append(types.Content(role="model", parts=parts))

        tool_results = []
        for part in tool_calls:
            fn_name = part.function_call.name
            fn_args = dict(part.function_call.args) if part.function_call.args else {}

            result = _TOOLS[fn_name](**fn_args) if fn_name in _TOOLS else {"error": f"Unknown tool: {fn_name}"}

            tool_results.append(types.Part(
                function_response=types.FunctionResponse(
                    name=fn_name,
                    response={"result": result}
                )
            ))

        contents.append(types.Content(role="user", parts=tool_results))

    if not final_reply:
        final_reply = "I'm sorry, I couldn't generate a response. Please try again."

    updated_history = history + [
        {"role": "user",      "text": user_message},
        {"role": "assistant", "text": final_reply},
    ]

    return final_reply, updated_history


def run_agent(
    user_message: str,
    user_email: str = "",
    history: List[Dict] = None,
    prefetched_context: Optional[dict] = None,
) -> Tuple[str, List[Dict]]:
    return asyncio.run(run_agent_async(user_message, user_email, history or [], prefetched_context))
