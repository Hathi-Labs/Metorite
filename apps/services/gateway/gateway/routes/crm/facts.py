"""CRM · the facts of the CRM, for the org-facts seam (WS-53 CRM-Q32-5).

Spec: ``project-docs/specs/crm_platform.md`` §18.14.4.

The CRM provider of ``gateway/org_facts.py``. The facts of the CRM stay in
the mapping plan, so this module reads the catalogue (``catalogue._load``
and ``catalogue.build``) and the approved plan, and builds each row through
``org_facts.fact``, the one builder. It returns the purpose and each key
part of each entry, each approved role, target and stage, and the meaning of
revenue. A part that the admin left as "none" is a fact too.

**It reads no record value and no email.** The catalogue reads none, and a
stage value is an option of a picklist that the plan holds. ``set_by`` is a
role, never an email.

**Who reads.** The org must be on ``CRM_QUESTIONS_ORGS``
(``flags.questions_allowed``), and the provider calls
``access.require_crm_access(user)`` first. A member that the CRM does not
admit reads no CRM fact. The reads run in the caller's tenant session, with
the org in each predicate and FORCE row level security under it (R5).
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import Any

from fastapi import HTTPException
from gateway import org_facts
from gateway.routes.crm.access import require_crm_access
from gateway.routes.crm.flags import questions_allowed
from sqlalchemy import text

APP = "crm"

#: What a fact calls each key part.
PART_WORDS: Mapping[str, str] = {
    "amount": "amount", "date": "date", "status": "status", "parent": "customer",
}

#: What a fact calls each stage word.
STAGE_WORDS: Mapping[str, str] = {"won": "Won", "lost": "Lost", "open": "Open"}

#: The time of the approved version: the ``set_at`` of each stored part.
_APPROVED_AT_SQL = (
    "SELECT approved_at FROM crm_mapping_plans WHERE organization_id = CAST(:org AS uuid) "
    "AND state = 'approved'"
)


def _source(word: Any) -> str:
    """The ``source`` of a part: a ``role`` is approved by an admin."""
    return "admin" if word in ("admin", "role") else ("ai" if word == "ai" else "code")


def _when(source: str, approved_at: Any) -> Any:
    return approved_at if source in ("ai", "admin") else None


def _names(labels: Sequence[str]) -> str:
    if len(labels) < 2:
        return "".join(labels)
    return f"{', '.join(labels[:-1])} and {labels[-1]}"


def revenue_fact(entries: Sequence[Any], stored: Mapping[str, Any], approved_at: Any,
                 ) -> org_facts.Fact | None:
    """The meaning of revenue: the admin's answer, else billed (§18.14.11)."""
    from gateway.crm_sync import catalogue as cat

    if not any(e.purpose in cat.REVENUE_VALUES and e.records > 0 for e in entries):
        return None
    value, by = cat.revenue_of(stored)
    value = value or cat.DEFAULT_REVENUE
    source = "admin" if by == "admin" else "code"
    labels = [e.label for e in entries if e.purpose == value and e.records > 0]
    word = "billed" if value == "invoice" else "collected"
    meaning = (f"Revenue means {word}: the amount of {_names(labels)}." if labels
               else f"Revenue means {word}.")
    return org_facts.fact(APP, "term:revenue", "revenue", value, meaning, source,
                          _when(source, approved_at))


def entry_facts(entry: Any, approved_at: Any) -> list[org_facts.Fact]:
    """The purpose and each answered key part of one entry. A part that
    waits for a question, or that the AI left as null, is no fact."""
    from gateway.crm_sync import catalogue as cat
    from gateway.crm_sync.questions import PURPOSE_LABELS

    out: list[org_facts.Fact] = []
    subject = f"module:{entry.module}"
    if entry.purpose is not None:
        source = _source(entry.by.get("purpose"))
        noun = PURPOSE_LABELS.get(entry.purpose, entry.purpose).lower()
        out.append(org_facts.fact(APP, subject, "purpose", entry.purpose,
                                  f"{entry.label} is a {noun} module.", source,
                                  _when(source, approved_at)))
    if entry.purpose not in cat.MONEY_PURPOSES:
        return out
    for part in cat.KEY_PARTS:
        if part not in entry.fields or part not in entry.by:
            continue
        found = entry.fields[part]
        source = _source(entry.by.get(part))
        if found is None:
            if source != "admin":
                continue
            out.append(org_facts.fact(APP, subject, part, None,
                                      f"{entry.label} has no single {PART_WORDS[part]}.",
                                      source, _when(source, approved_at)))
            continue
        out.append(org_facts.fact(
            APP, f"field:{entry.module}.{found.key}", part, found.key,
            f"The {PART_WORDS[part]} of {entry.label} is {found.label} ({found.key}).",
            source, _when(source, approved_at)))
    return out


def plan_facts(others: Sequence[Mapping[str, Any]], labels: Mapping[str, str],
               approved_at: Any) -> list[org_facts.Fact]:
    """Each approved ``role``, ``target`` and ``stage``. An admin approved
    each one, so the source is ``admin``."""
    from gateway.crm_sync.questions import SLOT_LABELS

    out: list[org_facts.Fact] = []
    for item in others:
        kind, slot = item.get("kind"), str(item.get("slot") or "")
        module = str(item.get("module") or "")
        label, slot_label = labels.get(module, module), SLOT_LABELS.get(slot, slot)
        if kind == "role" and module:
            out.append(org_facts.fact(APP, f"slot:{slot}", "role", module,
                                      f"{label} plays {slot_label}: its records show there.",
                                      "admin", approved_at))
        elif kind == "target":
            for key in item.get("fields") or []:
                out.append(org_facts.fact(
                    APP, f"field:{module}.{key}", f"target:{item.get('target')}",
                    str(item.get("target")),
                    f"{key} of {label} fills {item.get('target')} of {slot_label}.",
                    "admin", approved_at))
        elif kind == "stage":
            field_key = str(item.get("field_api_name") or "")
            for value, word in sorted((item.get("values") or {}).items()):
                out.append(org_facts.fact(
                    APP, f"field:{module}.{field_key}", f"stage:{value}", str(word),
                    f"In {label}, the {field_key} value {value} means "
                    f"{STAGE_WORDS.get(str(word), str(word))}.", "admin", approved_at))
    return out


def _ordered(entries: Sequence[Any]) -> list[Any]:
    """The money entries first, then the rest, the most records first. An
    entry with no record and no answer of the admin gives no fact."""
    from gateway.crm_sync import catalogue as cat

    kept = [e for e in entries if e.records > 0 or "admin" in e.by.values()]
    return sorted(kept, key=lambda e: (e.purpose not in cat.MONEY_PURPOSES, -e.records,
                                       e.module))


async def crm_facts(db: Any, user: Any) -> list[org_facts.Fact]:
    """The facts of the CRM of the caller's org, in the caller's tenant
    session. None outside ``CRM_QUESTIONS_ORGS``, and none for a member that
    the CRM access rule does not admit."""
    from gateway.crm_sync import catalogue as cat
    from gateway.routes.crm.catalogue import _load

    org = getattr(user, "organization_id", None)
    if not org or not questions_allowed(str(org)):
        return []
    try:
        await require_crm_access(user)
    except HTTPException:
        return []
    org = str(org)
    _mode, sources, roles, others = await _load(db, org)
    entries = cat.build(sources, roles, others)
    row = (await db.execute(text(_APPROVED_AT_SQL), {"org": org})).first()
    approved_at = row.approved_at if row is not None else None
    out: list[org_facts.Fact] = []
    revenue = revenue_fact(entries, cat.stored_items(others), approved_at)
    if revenue is not None:
        out.append(revenue)
    for entry in _ordered(entries):
        out.extend(entry_facts(entry, approved_at))
    out.extend(plan_facts(others, {e.module: e.label for e in entries}, approved_at))
    return out


org_facts.register(APP, crm_facts)


__all__ = ["APP", "crm_facts", "entry_facts", "plan_facts", "revenue_fact"]
