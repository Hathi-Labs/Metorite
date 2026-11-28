"""The no-match path of ``find_tools`` (SBX-1a, task 3800).

Spec: ``project-docs/specs/agent_sandbox.md`` §6.1 and §12.1. Dark behind
``user_tool_find_enabled`` (default off).

``tool_packs.FindTools`` takes an optional hook ``on_gap(query, live)``, and
runs it only when its rank found no tool. :func:`make_gap_hook` gives that
hook, or None with the flag off. ``_tool_injection._make_finder`` is its one
caller. With None, the no-match answer stays ``FIND_NONE`` byte for byte, and
nothing writes a row, asks the decide engine or reads the catalog.

**The steps of the hook** (:func:`gap_hook`), in this order:

a. The gate. ``_tool_injection._studio_member_open()``: ``STUDIO_ORGS``
   names the org, and the member holds ``feature:studio``. An empty query
   gives None.
b. The facts of the run: the bound tenant, the verified member
   (``executor._verified_run_member``), and the thread and the run of the run
   binding. Never a tool argument (R5). A missing fact gives None.
c. The compose check. ``routes/studio/actions.catalog_rows()`` runs BEFORE
   any session of the hook opens. ``package_search.callable_catalog`` keeps
   the rows that the member can call, and a lexical match on the title keeps
   :data:`CATALOG_LIMIT` at most.
d. The library check. ONE ``tenant_session()`` for
   ``package_search.search_visible``. It closes before the next step.
e. The :data:`LIVE_LIMIT` live tools that share the most stems with the need.
   A tool of an MCP server never counts.
f. One ``decide_tools.ask_typed`` call with two items (the answer and the
   category), only when ``decide_tools.typed_choices_on()`` is true. No
   session is open while it runs.
g. The map of §12.1 decision 3 (:func:`decide_outcome`).
h. ONE ``tenant_session()`` for ``studio/tool_gaps.record_gap``. A chat that
   the bound tenant cannot see gives no row and None.
i. The log line :data:`GAP_EVENT`, with no need text.
j. The fixed sentence of the outcome (:func:`sentence`). The ``no_match``
   sentence holds the id of the new row, so the model can pass it to
   ``build_tool`` (SBX-4b2, task 4049, §12.4 decision 12). No other sentence
   holds it.

**What the model sees** (decision 8): the need, ids and fixed sentences only.
No text that a member wrote in a package reaches the decide context or the
answer. A fault raises, and ``FindTools`` answers ``FIND_NONE`` and logs the
class only.
"""

from __future__ import annotations

import json
import re
import uuid
from collections.abc import Awaitable, Callable, Mapping, Sequence
from dataclasses import dataclass
from typing import Any

from acb_common import get_logger

_log = get_logger("orchestrator.tool_gap_path")

#: The log line of each row (``agent_sandbox.md`` §9.1).
GAP_EVENT = "sandbox.tool.gap"
#: The purpose of the decide call, for the logs.
PURPOSE = "tools.gap"
#: The longest need, in characters (the CHECK of ``tool_gaps.need``).
NEED_MAX = 280
#: The most live tools of the decide question.
LIVE_LIMIT = 5
#: The most catalog rows of the compose check.
CATALOG_LIMIT = 20
#: The most ids in ``tool_gaps.candidates``, and their most characters
#: joined with a comma (the CHECK of the column).
CANDIDATES_MAX = 10
CANDIDATES_CHARS = 2000

#: The words of a pick.
PKG = "pkg:"
TOOL = "tool:"
COMPOSE = "compose"
NONE = "none"
UNSURE = "unsure"
#: The category of a need that no family fits, and of no answer.
OTHER = "other"

#: The two questions (§12.1 decisions 4 and 5). Fixed text: the context holds
#: the need and the ids.
ANSWER_QUESTION = (
    "Which choice can do the need in the context? A pkg: choice is a saved app of the "
    "library. A tool: choice is a tool that the chat holds. compose means that the "
    "catalog actions together do the need with no code. none means that nothing here "
    "does it. unsure means that you cannot tell.")
CATEGORY_QUESTION = (
    "Which family of work holds the need in the context? Answer other when no family fits.")

#: The fixed sentences of the answer, by outcome. Only ids fill them.
SAY_EXISTING = "A tool that you hold may do this: {name}. Call it."
SAY_REUSE = ("A saved AI Studio app of this organization may do this ({ref}). Tell the "
             "member, so they can open it in AI Studio.")
SAY_COMPOSE = ("AI Studio actions can do this with no new tool: {ids}. Offer the member a "
               "Studio skill or workflow of these actions.")
SAY_NO_MATCH = ("No tool, saved app or AI Studio action does this. Tell the member that no "
                "tool does this yet. The gap id of this need is {gap_id}.")
SAY_UNSURE = "Some tools may do this: {ids}. Try one first, or ask the member."
SAY_UNSURE_EMPTY = "No clear match was found. Ask the member what the tool must do."

#: A live tool name that may go into the question and the row.
_TOOL_NAME = re.compile(r"[a-z0-9_]{1,80}")

GapHook = Callable[[str, Sequence[Any]], Awaitable[str | None]]


@dataclass(frozen=True)
class Decision:
    """The map of one pick (``agent_sandbox.md`` §12.1 decision 3)."""

    answer: str
    outcome: str
    app_id: str | None = None
    name: str | None = None


def find_enabled() -> bool:
    """True when ``user_tool_find_enabled`` is on. A fault reads as off."""
    try:
        from acb_common import get_settings

        return getattr(get_settings(), "user_tool_find_enabled", False) is True
    except Exception:  # fail closed
        return False


def make_gap_hook() -> GapHook | None:
    """THE hook of the no-match path for one run, or None with the flag off.
    ``_tool_injection._make_finder`` passes it to ``FindTools`` as ``on_gap``."""
    if not find_enabled():
        return None
    return gap_hook


def clean_need(query: object) -> str:
    """The need of a row: the query with no hidden character and one space
    between words, cut at :data:`NEED_MAX`. "" for an empty query."""
    from acb_skills.hidden_text import strip_hidden

    plain = strip_hidden(str(query or "").replace("\x00", " "))
    return " ".join(plain.split())[:NEED_MAX].strip()


def categories() -> tuple[str, ...]:
    """The options of the category: each pack family, then :data:`OTHER`."""
    from acb_skills.skill_families import SKILL_FAMILIES

    return (*SKILL_FAMILIES, OTHER)


def _run_facts() -> tuple[str, str, str, str] | None:
    """The org, the member, the thread and the run of the run on this frame,
    from the run binding only (R5), or None when one is missing."""
    from acb_common.db import current_tenant
    from acb_skills.write_artifact import artifact_context

    from orchestrator.executor import _verified_run_member

    org = str(current_tenant() or "").strip().lower()
    member = _verified_run_member()
    ctx = artifact_context() or {}
    thread = str(ctx.get("session_id") or "").strip()
    run = str(ctx.get("run_id") or "").strip()
    if not (org and member and thread and run):
        return None
    return org, member, thread, run


def nearest_rows(need: str, rows: Sequence[Mapping[str, Any]]) -> list[str]:
    """The ids of at most :data:`CATALOG_LIMIT` rows whose title shares a stem
    with *need*, best first. A tie breaks by the id."""
    from acb_skills.tool_search import shared_stems

    scored = []
    for row in rows:
        row_id = str(row.get("id") or "")
        if not row_id or any(ch.isspace() for ch in row_id) or len(row_id) > 200:
            continue
        hits = shared_stems(need, f"{row.get('title') or ''} {row_id.replace('.', ' ')}")
        if hits > 0:
            scored.append((-hits, row_id))
    return [row_id for _neg, row_id in sorted(set(scored))[:CATALOG_LIMIT]]


def nearest_tools(need: str, live: Sequence[Any]) -> list[str]:
    """The names of at most :data:`LIVE_LIMIT` live tools that share a stem
    with *need*, best first. A tool of an MCP server, ``find_tools`` and
    ``load_skill_tools`` never count, because only a first-party name may
    reach the decide context."""
    from acb_skills.egress import _from_mcp
    from acb_skills.tool_guard import tool_name
    from acb_skills.tool_packs import FIND_TOOL, LOAD_TOOL, _tool_text
    from acb_skills.tool_search import _first_line, shared_stems

    scored: dict[str, int] = {}
    for item in live:
        if _from_mcp(item):
            continue
        name = tool_name(item)
        if name in (FIND_TOOL, LOAD_TOOL) or not _TOOL_NAME.fullmatch(name):
            continue
        hits = shared_stems(need, f"{name.replace('_', ' ')} {_first_line(_tool_text(item))}")
        if hits > 0:
            scored[name] = max(hits, scored.get(name, 0))
    ranked = sorted((-hits, name) for name, hits in scored.items())
    return [name for _neg, name in ranked[:LIVE_LIMIT]]


def decide_outcome(
    pick: str | None, *, packages: Sequence[str], tools: Sequence[str], composable: bool,
) -> Decision:
    """The answer and the outcome of *pick* (§12.1 decision 3). Pure.

    *packages* holds the app ids of the library check, *tools* the names of
    the live tools, and *composable* says that the compose check kept a row.
    A pick outside the offered options reads as ``unsure``."""
    if isinstance(pick, str):
        if pick.startswith(PKG) and pick[len(PKG):] in packages:
            return Decision("tool", "reuse", app_id=pick[len(PKG):])
        if pick.startswith(TOOL) and pick[len(TOOL):] in tools:
            return Decision("tool", "existing", name=pick[len(TOOL):])
        if pick == COMPOSE and composable:
            return Decision("compose", "compose")
        if pick == NONE:
            return Decision("none", "no_match")
    return Decision("unsure", "unsure")


def _context(need: str, hits: Sequence[Any], tools: Sequence[str], rows: Sequence[str]) -> str:
    """The decide context: the need, ids and counts only (decision 8)."""
    return json.dumps({
        "need": need,
        "library": [{"id": f"{PKG}{h.app_id}", "shared_words": int(h.shared)} for h in hits],
        "tools": list(tools),
        "catalog": list(rows),
    }, ensure_ascii=False)


async def ask(
    need: str, hits: Sequence[Any], tools: Sequence[str], rows: Sequence[str],
) -> tuple[str | None, str]:
    """The pick of the answer and the category, from ONE ``ask_typed`` call.
    No engine, or a pick below the threshold, gives None and :data:`OTHER`."""
    from acb_skills import decide_tools
    from acb_skills.system_one import Item

    if not decide_tools.typed_choices_on():
        return None, OTHER
    options = (*(f"{PKG}{h.app_id}" for h in hits), *(f"{TOOL}{t}" for t in tools),
               *((COMPOSE,) if rows else ()), NONE, UNSURE)
    items = [
        Item(id="answer", question=ANSWER_QUESTION, kind="choice", options=tuple(options)),
        Item(id="category", question=CATEGORY_QUESTION, kind="choice", options=categories()),
    ]
    answers = await decide_tools.ask_typed(_context(need, hits, tools, rows), items,
                                           purpose=PURPOSE)
    if not answers:
        return None, OTHER
    by_id = {str(getattr(a, "id", "")): a for a in answers}
    pick = decide_tools.sure_choice(by_id.get("answer"))
    category = decide_tools.sure_choice(by_id.get("category"))
    return pick, category if category in categories() else OTHER


def candidates_of(
    decision: Decision, hits: Sequence[Any], tools: Sequence[str], rows: Sequence[str],
) -> list[str]:
    """The ids of the row: the pick first, then each id that the checks
    found, at most :data:`CANDIDATES_MAX`. Ids only (decision 8)."""
    lead: list[str] = []
    if decision.app_id:
        lead.append(f"{PKG}{decision.app_id}")
    elif decision.name:
        lead.append(decision.name)
    elif decision.outcome == "compose":
        lead.extend(rows)
    found = [*lead, *(f"{PKG}{h.app_id}" for h in hits), *tools, *rows]
    kept: list[str] = []
    for one in dict.fromkeys(found):
        if len(kept) >= CANDIDATES_MAX or len(",".join([*kept, one])) > CANDIDATES_CHARS:
            break
        kept.append(one)
    return kept


def sentence(decision: Decision, candidates: Sequence[str], rows: Sequence[str], *,
             gap_id: str) -> str:
    """The fixed sentence of the outcome, filled with ids only. Only the
    ``no_match`` sentence holds *gap_id*, the id of the row. A *gap_id* that
    is not a UUID raises ``ValueError`` there, so no other text gets in."""
    if decision.outcome == "existing":
        return SAY_EXISTING.format(name=decision.name)
    if decision.outcome == "reuse":
        return SAY_REUSE.format(ref=f"{PKG}{decision.app_id}")
    if decision.outcome == "compose":
        return SAY_COMPOSE.format(ids=", ".join(rows[:CANDIDATES_MAX]))
    if decision.outcome == "no_match":
        return SAY_NO_MATCH.format(gap_id=str(uuid.UUID(gap_id)))
    if candidates:
        return SAY_UNSURE.format(ids=", ".join(candidates))
    return SAY_UNSURE_EMPTY


async def gap_hook(query: str, live: Sequence[Any]) -> str | None:
    """The hook of the no-match path of ONE call of ``find_tools``. The
    steps a to j of the module text. None writes no row."""
    need = clean_need(query)
    if not need:
        return None
    # a. The gate: STUDIO_ORGS and feature:studio of the verified member.
    from orchestrator._tool_injection import _studio_member_open

    if not await _studio_member_open():
        return None
    # b. The facts of the run, from the run binding only.
    facts = _run_facts()
    if facts is None:
        return None
    org, member, thread, run = facts
    from acb_skills.tool_packs import tool_search_on

    if not tool_search_on(org):
        return None
    # c. The compose check. catalog_rows opens its own session, so it runs
    # before any session of the hook.
    from acb_common.db import tenant_session
    from gateway.routes.studio.actions import catalog_rows
    from gateway.studio import package_search
    from gateway.studio import tool_gaps as gaps_store

    from orchestrator import in_app_path

    features = await in_app_path.member_features()
    if features is None:
        return None
    rows = nearest_rows(need, package_search.callable_catalog(features, await catalog_rows()))
    # d. The library check, in one session that closes here.
    async with tenant_session() as db:
        hits = await package_search.search_visible(db, viewer=member, query=need)
    # e. The live tools of the run.
    tools = nearest_tools(need, live)
    # f. One typed question, with no session open.
    pick, category = await ask(need, hits, tools, rows)
    # g. The map of decision 3.
    decision = decide_outcome(pick, packages=[h.app_id for h in hits], tools=tools,
                              composable=bool(rows))
    candidates = candidates_of(decision, hits, tools, rows)
    # h. The record, in its own session.
    async with tenant_session() as db:
        gap_id = await gaps_store.record_gap(
            db, member=member, thread_id=thread, run_id=run, need=need, category=category,
            candidates=candidates, answer=decision.answer, outcome=decision.outcome,
            app_id=decision.app_id)
    if gap_id is None:
        return None
    # i. The log line: no need text.
    _log.info(GAP_EVENT, org=org, member=member, run=run, category=category,
              answer=decision.answer, outcome=decision.outcome)
    # j. The fixed sentence. Only the no_match sentence holds the gap id.
    return sentence(decision, candidates, rows, gap_id=gap_id)
