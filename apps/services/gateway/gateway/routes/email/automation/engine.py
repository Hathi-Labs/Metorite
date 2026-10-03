"""Automation · rule matching engine — LLM + static + learned-pattern matching
that decides which rule(s) an email triggers (read-only; no side effects)."""

from __future__ import annotations

import json
import re
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from types import MappingProxyType
from typing import Any

from fastapi import HTTPException
from gateway import decide_features
from gateway.routes.email.automation.identity import (
    own_addresses,
    resolve_org_domains,
    resolve_self_addresses,
    sender_scope,
)
from gateway.routes.email.automation.rules import _load_rules
from gateway.routes.email.core import (
    _attachment_summaries,
    _fmt_addr_list,
    _llm_json,
    _log,
    _savepoint,
)
from sqlalchemy import text


class LLMUnavailable(Exception):
    """The classifier LLM could not be reached to evaluate an email.

    Raised (not swallowed to None) so a caller that stamps the
    ``rules_processed_at`` watermark can tell "the model said no rule fits"
    apart from "the model was down". The first is a real classification and the
    message is done; the second must leave the message unstamped so the next
    cycle retries it — otherwise one bad LLM window marks a whole batch
    processed forever and the mail is never looked at again."""


def _addr_emails(field: Any) -> set[str]:
    """Lowercased email set from a JSONB ``[{name,email}]`` list (str-tolerant)."""
    try:
        items = field if isinstance(field, list) else json.loads(field or "[]")
    except Exception:  # noqa: BLE001
        return set()
    return {(it.get("email") or "").strip().lower()
            for it in (items or []) if isinstance(it, dict) and it.get("email")}


def _recipient_role(
    self_email: str, to_field: Any, cc_field: Any,
    self_addresses: frozenset[str] | set[str] = frozenset(),
) -> str:
    """Deterministic role of the mailbox owner on this email — a COMPUTED signal
    so the classifier need not parse address lists to tell a direct recipient
    from a Cc'd one. Returns 'direct' (in To), 'cc' (only in Cc), or '' (neither /
    unknown: Bcc, a mailing list, or self not resolvable).

    The owner is each address in ``self_email`` and ``self_addresses``, so a
    member who is in To under another of their mailboxes is a direct
    recipient (D-EM-27, EM-T8e-1)."""
    mine = own_addresses(self_email, self_addresses)
    if not mine:
        return ""
    if mine & _addr_emails(to_field):
        return "direct"
    if mine & _addr_emails(cc_field):
        return "cc"
    return ""


def email_dict_from_row(
    row: Any, self_email: str = "", about: str = "", self_name: str = "",
    extra_domains: frozenset[str] | set[str] = frozenset(),
    attachments: str = "",
    self_addresses: frozenset[str] | set[str] = frozenset(),
) -> dict[str, str]:
    """Build the classifier's email dict from an ``email_messages`` row.

    Mirrors the inputs inbox-zero feeds its rule AI: the recipient envelope
    (``to`` / ``cc``) + the account's own address/name (``self`` / ``self_name``)
    so it can tell direct-recipient from CC'd, the email ``date``, and the user's
    free-text ``about`` context (role / preferences) for relevance judgement.

    Also carries ``sender_scope`` (self / internal / external — see identity.py):
    the provenance signal that stops an OUTBOUND/internal email (e.g. an invoice
    your org sent a customer) being mislabelled as a RECEIVED category.

    ``self_addresses`` is the address of each mailbox of the member
    (``identity.resolve_self_addresses``). Pass it as a KEYWORD, as
    ``extra_domains``. Mail from another mailbox of the member is then
    ``self``, not external (D-EM-27). ``self`` stays the current mailbox."""
    raw_from = getattr(row, "from_address", None)
    frm = raw_from if isinstance(raw_from, dict) else json.loads(raw_from or "{}")
    received = getattr(row, "received_at", None)
    return {
        "subject": getattr(row, "subject", "") or "",
        "from": frm.get("email", ""),
        "from_name": frm.get("name", "") or "",
        "body": getattr(row, "body_text", None) or getattr(row, "snippet", None) or "",
        "to": _fmt_addr_list(getattr(row, "to_addresses", None)),
        "cc": _fmt_addr_list(getattr(row, "cc_addresses", None)),
        "self": self_email or "",
        "self_name": self_name or "",
        "about": about or "",
        "date": received.isoformat() if hasattr(received, "isoformat") else "",
        "thread_id": getattr(row, "thread_id", "") or "",
        "sender_scope": sender_scope(
            frm.get("email", ""), self_email or "", extra_domains,
            self_addresses=self_addresses),
        # Deterministic recipient role (direct/cc/'') — a computed CC-vs-To signal
        # the classifier reads instead of parsing the To/Cc lines itself.
        "recipient_role": _recipient_role(
            self_email or "", getattr(row, "to_addresses", None),
            getattr(row, "cc_addresses", None), self_addresses),
        # "Attachments: file.pdf (…)" line (or "") — see core._attachment_summaries.
        "attachments": attachments or "",
    }


def _user_info_block(email: dict[str, str]) -> str:
    """Who the AI is acting for (inbox-zero's ``getUserInfoPrompt`` parity):
    the owner's email, name and free-text "about" context."""
    parts = []
    if email.get("self"):
        parts.append(f"  email: {email['self']}")
    if email.get("self_name"):
        parts.append(f"  name: {email['self_name']}")
    if (email.get("about") or "").strip():
        parts.append(f"  about: {email['about'].strip()[:1200]}")
    if not parts:
        return ""
    body = "\n".join(parts)
    return f"USER (you are acting on behalf of this person):\n{body}\n\n"


_PROVENANCE_LINE = {
    "self": "Provenance: OUTBOUND — the mailbox owner SENT this email.\n",
    "internal": ("Provenance: INTERNAL/OUTBOUND — sent by the owner's own "
                 "organisation (same domain), not received from an outside "
                 "party.\n"),
}


def _email_block(email: dict[str, str]) -> str:
    """Render the email envelope for the classifier prompt — including To/Cc, the
    date, who "You" are (so recipient role, direct vs CC'd, is visible), and the
    sender PROVENANCE (self/internal → outbound) so receive-only categories aren't
    applied to the owner's own outbound/internal mail."""
    date_line = f"Date: {email['date']}\n" if email.get("date") else ""
    prov_line = _PROVENANCE_LINE.get(email.get("sender_scope", ""), "")
    # Render the sender's display name (inbox-zero passes it); from_name is
    # captured by email_dict_from_row but was previously unused here.
    frm = email.get("from", "")
    from_disp = f"{email['from_name']} <{frm}>" if email.get("from_name") else frm
    attach = (email.get("attachments") or "").strip()
    attach_line = f"{attach}\n" if attach else ""
    # Deterministic recipient-role line (computed, not inferred from the addresses).
    role_line = {
        "direct": "Your role: you are a DIRECT recipient (in To).\n",
        "cc": "Your role: you are only CC'd (NOT in To) — usually informational; "
              "a reply from you is usually not required.\n",
    }.get(email.get("recipient_role", ""), "")
    return (
        _user_info_block(email)
        + f"EMAIL\n{prov_line}From: {from_disp}\n"
        f"To: {email.get('to', '') or '(unknown)'}\n"
        f"Cc: {email.get('cc', '') or '(none)'}\n"
        + role_line
        + date_line
        + f"Subject: {email.get('subject', '')}\n"
        + attach_line
        + f"Body: {(email.get('body', '') or '')[:1500]}"
    )


_RECIPIENT_GUIDELINE = (
    "Honour the computed 'Your role' line: when the mailbox owner is only CC'd "
    "(not in To), the email is usually informational and does not require a reply "
    "from them — prefer an FYI/informational rule over a reply rule unless they "
    "are directly asked something."
)

# Classification guidance ported from inbox-zero's choose-rule system prompt, so
# our rule AI reasons the same way (specific over catch-all, honour excludes,
# more-specific wins, reply rules only when a response is genuinely needed).
_CLASSIFIER_GUIDELINES = (
    "Follow these guidelines: (1) Match the email to the most SPECIFIC rule that "
    "fits its content and purpose; when several could apply, prefer the more "
    "specific one. (2) If a rule says to exclude certain emails, do NOT pick it "
    "for those. (3) Prioritise reply-related rules only when the email clearly "
    "needs a response from the mailbox owner. " + _RECIPIENT_GUIDELINE + " "
    "(4) Use the USER context (their role and what they care about) to judge "
    "relevance, and only fall back to a catch-all rule when no specific rule fits. "
    "(5) DIRECTION MATTERS: if the Provenance line says OUTBOUND or "
    "INTERNAL/OUTBOUND, the owner (or their organisation) SENT this — do NOT pick "
    "a rule meant for RECEIVED mail (e.g. Receipt, Newsletter, Marketing, Cold "
    "Email). An invoice/quote/document your side sent a customer is your own "
    "outbound correspondence, not a receipt you got; treat it as FYI/informational "
    "unless a rule specifically targets your outbound mail."
)


async def _fetch_sender_history(
    db: Any, account_id: str, sender_email: str, *, limit: int = 5,
) -> list[dict[str, Any]]:
    """How mail from this sender has been classified before, as ROWS.

    An ADVISORY hint for the classifier (inbox-zero's classificationFeedback),
    NOT a hard rule. Each row is ``{"rule": <rule name>, "count": <n>}``,
    most frequent first, or ``[]`` when there is no history. The old call
    renders it as text (:func:`_hints_text`). The ``decide`` state carries
    the rows (EM-T5b-1, §10.4.8 "State shapes")."""
    sender = (sender_email or "").strip().lower()
    if not sender:
        return []
    try:
        rows = (await db.execute(text(
            """SELECT rule_name, COUNT(*) AS n
               FROM email_executed_rules
               WHERE account_id = :aid
                 AND rule_name IS NOT NULL
                 AND status NOT IN ('SKIPPED', 'REJECTED')
                 AND LOWER(COALESCE(from_address, '')) LIKE :pat
               GROUP BY rule_name ORDER BY n DESC LIMIT :lim"""
        ), {"aid": account_id, "pat": f"%{sender}%", "lim": limit})).fetchall()
        return [{"rule": r.rule_name, "count": r.n}
                for r in rows if getattr(r, "rule_name", None)]
    except Exception as exc:  # noqa: BLE001
        _log.warning("email.classification_hints_failed", error=str(exc)[:160])
        return []


def _hints_text(history: list[dict[str, Any]]) -> str:
    """The history rows as the old prompt's text: "Newsletter (x4), FYI (x1)"."""
    return ", ".join(f"{h['rule']} (x{h['count']})" for h in history or [])


async def _fetch_classification_hints(
    db: Any, account_id: str, sender_email: str, *, limit: int = 5,
) -> str:
    """The sender history as text: e.g. "Newsletter (x4), FYI (x1)", or ""."""
    return _hints_text(
        await _fetch_sender_history(db, account_id, sender_email, limit=limit))


async def _load_rule_guidance(db: Any, account_id: str) -> dict[str, list[str]]:
    """User corrections that teach the CLASSIFIER, keyed by rule id.

    The counterpart to ``_load_rule_patterns``. A pattern REPLACES the model's
    judgment for one sender; guidance CHANGES it for everyone — so a correction
    about "vendor product digests are Newsletter, not Cold Email" generalises to
    every vendor rather than exempting the one that was wrong.

    The empty-string key holds account-wide guidance (``rule_id IS NULL``) that
    belongs to no single rule.

    Best-effort: a failure here must degrade classification to "no corrections
    applied", never break it. Returning silently would hide that, so it logs.
    """
    try:
        rows = (await db.execute(text(
            """SELECT rule_id, guidance FROM email_rule_guidance
                WHERE account_id = :aid AND active
                ORDER BY created_at"""
        ), {"aid": account_id})).fetchall()
    except Exception as exc:  # table optional / never fatal
        _log.warning("email.rule_guidance_load_failed",
                     account_id=account_id, error=str(exc)[:160])
        return {}
    out: dict[str, list[str]] = {}
    for r in rows:
        key = str(r.rule_id) if r.rule_id else ""
        text_ = (r.guidance or "").strip()
        if text_:
            out.setdefault(key, []).append(text_)
    return out


def _rule_lines(rules: list[dict[str, Any]],
                guidance: dict[str, list[str]] | None = None) -> str:
    """The numbered rule list for the classifier prompt.

    A rule's own instructions come first, then the user's corrections for it.
    They are labelled as corrections rather than merged into the description
    because that is what they are — the model should weigh "the user has told me
    this specific thing before" differently from the rule's generic blurb.
    """
    g = guidance or {}
    lines = []
    for i, r in enumerate(rules):
        line = f"{i}. {r['name']}: {r.get('instructions') or '(no description)'}"
        for note in g.get(str(r.get("id")), []):
            line += f"\n   - correction from the user: {note}"
        lines.append(line)
    return "\n".join(lines)


def _global_guidance_block(guidance: dict[str, list[str]] | None) -> str:
    notes = (guidance or {}).get("", [])
    if not notes:
        return ""
    body = "\n".join(f"- {n}" for n in notes)
    return ("\n\nCORRECTIONS THE USER HAS MADE BEFORE (these override your "
            f"default reading):\n{body}")


def _hint_block(hints: str) -> str:
    """Render the advisory classification-history hint for the prompt."""
    if not hints:
        return ""
    return (
        "\n\nCLASSIFICATION HISTORY (advisory only — still judge THIS email on "
        f"its own merits): mail from this sender was previously filed under: "
        f"{hints}."
    )


# ── The rule match on `decide` (EM-T5b-1, §10.4.8) ────────────────────────────
#
# System One answers narrow questions about one state (§6A.14 "Question
# conventions"). So the rule match asks ONE BOOLEAN for each candidate rule,
# "does this rule apply?", plus two choices:
#
# - `conv` over the conversation-status rules, because Reply, Awaiting Reply,
#   FYI and Done exclude each other,
# - `best` over every candidate, which only RANKS the matched rules.
#
# The rubric that the old prompt carried in `_CLASSIFIER_GUIDELINES` is in the
# instructions and the criteria. The state holds FACTS only: no persona, no
# command and no question. Option keys are `r<i>`, the index into the
# candidate list, and no instruction names a key, because the model never
# sees one. In EM-T5b-1 this runs in shadow only, and the old LLM answer is
# the one acted on.

#: The actions that move mail out of the inbox. The ONE copy: the undo in
#: `runner.py` imports it. A rule with one of them needs a higher
#: probability to match.
_MOVE_ACTIONS = frozenset({"ARCHIVE", "MOVE_FOLDER", "TRASH", "MARK_SPAM"})

#: The probability at which a rule boolean matches (§10.4.8 "Thresholds").
#: Start values. The shadow window tunes them.
_RULE_MATCH_THRESHOLD = 0.5
#: A wrong move hides real mail from the inbox, so a moving rule needs more.
_RULE_MOVE_THRESHOLD = 0.7

#: ``sender_scope`` → the ``direction`` fact of the state.
_DIRECTION = {
    "external": "received",
    "self": "sent_by_owner",
    "internal": "sent_by_owner_organisation",
}
#: ``_recipient_role`` → the ``recipient_role`` fact of the state.
_RECIPIENT_ROLE = {"direct": "to", "cc": "cc"}

# Clips on the state fields. The Console refuses a request whose state plus
# its longest question passes its window, and a refused request is a lost
# decision, so every free-text field has a ceiling.
_BODY_CLIP = 1500
_ABOUT_CLIP = 1200
_ADDRESS_LIST_CLIP = 2000
_SUBJECT_CLIP = 500
_LINE_CLIP = 1000
_NAME_CLIP = 320

_RULE_QUESTION = "Does the rule that the criteria describe apply to the email in `email`?"
_G_OWN_CONTENT = "- Judge `email` on its own content. `sender_history` is a hint only."
_G_EXCLUDE = ("- When the rule text excludes some emails, an excluded email does "
              "not meet the rule.")
_G_DIRECTION = ("- When `direction` is not \"received\", the owner's side sent the "
                "email. A rule for received mail (receipts, newsletters, "
                "marketing, cold outreach) does not apply, unless the rule names "
                "outbound mail.")
_G_REPLY = ("- A rule about replying applies only when the email asks the owner "
            "for a response. When `mailbox_owner.recipient_role` is \"cc\", a "
            "reply is usually not needed.")
_G_ABOUT = "- Use `mailbox_owner.about` to judge what matters to the owner."
_RULE_GUIDANCE = (_G_OWN_CONTENT, _G_EXCLUDE, _G_DIRECTION, _G_REPLY, _G_ABOUT)
_RULE_FALSE = "The email does not meet the rule, or the rule excludes it."

_CONV_QUESTION = "Which conversation rule in the criteria fits the email in `email`?"
_CONV_GUIDANCE = (
    "- A conversation rule fits mail from a person that is part of an exchange.",
    "- Bulk, automated and one-way mail fits no conversation rule.",
    _G_DIRECTION,
    _G_REPLY,
)
_CONV_NONE = "No conversation rule fits. The email is bulk, automated or one-way mail."

_BEST_QUESTION = ("Which one rule in the criteria fits the email in `email` most "
                  "specifically?")
_BEST_GUIDANCE = (
    "- Prefer the most specific rule. Choose a catch-all rule only when no "
    "specific rule fits.",
    _G_EXCLUDE,
    _G_DIRECTION,
    _G_REPLY,
)
_BEST_NONE = "No rule fits this email."


def _email_facts(email: dict[str, str]) -> dict[str, Any]:
    """The ``email`` object of a ``decide`` state: facts, clipped.

    Each clip also bounds the JSON-escaped form (``decide_features.
    clip_fact``), because the Console measures the escaped state.
    """
    fact = decide_features.clip_fact
    return {
        "from": {"name": fact(email.get("from_name"), _NAME_CLIP),
                 "address": fact(email.get("from"), _NAME_CLIP)},
        "to": fact(email.get("to"), _ADDRESS_LIST_CLIP),
        "cc": fact(email.get("cc"), _ADDRESS_LIST_CLIP),
        "date": fact(email.get("date"), 64),
        "subject": fact(email.get("subject"), _SUBJECT_CLIP),
        "attachments": fact(email.get("attachments"), _LINE_CLIP),
        "body": fact(email.get("body"), _BODY_CLIP),
    }


def _email_direction(email: dict[str, str]) -> str:
    """The ``direction`` fact. An email with no scope reads as received, as
    ``sender_scope`` itself fails safe to ``external``."""
    return _DIRECTION.get(email.get("sender_scope") or "external", "received")


def _history_count(value: Any) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return 0


def _rule_state(
    email: dict[str, str], history: list[dict[str, Any]] | None,
) -> dict[str, Any]:
    """The state of the rule match: facts about the email, never a command.

    The owner's role, the direction and the sender history are facts here.
    The old prompt said them as orders ("you are acting on behalf of …").
    """
    fact = decide_features.clip_fact
    return {
        "email": _email_facts(email),
        "direction": _email_direction(email),
        "mailbox_owner": {
            "address": fact(email.get("self"), _NAME_CLIP),
            "name": fact(email.get("self_name"), _NAME_CLIP),
            "recipient_role": _RECIPIENT_ROLE.get(
                email.get("recipient_role") or "", "other"),
            "about": fact(str(email.get("about") or "").strip(), _ABOUT_CLIP),
        },
        "sender_history": [
            {"rule": fact(h.get("rule"), 200),
             "count": _history_count(h.get("count"))}
            for h in history or []
        ],
    }


def _rule_text(rule: dict[str, Any], guidance: dict[str, list[str]]) -> str:
    """A rule as one criterion: its name, its text, then the user's
    corrections for it. The same words the old prompt's rule list carries."""
    text_ = f"{rule['name']}: {rule.get('instructions') or '(no description)'}"
    for note in guidance.get(str(rule.get("id")), []):
        text_ += f"\n- correction from the user: {note}"
    return text_


def _moves_mail(rule: dict[str, Any]) -> bool:
    return any((a.get("type") or "").upper() in _MOVE_ACTIONS
               for a in rule.get("actions") or [] if isinstance(a, dict))


def _rule_threshold(rule: dict[str, Any]) -> float:
    """The probability at which this rule's boolean matches."""
    return _RULE_MOVE_THRESHOLD if _moves_mail(rule) else _RULE_MATCH_THRESHOLD


@dataclass(frozen=True)
class _RulePlan:
    """Which candidate asks which question. The request builder and the
    reader both derive it from the candidates, so they cannot disagree."""

    booleans: tuple[int, ...]
    conv: tuple[int, ...]
    best: bool


def _rule_match_plan(rules: list[dict[str, Any]]) -> _RulePlan:
    conv = tuple(i for i, r in enumerate(rules) if _is_conversation_status_rule(r))
    if len(conv) + 1 > decide_features.CHOICE_OPTION_LIMIT:
        conv = ()  # too many for one choice: each one asks its own boolean
    in_conv = set(conv)
    return _RulePlan(
        booleans=tuple(i for i in range(len(rules)) if i not in in_conv),
        conv=conv,
        # A choice takes 255 options or fewer, `none` included (item 4).
        best=2 <= len(rules) <= decide_features.CHOICE_OPTION_LIMIT - 1,
    )


def _rule_match_requests(
    email: dict[str, str], rules: list[dict[str, Any]],
    guidance: dict[str, list[str]] | None = None,
    history: list[dict[str, Any]] | None = None,
) -> list[tuple[dict[str, Any], dict[str, Any]]]:
    """The ``decide`` requests of the rule match (§10.4.8, items 1 to 7).

    One boolean ``r<i>`` for each candidate that is not a conversation rule,
    the choice ``conv`` over the conversation rules, and the choice ``best``
    over every candidate when there are 2 to 254. ``conv`` and ``best`` go in
    the first request. Each request holds 16 questions or fewer, and every
    request carries the same state.
    """
    if not rules:
        return []
    from acb_llm import BooleanQuestion, ChoiceQuestion  # `shadow` and `on` only

    g = guidance or {}
    # The account-wide corrections, newest first (`_load_rule_guidance`
    # returns them oldest first).
    corrections = list(reversed(g.get("", [])))
    plan = _rule_match_plan(rules)
    texts = [_rule_text(r, g) for r in rules]

    first: dict[str, Any] = {}
    if plan.conv:
        first["conv"] = ChoiceQuestion(
            instructions=decide_features.instructions(
                _CONV_QUESTION, _CONV_GUIDANCE, corrections),
            criteria=decide_features.clip_choice(
                {**{f"r{i}": texts[i] for i in plan.conv}, "none": _CONV_NONE}),
        )
    if plan.best:
        first["best"] = ChoiceQuestion(
            instructions=decide_features.instructions(
                _BEST_QUESTION, _BEST_GUIDANCE, corrections),
            criteria=decide_features.clip_choice(
                {**{f"r{i}": t for i, t in enumerate(texts)}, "none": _BEST_NONE}),
        )
    rule_instructions = decide_features.instructions(
        _RULE_QUESTION, _RULE_GUIDANCE, corrections)

    state = _rule_state(email, history)
    requests: list[tuple[dict[str, Any], dict[str, Any]]] = []
    current = first
    for i in plan.booleans:
        if len(current) >= decide_features.QUESTION_LIMIT:
            requests.append((state, current))
            current = {}
        current[f"r{i}"] = BooleanQuestion(
            instructions=rule_instructions,
            criteria={"true": decide_features.clip(texts[i]), "false": _RULE_FALSE},
        )
    if current:
        requests.append((state, current))
    return requests


def _match_reason(probability: float) -> str:
    """System One gives no reason text, so History shows the probability."""
    return f"Matched by AI (probability {probability:.2f})."


@dataclass(frozen=True)
class RuleMatch:
    """What ``decide`` said about the candidates (§10.4.8, items 8 to 11).

    ``matched`` holds candidate indexes in the canonical order of
    ``_load_rules``. ``main`` is the main rule, or None. ``probabilities``
    holds the probability of each candidate that ``decide`` scored.
    ``fields`` holds the log fields: keys and numbers only.
    """

    matched: tuple[int, ...]
    main: int | None
    probabilities: Mapping[int, float]
    fields: Mapping[str, Any]

    def as_pick(self) -> dict[str, Any] | None:
        """The one-rule shape: ``{"index", "reason"}``, or None."""
        if self.main is None:
            return None
        return {"index": self.main,
                "reason": _match_reason(self.probabilities.get(self.main, 0.0))}

    def as_picks(self) -> list[dict[str, Any]]:
        """The multi-rule shape: ``{"index", "reason", "primary"}`` each."""
        return [{"index": i,
                 "reason": _match_reason(self.probabilities.get(i, 0.0)),
                 "primary": i == self.main}
                for i in self.matched]


def _option_index(key: Any) -> int | None:
    """The candidate index of an exact option key ``r<i>``, else None."""
    if not isinstance(key, str) or key[:1] != "r" or not key[1:].isdigit():
        return None
    index = int(key[1:])
    return index if key == f"r{index}" else None


def _logged_key(choice: Any, valid: set[int] | range) -> str:
    """The option key to LOG for a choice answer. Never the vendor's raw
    string: one of our ``r<i>`` keys, ``none``, or ``unknown``."""
    if choice == "none":
        return "none"
    index = _option_index(choice)
    return f"r{index}" if index is not None and index in valid else "unknown"


def _read_rule_match(decision: Any, rules: list[dict[str, Any]]) -> RuleMatch:
    """Read the answers to :func:`_rule_match_requests`. Pure.

    - A boolean matches at its threshold or above: 0.7 for a rule that moves
      mail, 0.5 for every other rule.
    - ``conv`` matches its answer, unless the answer is ``none``. A
      label-only conversation rule matches by plurality. A conversation rule
      that MOVES mail (a member can add ARCHIVE to "Done") must also reach
      the 0.7 bar with the probability of its option (fix round 2).
    - At most ONE rule that moves mail matches: the one with the highest
      probability, and a tie goes to the canonical order. A second move
      would act on a provider id that the first move made stale.
    - The main rule is the ``best`` answer when that rule matched. If not, it
      is the matched rule with the highest probability, and a tie goes to the
      canonical order. ``best`` never adds a match.
    """
    plan = _rule_match_plan(rules)
    probabilities: dict[int, float] = {}
    matched: set[int] = set()
    fields: dict[str, Any] = {"rules": len(rules)}

    for i in plan.booleans:
        p = float(decision[f"r{i}"].probability)
        probabilities[i] = p
        fields[f"p_r{i}"] = round(p, 4)
        if p >= _rule_threshold(rules[i]):
            matched.add(i)

    if plan.conv:
        conv = decision["conv"]
        for i in plan.conv:
            probabilities[i] = float(conv.probabilities.get(f"r{i}", 0.0))
        fields.update(conv=_logged_key(conv.choice, set(plan.conv)),
                      conv_confidence=conv.confidence,
                      conv_margin=decide_features.top_margin(conv.probabilities))
        picked = _option_index(conv.choice)
        if picked in plan.conv:
            if conv.choice not in conv.probabilities:
                probabilities[picked] = float(conv.confidence or 0.0)
            # A moving conversation rule needs the same bar as a moving
            # boolean. A label-only one keeps the plurality of the choice.
            if (not _moves_mail(rules[picked])
                    or probabilities[picked] >= _rule_threshold(rules[picked])):
                matched.add(picked)

    moving = [i for i in matched if _moves_mail(rules[i])]
    if len(moving) > 1:
        keep = min(moving, key=lambda i: (-probabilities.get(i, 0.0), i))
        dropped = sorted(i for i in moving if i != keep)
        matched.difference_update(dropped)
        fields["dropped_moves"] = [f"r{i}" for i in dropped]

    main: int | None = None
    if plan.best:
        best = decision["best"]
        fields.update(best=_logged_key(best.choice, range(len(rules))),
                      best_confidence=best.confidence,
                      best_margin=decide_features.top_margin(best.probabilities))
        picked = _option_index(best.choice)
        if picked in matched:
            main = picked
    if main is None and matched:
        main = min(matched, key=lambda i: (-probabilities.get(i, 0.0), i))

    ordered = tuple(sorted(matched))
    fields.update(matched=[f"r{i}" for i in ordered],
                  main=f"r{main}" if main is not None else "none")
    return RuleMatch(matched=ordered, main=main,
                     probabilities=MappingProxyType(probabilities),
                     fields=MappingProxyType(fields))


def _rule_key(index: int | None) -> str:
    return f"r{index}" if index is not None else "none"


def _rule_match_compare(
    rules: list[dict[str, Any]], *, multi: bool,
) -> Callable[[Any, Any], decide_features.Comparison]:
    """The shadow comparison of the rule match, in keys and numbers only.

    ``old`` and ``new`` are the main keys. The fields add the old key set,
    ``agree_set``, ``agree_main`` and ``p_old``, the probability that
    ``decide`` gives to the old main rule. In one-rule mode ``agree`` is
    ``agree_main``. In multi-rule mode it needs both.

    In multi-rule mode the old main rule is the one that the live path puts
    first. `_match_email_to_rules_multi` sorts primary first, then by the
    canonical order, which is the candidate index. So the old main rule is the
    lowest index among the picks marked primary, else the lowest index of all.
    """

    def _compare(old_result: Any, decision: Any) -> decide_features.Comparison:
        reading = _read_rule_match(decision, rules)
        if multi:
            picks = list(old_result or [])
            old_set = {int(p["index"]) for p in picks}
            primaries = {int(p["index"]) for p in picks if p.get("primary")}
            old_main = min(primaries or old_set) if old_set else None
        else:
            old_set = {int(old_result["index"])} if old_result else set()
            old_main = int(old_result["index"]) if old_result else None
        agree_set = old_set == set(reading.matched)
        agree_main = old_main == reading.main
        p_old = reading.probabilities.get(old_main) if old_main is not None else None
        fields = dict(reading.fields)
        fields.update(
            old_keys=[f"r{i}" for i in sorted(old_set)],
            agree_set=agree_set,
            agree_main=agree_main,
            p_old=round(p_old, 4) if p_old is not None else None,
        )
        return decide_features.Comparison(
            old=_rule_key(old_main),
            new=_rule_key(reading.main),
            agree=(agree_set and agree_main) if multi else agree_main,
            options=len(rules),
            probability=(reading.probabilities.get(reading.main)
                         if reading.main is not None else None),
            fields=fields,
        )

    return _compare


class DecisionUnavailable(LLMUnavailable):
    """``decide`` gave no decision for an email, in ``on`` (EM-T5b-2, D-EM-8).

    A subclass of :class:`LLMUnavailable`, so every caller that already skips
    the ``rules_processed_at`` stamp on a classifier outage does the same
    here, and the next cycle asks again. No LLM call replaces the answer.
    """


async def _decide_rule_match(
    email: dict[str, str], rules: list[dict[str, Any]],
    guidance: dict[str, list[str]] | None,
    history: list[dict[str, Any]] | None,
    *, account_id: str | None, message_id: str | None, member: str | None,
) -> RuleMatch:
    """The rule match in ``on``: the ``decide`` answer decides.

    Raises :class:`DecisionUnavailable` when there is no decision, for any
    reason (`decide_features.ask` logs which one).
    """
    match = await decide_features.ask(
        "email.rule_match", account_id=account_id, message_id=message_id,
        build=lambda: _rule_match_requests(email, rules, guidance, history),
        read=lambda decision: _read_with_fields(decision, rules),
        member=member,
    )
    if match is None:
        raise DecisionUnavailable("decide gave no decision")
    return match


def _read_with_fields(
    decision: Any, rules: list[dict[str, Any]],
) -> tuple[RuleMatch, Mapping[str, Any]]:
    match = _read_rule_match(decision, rules)
    return match, match.fields


async def _decide_member(
    db: Any, account_id: str, feature: str = "email.rule_match",
) -> str | None:
    """The mailbox owner for a ``decide`` call of ``feature`` in ``on``, else None.

    A deployment Router key refuses a ``decide`` call that names no member,
    and a request job runs as its request member, not as the owner. So in
    ``on`` each site names the owner, ``email_accounts.user_id``, read by
    the account id the server holds, in the caller's own session (EM-T5b-2
    item 5). The rule match, the thread status, the cold check and the
    sender pin all read it here. Outside ``on`` this reads nothing. A failed
    read gives None, and the call keeps the member of the run context.
    """
    if decide_features.mode_for(feature) != "on":
        return None
    try:
        async with _savepoint(db):
            row = (await db.execute(text(
                "SELECT user_id FROM email_accounts WHERE id = :aid"
            ), {"aid": account_id})).fetchone()
    except Exception as exc:  # the run context member stays
        _log.warning("email.decide_member_unresolved", account_id=account_id,
                     error_type=type(exc).__name__)
        return None
    owner = str(getattr(row, "user_id", "") or "") if row is not None else ""
    return owner if "@" in owner else None


async def _llm_pick_rule(
    email: dict[str, str], rules: list[dict[str, Any]], hints: str = "",
    *, model: str = "tier-fast",
    guidance: dict[str, list[str]] | None = None,
    account_id: str | None = None,
    history: list[dict[str, Any]] | None = None,
    message_id: str | None = None,
    member: str | None = None,
) -> dict[str, Any] | None:
    """Ask the LLM which instruction-based rule matches the email.

    Runs on the account's rule-evaluation ``model`` with the prompt fitted to its
    context window (acompletion_with_fallback handles keys + fitting) and forces
    JSON output so the reply is always parseable.

    Returns {"index": int, "reason": str} (index into `rules`) or None for a
    genuine "no rule fits". Raises LLMUnavailable when the model call itself
    fails, so the caller does NOT mistake an outage for a no-match.

    EM-T5b-1: in ``shadow`` mode of ``email.rule_match``, the ``decide``
    requests of :func:`_rule_match_requests` run beside it. ``history`` is
    the sender history as rows, for the state. ``message_id`` goes into each
    log line.

    EM-T5b-2: in ``on``, the ``decide`` answer decides (``RuleMatch.as_pick``)
    and no LLM call is made. No answer raises :class:`DecisionUnavailable`.
    ``member`` is the mailbox owner, for a deployment Router key.
    """
    if not rules:
        return None
    if decide_features.mode_for("email.rule_match") == "on":
        match = await _decide_rule_match(
            email, rules, guidance, history,
            account_id=account_id, message_id=message_id, member=member)
        return match.as_pick()

    async def _old() -> dict[str, Any] | None:
        try:
            rule_lines = _rule_lines(rules, guidance)
            sys_prompt = (
                "You are an email classifier helping the user manage their inbox. "
                "Given an email and a numbered list of rules, choose the single "
                "best-matching rule. " + _CLASSIFIER_GUIDELINES
                + ' Respond with ONLY a JSON object: {"index": <number or -1 if none '
                'match>, "reason": "<short why>"}.'
            )
            user_prompt = (
                f"{_email_block(email)}\n\nRULES\n{rule_lines}"
                f"{_global_guidance_block(guidance)}{_hint_block(hints)}"
            )
            # Force structured output so the reply is parseable JSON, not prose we
            # have to scrape (the #1 cause of silent "no match"); _llm_json drops
            # json_object automatically for models that don't support it.
            data, content, _used = await _llm_json(
                model,
                [{"role": "system", "content": sys_prompt},
                 {"role": "user", "content": user_prompt}],
                max_tokens=800,
            )
            if isinstance(data, dict) and isinstance(data.get("index"), int):
                idx = data["index"]
                if 0 <= idx < len(rules):
                    return {"index": idx, "reason": str(data.get("reason", ""))[:300]}
            # Distinguish an unparseable/empty reply (a real failure) from a genuine
            # "no rule fits" (-1) — otherwise a high parse-failure rate looks
            # identical to "nothing matched" and stays invisible.
            if data is None and content.strip():
                _log.warning("email.llm_pick_rule_unparseable",
                             model=_used, sample=content[:200])
            return None
        except Exception as exc:  # noqa: BLE001
            # The call failed (gateway/network/timeout) — NOT a no-match. Signal it
            # so the watermark isn't burned on mail the classifier never saw.
            _log.warning("email.llm_pick_rule_failed", error=str(exc)[:200])
            raise LLMUnavailable(str(exc)[:200]) from exc

    # EM-T5b-1: in `shadow` mode `decide` runs beside the old call. The result
    # is ALWAYS the old call's, and an LLMUnavailable from it still propagates.
    return await decide_features.shadow(
        "email.rule_match", _old, account_id=account_id, message_id=message_id,
        build=lambda: _rule_match_requests(email, rules, guidance, history),
        compare=_rule_match_compare(rules, multi=False),
    )


async def _llm_pick_rules(
    email: dict[str, str], rules: list[dict[str, Any]], hints: str = "",
    *, model: str = "tier-fast",
    guidance: dict[str, list[str]] | None = None,
    account_id: str | None = None,
    history: list[dict[str, Any]] | None = None,
    message_id: str | None = None,
    member: str | None = None,
) -> list[dict[str, Any]]:
    """Multi-rule selection (inbox-zero parity): ask the LLM for ALL instruction
    rules that apply to the email, not just the single best.

    Like :func:`_llm_pick_rule`, runs on the account's rule-evaluation ``model``
    with the prompt fitted to its context window and JSON output forced.

    Returns a list of {"index": int, "reason": str} (indexes into `rules`) — an
    empty list for a genuine "none apply". Raises LLMUnavailable when the model
    call itself fails, so the caller doesn't mistake an outage for "no matches".

    EM-T5b-1: multi-rule had no shadow before. It now asks the same
    ``decide`` requests as :func:`_llm_pick_rule`, under the same feature,
    ``email.rule_match``, and still returns the LLM answer.

    EM-T5b-2: in ``on``, the ``decide`` answer decides
    (``RuleMatch.as_picks``) and no LLM call is made.
    """
    if not rules:
        return []
    if decide_features.mode_for("email.rule_match") == "on":
        match = await _decide_rule_match(
            email, rules, guidance, history,
            account_id=account_id, message_id=message_id, member=member)
        return match.as_picks()

    async def _old() -> list[dict[str, Any]]:
        try:
            rule_lines = _rule_lines(rules, guidance)
            sys_prompt = (
                "You are an email classifier helping the user manage their inbox. "
                "Given an email and a numbered list of rules, choose EVERY rule that "
                "genuinely applies to the email (there may be more than one, or none). "
                "Do not force a match. Mark exactly ONE match as the primary (the "
                "single most specific rule that best fits the email) with "
                '"primary": true. ' + _CLASSIFIER_GUIDELINES
                + ' Respond with ONLY a JSON object: {"matches": [{"index": <number>, '
                '"reason": "<short why>", "primary": <true|false>}]} — an empty list '
                "if none apply."
            )
            user_prompt = (
                f"{_email_block(email)}\n\nRULES\n{rule_lines}"
                f"{_global_guidance_block(guidance)}{_hint_block(hints)}"
            )
            # Force structured output (see _llm_pick_rule); a generous budget so a
            # multi-rule object with several reasons isn't truncated mid-JSON.
            data, content, _used = await _llm_json(
                model,
                [{"role": "system", "content": sys_prompt},
                 {"role": "user", "content": user_prompt}],
                max_tokens=1500,
            )
            out: list[dict[str, Any]] = []
            seen: set[int] = set()
            if isinstance(data, dict) and isinstance(data.get("matches"), list):
                for m in data["matches"]:
                    if not isinstance(m, dict):
                        continue
                    idx = m.get("index")
                    if isinstance(idx, int) and 0 <= idx < len(rules) and idx not in seen:
                        seen.add(idx)
                        out.append({"index": idx,
                                    "reason": str(m.get("reason", ""))[:300],
                                    "primary": bool(m.get("primary"))})
            elif data is None and content.strip():
                # Unparseable reply (truncation/prose) — log so it's not silently
                # read as "no rules apply" (see _llm_pick_rule).
                _log.warning("email.llm_pick_rules_unparseable",
                             model=_used, sample=content[:200])
            return out
        except Exception as exc:  # noqa: BLE001
            # The call failed — NOT "no rules apply". Signal it (see _llm_pick_rule).
            _log.warning("email.llm_pick_rules_failed", error=str(exc)[:200])
            raise LLMUnavailable(str(exc)[:200]) from exc

    return await decide_features.shadow(
        "email.rule_match", _old, account_id=account_id, message_id=message_id,
        build=lambda: _rule_match_requests(email, rules, guidance, history),
        compare=_rule_match_compare(rules, multi=True),
    )


# ── Conversation-status (Reply Zero) pre-filter ───────────────────────────────
# A faithful port of inbox-zero's filterConversationStatusRulesWithMetadata
# (utils/reply-tracker/match-rules.ts). Before the AI is even asked, we decide
# whether the conversation-status rules (Reply / Awaiting Reply / FYI /
# Done) are eligible at all. Without this gate every newsletter, notification
# and one-way broadcast that the classifier mis-reads becomes "Reply" — the
# root cause of "everything shows up in the Reply tab".

# System rules that track a thread's reply status (Reply Zero). Identified by
# system_type, falling back to the name (seeded presets store system_type NULL).
# Current keys plus the pre-rename legacy tokens (TO_REPLY / ACTIONED), so a
# straggler rule that predates the "To Reply"→"Reply" / "Actioned"→"Done" rename
# is still recognised as a conversation rule (and thus still gated / never pinned).
_CONVERSATION_SYSTEM_KEYS = {"REPLY", "AWAITING_REPLY", "FYI", "DONE",
                             "TO_REPLY", "ACTIONED", "NEEDS_REPLY"}

# Senders that never expect a reply (inbox-zero's NO_REPLY_PREFIXES + a few
# obvious extras). Matched as a case-insensitive prefix of the full address.
_NO_REPLY_PREFIXES = (
    "noreply@", "no-reply@", "no_reply@", "donotreply@", "do-not-reply@",
    "notifications@", "notification@", "notify@", "notif@", "info@",
    "newsletter@", "news@", "updates@", "update@", "account@", "accounts@",
    "mailer@", "mailer-daemon@", "bounce@", "bounces@",
)

# Never replied to a sender but received at least this many from them → it's a
# one-way broadcast, not a conversation (inbox-zero REPLY_RECEIVED_THRESHOLD).
_REPLY_RECEIVED_THRESHOLD = 10


def _conversation_rule_key(rule: dict[str, Any]) -> str:
    """The canonical conversation-status key for a rule (system_type, falling
    back to its UPPER_SNAKE name), or "" when it isn't a conversation rule."""
    key = (rule.get("system_type") or "").upper().strip()
    if not key:
        key = (rule.get("name") or "").upper().strip().replace(" ", "_")
    return key if key in _CONVERSATION_SYSTEM_KEYS else ""


def _is_conversation_status_rule(rule: dict[str, Any]) -> bool:
    return bool(_conversation_rule_key(rule))


async def _is_reply_candidate(
    db: Any, account_id: str, email: dict[str, str],
) -> tuple[bool, str]:
    """Whether an email may match the conversation-status rules at all.

    Returns ``(allowed, reason_when_blocked)``. Deterministic — no LLM. Blocks
    no-reply senders, mass mail carrying a List-Unsubscribe link, and one-way
    broadcast senders the user has never replied to (inbox-zero parity). Fails
    OPEN (allowed) on any error so a transient DB issue can't hide real mail."""
    sender = (email.get("from") or "").strip().lower()
    if not sender or "@" not in sender:
        return True, ""
    if any(sender.startswith(p) for p in _NO_REPLY_PREFIXES):
        return False, "no_reply_sender"
    try:
        # Mass/automated mail: a List-Unsubscribe link was parsed for this sender.
        unsub = (await db.execute(text(
            "SELECT 1 FROM email_messages WHERE account_id = :aid "
            "AND LOWER(from_address->>'email') = :s "
            "AND unsubscribe_link IS NOT NULL LIMIT 1"
        ), {"aid": account_id, "s": sender})).fetchone()
        if unsub:
            return False, "list_unsubscribe"
        # Reply-history threshold: never replied + many received → broadcast.
        recv = (await db.execute(text(
            "SELECT COUNT(*) AS c FROM (SELECT 1 FROM email_messages "
            "WHERE account_id = :aid AND LOWER(from_address->>'email') = :s "
            "AND LOWER(COALESCE(folder, '')) = 'inbox' LIMIT :thr) t"
        ), {"aid": account_id, "s": sender,
            "thr": _REPLY_RECEIVED_THRESHOLD})).fetchone()
        if recv and int(recv.c) >= _REPLY_RECEIVED_THRESHOLD:
            replied = (await db.execute(text(
                "SELECT 1 FROM email_messages WHERE account_id = :aid "
                "AND LOWER(COALESCE(folder, '')) = 'sent' "
                "AND CAST(to_addresses AS TEXT) ILIKE :pat LIMIT 1"
            ), {"aid": account_id, "pat": f"%{sender}%"})).fetchone()
            if not replied:
                return False, "reply_history_threshold"
    except Exception as exc:  # noqa: BLE001 — never hide mail on a gate error
        _log.warning("email.reply_candidate_gate_failed", error=str(exc)[:160])
        return True, ""
    return True, ""


def _gate_conversation_rules(
    rules: list[dict[str, Any]], allowed: bool,
) -> list[dict[str, Any]]:
    """Drop the conversation-status rules from the candidate set when the email
    isn't a reply candidate, so it falls through to Newsletter/Marketing/etc."""
    if allowed:
        return rules
    return [r for r in rules if not _is_conversation_status_rule(r)]


def _static_match(rule: dict[str, Any], email: dict[str, str]) -> bool | None:
    """Evaluate a rule's static patterns. Returns None if the rule has none."""
    checks: list[bool] = []
    field_map = [
        ("from_pattern", email.get("from", "")),
        ("to_pattern", email.get("to", "")),
        ("subject_pattern", email.get("subject", "")),
        ("body_pattern", email.get("body", "")),
    ]
    for key, value in field_map:
        pat = rule.get(key)
        if pat:
            checks.append(pat.lower() in (value or "").lower())
    if not checks:
        return None
    return all(checks) if rule.get("conditional_operator", "AND") == "AND" else any(checks)


async def _load_rule_patterns(
    db: Any, account_id: str, *, approved_includes_only: bool = False,
) -> dict[str, dict[str, list[tuple[str, str]]]]:
    """Learned classification patterns per rule (inbox-zero parity).

    Returns ``{rule_id: {"include": [(type, value), …], "exclude": […]}}``.
    Rejected patterns are never returned to anyone — the user has said they are
    wrong.

    ``approved_includes_only`` drops INCLUDE patterns awaiting review, and is set
    by the Email Cleaner. The asymmetry is deliberate, in two directions:

    * Includes vs excludes. An include pattern ASSERTS a category; an exclude
      only ever PREVENTS one. There is nothing to approve about "this sender is
      not Marketing" — refusing to honour it until reviewed would make the
      cleaner label mail the user had explicitly told it not to.

    * Cleaner vs classifier. The classifier keeps using unreviewed patterns: its
      alternative is an LLM call, so a pattern there saves money and any mistake
      is one message the user can Fix. The cleaner's alternative is to leave mail
      uncategorized, and it projects one pattern across every matching message in
      the mailbox with destructive actions offered on top. The blast radius is
      not comparable, so the bar isn't either.

    Best-effort: returns {} if the table doesn't exist yet (pre-migration)."""
    sql = ("SELECT rule_id, pattern_type, value, exclude "
           "FROM email_rule_patterns "
           "WHERE account_id = :aid AND rejected_at IS NULL")
    if approved_includes_only:
        sql += " AND (exclude OR approved_at IS NOT NULL)"
    try:
        rows = (await db.execute(text(sql), {"aid": account_id})).fetchall()
    except Exception as e:  # noqa: BLE001
        # Silently returning {} here disables EVERY learned pattern at once, so
        # a missing migration reads as "the user never taught us anything".
        _log.warning("email.rule_patterns_load_failed",
                     account_id=account_id, error=str(e)[:160])
        return {}
    out: dict[str, dict[str, list[tuple[str, str]]]] = {}
    for r in rows:
        d = out.setdefault(str(r.rule_id), {"include": [], "exclude": []})
        d["exclude" if r.exclude else "include"].append((r.pattern_type, r.value))
    return out


def _generalize_subject(s: str) -> str:
    """Strip parenthesised content, numbers and IDs (inbox-zero's
    generalizeSubject) so a learned subject pattern matches across varying
    invoice / order / ticket numbers."""
    s = re.sub(r"\([^)]*\)", "", s or "")
    s = re.sub(r"(?:#\d+|\b\d+\b)", "", s)
    return re.sub(r"\s+", " ", s).strip()


# A generalised SUBJECT pattern has had its numbers and parenthesised parts
# stripped, so it can collapse to something far broader than what the user
# taught. "Re: 12345" becomes "re:", which is a substring of every reply in the
# mailbox — and a pattern hit short-circuits the whole classifier, so one such
# pattern silently mislabels everything. Require the residue to still carry real
# signal before trusting it. The raw (ungeneralised) value is unaffected: if the
# user's literal text appears in the subject, that is an exact match either way.
_MIN_GENERALIZED_SUBJECT_CHARS = 6
_MIN_GENERALIZED_SUBJECT_WORDS = 2
# Reply/forward prefixes carry no topical signal — a generalised pattern made
# only of these is exactly the runaway case above.
_SUBJECT_NOISE_WORDS = {"re", "re:", "fw", "fw:", "fwd", "fwd:", "aw", "sv",
                        "the", "a", "an", "your", "you", "and", "for", "to"}


def _generalized_subject_is_specific(gv: str) -> bool:
    """True if a generalised SUBJECT pattern is still discriminating enough.

    Guards the runaway case: strip the numbers out of "Order #1042" and you get
    "order", out of "Re: 12345" and you get "re:". Matching on those turns one
    Fix click into a mailbox-wide mislabel.
    """
    words = [w for w in gv.split() if w not in _SUBJECT_NOISE_WORDS]
    if not words:
        return False
    residue = " ".join(words)
    return (len(residue) >= _MIN_GENERALIZED_SUBJECT_CHARS
            or len(words) >= _MIN_GENERALIZED_SUBJECT_WORDS)


def _pattern_hit(pat: tuple[str, str], email: dict[str, str]) -> bool:
    """True if a learned pattern (type, value) matches the email — inbox-zero
    parity: FROM is a *bidirectional* case-insensitive substring; SUBJECT matches
    on raw substring OR with numbers/IDs generalised away."""
    ptype, value = pat
    v = (value or "").strip().lower()
    if not v:
        return False
    if ptype == "SUBJECT":
        subj = (email.get("subject", "") or "").lower()
        if v in subj:
            return True
        gv = _generalize_subject(v)
        if not _generalized_subject_is_specific(gv):
            return False
        return gv in _generalize_subject(subj)
    frm = (email.get("from", "") or "").lower()  # FROM (bidirectional)
    if not frm:
        return False
    # The full sender contained in the pattern value (e.g. value "Jo <jo@x.com>",
    # sender "jo@x.com") is specific — but only when the sender appears as a
    # whole address, not as a SUFFIX of a longer one. Without that check,
    # "reply@github.com" matches a pattern learned for "noreply@github.com" (and
    # "no-reply@stripe.com" ⊃ "reply@stripe.com"), short-circuiting the
    # classifier onto a rule the user never taught for that sender.
    if frm in v and _whole_address_in(frm, v):
        return True
    # The other direction (value is a substring of the sender) must NOT let a
    # short/generic value match every sender: require an address- or domain-shaped
    # token (contains '@' or '.', length >= 4). Learned FROM values are real
    # sender addresses/domains, so this only rejects over-broad fragments.
    if len(v) >= 4 and ("@" in v or "." in v):
        return v in frm and (v.startswith("@") or _whole_address_in(v, frm)
                             or _domain_suffix_of(v, frm))
    return False


def _whole_address_in(needle: str, haystack: str) -> bool:
    """``needle`` occurs in ``haystack`` on an address boundary, not mid-token.

    "reply@github.com" is *inside* "noreply@github.com" but is a different
    address; requiring the preceding character to be a non-address character
    (whitespace, '<', ',', ':') rejects that while still allowing the real case
    of a display-name-wrapped address, "Jo <jo@x.com>".
    """
    i = haystack.find(needle)
    while i != -1:
        before = haystack[i - 1] if i > 0 else " "
        if not (before.isalnum() or before in "._%+-"):
            return True
        i = haystack.find(needle, i + 1)
    return False


def _domain_suffix_of(value: str, frm: str) -> bool:
    """``value`` is a bare domain and ``frm``'s address sits on that domain.

    Keeps the intended "learn the whole domain" behaviour ("github.com" matching
    "noreply@github.com") that the address-boundary check would otherwise reject.
    """
    if "@" in value:
        return False
    domain = frm.rsplit("@", 1)[-1].strip(" >")
    return domain == value or domain.endswith("." + value)


def _patterns_excluded_rules(
    patterns: dict[str, dict[str, list[tuple[str, str]]]], email: dict[str, str],
) -> set[str]:
    """Rule ids whose learned EXCLUDE pattern matches this email — skip them."""
    return {
        rid for rid, p in patterns.items()
        if any(_pattern_hit(pt, email) for pt in p["exclude"])
    }


def _patterns_included_rule(
    rule: dict[str, Any],
    patterns: dict[str, dict[str, list[tuple[str, str]]]],
    email: dict[str, str],
) -> bool:
    """True if a learned INCLUDE pattern for this rule matches the email."""
    p = patterns.get(str(rule.get("id")))
    return bool(p and any(_pattern_hit(pt, email) for pt in p["include"]))


async def _match_email_to_rule(
    db: Any, account_id: str, email: dict[str, str],
    *, message_id: str | None = None,
) -> dict[str, Any] | None:
    """Return the first matching rule + reason, or None.

    Evaluation order per rule: static patterns (local) first, then NL
    instructions (one batched LLM call). Static-first keeps it cheap &
    deterministic. ``message_id`` reaches the ``decide`` shadow log only.
    """
    rules = [r for r in await _load_rules(db, account_id) if r["enabled"]]
    if not rules:
        return None

    # Reply Zero gate (inbox-zero parity): drop the conversation-status rules
    # (Reply / Awaiting / FYI / Done) for no-reply, mass and broadcast
    # mail so they can never match "Reply".
    allowed, _why = await _is_reply_candidate(db, account_id, email)
    rules = _gate_conversation_rules(rules, allowed)
    if not rules:
        return None

    # Learned patterns (inbox-zero parity): an EXCLUDE pattern skips a rule
    # entirely; an INCLUDE pattern short-circuits to an immediate match (no LLM).
    patterns = await _load_rule_patterns(db, account_id)
    excluded = _patterns_excluded_rules(patterns, email)
    for rule in rules:
        if str(rule.get("id")) in excluded:
            continue
        if _patterns_included_rule(rule, patterns, email):
            return {"rule": rule, "reason": "Matched a learned pattern.",
                    "source": "pattern"}
    # Corrections that teach the model rather than bypass it. Loaded AFTER the
    # pattern short-circuit on purpose: a pinned sender never reaches the LLM,
    # so building its prompt context would be wasted work.
    guidance = await _load_rule_guidance(db, account_id)

    instruction_rules: list[dict[str, Any]] = []
    for rule in rules:
        if str(rule.get("id")) in excluded:
            continue
        sm = _static_match(rule, email)
        has_instr = bool((rule.get("instructions") or "").strip())
        if has_instr:
            # Static (if any) must not contradict; let the LLM decide.
            if sm is not False:
                instruction_rules.append(rule)
            continue
        if sm is True:
            return {"rule": rule, "reason": "Matched static conditions.",
                    "source": "static"}
        # No instructions and static didn't match → this rule doesn't apply.

    if instruction_rules:
        history = await _fetch_sender_history(db, account_id, email.get("from", ""))
        # D-EM-7: no member chooses the rules model. The old call runs on its
        # fixed tier (`tier-fast`), and `on` asks Jev on `tier-decide`.
        pick = await _llm_pick_rule(
            email, instruction_rules, hints=_hints_text(history),
            guidance=guidance, account_id=account_id,
            history=history, message_id=message_id,
            member=await _decide_member(db, account_id))
        if pick:
            return {"rule": instruction_rules[pick["index"]],
                    "reason": pick["reason"] or "Matched by AI.", "source": "ai"}
    return None


async def _match_email_to_rules_multi(
    db: Any, account_id: str, email: dict[str, str],
    *, message_id: str | None = None,
) -> list[dict[str, Any]]:
    """Multi-rule selection (inbox-zero parity): return ALL matching rules, not
    just the best one. Each item is {"rule": ..., "reason": ...}.

    Static matches are collected locally; the LLM is asked once for EVERY
    instruction rule that applies (via _llm_pick_rules). De-duped by id and
    returned in rule sort order. ``message_id`` reaches the ``decide`` shadow
    log only.
    """
    rules = [r for r in await _load_rules(db, account_id) if r["enabled"]]
    if not rules:
        return []

    # Reply Zero gate (inbox-zero parity) — see _match_email_to_rule.
    allowed, _why = await _is_reply_candidate(db, account_id, email)
    rules = _gate_conversation_rules(rules, allowed)
    if not rules:
        return []

    patterns = await _load_rule_patterns(db, account_id)
    excluded = _patterns_excluded_rules(patterns, email)
    guidance = await _load_rule_guidance(db, account_id)

    matches: list[dict[str, Any]] = []
    seen: set[str] = set()

    def _add(rule: dict[str, Any], reason: str, source: str,
             is_primary: bool = False) -> None:
        rid = str(rule.get("id"))
        if rid in seen:
            return
        seen.add(rid)
        matches.append({"rule": rule, "reason": reason, "source": source,
                        "is_primary": is_primary})

    # Learned INCLUDE patterns match immediately (and skip the LLM for that rule).
    for rule in rules:
        if str(rule.get("id")) in excluded:
            continue
        if _patterns_included_rule(rule, patterns, email):
            _add(rule, "Matched a learned pattern.", "pattern")

    instruction_rules: list[dict[str, Any]] = []
    for rule in rules:
        if str(rule.get("id")) in excluded or str(rule.get("id")) in seen:
            continue
        sm = _static_match(rule, email)
        has_instr = bool((rule.get("instructions") or "").strip())
        if has_instr:
            if sm is not False:
                instruction_rules.append(rule)
            continue
        if sm is True:
            _add(rule, "Matched static conditions.", "static")

    if instruction_rules:
        history = await _fetch_sender_history(db, account_id, email.get("from", ""))
        # D-EM-7: no member chooses the rules model (see _match_email_to_rule).
        for pick in await _llm_pick_rules(
            email, instruction_rules, hints=_hints_text(history),
            guidance=guidance, account_id=account_id,
            history=history, message_id=message_id,
            member=await _decide_member(db, account_id),
        ):
            _add(instruction_rules[pick["index"]],
                 pick["reason"] or "Matched by AI.", "ai",
                 is_primary=bool(pick.get("primary")))

    # The LLM-chosen primary (most specific) leads; the rest follow in canonical
    # system order (rules arrive from _load_rules sorted that way — inbox-zero
    # parity, not a user priority).
    order = {str(r.get("id")): i for i, r in enumerate(rules)}
    matches.sort(key=lambda m: (0 if m.get("is_primary") else 1,
                                order.get(str(m["rule"].get("id")), 1_000)))
    return matches


async def classify_matches(
    db: Any, account_id: str, message_row: Any, email: dict[str, str],
    *, multi_rule: bool = False, resolve: bool = True, provider: Any = None,
) -> list[dict[str, Any]]:
    """THE match → conversation-resolve step, in one place.

    Every path that classifies inbound mail — the live runner, "Process past
    emails", the single-message re-run, the Reply Zero backfill — must obey the
    same #110 invariant: a conversation has ONE classification, re-evaluated on
    each new message. That invariant was enforced at only some call sites, which
    is how run-message/process-past splintered conversations. It lives HERE now:

      1. match the email to rules (``multi_rule`` → every match, else the best),
      2. unless ``resolve`` is off, re-evaluate the whole CONVERSATION so a
         thread keeps its single status (the resolver is itself cost-aware — it
         spends no model call on bulk mail, and degrades to the per-message pick
         on any failure).

    ``resolve=False`` is the dry-run/preview policy: match only, touch nothing,
    spend no thread-status model call. Raises ``LLMUnavailable`` when the
    classifier model itself is down (a genuine no-match still returns ``[]``), so
    the caller can skip its ``rules_processed_at`` watermark and retry next cycle
    instead of burning the message unseen.

    EM-T5b-2 fix round 3: in ``on`` of ``email.thread_status``, the status
    is asked BEFORE the match when it is sure to be asked
    (``replyzero.status_before_match``). A missing status then raises
    ``DecisionUnavailable`` before the match is paid. Outside ``on`` the
    step reads nothing, and the order is the one above.
    """
    # The message id for the `decide` shadow log (EM-T5b-1). It comes from the
    # row, so no caller of this function changes.
    row_id = getattr(message_row, "id", None)
    message_id = str(row_id) if row_id is not None else None
    # Lazy import: replyzero sits ABOVE the engine (it imports match helpers from
    # here), so importing it at module scope would cycle. The resolver is the
    # thread-status authority; it owns the #110 conversation logic.
    from gateway.routes.email.automation.replyzero import (  # noqa: PLC0415
        resolve_conversation_status_matches,
        status_before_match,
    )
    first = (await status_before_match(db, account_id, message_row)
             if resolve else None)
    if multi_rule:
        matches = await _match_email_to_rules_multi(
            db, account_id, email, message_id=message_id)
    else:
        m = await _match_email_to_rule(db, account_id, email, message_id=message_id)
        matches = [m] if m else []
    if not resolve:
        return matches
    resolved = await resolve_conversation_status_matches(
        db, account_id, message_row, matches, provider=provider, first=first)
    return resolved or []


async def _email_payload_from_id(
    db: Any, message_id: str, user_email: str, account_id: str | None = None,
) -> dict[str, str]:
    """The classifier payload of one stored mail of the member.

    With ``account_id``, the mail must be a mail of that mailbox, or the
    answer is 404 (D-EM-19, EM-T8e-1). ``POST /email/rules/test`` passes it,
    so a mail of mailbox B is never tested against the rules of mailbox A.
    """
    in_mailbox = " AND em.account_id = :aid" if account_id else ""
    params: dict[str, Any] = {"mid": message_id, "uid": user_email}
    if account_id:
        params["aid"] = account_id
    row = (await db.execute(text(
        f"""SELECT em.id, em.account_id, em.subject, em.body_text, em.snippet,
                  em.from_address, em.to_addresses, em.cc_addresses, em.thread_id,
                  em.received_at, ea.email_address
           FROM email_messages em JOIN email_accounts ea ON em.account_id = ea.id
           WHERE em.id = :mid AND ea.user_id = :uid{in_mailbox}"""
    ), params)).fetchone()
    if not row:
        raise HTTPException(status_code=404, detail="Message not found")
    org_domains = await resolve_org_domains(db, str(row.account_id))
    selves = await resolve_self_addresses(db, str(row.account_id))
    attach = (await _attachment_summaries(db, [row.id])).get(str(row.id), "")
    return email_dict_from_row(
        row, getattr(row, "email_address", "") or "",
        extra_domains=org_domains, attachments=attach, self_addresses=selves)
