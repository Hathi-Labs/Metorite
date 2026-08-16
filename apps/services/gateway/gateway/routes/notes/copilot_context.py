"""Meeting context — what the copilot knows before anyone speaks.

A copilot watching only the transcript can tell *that* an objection was raised,
but not who raised it or which deal it concerns — so its suggestions stay
generic. This module assembles the background that makes them specific, from
three independent sources (spec §5):

    Layer 1  BRIEF     what you told it        — `meeting.copilot_brief`
    Layer 2  HISTORY   what we already know    — past meetings with the same
                                                 attendees, their decisions, and
                                                 action items still open
    Layer 3  SYSTEMS   what the other agents know — CRM / tasks / email, via
                                                 ``call_agent`` (opt-in)

Two design rules, both load-bearing:

**Assembled once, before the conversation.** An agent call takes seconds; a
meeting moves in less. So the pack is built at session start and cached for the
session — the copilot carries it as compact background rather than fetching
mid-sentence. That also keeps context out of the per-suggestion token cost.

**Every layer degrades on its own.** No brief written, no past meetings, Zoho
not connected, an agent that errors — each just omits its section. There is no
combination of missing pieces that breaks the copilot; it simply knows less.

Layer 3 is **opt-in per session** (`live_session.deep_context`) because fanning
out to agents spends tokens before the meeting has produced a word — a quick
internal standup shouldn't trigger a CRM sweep.
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
from typing import Any

from acb_auth import UserContext, get_current_user
from fastapi import Depends, HTTPException
from gateway.routes.notes.core import _get_db, _log, _tenant_session, router
from pydantic import BaseModel
from sqlalchemy import text

# Keep the pack small — it rides in every copilot prompt, so it is pure
# recurring cost. These caps are what stop a long history from quietly
# doubling the price of every suggestion.
_MAX_PAST_MEETINGS = 3
_MAX_OPEN_ACTIONS = 6
_MAX_BRIEF_CHARS = 1200
_MAX_SYSTEM_CHARS = 800
_AGENT_TIMEOUT_S = 45.0

# Which agent answers which question. Names match the Metorite agent
# registry (/agents); a name that isn't registered simply yields nothing.
_AGENT_QUERIES: tuple[tuple[str, str, str], ...] = (
    (
        "crm",
        "agent-sales-assistant",
        "For the upcoming meeting with {people}, summarise in under 80 words: "
        "the company, deal stage/value if any, and the single most important "
        "open point. If you find nothing, reply exactly: NONE.",
    ),
    (
        "tasks",
        "task-manager",
        "List up to 4 open tasks involving {people}, one per line, as "
        "'title — status'. If there are none, reply exactly: NONE.",
    ),
)


@dataclass
class ContextPack:
    """Compact background for one meeting. Rendered into the copilot prompt."""

    brief: str = ""                       # Layer 1
    attendees: list[str] = field(default_factory=list)
    past: list[str] = field(default_factory=list)      # Layer 2
    open_actions: list[str] = field(default_factory=list)
    systems: dict[str, str] = field(default_factory=dict)  # Layer 3

    @property
    def is_empty(self) -> bool:
        return not (self.brief or self.past or self.open_actions or self.systems)

    def as_prompt(self) -> str:
        """Render for the model. Ordered by trust: what the human said first,
        then our own records, then whatever the other agents reported."""
        parts: list[str] = []
        if self.brief:
            parts.append(f"WHY THIS MEETING (from the user):\n{self.brief}")
        if self.attendees:
            parts.append(f"Attendees: {', '.join(self.attendees)}")
        if self.past:
            parts.append("PREVIOUSLY WITH THESE PEOPLE:\n" + "\n".join(
                f"- {p}" for p in self.past
            ))
        if self.open_actions:
            parts.append("STILL OPEN FROM LAST TIME:\n" + "\n".join(
                f"- {a}" for a in self.open_actions
            ))
        for label, body in self.systems.items():
            parts.append(f"{label.upper()}:\n{body}")
        return "\n\n".join(parts)


def _clip(s: str, limit: int) -> str:
    s = (s or "").strip()
    return s if len(s) <= limit else s[:limit].rstrip() + "…"


def attendee_names(raw: Any) -> list[str]:
    """Readable names from ``meeting.attendees`` (``[{name, email}, …]``)."""
    out: list[str] = []
    if not isinstance(raw, list):
        return out
    for a in raw:
        if not isinstance(a, dict):
            continue
        name = (a.get("name") or "").strip() or (a.get("email") or "").strip()
        if name and name not in out:
            out.append(name)
    return out


def attendee_emails(raw: Any) -> list[str]:
    out: list[str] = []
    if not isinstance(raw, list):
        return out
    for a in raw:
        if isinstance(a, dict):
            e = (a.get("email") or "").strip().lower()
            if e and e not in out:
                out.append(e)
    return out


def summarise_past(title: str | None, when: Any, summary: str | None) -> str:
    """One line per past meeting — enough to jog the copilot, not a transcript."""
    date = ""
    try:
        date = when.strftime("%d %b") if when is not None else ""
    except Exception:
        date = ""
    head = (title or "Untitled meeting").strip()
    gist = _clip((summary or "").replace("\n", " ").replace("#", ""), 180)
    return f"{head}{f' ({date})' if date else ''}{f': {gist}' if gist else ''}"


# ── Layers 1 + 2: brief and our own records (no external dependency) ────────

async def _local_context(meeting_id: str) -> ContextPack:
    # H4: shared helper also consumed by the copilot orchestrator background
    # task (`copilot._run` via `get()`), which has no ambient tenant; derive
    # it from the meeting row before moving this off `_get_db`.
    pack = ContextPack()
    async with await _get_db() as db:
        m = (
            await db.execute(
                text(
                    "SELECT title, attendees, copilot_brief, owner_email "
                    "FROM meeting WHERE id = CAST(:id AS UUID)"
                ),
                {"id": meeting_id},
            )
        ).fetchone()
        if m is None:
            return pack
        pack.brief = _clip(m.copilot_brief or "", _MAX_BRIEF_CHARS)
        pack.attendees = attendee_names(m.attendees)
        emails = attendee_emails(m.attendees)
        if not emails:
            return pack

        # Past meetings that shared at least one attendee email. The JSONB
        # containment check keeps this a single indexed-ish query rather than
        # pulling every meeting the user has ever had.
        rows = (
            await db.execute(
                text(
                    """
                    SELECT title, start_at, summary_md, id
                    FROM meeting
                    WHERE id <> CAST(:id AS UUID)
                      AND owner_email = :owner
                      AND summary_md IS NOT NULL
                      AND EXISTS (
                        SELECT 1 FROM jsonb_array_elements(attendees) a
                        WHERE lower(a->>'email') = ANY(:emails)
                      )
                    ORDER BY COALESCE(start_at, created_at) DESC
                    LIMIT :lim
                    """
                ),
                {"id": meeting_id, "owner": m.owner_email or "",
                 "emails": emails, "lim": _MAX_PAST_MEETINGS},
            )
        ).fetchall()
        pack.past = [summarise_past(r.title, r.start_at, r.summary_md) for r in rows]

        if rows:
            actions = (
                await db.execute(
                    text(
                        "SELECT description FROM action_item "
                        "WHERE meeting_id = ANY(CAST(:ids AS UUID[])) "
                        "AND status IN ('draft','approved') "
                        "ORDER BY created_at DESC LIMIT :lim"
                    ),
                    {"ids": [str(r.id) for r in rows], "lim": _MAX_OPEN_ACTIONS},
                )
            ).fetchall()
            pack.open_actions = [_clip(a.description, 140) for a in actions]
    return pack


# ── Layer 3: ask the other agents (opt-in) ──────────────────────────────────

async def _ask_agent(label: str, agent: str, prompt: str) -> tuple[str, str]:
    """One agent lookup. Never raises — an unavailable or slow agent just
    contributes nothing, which is the whole point of the pack degrading."""
    try:
        from acb_skills import call_agent

        reply = await asyncio.wait_for(
            call_agent(agent, prompt), timeout=_AGENT_TIMEOUT_S
        )
        body = (reply or "").strip()
        # Agents are told to say NONE rather than narrate an empty result —
        # otherwise "I couldn't find anything" would eat prompt budget forever.
        if not body or body.upper().startswith("NONE"):
            return label, ""
        return label, _clip(body, _MAX_SYSTEM_CHARS)
    except TimeoutError:
        _log.info("notes.copilot_context_agent_timeout", agent=agent)
    except Exception as exc:
        _log.info(
            "notes.copilot_context_agent_failed", agent=agent, error=str(exc)[:160]
        )
    return label, ""


async def _system_context(people: list[str]) -> dict[str, str]:
    """Fan out to the business agents in parallel — this happens before the
    meeting, so the cost is one-off and the latency is hidden."""
    if not people:
        return {}
    who = ", ".join(people)
    results = await asyncio.gather(
        *(
            _ask_agent(label, agent, prompt.format(people=who))
            for label, agent, prompt in _AGENT_QUERIES
        )
    )
    return {label: body for label, body in results if body}


#: Agents a MID-CALL consult may ask, with how each should answer. Same
#: registry names as the pre-meeting fan-out; kept separate because a live
#: meeting needs terse, immediately-usable answers, not summaries.
_CONSULT_AGENTS: tuple[tuple[str, str], ...] = (
    ("crm", "agent-sales-assistant"),
    ("tasks", "task-manager"),
)

_CONSULT_PROMPT = (
    "A live meeting needs one specific fact. Answer the question below in "
    "under 60 words using only what you can actually find in your systems; "
    "no preamble. If you find nothing relevant, reply exactly: NONE.\n"
    "QUESTION: {question}"
)


async def consult(question: str) -> dict[str, str]:
    """Mid-call: ask the business agents ONE specific question the copilot
    decided it needs (spec §5.2 'on-demand retrieval'). Parallel, bounded by
    the same timeout as the pre-meeting fan-out, and never raises — a silent
    consult just means the suggestion goes out ungrounded or not at all."""
    q = (question or "").strip()
    if not q:
        return {}
    results = await asyncio.gather(
        *(
            _ask_agent(label, agent, _CONSULT_PROMPT.format(question=q[:400]))
            for label, agent in _CONSULT_AGENTS
        )
    )
    return {label: body for label, body in results if body}


# ── Assembly + per-session cache ────────────────────────────────────────────

_CACHE: dict[str, ContextPack] = {}


async def build(meeting_id: str, *, deep: bool = False) -> ContextPack:
    """Assemble the pack for a meeting. Never raises — a copilot with no
    context is much better than a copilot that crashed."""
    try:
        pack = await _local_context(meeting_id)
        if deep:
            pack.systems = await _system_context(pack.attendees)
        _log.info(
            "notes.copilot_context_built",
            meeting_id=meeting_id, deep=deep,
            brief=bool(pack.brief), past=len(pack.past),
            actions=len(pack.open_actions), systems=list(pack.systems),
        )
        return pack
    except Exception as exc:
        _log.warning(
            "notes.copilot_context_failed",
            meeting_id=meeting_id, error=str(exc)[:200],
        )
        return ContextPack()


async def get(meeting_id: str, *, deep: bool = False) -> ContextPack:
    """Cached per meeting — built once at session start, reused every prompt."""
    cached = _CACHE.get(meeting_id)
    if cached is not None:
        return cached
    pack = await build(meeting_id, deep=deep)
    _CACHE[meeting_id] = pack
    return pack


def forget(meeting_id: str) -> None:
    _CACHE.pop(meeting_id, None)


# ── API: write the brief, inspect what the copilot will know ────────────────

class BriefRequest(BaseModel):
    brief: str = ""


@router.put("/meetings/{meeting_id}/brief")
async def set_brief(
    meeting_id: str,
    body: BriefRequest,
    _user: UserContext = Depends(get_current_user),
) -> dict:
    """Set the meeting's briefing — the single highest-value piece of context,
    because it's usually the part only the human knows (what this meeting is
    FOR). Editable before or during; invalidates the cached pack so a mid-meeting
    edit takes effect on the next suggestion."""
    brief = _clip(body.brief or "", _MAX_BRIEF_CHARS)
    async with _tenant_session() as db:
        res = await db.execute(
            text("UPDATE meeting SET copilot_brief = :b WHERE id = CAST(:id AS UUID)"),
            {"b": brief or None, "id": meeting_id},
        )
        if res.rowcount == 0:
            raise HTTPException(status_code=404, detail="meeting not found")
    forget(meeting_id)
    return {"brief": brief}


@router.get("/meetings/{meeting_id}/context")
async def get_context(
    meeting_id: str,
    _user: UserContext = Depends(get_current_user),
) -> dict:
    """What the copilot knows about this meeting — so "why did it say that?"
    (and "why is it being generic?") are both answerable."""
    pack = await get(meeting_id)
    return {
        "brief": pack.brief,
        "attendees": pack.attendees,
        "past": pack.past,
        "open_actions": pack.open_actions,
        "systems": pack.systems,
        "is_empty": pack.is_empty,
    }


class DeepContextRequest(BaseModel):
    enabled: bool


@router.post("/meetings/{meeting_id}/context/deep")
async def set_deep_context(
    meeting_id: str,
    body: DeepContextRequest,
    _user: UserContext = Depends(get_current_user),
) -> dict:
    """Opt this session into asking the business agents (CRM, tasks) for
    background. Off by default because the fan-out spends tokens before the
    meeting starts — worth it for a customer call, not for a standup."""
    async with _tenant_session() as db:
        res = await db.execute(
            text(
                "UPDATE live_session SET deep_context = :e, updated_at = now() "
                "WHERE meeting_id = CAST(:m AS UUID) AND status = 'live'"
            ),
            {"e": body.enabled, "m": meeting_id},
        )
        if res.rowcount == 0:
            raise HTTPException(status_code=404, detail="no live session")
    forget(meeting_id)   # rebuild with (or without) the agent lookups
    return {"deep_context": body.enabled}
