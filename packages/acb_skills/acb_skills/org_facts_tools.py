"""The org-facts tool and the CRM facts block (WS-53 CRM-Q32-5).

Spec: ``project-docs/specs/crm_platform.md`` §18.14.4.

* ``org_facts(app, query)`` reads ``GET /org/facts`` as the run's member,
  with the transport of ``crm_tools.py`` (the internal bearer and
  ``X-User-Email``). It loads with the ``crm`` skill family only, so the
  core floor does not change. It takes no SQL and opens no session.
* :func:`crm_facts_block` builds the CRM facts block of a run: the answered
  facts of the money modules and of the stages, at most
  :data:`BLOCK_LINES` lines. The run assembly
  (``orchestrator._tool_injection._add_crm_facts_block``) reads it as the
  member, so a run of one org never carries the block of another org. The
  gateway gives no CRM fact to an org outside ``CRM_QUESTIONS_ORGS``, so
  the block is empty there.

Fences: ``tests/unit/test_org_facts.py`` and ``tests/unit/test_org_facts_r8.py``.
"""

from __future__ import annotations

import re
from typing import Any

from acb_common.paging import paged

from acb_skills.crm_tools import (
    CRM_QUESTION_TOOLS,
    CRM_RULE_TOOLS,
    _aggregate_text,
    _budgeted,
    _GatewayError,
    _get,
    _member_words,
    _post,
    _read_tool,
    _run_reads,
)

#: The route of the seam.
ORG_FACTS_PATH = "/org/facts"

#: The facts of one page of the tool.
MAX_FACTS = 60

#: The most lines of the CRM facts block, its heading and its routing line
#: included. So 11 lines at most hold facts, as before the routing line.
BLOCK_LINES = 13

#: The heading of the block. It marks the block, so a second append of one
#: run adds nothing.
BLOCK_MARKER = "## CRM facts"

#: The routing line, second in the block (WS-47 parity, 2026-10-19). The
#: block reaches only a run of an org whose CRM holds answered facts, so the
#: line never sends the business questions of an org with no CRM to the CRM.
#: The persona's rule 1 sends a company question to ``search_company`` first,
#: and that search finds documents. A WhatsApp member asked for the IP billing
#: of a circle, got "the company records hold nothing", and the CRM had it.
#: The words are general (WS-47 follow-up): "circles" and "sites" are one
#: org's words. Each org's own module names follow, from its catalogue
#: (:func:`crm_routing_line`).
CRM_ROUTING_LINE = (
    "Business figures live in the CRM: billing, invoices, collections, payments, "
    "revenue and amounts. Read them with the CRM tools: "
    "crm_list_modules, then crm_aggregate for a total. search_company finds "
    "documents, not CRM records, so an empty search never means that the CRM "
    "holds nothing."
)

#: The most module names that the routing line names, and the longest name.
ROUTING_MODULES = 8
ROUTING_NAME_CHARS = 40

#: The meaning of a purpose fact (``gateway/routes/crm/facts.entry_facts``):
#: "<module label> holds <purpose> records."
_PURPOSE_MEANING = re.compile(r"^(?P<label>.+?) holds .+ records\.$")


def _module_names(rows: list[Any]) -> list[str]:
    """The org's own CRM module names, from the purpose facts of its
    catalogue, in the gateway's order, each once. A name is cut to one line
    of :data:`ROUTING_NAME_CHARS` characters."""
    names: list[str] = []
    for row in rows:
        if not isinstance(row, dict) or row.get("key") != "purpose":
            continue
        found = _PURPOSE_MEANING.match(str(row.get("meaning") or ""))
        if found is None:
            continue
        # One label cut to its length, a text and not a list of rows.
        label = " ".join(found.group("label").split())
        name = f"{label[:ROUTING_NAME_CHARS]}"
        if name and name not in names:
            names.append(name)
        if len(names) >= ROUTING_MODULES:
            break
    return names


def crm_routing_line(rows: list[Any]) -> str:
    """:data:`CRM_ROUTING_LINE`, then this org's own module names, on ONE
    line, so each org reads its own words: "Its CRM modules: Invoices,
    Circles." With no purpose fact, the general line alone."""
    names = _module_names(rows)
    if not names:
        return CRM_ROUTING_LINE
    return f"{CRM_ROUTING_LINE} Its CRM modules: {', '.join(names)}."

#: The keys of a fact that the block keeps: the business rules (CRM-Q33),
#: the key parts of a money module, the meaning of revenue, the org's own
#: words and value aliases (WS-53 §18.16), and each stage value.
_BLOCK_KEYS = ("rule", "revenue", "amount", "date", "status", "parent", "term", "alias")


def _line(row: dict[str, Any]) -> str:
    by = row.get("set_by") or "Metorite"
    return f"- {row.get('meaning')} ({row.get('subject')}; set by {by})"


# ── Verified questions (WS-SEM SEM-8, ``semantic_model.md`` §5 step 5) ──────
#
# No new tool and no new argument: the schema of ``org_facts`` stays byte for
# byte. The gateway serves a ``query:`` fact only to an org on
# ``SEMANTIC_MODEL_ORGS``. Only then does ``org_facts`` add the matches of the
# member's own words, and the line that names the run form
# ``query="run:<key> <name>=<value>; ..."``. The run lowers the question on the
# gateway, then reads ``GET /crm/aggregate`` as the member, so the member's own
# visibility holds. It spends one aggregate read of the run's budget.

#: The prefix of the run form of ``query``.
RUN_PREFIX = "run:"
#: The subject prefix of a verified question fact.
QUERY_SUBJECT = "query:"
#: The code of the gateway for an org off ``SEMANTIC_MODEL_ORGS``.
SEMANTIC_OFF = "semantic_off"
#: The line that names the run form.
RUN_LINE = (
    'To answer with one, call org_facts(query="run:<key> <name>=<value>; <name>=<value>"). '
    "Its total is a verified answer: say so."
)
#: The mark of an answer from a verified question (§5.2).
VERIFIED_MARK = "Verified question"
#: The shape of a key of a verified question (``catalogue._SLUG``). Any
#: other text never reaches a URL.
_RUN_KEY = re.compile(r"[a-z][a-z0-9_]{0,39}")
#: The most characters of the member's words that a match reads. It is the
#: cap of ``MatchIn.q`` on the gateway, so a long message never gets a 422.
MATCH_WORDS_MAX = 2000


def _note_verified(rows: list[Any]) -> bool:
    """Mark the run when *rows* hold a verified question fact.

    The mark lives on the holder of the run (``RunReads.verified``), which
    a run makes new and keeps across its tool calls. So a mark never leaks
    into another run. Only an org on ``SEMANTIC_MODEL_ORGS`` gets such a
    fact. A run with no holder (a batch run) keeps no mark."""
    seen = any(isinstance(r, dict) and str(r.get("subject") or "").startswith(QUERY_SUBJECT)
               for r in rows)
    if seen:
        reads = _run_reads()
        if reads is not None:
            # The holder of the run lives across its tool calls.
            reads.verified = True
    return seen


def _verified_on() -> bool:
    """True when a facts read of this run served a verified question. A
    run with no holder gets False, so its run form makes the facts read of
    before: the same calls, the same budget and the same text."""
    reads = _run_reads()
    return bool(reads is not None and reads.verified)


def parse_run(text_: str) -> tuple[str, dict[str, str]]:
    """The key and the values of the run form ``<key> <name>=<value>; ...``.
    A value may hold spaces and commas. ``;`` ends it."""
    key, _sep, rest = text_.strip().partition(" ")
    values: dict[str, str] = {}
    for part in rest.split(";"):
        name, eq, value = part.partition("=")
        if eq and name.strip():
            values[name.strip()] = value.strip()
    return key.strip(), values


async def verified_lines() -> list[str]:
    """The verified questions that match the member's own words of this
    run, with the parameters to give, then :data:`RUN_LINE`. The words go
    in a POST body, never in a URL. Empty with no words or no match."""
    words = " ".join(_member_words().split())[:MATCH_WORDS_MAX]
    if not words:
        return []
    try:
        body = await _post("/crm/verified-queries/match", {"q": words}) or {}
    except Exception:  # an optional read: the facts still serve without it
        return []
    matches = [m for m in body.get("matches") or [] if isinstance(m, dict)]
    if not matches:
        return []
    lines = ["Verified questions that match the member's words (use one first):"]
    for m in matches:
        params = ", ".join(f"{p.get('name')} ({p.get('kind')})" for p in m.get("params") or [])
        lines.append(f'- "{m.get("question")}": key {m.get("key")}'
                     + (f", give {params}" if params else ""))
    lines.append(RUN_LINE)
    return lines


async def _verified_total(key: str, values: dict[str, str]) -> str:
    """The total of the verified question *key*, as the member: the gateway
    lowers it, and ``crm_aggregate``'s own read answers it."""
    params = {f"param.{name}": value for name, value in values.items()}
    lowered = await _get(f"/crm/verified-queries/{key}", params) or {}
    aggregate = {str(k): str(v) for k, v in (lowered.get("aggregate") or {}).items()}
    slug = str(lowered.get("entity") or aggregate.get("entity") or "")
    head = (f'{VERIFIED_MARK} (model version {lowered.get("model_version")}): '
            f'"{lowered.get("question")}".')
    return f"{head}\n{await _aggregate_text(slug, aggregate, 1)}"


async def verified_run(text_: str) -> str | None:
    """The answer of the run form, or ``None`` for an org off
    ``SEMANTIC_MODEL_ORGS``: the caller then reads the facts as before."""
    if not _verified_on():
        # No facts read of this run served a verified question: the facts
        # read of before answers, with no other call and no read spent.
        return None
    key, values = parse_run(text_)
    if not _RUN_KEY.fullmatch(key):
        return "No verified question has this key. Read the keys with org_facts."
    try:
        return await _budgeted("crm_aggregate", _verified_total)(key, values)
    except _GatewayError as exc:
        if exc.code == SEMANTIC_OFF:
            return None
        raise


@_read_tool
async def org_facts(app: str = "crm", query: str | None = None, offset: int = 0) -> str:
    """The fixed facts of the organization's data: what each CRM module is
    for, which field holds its amount, date, status and customer, each
    approved stage, and what revenue means. A CRM admin answered each fact,
    or Metorite and Jev found it, so use it as given. app is 'crm'. query
    narrows the facts by a word of the subject or the meaning."""
    asked = (query or "").strip()
    if asked.lower().startswith(RUN_PREFIX):
        answer = await verified_run(asked[len(RUN_PREFIX):])
        if answer is not None:
            return answer
    params = {"app": (app or "").strip() or None, "q": (query or "").strip() or None}
    body = await _get(ORG_FACTS_PATH, params)
    rows = [r for r in (body.get("facts") if isinstance(body, dict) else None) or []
            if isinstance(r, dict) and r.get("meaning")]
    if not rows:
        return ("No facts of this organization match. A CRM admin answers the open "
                "questions of the CRM in CRM settings.")
    page = paged(rows, offset, MAX_FACTS, default=MAX_FACTS, noun="facts")
    lines = ["Facts of this organization (fixed answers):"]
    lines.extend(_line(r) for r in page.items)
    if page.line:
        lines.append(page.line)
    if _note_verified(rows):
        # SEM-8: only an org on SEMANTIC_MODEL_ORGS has a verified question.
        lines.extend(await verified_lines())
    return "\n".join(lines)


def _kept(row: dict[str, Any]) -> bool:
    key = str(row.get("key") or "")
    return key in _BLOCK_KEYS or key.startswith("stage:")


def crm_facts_block(rows: list[Any]) -> str:
    """The CRM facts block: a heading, the routing line
    (:data:`CRM_ROUTING_LINE`), then the answered facts of the money
    modules, the meaning of revenue and the stages, in the order of the
    gateway, at most :data:`BLOCK_LINES` lines in all. When more facts are
    there, the last line names how many, and ``org_facts`` reads them, so no
    fact is lost in silence. Empty with no fact.

    SEM-8: when the gateway serves verified questions (an org on
    ``SEMANTIC_MODEL_ORGS``), one line inside the same cap names their count
    and sends the model to ``org_facts``. With none, the block is the block
    of before, byte for byte."""
    kept = [r for r in rows if isinstance(r, dict) and r.get("meaning") and _kept(r)]
    verified = sum(1 for r in rows if isinstance(r, dict)
                   and str(r.get("subject") or "").startswith(QUERY_SUBJECT))
    if not kept:
        return ""
    room = BLOCK_LINES - 2 - (1 if verified else 0)
    if len(kept) > room:
        room -= 1  # one line names the rest
    lines = [f"{BLOCK_MARKER} (fixed answers of this organization; use them as given)",
             crm_routing_line(rows)]
    lines.extend(f"- {r['meaning']}" for i, r in enumerate(kept) if i < room)
    if len(kept) > room:
        lines.append(f"- {len(kept) - room} more facts: call org_facts to read them.")
    if verified:
        lines.append(f"- {verified} verified questions: call org_facts, and answer a "
                     "question that matches one with it first.")
    return "\n".join(lines)


async def crm_facts_for_run() -> list[Any]:
    """The CRM facts of this run, read as the run's member. Empty when the
    read fails, so the run starts as before."""
    try:
        body = await _get(ORG_FACTS_PATH, {"app": "crm"})
    except Exception:  # the block is advice: a run never fails on it
        return []
    rows = body.get("facts") if isinstance(body, dict) else None
    out = [r for r in rows or [] if isinstance(r, dict)]
    _note_verified(out)
    return out


async def crm_facts_block_for_run() -> str:
    """The CRM facts block of this run, read as the run's member. A read
    that fails gives no block, so the run starts as before."""
    return crm_facts_block(await crm_facts_for_run())


ORG_FACTS_TOOLS = (org_facts,)

#: The tools that go with the facts (WS-53 CRM-Q32-8): ``org_facts`` and
#: ``crm_answer_question``, and since CRM-Q33 ``crm_propose_rule``. The run
#: assembly injects them together, and withholds them together
#: (``_facts_withheld`` and the drop of ``_add_crm_facts_block``), so a run
#: of an org off ``CRM_QUESTIONS_ORGS`` holds none. ``crm_propose_rule``
#: also needs ``CRM_ANSWER_FIRST_ORGS``.
FACTS_GATED_TOOLS = (*ORG_FACTS_TOOLS, *CRM_QUESTION_TOOLS, *CRM_RULE_TOOLS)

__all__ = [
    "BLOCK_LINES",
    "BLOCK_MARKER",
    "CRM_ROUTING_LINE",
    "FACTS_GATED_TOOLS",
    "ORG_FACTS_PATH",
    "ORG_FACTS_TOOLS",
    "RUN_PREFIX",
    "VERIFIED_MARK",
    "crm_facts_block",
    "crm_facts_block_for_run",
    "crm_facts_for_run",
    "crm_routing_line",
    "org_facts",
    "parse_run",
    "verified_lines",
    "verified_run",
]
