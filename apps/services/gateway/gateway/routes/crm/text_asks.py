"""CRM · a question in a chat with no cards (WS-53 CRM-Q33 part 2, ``crm_platform.md`` §18.15).

WhatsApp has no card. When a CRM answer of a WhatsApp run asks its one
follow-up question, or holds a business rule for the member's confirm, the
WhatsApp channel (``routes/whatsapp_channel/bot_run.py``) calls :func:`offer`
after the run. :func:`offer` builds the question again on the SERVER, as the
card of the web chat would show it, numbers its options, and keeps a PENDING
ASK in tenant Redis:

* the key holds the org (the tenant prefix), the member and the channel;
* the value holds the kind, the question id or the token of the held rule,
  and the key and the label of each option that the server built;
* it lives :data:`TTL_S` (24 hours), and one ask replaces the one before.

The member's NEXT message reaches :func:`settle` before any model run. Only
code matches it: "2", or the exact label of an option, or "YES" for a rule.
A match records the pick through the SAME function that the web pick uses
(``questions.reply_as``, or ``rules.confirm``), so an admin's pick is the org
fact and each other member's pick is a proposal. A message that matches
nothing goes to the chat as normal. Either way the ask is used up (GETDEL).

**Never from the text alone.** The message gives a number or a label. The
question id, the keys and the labels come from the server's own ask, and
``reply_as`` checks the key against the question again. An ask of another
org lives under another tenant prefix, so it cannot be read here.

⚠️ Dark: an org outside ``CRM_ANSWER_FIRST_ORGS`` offers nothing and settles
nothing, and reads no Redis key.

Fences: ``tests/unit/test_crm_text_asks.py`` and
``tests/unit/test_crm_answer_first_r8.py``.
"""

from __future__ import annotations

import json
import re
from collections.abc import Mapping, Sequence
from typing import Any

from acb_common import get_logger
from fastapi import HTTPException

_log = get_logger("crm.text_asks")

#: The tenant Redis namespace of a pending ask, and how long it lives.
NAMESPACE = "crm_text_ask"
TTL_S = 24 * 3600

#: The channels with no card. WhatsApp is the one today.
CHANNEL_WHATSAPP = "whatsapp"
CHANNELS: tuple[str, ...] = (CHANNEL_WHATSAPP,)

#: The kinds of a pending ask.
KIND_QUESTION = "question"
KIND_RULE = "rule"

#: The words of a rule's confirm.
YES_WORDS = frozenset({"yes", "y", "save"})
NO_WORDS = frozenset({"no", "n", "skip"})

#: The longest message that can be a pick.
PICK_MAX = 200

#: The replies of a settle, in plain words.
REPLY_MOVED = "That question changed, so nothing was saved. Ask me again for the latest figure."
REPLY_FAILED = "That choice did not save. You can answer it in the Metorite web app."


def _key(member: str, channel: str) -> Any:
    from acb_common.tenant_redis import key

    return key(NAMESPACE, member.strip().lower(), channel)


def _redis() -> Any:
    from acb_common.tenant_redis import get_tenant_redis

    return get_tenant_redis()


# ── Pure ────────────────────────────────────────────────────────────────────


def _lower_first(word: str) -> str:
    """*word* with its first letter in lower case: "Excludes GST" reads
    "excludes GST" inside a sentence, and GST keeps its capitals."""
    return word[:1].lower() + word[1:]


def option_text(choice: Mapping[str, Any]) -> str:
    """The label of one numbered option: the field label, and its evidence
    in plain words ("IP Basic Amount (excludes GST)")."""
    label = str(choice.get("label") or choice.get("value") or "")
    words = [_lower_first(str(w)) for w in choice.get("evidence") or []]
    return f"{label} ({', '.join(words)})" if words else label


def question_options(q: Mapping[str, Any]) -> list[tuple[str, str]]:
    """The key and the label of each option of a question, in order: each
    choice, then "None of these" where the question takes it."""
    out = [(str(c["value"]), option_text(c)) for c in q.get("choices") or []
           if isinstance(c, Mapping) and c.get("value")]
    if q.get("none"):
        out.append(("none", "None of these"))
    return out


#: The shape of a figure that a run hands over with a term question (§18.16
#: part 4): "₹79.4 lakh", "₹1.35 crore", "₹99,000" or "no value". Anything
#: else is dropped, so no text of a run reaches the member through it.
_FIGURE = re.compile(r"^(₹[0-9][0-9,]*(\.[0-9]+)?( lakh| crore)?|no value)$")


def figures_of(ask: Mapping[str, Any]) -> dict[str, str]:
    """The figures that a run handed over with its question, by option key,
    or none. Each figure must have the shape of :data:`_FIGURE`."""
    raw = ask.get("figures") if isinstance(ask, Mapping) else None
    if not isinstance(raw, Mapping):
        return {}
    return {str(k): str(v) for k, v in raw.items()
            if isinstance(k, str) and isinstance(v, str) and _FIGURE.match(v)}


def with_figures(options: Sequence[tuple[str, str]],
                 figures: Mapping[str, str]) -> list[tuple[str, str]]:
    """*options* with the total of each one that has a figure: "Fees (54%
    filled, ₹79.4 lakh)". The bare label before " (" still matches."""
    out = []
    for key, label in options:
        fig = figures.get(key)
        if fig and label.endswith(")") and " (" in label:
            label = f"{label[:-1]}, total {fig})"
        elif fig:
            label = f"{label} (total {fig})"
        out.append((key, label))
    return out


def _or_list(numbers: Sequence[str]) -> str:
    if len(numbers) < 2:
        return "".join(numbers)
    return f"{', '.join(numbers[:-1])} or {numbers[-1]}"


def question_text(q: Mapping[str, Any], options: Sequence[tuple[str, str]]) -> str:
    """The text of a question with numbered options, in plain words. It
    names no engine."""
    lines = [f"One question, so the next answer is exact: {q.get('question')}"]
    if q.get("why"):
        lines.append(str(q["why"]))
    if not q.get("can_answer"):
        lines.append("A CRM admin confirms your choice for everyone.")
    lines.extend(f"{n}. {label}" for n, (_key_, label) in enumerate(options, start=1))
    numbers = [str(n) for n in range(1, len(options) + 1)]
    lines.append(f"Reply {_or_list(numbers)}. Or keep chatting, and I keep my assumption.")
    return "\n".join(lines)


def rule_text(body: str, scope: str) -> str:
    """The text of the confirm of a held rule."""
    who = "everyone in your organization" if scope == "org" else "your own answers only"
    return (f'Save this business rule? "{body}"\n'
            f"It applies to {who}. Reply YES to save it, or NO.")


def _said(message: str) -> str:
    return " ".join(str(message or "").split()).strip().rstrip(".!").strip().casefold()


def match(pending: Mapping[str, Any], message: str) -> str | None:
    """The key of the option that *message* picks in *pending*, or None.

    A question takes the number of an option, or its exact label (with or
    without its evidence words). A rule takes YES or NO. Anything else is
    no pick: the message goes to the chat."""
    said = _said(message)
    if not said or len(said) > PICK_MAX:
        return None
    if pending.get("kind") == KIND_RULE:
        if said in YES_WORDS:
            return "save"
        return "skip" if said in NO_WORDS else None
    options = [(str(k), str(v)) for k, v in pending.get("options") or []
               if isinstance(k, str) and isinstance(v, str)]
    if said.isdigit():
        n = int(said)
        return options[n - 1][0] if 1 <= n <= len(options) else None
    for k, label in options:
        bare = label.split(" (", 1)[0]
        if said in (label.casefold(), bare.casefold()):
            return k
    return None


#: A line of a numbered option: "1. Rent", "2) EB".
_NUMBERED = re.compile(r"^[ \t]*\d{1,2}[.)][ \t]+\S", re.MULTILINE)
_REPLY_WORD = re.compile(r"\breply\b", re.IGNORECASE)


def reply_asks_a_choice(text_: str) -> bool:
    """True when the model's own reply asks the member to pick a numbered
    option: two or more numbered lines, and the word "reply" or a closing
    question. Then the CRM offers no numbered question of its own in that
    reply (CRM-Q33 fix round 1), so a "2" never answers the wrong list."""
    body = str(text_ or "")
    if len(_NUMBERED.findall(body)) < 2:
        return False
    last = next((line.strip() for line in reversed(body.splitlines()) if line.strip()), "")
    return bool(_REPLY_WORD.search(body)) or last.endswith("?")


def parse(raw: Any) -> dict[str, Any] | None:
    """A stored pending ask, or None when it does not parse."""
    if isinstance(raw, bytes):
        raw = raw.decode("utf-8", "replace")
    try:
        found = json.loads(raw) if isinstance(raw, str) else None
    except ValueError:
        return None
    if not isinstance(found, dict) or found.get("kind") not in (KIND_QUESTION, KIND_RULE):
        return None
    if not isinstance(found.get("id"), str) or not found["id"]:
        return None
    return found


# ── Offer and settle ────────────────────────────────────────────────────────


async def _question(org: str, member: str, question_id: str, user: Any) -> dict | None:
    """The question *question_id* as the card of the web chat would show it
    to *member*: the same read, the same steering, the same samples."""
    from gateway.routes.crm import questions
    from gateway.routes.crm.access import ADMIN_PERMISSION
    from gateway.routes.crm.core import _tenant_session

    async with _tenant_session() as db:
        inputs = await questions.load(db, org, full=False)
        if inputs is None:
            return None
        inputs, picked, steer = await questions.chat_card_questions(
            db, org, inputs, [question_id], True, member)
    if not picked:
        return None
    return questions._ask_model(
        picked[0], inputs, member=member, admin=user.has_permission(ADMIN_PERMISSION),
        held=False, steer=steer).model_dump()


async def offer(org: str, member: str, channel: str, ask: Mapping[str, Any]) -> str | None:
    """The text of the one follow-up of a run of a channel with no cards,
    or None. It stores the pending ask of the member. The org and the member
    come from the channel's link row, never from the message (R5, R11)."""
    from acb_auth.deps import member_context
    from acb_common.tenant_redis import organization_scope
    from gateway.routes.crm.flags import answer_first_allowed
    from gateway.routes.crm.questions import chat_admits
    from gateway.routes.crm.rules import pending_rule

    member = (member or "").strip().lower()
    if channel not in CHANNELS or not answer_first_allowed(org) or "@" not in member:
        return None
    kind, ident = str(ask.get("kind") or ""), str(ask.get("id") or "")
    try:
        user = await member_context(member)
        if str(getattr(user, "organization_id", "") or "") != org or not await chat_admits(user):
            return None
        if kind == KIND_QUESTION:
            q = await _question(org, member, ident, user)
            if q is None:
                return None
            figures = figures_of(ask)
            options = with_figures(question_options(q), figures)
            if not options:
                return None
            text_, stored = question_text(q, options), [[k, v] for k, v in options]
        elif kind == KIND_RULE:
            held = await pending_rule(org, member, ident)
            if held is None:
                return None
            text_, stored = rule_text(held["body"], held["scope"]), []
        else:
            return None
        with organization_scope(org):
            await _redis().setex(_key(member, channel), TTL_S, json.dumps(
                {"kind": kind, "id": ident, "options": stored,
                 **({"figures": figures} if kind == KIND_QUESTION and figures else {})}))
    except Exception as exc:  # a follow-up never fails the answer
        _log.warning("crm.text_asks.offer_failed", organization_id=org, kind=kind,
                     error_type=type(exc).__name__)
        return None
    _log.info("crm.text_asks.offered", organization_id=org, kind=kind)
    return text_


async def drop(org: str, member: str, channel: str) -> None:
    """Drop the pending ask of *member*, if any. A Redis fault only logs."""
    from acb_common.tenant_redis import organization_scope

    member = (member or "").strip().lower()
    if channel not in CHANNELS or "@" not in member:
        return
    try:
        with organization_scope(org):
            await _redis().delete(_key(member, channel))
    except Exception as exc:
        _log.warning("crm.text_asks.drop_failed", organization_id=org,
                     error_type=type(exc).__name__)


async def settle(org: str, member: str, channel: str, message: str) -> str | None:
    """The reply to the member's next message when it picks an option of
    the pending ask, or None: no ask, no match, or an org off the list.
    The pick goes through the same function as the web pick. The ask is
    used up either way."""
    from acb_auth.deps import member_context
    from acb_common.tenant_redis import organization_scope
    from gateway.routes.crm import questions, rules
    from gateway.routes.crm.flags import answer_first_allowed

    member = (member or "").strip().lower()
    if channel not in CHANNELS or not answer_first_allowed(org) or "@" not in member:
        return None
    try:
        with organization_scope(org):
            raw = await _redis().getdel(_key(member, channel))
    except Exception as exc:
        _log.warning("crm.text_asks.read_failed", organization_id=org,
                     error_type=type(exc).__name__)
        return None
    pending = parse(raw)
    picked = match(pending, message) if pending is not None else None
    if pending is None or picked is None:
        return None
    try:
        user = await member_context(member)
        if str(getattr(user, "organization_id", "") or "") != org or not await questions.chat_admits(user):
            return None
        if pending["kind"] == KIND_RULE:
            done = await rules.confirm(org, user, pending["id"], picked, rules.ORIGIN_WHATSAPP)
            return done.line
        out = await questions.reply_as(user, pending["id"], picked)
    except HTTPException as exc:
        code = exc.detail.get("code") if isinstance(exc.detail, dict) else None
        _log.info("crm.text_asks.refused", organization_id=org, status=exc.status_code,
                  code=code)
        return REPLY_MOVED if exc.status_code == 409 else REPLY_FAILED
    except Exception as exc:
        _log.warning("crm.text_asks.settle_failed", organization_id=org,
                     error_type=type(exc).__name__)
        return REPLY_FAILED
    _log.info("crm.text_asks.settled", organization_id=org, result=out.result)
    figure = figures_of(pending).get(picked)
    if figure:
        # §18.16 part 4: the question carried the total, so the pick answers.
        return f"Thanks. {out.line} The total is {figure}, for the period that you asked."
    return f"Thanks. {out.line} Ask me again, and I use it."


__all__ = [
    "CHANNELS",
    "CHANNEL_WHATSAPP",
    "KIND_QUESTION",
    "KIND_RULE",
    "NAMESPACE",
    "TTL_S",
    "drop",
    "match",
    "offer",
    "option_text",
    "parse",
    "question_options",
    "question_text",
    "reply_asks_a_choice",
    "rule_text",
    "settle",
]
