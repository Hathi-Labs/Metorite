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

from acb_skills.crm_tools import _get, _read_tool

#: The route of the seam.
ORG_FACTS_PATH = "/org/facts"

#: The most facts that one answer of the tool shows.
MAX_FACTS = 60

#: The most lines of the CRM facts block, its heading included.
BLOCK_LINES = 12

#: The heading of the block. It marks the block, so a second append of one
#: run adds nothing.
BLOCK_MARKER = "## CRM facts"

#: The keys of a fact that the block keeps: the key parts of a money module,
#: the meaning of revenue and each stage value.
_BLOCK_KEYS = ("revenue", "amount", "date", "status", "parent")


def _line(row: dict[str, Any]) -> str:
    by = row.get("set_by") or "Metorite"
    return f"- {row.get('meaning')} ({row.get('subject')}; set by {by})"


@_read_tool
async def org_facts(app: str = "crm", query: str | None = None) -> str:
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
    lines = ["Facts of this organization (fixed answers):"]
    lines.extend(_line(r) for r in rows[:MAX_FACTS])
    if len(rows) > MAX_FACTS:
        lines.append(f"{len(rows) - MAX_FACTS} more facts. Narrow the query to see them.")
    return "\n".join(lines)


def _kept(row: dict[str, Any]) -> bool:
    key = str(row.get("key") or "")
    return key in _BLOCK_KEYS or key.startswith("stage:")


def crm_facts_block(rows: list[Any]) -> str:
    """The CRM facts block: a heading, then the answered facts of the money
    modules, the meaning of revenue and the stages, in the order of the
    gateway, at most :data:`BLOCK_LINES` lines in all. Empty with no fact."""
    kept = [r for r in rows if isinstance(r, dict) and r.get("meaning") and _kept(r)]
    if not kept:
        return ""
    lines = [f"{BLOCK_MARKER} (fixed answers of this organization; use them as given)"]
    lines.extend(f"- {r['meaning']}" for r in kept[:BLOCK_LINES - 1])
    return "\n".join(lines)


async def crm_facts_block_for_run() -> str:
    """The CRM facts block of this run, read as the run's member. A read
    that fails gives no block, so the run starts as before."""
    try:
        body = await _get(ORG_FACTS_PATH, {"app": "crm"})
    except Exception:  # the block is advice: a run never fails on it
        return ""
    rows = body.get("facts") if isinstance(body, dict) else None
    return crm_facts_block(list(rows or []))


ORG_FACTS_TOOLS = (org_facts,)

__all__ = [
    "BLOCK_LINES",
    "BLOCK_MARKER",
    "ORG_FACTS_PATH",
    "ORG_FACTS_TOOLS",
    "crm_facts_block",
    "crm_facts_block_for_run",
    "org_facts",
]
