"""
agent/agent.py

Contribution Bot — Gemini-powered conversational agent.

Architecture: direct Gemini API calls with tool use.
- Uses GeminiKeyPool for automatic key rotation on 429 (no sleep, immediate failover)
- Conversation history passed explicitly as Content list each turn
- prefetched_context: contribution data pre-fetched in app.py and injected
  into the system prompt so Gemini answers in ONE round trip for most queries

Entry points:
    run_agent_async(user_message, user_email, history, prefetched_context, is_admin)
    run_agent(...)  [sync wrapper]
"""

from __future__ import annotations

import asyncio
import json
import os
from datetime import date
from typing import List, Dict, Tuple, Optional

import google.genai as genai
from google.genai import types
from google.genai import errors as genai_errors

from .tools import (
    get_contribution,
    get_session_details,
    get_contributions_in_period,
    query_contributions,
    get_all_contributors,
    get_top_contributors,
)
from .key_pool import get_pool, AllKeysExhausted

MODEL = os.environ.get("GEMINI_MODEL", "gemini-2.0-flash")


# ---------------------------------------------------------------------------
# Tool registry
# ---------------------------------------------------------------------------
_TOOLS = {
    "get_contribution":            get_contribution,
    "get_session_details":         get_session_details,
    "get_contributions_in_period": get_contributions_in_period,
    "query_contributions":         query_contributions,
    "get_all_contributors":        get_all_contributors,
    "get_top_contributors":        get_top_contributors,
}

_TOOL_CONFIG = types.Tool(function_declarations=[
    types.FunctionDeclaration(
        name="get_contribution",
        description=(
            "Look up contribution summary for ONE person by EPAM email. "
            "Returns: eligible_points, bonus_usd, row_count, included_statuses, "
            "learning_categories, learning_paths, programs. "
            "Use for personal data questions (points, bonus, earnings). "
            "Email is already in session context — do NOT ask the user."
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
        name="get_session_details",
        description=(
            "Get detailed session data for a contributor by EPAM email OR full/partial name. "
            "Returns all eligible sessions with: learning path name, learning category "
            "(e.g. JavaScript, Java, .NET), program name, role, block name, activity dates. "
            "Use this when asked: which learning paths/categories/programs someone contributed to, "
            "session history, what practices they worked on, mentee details. "
            "Accepts full name (e.g. 'Saikrishna Tammi') or email."
        ),
        parameters=types.Schema(
            type=types.Type.OBJECT,
            properties={
                "email_or_name": types.Schema(
                    type=types.Type.STRING,
                    description="EPAM email OR contributor's full or partial name"
                )
            },
            required=["email_or_name"]
        )
    ),
    types.FunctionDeclaration(
        name="get_contributions_in_period",
        description=(
            "Get contribution sessions for a person filtered by date range. "
            "Use for: 'what did I contribute to last month?', 'my sessions from Jan to March', "
            "'contributions between 2026-01-01 and 2026-06-30', "
            "'how many points did I earn in Q1?', 'learning paths I worked on this year'. "
            "Dates MUST be YYYY-MM-DD. Derive relative dates (last month, this year, Q1) "
            "from today's date in the system prompt. "
            "Email is already in session context — do NOT ask the user."
        ),
        parameters=types.Schema(
            type=types.Type.OBJECT,
            properties={
                "email_or_name": types.Schema(
                    type=types.Type.STRING,
                    description="EPAM email OR contributor's full or partial name"
                ),
                "start_date": types.Schema(
                    type=types.Type.STRING,
                    description="Start date YYYY-MM-DD (inclusive). Empty = no lower bound."
                ),
                "end_date": types.Schema(
                    type=types.Type.STRING,
                    description="End date YYYY-MM-DD (inclusive). Empty = no upper bound."
                ),
            },
            required=["email_or_name"]
        )
    ),
    types.FunctionDeclaration(
        name="query_contributions",
        description=(
            "PRIMARY generic query tool — filter one person's sessions by ANY combination of fields. "
            "Use for any query mentioning category, path, program, role, status, dates, or any mix. "
            "IMPORTANT: use group_by whenever the user asks for a breakdown, summary, or 'how much per X' — "
            "it returns aggregated rows instead of raw sessions, keeping the response tiny. "
            "Examples: "
            "'Java contributions last two months' → learning_category='java', start_date=..., end_date=...; "
            "'points by learning category' → group_by='learning_category'; "
            "'sessions per month this year' → start_date='YYYY-01-01', group_by='month'; "
            "'approved sessions in Explora' → program='explora', status='approved'; "
            "'breakdown of my contributions' → group_by='learning_category' or group_by='role'. "
            "All text filters are partial, case-insensitive. Derive dates from today. "
            "Email is already in session context."
        ),
        parameters=types.Schema(
            type=types.Type.OBJECT,
            properties={
                "email_or_name": types.Schema(
                    type=types.Type.STRING,
                    description="EPAM email OR full or partial name"
                ),
                "learning_category": types.Schema(
                    type=types.Type.STRING,
                    description="Partial match on category, e.g. 'java', 'javascript', '.net', 'business analysis'"
                ),
                "learning_path": types.Schema(
                    type=types.Type.STRING,
                    description="Partial match on learning path name, e.g. 'fullstack', 'jan batch', 'microlearning'"
                ),
                "program": types.Schema(
                    type=types.Type.STRING,
                    description="Partial match on program name, e.g. 'explora', 'specialization', 'campus'"
                ),
                "role": types.Schema(
                    type=types.Type.STRING,
                    description="Partial match on role: 'mentor', 'reviewer', 'coordinator', 'trainer'"
                ),
                "status": types.Schema(
                    type=types.Type.STRING,
                    description="Contribution status: 'approved', 'submitted'"
                ),
                "learning_status": types.Schema(
                    type=types.Type.STRING,
                    description="Learning delivery status: 'delivered', 'inprogress', 'draft', 'archived'"
                ),
                "start_date": types.Schema(
                    type=types.Type.STRING,
                    description="Start date YYYY-MM-DD (inclusive)"
                ),
                "end_date": types.Schema(
                    type=types.Type.STRING,
                    description="End date YYYY-MM-DD (inclusive)"
                ),
                "group_by": types.Schema(
                    type=types.Type.STRING,
                    description=(
                        "Aggregate sessions by this dimension instead of returning raw list. "
                        "Values: 'learning_category', 'learning_path', 'program', 'role', "
                        "'status', 'learning_status', 'month', 'year'. "
                        "Each group returns: {group, session_count, points, bonus_usd, total_mentees, total_duration_min}. "
                        "Use this for any breakdown/summary question to avoid sending raw sessions."
                    )
                ),
            },
            required=["email_or_name"]
        )
    ),
    types.FunctionDeclaration(
        name="get_all_contributors",
        description=(
            "Return ALL contributors with eligible data. "
            "ADMIN ONLY. Use for: 'list everyone', 'how many people', 'total program stats'."
        ),
        parameters=types.Schema(type=types.Type.OBJECT, properties={})
    ),
    types.FunctionDeclaration(
        name="get_top_contributors",
        description=(
            "Return top N contributors ranked by eligible points (highest first). "
            "ADMIN ONLY. Use for: 'who has the most points', 'leaderboard', 'top contributors'."
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
_BASE_SYSTEM_PROMPT = """You are Kitrak Campus — Contribution Assistant for EPAM Systems.
You help contributors (Mentors, SMEs, Scrum Masters, Product Owners) in the Campus Junior
Training program check their contribution points, bonus, and learning activity history.

== ORGANISATION CONTEXT ==
EPAM runs a Campus Junior Training program that trains new junior employees.
Contributors support these juniors by:
  - Mentoring them in technical disciplines (JavaScript, Java, .NET, Python, etc.)
  - Running "Group Meeting with contributor" training sessions
  - Contributing to Learning Paths across one or more technology categories

The data is organised as:
  Learning Category  — technology/discipline area (e.g. "JavaScript", "Java", "Python")
  Learning Path      — specific training program (e.g. "JavaScript fullstack - Jan Batch 01 - 2026")
  Program Name       — umbrella program containing multiple learning paths
                       (e.g. "[Explora][Master] JavaScript fullstack - Campus India")

Contributors typically serve a specific practice or multiple practices. Their sessions
are linked to a Learning Path and Category — so you can answer questions like
"what learning paths did I contribute to?" or "which categories did I work in?".

== PROGRAM RULES ==
Program:           EPAM Campus Junior Training
Eligible roles:    Mentor, SME, Scrum Master, Product Owner
Eligible format:   "Group Meeting with contributor" sessions ONLY
Eligible statuses: Submitted, Approved
Point value:       $15 per eligible point
Bonus formula:     bonus_usd = eligible_points × $15
Points source:     Verified Points if available, otherwise Submitted Points
Data freshness:    Refreshed from Learn export on each admin upload. Recent sessions may lag.

== DATA SCHEMA (summary — from get_contribution) ==
  email                — EPAM email address (unique key)
  name                 — Full name
  eligible_points      — Total points from eligible sessions
  bonus_usd            — eligible_points × $15
  row_count            — Number of eligible sessions counted
  included_statuses    — e.g. "12 submitted"
  learning_categories  — Technology areas contributed to
  learning_paths       — Learning path names contributed to
  programs             — Umbrella program names
  total_mentees        — Total mentees across all sessions
  total_duration_min   — Total session time in minutes

== DATA SCHEMA (per-session — from get_session_details / query_contributions) ==
  learning          — Learning path/activity name
  learning_category — Technology category (JavaScript, Java, Business Analysis, etc.)
  program_name      — Umbrella program name
  role              — Mentor | Reviewer | Coordinator | Trainer
  block_name        — Session block/topic name
  status            — submitted | approved
  learning_status   — Delivered | InProgress | Draft | Archived
  points            — Points earned for that session
  total_mentees     — Number of mentees in that session
  duration_min      — Session duration in minutes
  activity_start    — Session start date (YYYY-MM-DD)
  activity_end      — Session end date (YYYY-MM-DD)

== TOOLS ==
- get_contribution(email)                      — Points/bonus summary. Use pre-fetched data first.
- get_session_details(email_or_name)           — All sessions, no filtering. Use when no filters mentioned.
- query_contributions(email_or_name, ...)      — PRIMARY tool for any filtered query. Accepts: learning_category, learning_path, program, role, status, start_date, end_date — any combination. All text filters are partial/case-insensitive. USE THIS for any query mixing filters.
- get_contributions_in_period(email_or_name, start_date, end_date) — Date-only shortcut. Use only when the query is purely date-based with no other filters.
- get_all_contributors()                       — All contributors — ADMIN ONLY
- get_top_contributors(n)                      — Top N by points — ADMIN ONLY

== TOOL SELECTION GUIDE ==
  Any filter OR breakdown OR summary query         → query_contributions (use group_by for breakdowns)
  "show all my sessions / full history"            → get_session_details
  "my points / bonus / summary"                    → pre-fetch or get_contribution
  Date range only, no other filters                → get_contributions_in_period

== DATE HANDLING ==
Today: {today}
Derive absolute YYYY-MM-DD dates from relative phrases before calling tools:
  "last month"        → first day ... last day of the month before today
  "this month"        → first day of this month ... today
  "this year"         → YYYY-01-01 ... today
  "Q1 2026"           → 2026-01-01 ... 2026-03-31
  "last 3 months"     → today minus 90 days ... today
  "last two months"   → today minus 60 days ... today
  "from Jan to March" → infer the most recent Jan 1 ... March 31
Never ask the user to provide dates — compute them yourself.

== CLARIFICATION RULES ==
Ask ONE focused question before calling a tool when the query is genuinely ambiguous or incomplete:
  - "Java" could mean learning_category OR learning_path — if unclear, ask "Do you mean the Java category or a specific Java learning path?"
  - "last year" is fine to compute; "few months ago" is vague — ask for the timeframe
  - If a query could apply to multiple people and the user isn't an admin, confirm whose data they want
  - Only ask if it will materially change the result. If you can make a reasonable inference, do it and state your assumption in the reply.
  - Never ask more than one question at a time. Never ask for email.

== GROUP_BY USAGE ==
  Use group_by whenever the user wants a breakdown, comparison, or "how much per X":
    "breakdown by category"     → group_by="learning_category"
    "how many sessions per month" → group_by="month"
    "points by role"             → group_by="role"
    "sessions this year by path" → start_date="YYYY-01-01", group_by="learning_path"
  When group_by is used the response will have "groups" not "sessions" — each group has:
    {group, session_count, points, bonus_usd, total_mentees, total_duration_min}
  Present this as a table or bullet list, sorted by points (already sorted by the tool).

== RESPONSE RULES ==
- Be friendly, concise, and professional
- Address the user by name when known
- NEVER ask the user for their email — it is already in session context
- For any filtered query (category, path, program, role, status, dates, or any combo) → use query_contributions
- For breakdown/summary questions → use query_contributions with group_by
- For "my points / bonus / summary" → use pre-fetched data or get_contribution
- When showing duration, convert minutes to hours (e.g. 120 min = 2 hrs)
- Remember context from earlier in this conversation for follow-up questions
- Keep replies short and focused; use bullet points or tables for structured data

Format for personal contribution results:
  Hi [Name]! Here's your Campus Junior Training summary:
  • Eligible points: [X]
  • Sessions counted: [N] ([breakdown])
  • Bonus at $15/point: $[amount]
  • Learning categories: [list]
  • Learning paths: [list]
  Data reflects the latest export. Recent sessions may not appear yet."""

_ADMIN_AUTH = """\n\n== AUTHORIZATION ==
This user IS an administrator. They can view any contributor's data and ask
aggregate questions (top contributors, full list, program stats, etc.).
All tools are available."""

_USER_AUTH_TEMPLATE = """\n\n== AUTHORIZATION ==
This user is a regular contributor (NOT an admin).
You MUST ONLY answer questions about their own data (email: {email}).
- If asked about someone else's contributions → politely refuse; say only their own data is visible.
- Do NOT call get_all_contributors or get_top_contributors for non-admin users.
- For get_session_details, only call with the user's own email or name."""


def _build_system_prompt(
    prefetched_context: Optional[dict],
    is_admin: bool = False,
    user_email: str = "",
) -> str:
    auth = _ADMIN_AUTH if is_admin else _USER_AUTH_TEMPLATE.format(email=user_email)
    base = _BASE_SYSTEM_PROMPT.replace("{today}", date.today().isoformat()) + auth

    if prefetched_context:
        base += (
            "\n\n[PRE-FETCHED: User's contribution data is already loaded — "
            "use it directly without calling get_contribution unless the user asks about someone else]\n"
            + json.dumps(prefetched_context, indent=2)
        )
    return base


# ---------------------------------------------------------------------------
# Pool-aware generate call
# ---------------------------------------------------------------------------
async def _call_with_pool(pool, model: str, contents, config) -> types.GenerateContentResponse:
    """
    Call generate_content with automatic key rotation on 429 and retry on 5xx.

    Error strategy:
      ClientError 429 / RESOURCE_EXHAUSTED → mark key cooling/exhausted, rotate immediately
      ServerError 5xx / transient           → retry same key up to 3 times with backoff,
                                              then rotate to next key
      ClientError 4xx (bad request, etc.)  → raise immediately (caller bug, not key issue)
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
            except genai_errors.ClientError as e:
                err = str(e)
                if "429" in err or "RESOURCE_EXHAUSTED" in err:
                    client, key_idx = pool.report_429(key_idx, err)
                    break  # go to next rotation
                raise  # 400 bad-request etc. — not a key issue, raise immediately

            except genai_errors.ServerError as e:
                # 500 / 502 / 503 from Gemini backend — transient, retry with backoff
                if attempt < 2:
                    wait = 2 ** attempt  # 1s, 2s
                    print(f"[agent] Server error key {key_idx+1} attempt {attempt+1}, retry in {wait}s: {str(e)[:120]}", flush=True)
                    await asyncio.sleep(wait)
                    continue
                # 3 attempts failed on this key — rotate to next
                print(f"[agent] Server error persists on key {key_idx+1} after 3 attempts, rotating.", flush=True)
                client, key_idx = pool.report_429(key_idx, str(e))
                break

            except Exception as e:
                # Fallback for SDK versions that don't raise typed errors
                err = str(e)
                if "429" in err or "RESOURCE_EXHAUSTED" in err:
                    client, key_idx = pool.report_429(key_idx, err)
                    break
                if ("500" in err or "503" in err or "502" in err or "UNAVAILABLE" in err) and attempt < 2:
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
    is_admin: bool = False,
) -> Tuple[str, List[Dict]]:
    """
    Run one conversation turn. Returns (reply_text, updated_history).

    Args:
        user_message:       text from the user
        user_email:         verified Teams email
        history:            previous turns [{role, text}, ...]
        prefetched_context: get_contribution result already fetched in app.py
        is_admin:           whether the user is an admin (can see all contributors)
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

    system_prompt = _build_system_prompt(prefetched_context, is_admin, user_email)
    config = types.GenerateContentConfig(
        system_instruction=system_prompt,
        tools=[_TOOL_CONFIG],
        temperature=0.2,
    )

    # Cap history to last 8 messages (4 turns) to keep token count low
    if len(history) > 8:
        history = history[-8:]

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
        for iteration in range(max_iterations):
            response = await _call_with_pool(pool, MODEL, contents, config)

            # Guard: empty candidates means blocked/safety-filtered response
            if not response.candidates:
                finish = getattr(response, "prompt_feedback", None)
                print(f"[agent] iter={iteration} empty candidates, feedback={finish}", flush=True)
                final_reply = "I couldn't process that request. Please try rephrasing."
                break

            candidate = response.candidates[0]

            # Guard: content can be None when finish_reason is SAFETY / RECITATION
            if candidate.content is None:
                finish = getattr(candidate, "finish_reason", "unknown")
                print(f"[agent] iter={iteration} candidate.content=None finish_reason={finish}", flush=True)
                final_reply = "I couldn't process that request. Please try rephrasing."
                break

            parts = candidate.content.parts or []
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
                print(f"[agent] iter={iteration} tool={fn_name} args={fn_args}", flush=True)
                try:
                    result = _TOOLS[fn_name](**fn_args) if fn_name in _TOOLS else {"error": f"Unknown tool: {fn_name}"}
                except Exception as tool_err:
                    print(f"[agent] tool={fn_name} raised: {tool_err}", flush=True)
                    result = {"error": str(tool_err)}
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
    except Exception as e:
        print(f"[agent] Unexpected error in agent loop: {type(e).__name__}: {e}", flush=True)
        raise  # let app.py log the traceback; don't swallow unexpected errors silently

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
    is_admin: bool = False,
) -> Tuple[str, List[Dict]]:
    return asyncio.run(
        run_agent_async(user_message, user_email, history or [], prefetched_context, is_admin)
    )
