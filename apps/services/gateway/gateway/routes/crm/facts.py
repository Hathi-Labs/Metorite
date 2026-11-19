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

**The semantic kinds (SEM-7, ``semantic_model.md`` §3.4).** For an org on
``SEMANTIC_MODEL_ORGS`` too, each approved ``measure``, ``join`` and
``query`` item is a fact, with the subjects ``measure:``, ``join:`` and
``query:``. An admin accepted each one, so it is the admin's text, with the
source ``admin``, beside the other plan facts (§3.3 point 6). Only the
approved version serves, never a draft (D-SEM-11). An item that no longer
fits the catalogue serves nothing. A verified query shows its question words
and its parameter names only.

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
from gateway.routes.crm.flags import questions_allowed, semantic_model_allowed
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
    waits for a question, or that the AI left as null, is no fact. A sum
    of fields (CRM-Q32-9) names each of its parts."""
    from gateway.crm_sync import catalogue as cat
    from gateway.crm_sync.questions import PURPOSE_LABELS

    out: list[org_facts.Fact] = []
    subject = f"module:{entry.module}"
    if entry.purpose is not None:
        source = _source(entry.by.get("purpose"))
        noun = PURPOSE_LABELS.get(entry.purpose, entry.purpose).lower()
        out.append(org_facts.fact(APP, subject, "purpose", entry.purpose,
                                  f"{entry.label} holds {noun} records.", source,
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
        summed = getattr(found, "parts", ())
        meaning = (
            f"The {PART_WORDS[part]} of {entry.label} is the sum of "
            f"{_names([p.label for p in summed])}." if summed
            else f"The {PART_WORDS[part]} of {entry.label} is {found.label} ({found.key})."
        )
        out.append(org_facts.fact(
            APP, f"field:{entry.module}.{found.key}", part, found.key, meaning,
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


def _field_names(src: Any, keys: Sequence[Any]) -> str:
    return _names([(src.field(str(k)) or src).label for k in keys])


def _filter_words(src: Any, filters: Sequence[Mapping[str, Any]]) -> str:
    parts = []
    for one in filters:
        found = src.field(str(one.get("field")))
        label = found.label if found is not None else str(one.get("field"))
        options = [str(o) for o in one.get("options") or []]
        parts.append(f"{label} is {' or '.join(options)}")
    return f", where {' and '.join(parts)}" if parts else ""


def measure_facts(items: Sequence[Mapping[str, Any]], sources: Mapping[str, Any],
                  approved_at: Any) -> list[org_facts.Fact]:
    """Each approved ``measure`` item that fits the catalogue, as subject
    ``measure:<key>``. It names the aggregate, the fields, the fixed
    filters and the unit. It holds no total."""
    from gateway.crm_sync import catalogue as cat

    out: list[org_facts.Fact] = []
    for item in items:
        if item.get("kind") != cat.KIND_MEASURE:
            continue
        src = sources[str(item["module"])]
        fields = item.get("fields") or []
        what = (f"the count of {src.label} records" if item["aggregate"] == "count"
                else f"the {item['aggregate']} of {_field_names(src, fields)} of {src.label}")
        out.append(org_facts.fact(
            APP, f"measure:{item['key']}", "measure", str(item["key"]),
            f'The measure "{item["label"]}" is {what}'
            f"{_filter_words(src, item.get('filters') or [])}, in {item['unit']}.",
            "admin", approved_at))
    return out


def join_facts(items: Sequence[Mapping[str, Any]], sources: Mapping[str, Any],
               approved_at: Any) -> list[org_facts.Fact]:
    """Each approved ``join`` item that fits the catalogue, as subject
    ``join:<Module>.<field>``: the lookup that links two modules."""
    from gateway.crm_sync import catalogue as cat

    out: list[org_facts.Fact] = []
    for item in items:
        if item.get("kind") != cat.KIND_JOIN:
            continue
        src = sources[str(item["module"])]
        field = src.field(str(item["field"]))
        target = sources.get(str(item["target"]))
        target_label = target.label if target is not None else str(item["target"])
        out.append(org_facts.fact(
            APP, f"join:{item['module']}.{item['field']}", "join", str(item["target"]),
            f"{field.label} of {src.label} links each record to a {target_label} record.",
            "admin", approved_at))
    return out


def query_facts(items: Sequence[Mapping[str, Any]], approved_at: Any,
                ) -> list[org_facts.Fact]:
    """Each approved ``query`` item that fits the catalogue, as subject
    ``query:<key>``. It shows the question words and the parameter names
    only: no query, no total and no value (§6.3)."""
    from gateway.crm_sync import catalogue as cat

    out: list[org_facts.Fact] = []
    for item in items:
        if item.get("kind") != cat.KIND_QUERY:
            continue
        names = [str(p["name"]) for p in item.get("params") or []]
        tail = f" Its parameters: {', '.join(names)}." if names else ""
        out.append(org_facts.fact(
            APP, f"query:{item['key']}", "query", str(item["key"]),
            f'A verified question: "{item["question"]}".{tail}', "admin", approved_at))
    return out


def semantic_facts(others: Sequence[Mapping[str, Any]], sources: Sequence[Any],
                   approved_at: Any) -> list[org_facts.Fact]:
    """The ``measure``, ``join`` and ``query`` facts of the approved items
    *others* (SEM-7). An item that fails ``catalogue.semantic_problem`` or
    no longer fits the catalogue (``semantic_ref_problem``) serves nothing.
    The caller checks ``SEMANTIC_MODEL_ORGS``."""
    from gateway.crm_sync import catalogue as cat

    by_module = {src.module: src for src in sources}
    kept = [o for o in others if cat.is_semantic(o) and cat.semantic_problem(o) is None
            and cat.semantic_ref_problem(o, by_module) is None]
    return [*measure_facts(kept, by_module, approved_at),
            *join_facts(kept, by_module, approved_at),
            *query_facts(kept, approved_at)]


async def rule_facts(db: Any, org: str, user: Any) -> list[org_facts.Fact]:
    """The business rules of the org and of the caller (CRM-Q33 part 3), as
    subject ``rule:<slug>``. An org rule comes from an admin, and a member's
    own rule reads as ``set_by: you``. Empty outside
    ``CRM_ANSWER_FIRST_ORGS``. A member never reads another member's rule."""
    from gateway.routes.crm.rules import SCOPE_ORG, rules_for

    member = (getattr(user, "email", None) or "").strip().lower()
    out: list[org_facts.Fact] = []
    for rule in await rules_for(db, org, member or None):
        mine = rule.scope != SCOPE_ORG
        out.append(org_facts.fact(
            APP, f"rule:{rule.slug}", "rule", rule.body,
            (f"Your business rule: {rule.body}" if mine
             else f"Business rule of the organization: {rule.body}"),
            "member" if mine else "admin"))
    return out


#: The most term facts and value-alias facts of one read (§18.16).
TERM_FACTS = 12
ALIAS_FACTS = 12


def term_facts(state: Mapping[str, Any] | None, entries: Sequence[Any],
               sources: Mapping[str, Any]) -> list[org_facts.Fact]:
    """The org's own words for its figures (§18.16 part 4), as subject
    ``term:<word>``: each term that the chat met, with the field that an
    admin set, or the fields that it may mean. And each value alias that a
    filter seeded (part 6), as subject ``value:<Module>.<key>``. They come
    from ``zoho:questions``, so no read of a record runs here."""
    from gateway.crm_sync import terms

    found = terms.term_state(state)
    labels = {e.module: e.label for e in entries}
    out: list[org_facts.Fact] = []
    for qid, asked in sorted(found.asked.items())[:TERM_FACTS]:
        module, word = asked["module"], asked["word"]
        src = sources.get(module)
        if src is None:
            continue
        cands = terms.candidates(src.fields, src.fills, word)
        names = {c.key: c.label for c in cands}
        label = labels.get(module, module)
        answer = found.answers.get(qid)
        if answer in names:
            out.append(org_facts.fact(
                APP, f"term:{word}", "term", answer,
                f'"{word}" in {label} means {names[answer]} ({answer}).', "admin"))
        elif cands:
            out.append(org_facts.fact(
                APP, f"term:{word}", "term", None,
                f'"{word}" in {label} may mean {_names([c.label for c in cands])}. Pass it '
                "to crm_aggregate as term, which picks one the same way each time.", "code"))
    for head, value, values in terms.alias_lines(state)[:ALIAS_FACTS]:
        module, _sep, key = head.partition(".")
        others = [v for v in values if v != value]
        out.append(org_facts.fact(
            APP, f"value:{head}", "alias", value,
            f'In {labels.get(module, module)}, {key} "{value}" also reads as {_names(others)}.',
            "code"))
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
    from gateway.routes.crm.catalogue import COUNT_FROM_FILL, _load

    org = getattr(user, "organization_id", None)
    if not org or not questions_allowed(str(org)):
        return []
    try:
        await require_crm_access(user)
    except HTTPException:
        return []
    org = str(org)
    # Task 3593: the facts read `records > 0` and the order, never the exact
    # count, so the count comes from the stored fill rows (no table scan).
    _mode, sources, roles, others = await _load(db, org, count=COUNT_FROM_FILL)
    entries = cat.build(sources, roles, others)
    row = (await db.execute(text(_APPROVED_AT_SQL), {"org": org})).first()
    approved_at = row.approved_at if row is not None else None
    out: list[org_facts.Fact] = []
    # CRM-Q33: the business rules first, because they steer each answer.
    # The org's rules, and the caller's own, never another member's.
    out.extend(await rule_facts(db, org, user))
    revenue = revenue_fact(entries, cat.stored_items(others), approved_at)
    if revenue is not None:
        out.append(revenue)
    for entry in _ordered(entries):
        out.extend(entry_facts(entry, approved_at))
    out.extend(plan_facts(others, {e.module: e.label for e in entries}, approved_at))
    # SEM-7: the admin's measures, joins and verified questions, beside the
    # other plan facts. ``others`` holds the approved version only.
    if semantic_model_allowed(org):
        out.extend(semantic_facts(others, sources, approved_at))
    # WS-53 §18.16: the org's own words and its value aliases, last.
    from gateway.crm_sync import runs
    from gateway.crm_sync.questions import QUESTIONS_ROW

    cursors = await runs.read_cursors(db, org, QUESTIONS_ROW)
    state = getattr(cursors.get(QUESTIONS_ROW), "state", None)
    out.extend(term_facts(state, entries, {src.module: src for src in sources}))
    return out


org_facts.register(APP, crm_facts)


__all__ = ["APP", "crm_facts", "entry_facts", "join_facts", "measure_facts", "plan_facts",
           "query_facts", "revenue_fact", "rule_facts", "semantic_facts", "term_facts"]
