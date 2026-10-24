"""The member's "always copy" list of a mailbox (``email_app_master_plan.md`` §21.15).

Item 7b of §21.12 (owner, 2026-10-24): "always Cc Kiran and Veena on mail to
jainuniversity.ac.in". A rule names a domain or an address (``match``) and the
colleagues to copy (``cc``). It ADDS those colleagues to the member's own reply
card and new-mail card, as rows that the member can untick. It never sends and
never acts on its own.

It is not a mail rule. A mail rule runs on incoming mail, and the rule tools
count a ``cc_address`` as an outward act. So the list is a setting of the
mailbox: the column ``email_assistant_settings.always_copy`` (migration 299).

Three rules hold.

1. **Only a known colleague.** The PUT takes a ``cc`` person only when THE
   classifier (``held_card.classify_addresses``) says ``org`` or ``sent``. A
   held chat can never put a stranger on the list, and nor can any chat.
2. **Checked again on use.** :func:`always_copy_people` classes each person
   again when a card reads the list, and keeps an ``org`` or a ``sent`` one
   only. A colleague who left the organization drops off.
3. **The member's own list.** Each route checks the owner of the mailbox. A
   held chat refuses a change (``held_card.HELD_SETTING_DECISIONS``).

Fence: ``tests/unit/test_email_always_copy.py``, and its R8 half
``tests/unit/test_email_always_copy_r8.py``.
"""
from __future__ import annotations

import json
import re
from collections.abc import Iterable, Sequence
from typing import Any

from acb_auth import UserContext, get_current_user
from fastapi import Depends, HTTPException, Query
from gateway.routes.email.core import (
    _assert_account_owner,
    _log,
    _savepoint,
    _tenant_session,
    router,
)
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import text

#: The most rules of one mailbox, and the most colleagues of one rule.
MAX_RULES = 20
MAX_CC = 5
#: The classes that a colleague of the list may have.
KNOWN_CLASSES = frozenset({"org", "sent"})

#: A domain as a rule takes it: ASCII labels, and a top level of letters.
_DOMAIN = re.compile(r"(?=.{4,253}$)([a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?\.)+[a-z]{2,63}")

READ_SQL = "SELECT always_copy FROM email_assistant_settings WHERE account_id = CAST(:aid AS uuid)"
#: The one writer. A mailbox with no settings row gets one with the defaults.
WRITE_SQL = (
    "INSERT INTO email_assistant_settings (account_id, always_copy, updated_at) "
    "VALUES (CAST(:aid AS uuid), CAST(:rules AS jsonb), now()) "
    "ON CONFLICT (account_id) DO UPDATE SET always_copy = EXCLUDED.always_copy, "
    "updated_at = now()")


class AlwaysCopyRule(BaseModel):
    """One rule: a domain or an address, and the colleagues to copy."""

    model_config = ConfigDict(extra="forbid")

    match: str = Field(max_length=254)
    cc: list[str] = Field(max_length=MAX_CC)


class AlwaysCopyPut(BaseModel):
    """The WHOLE list of one mailbox. An empty list removes each rule."""

    model_config = ConfigDict(extra="forbid")

    account_id: str
    rules: list[AlwaysCopyRule] = Field(default_factory=list, max_length=MAX_RULES)


def clean_match(raw: str) -> str:
    """The match of a rule in lower case: a domain, or a whole address.
    Raises ``ValueError`` with the words that the member reads."""
    from acb_skills.held_card import address_problem

    m = str(raw or "").strip().lower().lstrip("@")
    if "@" in m:
        why = address_problem(m)
        if why:
            raise ValueError(f"{m} {why}")
        return m
    if not _DOMAIN.fullmatch(m) or "*" in m:
        raise ValueError(f"{m or '(empty)'} is not a domain or an address. Name a domain "
                         "such as university.edu, or a whole address")
    return m


def clean_rules(rules: Iterable[Any]) -> list[dict[str, Any]]:
    """The rules in their stored form: each match once, each colleague once
    and in lower case. Raises ``ValueError`` for a bad match or address."""
    from acb_skills.held_card import address_problem

    out: dict[str, list[str]] = {}
    for r in rules:
        match = clean_match(r.match if hasattr(r, "match") else (r or {}).get("match"))
        cc = r.cc if hasattr(r, "cc") else list((r or {}).get("cc") or [])
        people = out.setdefault(match, [])
        for a in cc:
            addr = str(a or "").strip().lower()
            why = address_problem(addr)
            if why:
                raise ValueError(f"{addr or '(empty)'} {why}")
            if addr not in people:
                people.append(addr)
        if not people:
            raise ValueError(f"The rule for {match} names nobody to copy")
        if len(people) > MAX_CC:
            raise ValueError(f"The rule for {match} names more than {MAX_CC} people")
    if len(out) > MAX_RULES:
        raise ValueError(f"A mailbox holds at most {MAX_RULES} rules")
    return [{"match": m, "cc": c} for m, c in out.items()]


def _stored(value: Any) -> list[dict[str, Any]]:
    """The stored list, read leniently: a bad entry is skipped, never raised."""
    if isinstance(value, str):
        try:
            value = json.loads(value or "[]")
        except ValueError:
            value = []
    out: list[dict[str, Any]] = []
    for r in value if isinstance(value, list) else []:
        if isinstance(r, dict) and isinstance(r.get("match"), str):
            cc = [str(a).strip().lower() for a in r.get("cc") or [] if str(a or "").strip()]
            out.append({"match": r["match"].strip().lower(), "cc": cc})
    return out


async def read_rules(db: Any, account_id: str) -> list[dict[str, Any]]:
    """The stored rules of one mailbox. No row reads as no rule."""
    row = (await db.execute(text(READ_SQL), {"aid": account_id})).fetchone()
    return _stored(getattr(row, "always_copy", None) if row is not None else None)


def _domain(addr: str) -> str:
    return str(addr or "").rpartition("@")[2].strip().lower()


def matches(rule_match: str, person: str) -> bool:
    """True when *person* fits the match: the same address, or an address at
    the domain or at a subdomain of it."""
    p = str(person or "").strip().lower()
    m = str(rule_match or "").strip().lower()
    if not p or not m:
        return False
    if "@" in m:
        return p == m
    d = _domain(p)
    return d == m or d.endswith("." + m)


def match_people(rules: Sequence[dict[str, Any]], people: Iterable[str],
                 skip: Iterable[str] = ()) -> list[tuple[str, str]]:
    """``(colleague, match)`` for each colleague that a rule adds for
    *people*, in rule order, each once. A colleague in *skip* (a person
    already on the mail, the member) is left out."""
    seen = {str(a).strip().lower() for a in skip if a}
    persons = [str(p).strip().lower() for p in people if p]
    out: list[tuple[str, str]] = []
    for r in rules:
        if not any(matches(r["match"], p) for p in persons):
            continue
        for a in r.get("cc") or []:
            if a not in seen:
                seen.add(a)
                out.append((a, r["match"]))
    return out


async def always_copy_people(
    db: Any, owner: str, account_id: str, people: Iterable[str], *,
    skip: Iterable[str] = (),
) -> list[tuple[str, str]]:
    """The colleagues that the member's rules add for *people*, each an
    ``org`` or a ``sent`` person NOW (rule 2). A fault reads as none. The
    reads run in a savepoint, so a fault leaves the caller's transaction
    usable."""
    from gateway.routes.email.automation.held_card import classify_addresses

    try:
        async with _savepoint(db):
            found = match_people(await read_rules(db, account_id), people, skip)
            if not found:
                return []
            classes = await classify_addresses(db, owner, [a for a, _m in found])
    except Exception as exc:  # the card shows without the rule's people
        _log.warning("email.always_copy_read_failed", error=type(exc).__name__)
        return []
    return [(a, m) for a, m in found if classes.get(a) in KNOWN_CLASSES]


@router.get("/assistant/always-copy")
async def get_always_copy(
    account_id: str = Query(...),
    user: UserContext = Depends(get_current_user),
) -> dict[str, Any]:
    """The always-copy rules of one mailbox of the caller. A read."""
    async with _tenant_session() as db:
        await _assert_account_owner(db, account_id, user.email or "anonymous")
        return {"account_id": account_id, "rules": await read_rules(db, account_id)}


@router.get("/assistant/always-copy/match")
async def match_always_copy(
    account_id: str = Query(...),
    people: list[str] = Query(default=[]),
    user: UserContext = Depends(get_current_user),
) -> dict[str, Any]:
    """The colleagues that the caller's rules add for *people*, for the card
    of a new mail (§21.15). A read: it writes nothing and sends nothing."""
    owner = user.email or "anonymous"
    async with _tenant_session() as db:
        await _assert_account_owner(db, account_id, owner)
        found = await always_copy_people(db, owner, account_id, people[:100],
                                         skip=[*people, owner])
    return {"people": [{"address": a, "match": m} for a, m in found]}


@router.put("/assistant/always-copy")
async def put_always_copy(
    req: AlwaysCopyPut,
    user: UserContext = Depends(get_current_user),
) -> dict[str, Any]:
    """Replace the always-copy rules of one mailbox of the caller.

    Each colleague must be ``org`` or ``sent`` by THE classifier, or the
    route answers 422 and changes nothing (rule 1). The member's own
    addresses are refused, because a rule never copies the member."""
    from gateway.routes.email.automation.held_card import classify_addresses
    from gateway.routes.email.automation.identity import resolve_self

    owner = user.email or "anonymous"
    try:
        rules = clean_rules(req.rules)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=f"Nothing changed. {exc}.") from None
    async with _tenant_session() as db:
        await _assert_account_owner(db, req.account_id, owner)
        me = await resolve_self(db, req.account_id)
        own = {a.lower() for a in getattr(me, "self_addresses", ()) or ()} | {owner.lower()}
        people = sorted({a for r in rules for a in r["cc"]})
        mine = [a for a in people if a in own]
        if mine:
            raise HTTPException(status_code=422, detail=(
                f"Nothing changed. {mine[0]} is your own address, and a rule never "
                "copies you."))
        classes = await classify_addresses(db, owner, people)
        stranger = [a for a in people if classes.get(a) not in KNOWN_CLASSES]
        if stranger:
            raise HTTPException(status_code=422, detail=(
                f"Nothing changed. {stranger[0]} is not a colleague, and you never wrote "
                "to them. A rule copies only a colleague or a person you wrote to before."))
        await db.execute(text(WRITE_SQL), {"aid": req.account_id, "rules": json.dumps(rules)})
    _log.info("email.always_copy_saved", rules=len(rules), people=len(people))
    return {"account_id": req.account_id, "rules": rules}
