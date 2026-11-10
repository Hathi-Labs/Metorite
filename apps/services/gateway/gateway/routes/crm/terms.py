"""CRM · the org's own word for a figure (WS-53 §18.16 part 4).

Spec: ``project-docs/specs/crm_platform.md`` §18.16.

    GET /crm/terms/resolve?entity=module:<ApiName>&term=<the member's word>

The chat tool ``crm_aggregate(term=…)`` calls it first. It answers which
fields of the module the word can mean (``crm_sync/terms.candidates``), and
who decides between them (``crm_sync/terms.decide``): an admin's answer, a
business rule, the member's own pick, another member's pick, or the one
field that matches. So one word maps to one field the same way each time.

**An open term.** When two or more fields match and nothing decides, the
answer holds ``ask``: the question of the term in the CRM-Q32 store, built
as ``/crm/questions/ask`` builds a card. The route records the term in
``zoho:questions`` under ``terms`` first, so the card, the WhatsApp ask and
the pick all find the same question. The tool reads a total for each
choice, and asks first only when the totals differ (the confidence rule,
``crm_sync/terms.CLOSE_SHARE``). The record is made only for an org on
``CRM_QUESTIONS_ORGS``. Off the list, an open term answers on the most
filled field.

**Read as the member.** The route sits on the shared ``core.router``, so the
kill switch, ``feature:crm`` and the access rule apply. The org comes from
the session, and the member from ``UserContext`` (R5, R11). The only write
is the record of the asked term.

Fences: ``tests/unit/test_crm_answer_consistency.py`` and
``tests/unit/test_crm_answer_consistency_r8.py`` (R8).
"""

from __future__ import annotations

from dataclasses import replace
from typing import Any

from acb_auth import UserContext, get_current_user
from acb_common import get_logger
from fastapi import Depends, HTTPException, Query
from gateway.crm_sync import questions as qs
from gateway.crm_sync import terms
from gateway.routes.crm.core import _tenant_session, router
from gateway.routes.crm.field_rules import _organization, _require_mirror
from gateway.routes.crm.flags import questions_allowed
from pydantic import BaseModel, Field

_log = get_logger("crm.terms")

#: The prefix of a module entity, as the aggregate takes it.
MODULE_PREFIX = "module:"
#: The longest term that the route takes.
TERM_MAX = 80


class TermChoiceModel(BaseModel):
    key: str
    label: str
    fill: float | None = None
    #: CRM-Q33 part 5: "Includes GST", "Excludes GST", "After TDS".
    evidence: list[str] = Field(default_factory=list)


class TermOut(BaseModel):
    """What one word of the member means in one module."""

    id: str
    word: str
    module: str
    #: The label of the module.
    label: str
    #: Who decides: ``admin``, ``rule``, ``me``, ``member``, ``single``, or
    #: None while the term is open.
    by: str | None = None
    #: The field that decides, and its label. None while the term is open.
    field: str | None = None
    field_label: str | None = None
    #: The words of why that field, for "How I got this".
    why: str = ""
    #: Each field that the word can mean, the most filled first.
    candidates: list[TermChoiceModel]
    #: True while the member keeps the best guess ("Keep this for now").
    held: bool = False
    #: The question of an open term, as ``/crm/questions/ask`` gives a card.
    ask: dict[str, Any] | None = None


def _module_of(entity: str) -> str:
    if not entity.startswith(MODULE_PREFIX) or not entity[len(MODULE_PREFIX):]:
        raise HTTPException(status_code=422, detail={
            "code": "term_needs_module",
            "message": "A term goes with a module entity: 'module:<ApiName>'.",
        })
    return entity[len(MODULE_PREFIX):]


async def _rule_bodies(db: Any, org: str, member: str) -> list[str]:
    """The business rules of the org and of the member (CRM-Q33 part 3).
    A failed read gives none, so a term never fails on a rule."""
    from gateway.routes.crm.rules import rules_for

    try:
        async with db.begin_nested():
            return [str(r.body) for r in await rules_for(db, org, member)]
    except Exception as exc:  # advice only
        _log.warning("crm.terms.rules_read_failed", error_type=type(exc).__name__)
        return []


async def _evidence(db: Any, org: str, src: Any) -> dict[str, tuple[str, ...]]:
    """The GST evidence of the money fields of *src* (CRM-Q33 part 5). A
    failed read gives none."""
    from gateway.routes.crm.questions import module_evidence

    try:
        async with db.begin_nested():
            return await module_evidence(db, org, src)
    except Exception as exc:  # advice only
        _log.warning("crm.terms.evidence_failed", error_type=type(exc).__name__)
        return {}


@router.get("/terms/resolve", response_model=TermOut)
async def resolve_term(
    entity: str,
    term: str = Query(min_length=1, max_length=TERM_MAX),
    user: UserContext = Depends(get_current_user),
) -> TermOut:
    """Which field of a module the member's word means, and who decides it
    (§18.16 part 4). An open term with two or more fields carries the
    question of the term, for the chat to ask first."""
    from gateway.crm_sync import runs
    from gateway.routes.crm.access import ADMIN_PERMISSION
    from gateway.routes.crm.modules import _require_module, mirrored_modules
    from gateway.routes.crm.questions import (
        _ask_model,
        chat_card_questions,
        held_ids,
        load_for_terms,
        term_pickers,
    )

    org = _organization(user)
    module = _module_of(entity)
    word = " ".join(str(term).split())
    if not terms.words(word):
        raise HTTPException(status_code=422, detail={
            "code": "term_empty", "message": "The term holds no word."})
    member = (getattr(user, "email", None) or "").strip().lower()
    qid = terms.term_id(module, word)
    async with _tenant_session() as db:
        await _require_mirror(db, org)
        _require_module(await mirrored_modules(db), module)
        inputs = await load_for_terms(db, org)
        src = inputs.sources.get(module) if inputs is not None else None
        if inputs is None or src is None:
            raise HTTPException(status_code=404, detail="Module not found")
        evidence = await _evidence(db, org, src)
        cands = terms.candidates(src.fields, src.fills, word, evidence)
        rules = await _rule_bodies(db, org, member)
        mine, others = term_pickers(inputs, qid, member)
        pick = terms.decide(cands, answer=inputs.term_answers.get(qid), rules=rules,
                            term=word, mine=mine, others=others)
        ask: dict[str, Any] | None = None
        if pick.by is None and len(cands) >= 2 and questions_allowed(org):
            asked = inputs.terms.get(qid) or {"module": module, "word": word}
            if qid not in inputs.terms:
                await runs.lock_cursor(db, org, qs.QUESTIONS_ROW)
                await runs.merge_cursor_entry(db, org, qs.QUESTIONS_ROW, terms.TERMS_BUCKET,
                                              qid, dict(asked))
            inputs = replace(inputs, terms={**inputs.terms, qid: asked})
            # The card as `/crm/questions/ask` builds it, with the same
            # steering, so the late check of a pick sees the same labels.
            inputs, picked, steer = await chat_card_questions(db, org, inputs, [qid], False,
                                                              member)
            if picked:
                ask = _ask_model(picked[0], inputs, member=member,
                                 admin=user.has_permission(ADMIN_PERMISSION),
                                 held=False, steer=steer).model_dump()
    held = bool(ask) and member and qid in await held_ids(org, member, [qid])
    entry = next((e for e in inputs.entries if e.module == module), None)
    labels = {c.key: c.label for c in cands}
    return TermOut(
        id=qid, word=word, module=module,
        label=str(getattr(entry, "label", None) or getattr(src, "label", None) or module),
        by=pick.by, field=pick.field, field_label=labels.get(pick.field or ""), why=pick.why,
        candidates=[TermChoiceModel(key=c.key, label=c.label, fill=c.fill,
                                    evidence=qs.evidence_labels(c.evidence)) for c in cands],
        held=bool(held), ask=ask,
    )


__all__ = ["TermOut", "resolve_term"]
