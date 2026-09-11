"""
agent/agent.py

Contribution Bot — Gemini-powered conversational agent.

Architecture: direct Gemini API calls with tool use.
- Uses GeminiKeyPool for automatic key rotation on 429 (no sleep, immediate failover)
- Conversation history passed explicitly as Content list each turn
- prefetched_context: contribution data pre-fetched in app.py and injected
  into the system prompt so Gemini answers in ONE round trip for most queries

Entry points:
    run_agent_async(user_message, user_email, history, prefetched_context)
    run_agent(...)  [sync wrapper]
"""

from __future__ import annotations

import asyncio
import json
import os
from typing import List, Dict, Tuple, Optional

import google.genai as genai
from google.genai import types

from .tools import get_contribution, get_all_contributors, get_top_contributors
from .key_pool import get_pool, AllKeysExhausted

MODEL = os.environ.get("GEMINI_MODEL", "gemini-3.6-flash")


# ---------------------------------------------------------------------------
# Tool registry
# ---------------------------------------------------------------------------
_TOOLS = {
    "get_contribution":     get_contribution,
    "get_all_contributors": get_all_contributors,
    "get_top_contributors": get_top_contributors,
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
])

# ---------------------------------------------------------------------------
# System prompt
# ---------------------------------------------------------------------------
_BASE_SYSTEM_PROMPT = """You are the Campus Junior Training Contribution Bot for EPAM Systems.
You help Mentors, SMEs, Scrum Masters, and Product Owners check their eligible
contribution points and bonus amount.

== PROGRAM RULES (answer these questions directly — no tool call needed) ==
Program:          EPAM Campus Junior Training
Eligible roles:   Mentor, SME, Scrum Master, Product Owner
Eligible format:  "Group Meeting with contributor" sessions ONLY
Eligible statuses: Submitted, Approved
  (Not Eligible, Rejected, Draft, Individual do NOT count)
Point value:      $15 per eligible point
Bonus formula:    bonus_usd = eligible_points × $15
Points source:    Verified Points if available, otherwise Submitted Points
Data freshness:   Refreshed from Learn export on each admin upload. Recent sessions may lag.

== DATA SCHEMA (per person in the dataset) ==
  email              — EPAM email address (unique key)
  name               — Full name
  eligible_points    — Total points from eligible sessions
  bonus_usd          — eligible_points × $15
  row_count          — Number of eligible sessions counted
  included_statuses  — Breakdown e.g. "6 approved / 9 submitted"

== TOOLS (call only when needed) ==
- get_contribution(email): personal data for ONE person (use pre-fetched data first)
- get_all_contributors():  full list — use for aggregate stats, "list everyone"
- get_top_contributors(n): top N by points — use for leaderboard queries

== RESPONSE RULES ==
- Be friendly, concise, and professional
- Address the user by name when known
- NEVER ask the user for their email — it is already in session context
- Remember context from earlier in this conversation for follow-up questions
- Keep replies short and focused

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
# Pool-aware generate call
# ---------------------------------------------------------------------------
async def _call_with_pool(pool, model: str, contents, config) -> types.GenerateContentResponse:
    """
    Call generate_content with automatic key rotation on 429 and retry on 503.
    - 429: immediately rotates to the next key (no sleep)
    - 503: retries up to 3 times with exponential backoff on the same key
    """
    client, key_idx = pool.get_active_client()

    for _rotation in range(len(pool._keys) + 1):
        for attempt in range(3):
            try:
                return await client.aio.models.generate_content(
                    model=model,
                    contents=contents,
                    config=config,
                )
            except Exception as e:
                err = str(e)
                if "429" in err or "RESOURCE_EXHAUSTED" in err:
                    # Rotate to next key immediately — no sleep
                    client, key_idx = pool.report_429(key_idx, err)
                    break   # break attempt loop; outer loop retries with new key
                if ("503" in err or "UNAVAILABLE" in err) and attempt < 2:
                    await asyncio.sleep(2 ** attempt)
                    continue
                raise

    raise AllKeysExhausted("Exhausted all key rotations without a successful response.")


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
        prefetched_context: get_contribution result already fetched in app.py
    """
    if history is None:
        history = []

    try:
        pool = get_pool()
    except AllKeysExhausted as e:
        msg = (
            "I'm temporarily unavailable — the AI quota is exhausted for all configured keys. "
            "Please try again after midnight Pacific Time."
        )
        print(f"[agent] AllKeysExhausted at startup: {e}", flush=True)
        return msg, history

    system_prompt = _build_system_prompt(prefetched_context)
    config = types.GenerateContentConfig(
        system_instruction=system_prompt,
        tools=[_TOOL_CONFIG],
        temperature=0.2,
    )

    # Cap history to last 8 messages (4 turns) to keep token count low
    if len(history) > 8:
        history = history[-8:]

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

    try:
        for _ in range(max_iterations):
            response = await _call_with_pool(pool, MODEL, contents, config)

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
                result  = _TOOLS[fn_name](**fn_args) if fn_name in _TOOLS else {"error": f"Unknown tool: {fn_name}"}
                tool_results.append(types.Part(
                    function_response=types.FunctionResponse(
                        name=fn_name,
                        response={"result": result}
                    )
                ))

            contents.append(types.Content(role="user", parts=tool_results))

    except AllKeysExhausted as e:
        print(f"[agent] AllKeysExhausted: {e}", flush=True)
        final_reply = (
            "I'm temporarily unavailable — the AI quota is exhausted for all configured keys. "
            "Please try again after midnight Pacific Time."
        )

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
