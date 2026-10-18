"""CRM · the business rules of an org (WS-53 CRM-Q33 part 3, ``crm_platform.md`` §18.15).

    GET    /crm/rules                  the org's rules and the caller's own
    POST   /crm/rules                  {"body", "scope"} adds one (settings)
    PATCH  /crm/rules/{id}             {"body"} changes one
    DELETE /crm/rules/{id}             removes one
    POST   /crm/rules/propose          {"body", "scope"} a rule from the chat,
                                       held for the member's confirm. It saves
                                       NOTHING.
    POST   /crm/rules/pending/{id}     {"choice": "save" | "skip"} the confirm,
                                       where id is ``rule:<token>``

A business rule is a short free-text statement of how the org reads its own
data: "All amounts net of GST", or "IP is our real earning. Rent and EB are
pass-through." The owner's words, 2026-10-18: "Store their answers as
preferences."

**The store.** The table ``crm_business_rules`` (migration 283), tenant-scoped
with FORCE row level security. A rule is no part of the mapping plan: a
member who is not an admin writes one, and the plan is the admin's versioned
store with one lock (§18.15, the decision). Only this module writes it.

**Who.** A CRM admin (``admin:access:manage``) writes, changes and removes an
org rule. Each member with CRM access writes, changes and removes a rule for
themselves only, and never reads the rule of another member. The org comes
from the session and the member from ``UserContext`` (R5, R11).

**From the chat, never by itself.** ``crm_propose_rule`` posts the member's
words to ``/crm/rules/propose``. The route checks them and holds them in
tenant Redis for 24 hours, keyed by the org, the member and a token that the
server makes. Nothing is saved until the member confirms: on the web a card,
whose pick passes the card gate of ``crm_answer_question``, and on WhatsApp a
"YES" that the gateway matches (``routes/crm/text_asks.py``). The confirm
reads the words from the server's own hold, never from the request.

**Where a rule goes.** The org-facts seam reads it as subject
``rule:<slug>`` (``routes/crm/facts.py``), so it reaches the CRM facts block
of a chat run. The org's rules reach the mapping prompts too
(``crm_sync/map_pipeline.py``), and a rule that asks for amounts net of GST
steers the recommendation of an amount question (``crm_sync/questions.
steered``).

⚠️ Dark: an org outside ``CRM_ANSWER_FIRST_ORGS``
(``flags.answer_first_allowed``) gets 404 ``rules_off`` from each route.

Fences: ``tests/unit/test_crm_rules.py`` and ``tests/unit/test_crm_rules_r8.py``.
"""

from __future__ import annotations

import json
import re
import secrets
import unicodedata
import uuid
from dataclasses import dataclass
from typing import Any, Literal

from acb_auth import UserContext, get_current_user
from acb_common import get_logger
from fastapi import Depends, HTTPException
from gateway.routes.crm.access import ADMIN_PERMISSION
from gateway.routes.crm.core import _tenant_session, router
from gateway.routes.crm.field_rules import _organization, _require_mirror
from gateway.routes.crm.flags import answer_first_allowed, require_crm_mirror
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import text
from sqlalchemy.exc import IntegrityError

_log = get_logger("crm.rules")

#: The code of the 404 that an org outside ``CRM_ANSWER_FIRST_ORGS`` gets.
RULES_OFF = "rules_off"

#: The longest rule, and the most rules of one owner (the org, or a member).
BODY_MAX = 300
RULES_MAX = 30

#: The words of a scope on the wire, and in the table.
SCOPE_ORG = "org"
SCOPE_ME = "me"
_TABLE_SCOPE = {SCOPE_ORG: "org", SCOPE_ME: "member"}

#: Where a member confirmed a rule.
ORIGIN_SETTINGS = "settings"
ORIGIN_CHAT = "chat"
ORIGIN_WHATSAPP = "whatsapp"

#: The tenant Redis namespace of a rule that waits for the member's confirm,
#: and how long it waits.
PENDING_NAMESPACE = "crm_rule_pending"
PENDING_TTL_S = 24 * 3600

#: The keys of the two options of a confirm.
SAVE = "save"
SKIP = "skip"
#: What the card shows for each key. The gateway checks a late pick against it.
OPTION_LABELS: dict[str, str] = {SAVE: "Save the rule", SKIP: "Do not save"}

#: The shape of a token of a held rule, and of its id on a card.
TOKEN_RE = re.compile(r"^[0-9a-f]{16}$")
RULE_ID_PREFIX = "rule:"

_GUARDS = [Depends(require_crm_mirror)]

_COLUMNS = ("id::text AS id, scope, member_email, slug, body, origin, created_by, "
            "updated_by, updated_at")

#: The org's rules and the caller's own, org rules first. The org is in the
#: predicate, and row level security is under it.
_LIST_SQL = (
    f"SELECT {_COLUMNS} FROM crm_business_rules "
    "WHERE organization_id = CAST(:org AS uuid) "
    "AND (scope = 'org' OR (scope = 'member' AND member_email = :me)) "
    "ORDER BY (scope = 'member'), created_at, id"
)
_ORG_SQL = (
    "SELECT body FROM crm_business_rules WHERE organization_id = CAST(:org AS uuid) "
    "AND scope = 'org' ORDER BY created_at, id"
)
_ONE_SQL = (
    f"SELECT {_COLUMNS} FROM crm_business_rules "
    "WHERE organization_id = CAST(:org AS uuid) AND id = CAST(:id AS uuid)"
)
_SLUGS_SQL = (
    "SELECT slug FROM crm_business_rules WHERE organization_id = CAST(:org AS uuid) "
    "AND scope = :scope AND COALESCE(member_email, '') = :member"
)
_INSERT_SQL = (
    "INSERT INTO crm_business_rules (organization_id, scope, member_email, slug, body, "
    "origin, created_by, updated_by) VALUES (CAST(:org AS uuid), :scope, :member, :slug, "
    f":body, :origin, :by, :by) RETURNING {_COLUMNS}"
)
_UPDATE_SQL = (
    "UPDATE crm_business_rules SET body = :body, updated_by = :by, updated_at = now() "
    "WHERE organization_id = CAST(:org AS uuid) AND id = CAST(:id AS uuid) "
    f"RETURNING {_COLUMNS}"
)
_DELETE_SQL = (
    "DELETE FROM crm_business_rules WHERE organization_id = CAST(:org AS uuid) "
    "AND id = CAST(:id AS uuid) RETURNING id"
)


def rules_off() -> HTTPException:
    return HTTPException(status_code=404, detail={
        "code": RULES_OFF, "message": "Business rules are off for this organization.",
    })


def _refuse(status: int, code: str, message: str) -> HTTPException:
    return HTTPException(status_code=status, detail={"code": code, "message": message})


# ── Pure rules ──────────────────────────────────────────────────────────────

#: A character that a rule never holds: a control character or a mark that
#: hides text. A rule reaches a prompt, so it stays plain text.
_HIDDEN = re.compile(r"[\x00-\x08\x0b-\x1f\x7f​-‏‪-‮⁠-⁤]")


def clean_body(raw: Any) -> str | None:
    """The words of a rule, or None when they are no rule: a string of 1 to
    :data:`BODY_MAX` characters after the spaces fold, with no control
    character and no mark that hides text."""
    if not isinstance(raw, str):
        return None
    text_ = unicodedata.normalize("NFC", raw)
    if _HIDDEN.search(text_.replace("\n", " ").replace("\t", " ")):
        return None
    folded = " ".join(text_.split())
    return folded if 0 < len(folded) <= BODY_MAX else None


def slug_of(body: str, taken: set[str] | frozenset[str] = frozenset()) -> str:
    """The short id of a rule: its first words in lower case, joined by
    ``-``, at most 40 characters, cut between two words. A slug that the
    owner holds already takes ``-2``, ``-3`` and on."""
    words = re.findall(r"[a-z0-9]+", unicodedata.normalize("NFKD", body).lower())
    joined = "-".join(words)
    base = joined[:40]
    if len(joined) > 40 and joined[40] != "-" and "-" in base:
        base = base.rsplit("-", 1)[0]
    base = base.strip("-") or "rule"
    slug, n = base, 2
    while slug in taken:
        slug = f"{base[:40]}-{n}"
        n += 1
    return slug


def member_of(user: Any) -> str:
    """The email key of the caller, from ``UserContext`` only (R5, R11)."""
    email = (getattr(user, "email", None) or "").strip().lower()
    if "@" not in email:
        raise HTTPException(status_code=403, detail="Forbidden: no member for this call.")
    return email


def is_admin(user: Any) -> bool:
    return bool(user.has_permission(ADMIN_PERMISSION))


def _check_scope(user: Any, scope: str) -> None:
    """403 ``rule_admin_only`` when a member who is not an admin names the org."""
    if scope == SCOPE_ORG and not is_admin(user):
        raise _refuse(403, "rule_admin_only",
                      "Only a CRM admin sets a rule for everyone. You can save it for "
                      "yourself.")


# ── The wire ────────────────────────────────────────────────────────────────


class RuleModel(BaseModel):
    id: str
    #: ``org`` (for everyone) or ``me`` (for the caller only).
    scope: str
    slug: str
    body: str
    #: The caller may change and remove it.
    editable: bool
    #: Who changed it last, by display name, never an email ("A member"
    #: when the directory names nobody), and when.
    by: str
    at: str
    origin: str


class RulesOut(BaseModel):
    rules: list[RuleModel]
    #: The caller is a CRM admin, so the screen offers an org rule.
    can_admin: bool


class RuleIn(BaseModel):
    model_config = ConfigDict(extra="forbid")

    body: str = Field(min_length=1, max_length=2000)
    scope: Literal["org", "me"] = SCOPE_ME


class RulePatch(BaseModel):
    model_config = ConfigDict(extra="forbid")

    body: str = Field(min_length=1, max_length=2000)


class ProposeOut(BaseModel):
    """A rule held for the member's confirm. It is no rule yet."""

    id: str
    token: str
    scope: str
    body: str
    #: The question of the confirm, why it asks, and its two options.
    question: str
    why: str
    options: list[dict[str, str]]


class PendingIn(BaseModel):
    model_config = ConfigDict(extra="forbid")

    choice: Literal["save", "skip"]


class PendingOut(BaseModel):
    #: ``saved`` or ``skipped``.
    result: str
    line: str
    rule: RuleModel | None = None


def _scope_word(row: Any) -> str:
    return SCOPE_ORG if str(row.scope) == "org" else SCOPE_ME


def _model(row: Any, user: Any, names: dict[str, str]) -> RuleModel:
    scope = _scope_word(row)
    who = str(row.updated_by or "").strip().lower()
    return RuleModel(
        id=str(row.id), scope=scope, slug=str(row.slug), body=str(row.body),
        editable=is_admin(user) if scope == SCOPE_ORG else True,
        by=names.get(who) or "A member", at=row.updated_at.isoformat()
        if row.updated_at is not None else "", origin=str(row.origin),
    )


async def _names(db: Any, rows: list[Any]) -> dict[str, str]:
    from gateway.routes.people.core import names_of

    return await names_of(db, {str(r.updated_by or "") for r in rows})


# ── The reads that other modules use ────────────────────────────────────────


@dataclass(frozen=True)
class RuleRow:
    """One rule as a reader of another module sees it: no email."""

    scope: str
    slug: str
    body: str


async def rules_for(db: Any, org: str, member: str | None) -> list[RuleRow]:
    """The org's rules and the rules of *member*, in the caller's tenant
    session. Empty outside ``CRM_ANSWER_FIRST_ORGS``. A member never reads
    the rule of another member."""
    if not answer_first_allowed(org):
        return []
    rows = (await db.execute(text(_LIST_SQL), {
        "org": org, "me": (member or "").strip().lower() or "\x00none"})).fetchall()
    return [RuleRow(scope=_scope_word(r), slug=str(r.slug), body=str(r.body)) for r in rows]


async def org_rule_texts(db: Any, org: str) -> list[str]:
    """The words of the org's rules (never a member's), for the mapping
    prompts. Empty outside ``CRM_ANSWER_FIRST_ORGS``."""
    if not answer_first_allowed(org):
        return []
    return [str(r.body) for r in (await db.execute(text(_ORG_SQL), {"org": org})).fetchall()]


# ── The writer ──────────────────────────────────────────────────────────────


async def add_rule(db: Any, org: str, user: Any, body: str, scope: str, origin: str) -> Any:
    """THE writer of a new rule, in the caller's transaction: the settings
    route and the confirm of a held rule both call it. The caller checked
    the words (:func:`clean_body`) and the scope (:func:`_check_scope`)."""
    member = member_of(user)
    table_scope = _TABLE_SCOPE[scope]
    owner = "" if scope == SCOPE_ORG else member
    taken = {str(r.slug) for r in (await db.execute(text(_SLUGS_SQL), {
        "org": org, "scope": table_scope, "member": owner})).fetchall()}
    if len(taken) >= RULES_MAX:
        raise _refuse(409, "too_many_rules",
                      f"A list holds at most {RULES_MAX} rules. Remove one first.")
    try:
        return (await db.execute(text(_INSERT_SQL), {
            "org": org, "scope": table_scope, "member": owner or None,
            "slug": slug_of(body, taken), "body": body, "origin": origin, "by": member,
        })).first()
    except IntegrityError as exc:
        raise _refuse(409, "rule_moved", "The rules changed. Try again.") from exc


async def _owned(db: Any, org: str, user: Any, rule_id: str) -> Any:
    """The rule *rule_id* that the caller may change, or 404 / 403. A rule
    of another member reads as absent, so its words never leak."""
    try:
        uuid.UUID(str(rule_id))
    except ValueError as exc:
        raise _refuse(404, "no_rule", "There is no such rule.") from exc
    row = (await db.execute(text(_ONE_SQL), {"org": org, "id": rule_id})).first()
    if row is None or (str(row.scope) == "member"
                       and str(row.member_email or "") != member_of(user)):
        raise _refuse(404, "no_rule", "There is no such rule.")
    if str(row.scope) == "org" and not is_admin(user):
        raise _refuse(403, "rule_admin_only", "Only a CRM admin changes a rule for everyone.")
    return row


def _org_or_off(user: Any) -> str:
    org = _organization(user)
    if not answer_first_allowed(org):
        raise rules_off()
    return org


def _body_or_422(raw: Any) -> str:
    body = clean_body(raw)
    if body is None:
        raise _refuse(422, "bad_rule",
                      f"A rule is plain text of 1 to {BODY_MAX} characters.")
    return body


# ── The routes ──────────────────────────────────────────────────────────────


@router.get("/rules", response_model=RulesOut, dependencies=_GUARDS)
async def list_rules(user: UserContext = Depends(get_current_user)) -> RulesOut:
    """The org's rules and the caller's own. Each member with CRM access
    reads them. It reads only."""
    org = _org_or_off(user)
    me = member_of(user)
    async with _tenant_session() as db:
        await _require_mirror(db, org)
        rows = (await db.execute(text(_LIST_SQL), {"org": org, "me": me})).fetchall()
        names = await _names(db, list(rows))
    return RulesOut(rules=[_model(r, user, names) for r in rows], can_admin=is_admin(user))


@router.post("/rules", response_model=RuleModel, dependencies=_GUARDS)
async def create_rule(payload: RuleIn, user: UserContext = Depends(get_current_user)) -> RuleModel:
    """Add a rule from the CRM settings. An org rule needs a CRM admin."""
    org = _org_or_off(user)
    body = _body_or_422(payload.body)
    _check_scope(user, payload.scope)
    async with _tenant_session() as db:
        await _require_mirror(db, org)
        row = await add_rule(db, org, user, body, payload.scope, ORIGIN_SETTINGS)
        names = await _names(db, [row])
    _log.info("crm.rules.added", organization_id=org, scope=payload.scope, origin="settings")
    return _model(row, user, names)


@router.patch("/rules/{rule_id}", response_model=RuleModel, dependencies=_GUARDS)
async def change_rule(rule_id: str, payload: RulePatch,
                      user: UserContext = Depends(get_current_user)) -> RuleModel:
    """Change the words of a rule. The slug stays, so its fact keeps its
    subject."""
    org = _org_or_off(user)
    body = _body_or_422(payload.body)
    async with _tenant_session() as db:
        await _require_mirror(db, org)
        await _owned(db, org, user, rule_id)
        row = (await db.execute(text(_UPDATE_SQL), {
            "org": org, "id": rule_id, "body": body, "by": member_of(user)})).first()
        names = await _names(db, [row])
    return _model(row, user, names)


@router.delete("/rules/{rule_id}", dependencies=_GUARDS)
async def remove_rule(rule_id: str, user: UserContext = Depends(get_current_user)) -> dict:
    """Remove a rule."""
    org = _org_or_off(user)
    async with _tenant_session() as db:
        await _require_mirror(db, org)
        await _owned(db, org, user, rule_id)
        await db.execute(text(_DELETE_SQL), {"org": org, "id": rule_id})
    return {"id": rule_id, "removed": True}


# ── From the chat: hold, then confirm ───────────────────────────────────────


def _redis() -> Any:
    from acb_common.tenant_redis import get_tenant_redis

    return get_tenant_redis()


def _pending_key(member: str, token: str) -> Any:
    from acb_common.tenant_redis import key

    return key(PENDING_NAMESPACE, member, token)


def confirm_question(body: str, scope: str) -> tuple[str, str]:
    """The question of a confirm, and why it asks. Plain words, and no name
    of the engine."""
    who = ("everyone in your organization" if scope == SCOPE_ORG
           else "your own answers only")
    return (f'Save this business rule? "{body}"',
            f"The AI then follows it in each answer about your CRM data. It applies to {who}. "
            "You can change it later in CRM settings, Business rules.")


def option_list() -> list[dict[str, str]]:
    return [{"key": k, "label": v} for k, v in OPTION_LABELS.items()]


async def propose(org: str, user: Any, raw_body: Any, scope: str) -> ProposeOut:
    """Hold a rule for the member's confirm (the chat, never by itself). It
    checks the words and the scope, and saves NOTHING. A Redis outage is a
    503: with no hold, there is nothing to confirm."""
    from acb_common.tenant_redis import OUTAGE_ERRORS, organization_scope

    body = _body_or_422(raw_body)
    _check_scope(user, scope)
    member = member_of(user)
    token = secrets.token_hex(8)
    try:
        with organization_scope(org):
            await _redis().setex(_pending_key(member, token), PENDING_TTL_S,
                                 json.dumps({"body": body, "scope": scope}))
    except OUTAGE_ERRORS as exc:
        raise HTTPException(status_code=503, headers={"Retry-After": "30"}, detail={
            "code": "hold_unavailable",
            "message": "The rule could not be held for your confirm now. Try again soon.",
        }) from exc
    question, why = confirm_question(body, scope)
    return ProposeOut(id=f"{RULE_ID_PREFIX}{token}", token=token, scope=scope, body=body,
                      question=question, why=why, options=option_list())


async def pending_rule(org: str, member: str, token: str) -> dict[str, str] | None:
    """The held rule of *member* with *token*, or None. It reads only."""
    from acb_common.tenant_redis import organization_scope

    if not TOKEN_RE.match(token or ""):
        return None
    try:
        with organization_scope(org):
            raw = await _redis().get(_pending_key(member, token))
    except Exception:
        return None
    return _held(raw)


def _held(raw: Any) -> dict[str, str] | None:
    if isinstance(raw, bytes):
        raw = raw.decode("utf-8", "replace")
    try:
        held = json.loads(raw) if isinstance(raw, str) else None
    except ValueError:
        return None
    if (not isinstance(held, dict) or held.get("scope") not in (SCOPE_ORG, SCOPE_ME)
            or clean_body(held.get("body")) is None):
        return None
    return {"body": str(held["body"]), "scope": str(held["scope"])}


async def confirm(org: str, user: Any, token: str, choice: str, origin: str) -> PendingOut:
    """The member's confirm of a held rule. The words come from the server's
    hold (GETDEL, one use), never from the request. ``save`` writes the rule
    through :func:`add_rule`, and checks the scope again. ``skip`` drops the
    hold. A hold that is gone gives 409 ``rule_moved``."""
    from acb_common.tenant_redis import OUTAGE_ERRORS, organization_scope

    if not TOKEN_RE.match(token or ""):
        raise _refuse(404, "no_rule", "There is no such rule.")
    member = member_of(user)
    try:
        with organization_scope(org):
            raw = await _redis().getdel(_pending_key(member, token))
    except OUTAGE_ERRORS as exc:
        raise HTTPException(status_code=503, headers={"Retry-After": "30"}, detail={
            "code": "hold_unavailable", "message": "Try again soon.",
        }) from exc
    held = _held(raw)
    if held is None:
        raise _refuse(409, "rule_moved", "This rule is no longer waiting. Say it again.")
    if choice == SKIP:
        return PendingOut(result="skipped", line="The rule is not saved.")
    _check_scope(user, held["scope"])
    # The tenant is the caller's: the request bound it, or the WhatsApp run
    # bound the org of the phone's link (R5).
    async with _tenant_session() as db:
        await _require_mirror(db, org)
        row = await add_rule(db, org, user, held["body"], held["scope"], origin)
        names = await _names(db, [row])
    _log.info("crm.rules.added", organization_id=org, scope=held["scope"], origin=origin)
    who = "for everyone" if held["scope"] == SCOPE_ORG else "for you"
    return PendingOut(result="saved", line=f'Saved {who}: "{held["body"]}"',
                      rule=_model(row, user, names))


@router.post("/rules/propose", response_model=ProposeOut, dependencies=_GUARDS)
async def propose_rule(payload: RuleIn, user: UserContext = Depends(get_current_user),
                       ) -> ProposeOut:
    """Hold a rule from the chat for the member's confirm. It saves nothing."""
    org = _org_or_off(user)
    async with _tenant_session() as db:
        await _require_mirror(db, org)
    return await propose(org, user, payload.body, payload.scope)


@router.post("/rules/pending/{rule_id}", response_model=PendingOut, dependencies=_GUARDS)
async def confirm_rule(rule_id: str, payload: PendingIn,
                       user: UserContext = Depends(get_current_user)) -> PendingOut:
    """The confirm of a held rule, from the card gate of the chat. The id is
    ``rule:<token>``, the id of the card."""
    org = _org_or_off(user)
    if not rule_id.startswith(RULE_ID_PREFIX):
        raise _refuse(404, "no_rule", "There is no such rule.")
    return await confirm(org, user, rule_id[len(RULE_ID_PREFIX):], payload.choice,
                         ORIGIN_CHAT)


__all__ = [
    "BODY_MAX",
    "OPTION_LABELS",
    "PENDING_NAMESPACE",
    "PENDING_TTL_S",
    "RULES_MAX",
    "RULES_OFF",
    "RULE_ID_PREFIX",
    "SAVE",
    "SCOPE_ME",
    "SCOPE_ORG",
    "SKIP",
    "TOKEN_RE",
    "PendingIn",
    "PendingOut",
    "ProposeOut",
    "RuleIn",
    "RuleModel",
    "RulePatch",
    "RuleRow",
    "RulesOut",
    "add_rule",
    "change_rule",
    "clean_body",
    "confirm",
    "confirm_question",
    "confirm_rule",
    "create_rule",
    "list_rules",
    "member_of",
    "option_list",
    "org_rule_texts",
    "pending_rule",
    "propose",
    "propose_rule",
    "remove_rule",
    "rules_for",
    "rules_off",
    "slug_of",
]
