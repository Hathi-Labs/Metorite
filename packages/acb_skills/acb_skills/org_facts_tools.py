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

from typing import Any

from acb_common.paging import paged

from acb_skills.crm_tools import CRM_QUESTION_TOOLS, CRM_RULE_TOOLS, _get, _read_tool

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
CRM_ROUTING_LINE = (
    "Business figures live in the CRM: billing, invoices, collections, payments, "
    "circles, sites, revenue and amounts. Read them with the CRM tools: "
    "crm_list_modules, then crm_aggregate for a total. search_company finds "
    "documents, not CRM records, so an empty search never means that the CRM "
    "holds nothing."
)

#: The keys of a fact that the block keeps: the business rules (CRM-Q33),
#: the key parts of a money module, the meaning of revenue and each stage
#: value.
_BLOCK_KEYS = ("rule", "revenue", "amount", "date", "status", "parent")


def _line(row: dict[str, Any]) -> str:
    by = row.get("set_by") or "Metorite"
    return f"- {row.get('meaning')} ({row.get('subject')}; set by {by})"


@_read_tool
async def org_facts(app: str = "crm", query: str | None = None, offset: int = 0) -> str:
    """The fixed facts of the organization's data: what each CRM module is
    for, which field holds its amount, date, status and customer, each
    approved stage, and what revenue means. A CRM admin answered each fact,
    or Metorite and Jev found it, so use it as given. app is 'crm'. query
    narrows the facts by a word of the subject or the meaning."""
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
    fact is lost in silence. Empty with no fact."""
    kept = [r for r in rows if isinstance(r, dict) and r.get("meaning") and _kept(r)]
    if not kept:
        return ""
    room = BLOCK_LINES - 2
    if len(kept) > room:
        room -= 1  # one line names the rest
    lines = [f"{BLOCK_MARKER} (fixed answers of this organization; use them as given)",
             CRM_ROUTING_LINE]
    lines.extend(f"- {r['meaning']}" for i, r in enumerate(kept) if i < room)
    if len(kept) > room:
        lines.append(f"- {len(kept) - room} more facts: call org_facts to read them.")
    return "\n".join(lines)


async def crm_facts_for_run() -> list[Any]:
    """The CRM facts of this run, read as the run's member. Empty when the
    read fails, so the run starts as before."""
    try:
        body = await _get(ORG_FACTS_PATH, {"app": "crm"})
    except Exception:  # the block is advice: a run never fails on it
        return []
    rows = body.get("facts") if isinstance(body, dict) else None
    return [r for r in rows or [] if isinstance(r, dict)]


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
    "crm_facts_block",
    "crm_facts_block_for_run",
    "crm_facts_for_run",
    "org_facts",
]
