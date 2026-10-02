"""Automation · learning — the auto-learn gate for sender→rule patterns.

Whether the assistant may pin a sender to a rule WITHOUT being asked. Moved out
of ``runner.py`` (2.3 split): these functions are the *policy* deciding when a
classification becomes a permanent learned pattern, distinct from the mechanics
of applying rule actions. All three checks fail CLOSED — not learning is free;
a wrong pin is silent and permanent.

The pattern WRITE stays in ``rules._upsert_rule_pattern`` (the one write path);
the apply-loop caller lives in ``runner._apply_and_log_match``.
"""

from __future__ import annotations

from typing import Any

from gateway import decide_features
from gateway.routes.email.core import _llm_json, _log
from sqlalchemy import text

_AUTO_LEARN_MIN_CONSISTENT = 5

# A learned FROM pattern asserts something about the SENDER'S IDENTITY: that this
# address only ever sends one kind of thing. That is true of a newsletter list, a
# marketing blast and a cold-outreach account, and it is the reason those senders
# are worth pinning at all — the mail is defined by who sent it.
#
# It is NOT true of Receipt, Calendar or Notification. Those describe what a
# message IS, and they routinely arrive from people you also have real
# conversations with: a colleague sends a meeting invite on Monday and asks a
# question on Tuesday. Pinning them to Calendar files the question as a calendar
# item. Two of the patterns purged from the live account were exactly this
# (midhun.vm → Calendar, debesh@metafora.sg → Receipt), and the correspondent
# check alone did not stop them.
#
# The user can still pin any category by hand — this restricts only what the
# assistant may decide on its own, unattended.
_AUTO_LEARNABLE_RULES = frozenset({"newsletter", "marketing", "cold email"})


def _is_auto_learnable_rule(rule: dict[str, Any]) -> bool:
    """May the assistant pin a sender to this rule without being asked?"""
    name = (rule.get("name") or "").strip().lower()
    system = (rule.get("system_type") or "").strip().lower().replace("_", " ")
    return name in _AUTO_LEARNABLE_RULES or system in _AUTO_LEARNABLE_RULES


#: The probability at which ``decide`` confirms a pin (§10.4.8
#: "Thresholds"). The old prompt asks for 90% sure, and a pin is permanent.
_PIN_THRESHOLD = 0.9

_PIN_QUESTION = ("Will every future email from `sender` belong under the rule in "
                 "the yes criterion?")
_PIN_GUIDANCE = (
    "- Answer yes only for a list or no-reply address that exists to send one "
    "kind of message.",
    "- A person is a no.",
    "- When `sender.public_mail_domain` is true, answer no, unless "
    "`recent_messages` show a plainly automated sender.",
    "- Mixed content across `recent_messages` is a no.",
    "- When in doubt, answer no.",
)
_PIN_TRUE = "Every message from this sender fits this rule."
_PIN_FALSE = ("A person, a sender of mixed content, or a sender that can send "
              "mail that needs a reply.")


def _sender_pin_question(
    sender: str, rule: dict[str, Any], rows: list[Any],
) -> tuple[dict[str, Any], dict[str, Any]]:
    """The ``decide`` request for the sender pin (EM-T5b-1, shadow only).

    The state holds facts about the sender and its recent mail. The rule
    name and text go in ``criteria.true``, never in the state. Both computed
    facts read a list that already exists: ``cleanup._SHARED_DOMAINS`` and
    ``engine._NO_REPLY_PREFIXES``.
    """
    # Imported here: `cleanup` and `engine` sit above this module.
    from acb_llm import BooleanQuestion  # shadow mode only
    from gateway.routes.email.automation.cleanup import _SHARED_DOMAINS
    from gateway.routes.email.automation.engine import _NO_REPLY_PREFIXES

    address = (sender or "").strip().lower()
    domain = address.rsplit("@", 1)[-1] if "@" in address else ""
    fact = decide_features.clip_fact
    state = {
        "sender": {
            "address": fact(address, 320),
            "domain": fact(domain, 255),
            "public_mail_domain": domain in _SHARED_DOMAINS,
            "automated_local_part": any(address.startswith(p) for p in _NO_REPLY_PREFIXES),
        },
        "recent_messages": [
            {"subject": fact(r.subject or "(no subject)", 120),
             "snippet": fact(r.snippet, 160)}
            for r in rows
        ],
    }
    rule_text = (f"{rule.get('name') or ''}: "
                 f"{rule.get('instructions') or '(no description)'}")
    return state, {"always": BooleanQuestion(
        instructions=decide_features.instructions(_PIN_QUESTION, _PIN_GUIDANCE),
        criteria={
            "true": decide_features.clip(f"{_PIN_TRUE}\n{rule_text}"),
            "false": _PIN_FALSE,
        },
    )}


async def _ai_confirms_sender_pattern(
    db: Any, account_id: str, sender: str, rule: dict[str, Any],
    *, message_id: str | None = None,
) -> bool:
    """Ask the model, once, whether this sender is inherently single-purpose.

    Counting agreements measures CONSISTENCY, not correctness. A classifier that
    is confidently wrong about a sender is wrong the same way five times, and
    without this step that streak is all it takes to write a permanent pin — the
    machine agreeing with itself, recorded as fact. This is the check that makes
    the difference: a separate judgment, over the sender's actual mail, about a
    different question — not "what is this email?" but "will EVERY future email
    from this address be the same kind of thing?"

    Ported from inbox-zero's ``aiDetectRecurringPattern``, which we had omitted:
    their threshold count is only a floor before asking this, whereas ours had
    been the entire bar.

    Fails CLOSED. No model, no samples, unparseable answer, anything unexpected
    → no pattern. Not learning is free; a wrong pin is silent and permanent.
    """
    rows = (await db.execute(text(
        """SELECT subject, snippet FROM email_messages
            WHERE account_id = :aid
              AND LOWER(from_address->>'email') = :sender
              AND LOWER(COALESCE(folder, '')) <> 'sent'
            ORDER BY received_at DESC LIMIT 10"""
    ), {"aid": account_id, "sender": sender})).fetchall()
    if len(rows) < 3:
        return False

    samples = "\n".join(
        f"- {(r.subject or '(no subject)')[:120]}"
        f" — {(r.snippet or '')[:160]}" for r in rows)
    rule_name = rule.get("name") or ""
    # The old call's text. `decide` gets the same facts as a structured state
    # (EM-T5b-1, `_sender_pin_question`).
    user_content = (
        f"Sender: {sender}\n"
        f'Proposed rule: "{rule_name}" — '
        f'{rule.get("instructions") or "(no description)"}\n\n'
        f"Their {len(rows)} most recent messages:\n{samples}"
    )

    async def _old() -> bool:
        try:
            data, _content, _used = await _llm_json(
                "tier-balanced",
                [{"role": "system", "content": (
                    "You decide whether a sender's mail should ALWAYS be filed "
                    "under one rule, without the classifier ever looking at it "
                    "again. Say yes ONLY if you are 90%+ confident that EVERY "
                    "future email from this address will serve the same purpose.\n"
                    "Yes: list and no-reply addresses whose whole reason to exist "
                    "is one kind of message — newsletter@, marketing@, an "
                    "outreach account that only ever pitches.\n"
                    "No: a person. No: an address at a generic domain "
                    "(gmail.com, outlook.com, yahoo.com) unless it is plainly an "
                    "automated sender. No: anyone who might also send something "
                    "that needs a reply. No: mixed content across the samples.\n"
                    "Be conservative. Any doubt at all is a no.\n"
                    'Respond with ONLY {"always": true|false, "why": "<short>"}.'
                )},
                 {"role": "user", "content": user_content}],
                max_tokens=200,
            )
        except Exception as exc:  # never fail a rule run on this
            _log.warning("email.auto_learn_verdict_failed",
                         account_id=account_id, error=str(exc)[:160])
            return False
        ok = bool(isinstance(data, dict) and data.get("always") is True)
        _log.info("email.auto_learn_verdict", account_id=account_id,
                  sender=sender, rule=rule_name, confirmed=ok,
                  why=str((data or {}).get("why", ""))[:120])
        return ok

    return await decide_features.shadow(
        "email.sender_pin", _old, account_id=account_id, message_id=message_id,
        build=lambda: _sender_pin_question(sender, rule, list(rows)),
        # The prompt asks for 90% sure, so `agree` is read at 0.9.
        compare=decide_features.compare_boolean(
            lambda r: r, qid="always", threshold=_PIN_THRESHOLD),
    )


async def _sender_is_a_correspondent(
    db: Any, account_id: str, sender: str,
) -> bool:
    """Has the user ever actually CONVERSED with this sender?

    A cleanup category (Newsletter / Receipt / Calendar / Notification) is only
    a safe thing to pin to a sender when the sender is a machine. Pin one to a
    person and their next genuine request is filed as a receipt.

    Conversation-status labels are the evidence: Reply / Awaiting Reply / Done /
    FYI are assigned per-thread from the whole conversation, so their presence
    means this address has been in a real exchange. Found on the live account:
    ``midhun.vm@…`` pinned to Calendar while carrying Awaiting Reply AND Done,
    and ``debesh@metafora.sg`` pinned to Receipt while carrying Awaiting Reply —
    both people who send documents *and* ask questions.
    """
    try:
        row = (await db.execute(text(
            """SELECT 1 FROM email_messages m
                WHERE m.account_id = :aid
                  AND LOWER(m.from_address->>'email') = :sender
                  AND m.categories && ARRAY['Needs Reply', 'Reply',
                                            'Awaiting Reply', 'Done', 'FYI']
                LIMIT 1"""
        ), {"aid": account_id, "sender": sender})).fetchone()
    except Exception as exc:  # noqa: BLE001
        # Roll back so the aborted transaction can't poison the caller's next
        # statement (the apply loop INSERTs the audit row on this same session).
        try:
            await db.rollback()
        except Exception:  # noqa: BLE001
            pass
        _log.warning("email.auto_learn_correspondent_probe_failed",
                     account_id=account_id, error=str(exc)[:160])
        return True  # unknown → refuse to pin; the safe direction
    return row is not None


async def _sender_consistent_for_rule(
    db: Any, account_id: str, sender: str, rule_id: str,
) -> bool:
    """Whether to auto-learn a sender→rule classification pattern yet.

    inbox-zero's analyze-sender-pattern only commits a learned pattern once a
    sender's mail has CONSISTENTLY matched one rule. We mirror that: require
    ``_AUTO_LEARN_MIN_CONSISTENT`` DISTINCT messages (counting the current one)
    and no *other* rule ever matched this sender.

    Three things this counted that are not evidence, all found live:

    * **Dry runs.** The filter excluded SKIPPED/REJECTED but not PENDING, which
      is what a *preview* logs. Pressing "Test" three times on one email taught
      a permanent pattern — ``arvind@exinous.com`` → Calendar was learned from 7
      log rows covering ONE message, 5 of them previews. A dry run is documented
      as changing nothing; it must not teach.

    * **The same message, repeatedly.** ``COUNT(*)`` over a log that gets a row
      per rule per run. ``donotreply@gst.gov.in`` had 10 rows for 1 message. The
      bar reads "3 consistent matches" and meant "3 log lines", so re-running a
      backfill over the same mail manufactured its own evidence.

    * **Rows with no message at all.** 616 APPLIED rows carry a NULL message_id.
      They cannot be corroborated, so they no longer count toward the bar.

    Requires APPLIED specifically rather than "not SKIPPED": a status this code
    has never seen should not be assumed to be evidence.

    The sender match is EXACT (case-folded), not a substring. The old
    ``LIKE '%sender%'`` collided across addresses — ``a@b.com`` matched
    ``aa@b.com`` and ``a@b.com.mx``, so one sender's history corroborated (or
    vetoed) another's, corrupting both the count and the "no other rule ever
    matched this sender" check. ``from_address`` in the log is the bare email
    the apply loop writes, so equality is the correct predicate.
    """
    sender = (sender or "").strip().lower()
    if not sender:
        return False
    try:
        rows = (await db.execute(text(
            """SELECT rule_id, COUNT(DISTINCT message_id) AS n
                 FROM email_executed_rules
                WHERE account_id = :aid AND rule_id IS NOT NULL
                  AND message_id IS NOT NULL
                  AND status = 'APPLIED'
                  AND LOWER(COALESCE(from_address, '')) = :sender
                GROUP BY rule_id"""
        ), {"aid": account_id, "sender": sender})).fetchall()
    except Exception as exc:  # noqa: BLE001
        # Same rollback discipline as above: never leave the session aborted.
        try:
            await db.rollback()
        except Exception:  # noqa: BLE001
            pass
        _log.warning("email.auto_learn_consistency_probe_failed",
                     account_id=account_id, error=str(exc)[:160])
        return False
    by_rule = {str(row.rule_id): int(row.n) for row in rows}
    if any(rid != str(rule_id) and n > 0 for rid, n in by_rule.items()):
        return False  # a different rule has matched this sender → not consistent
    return by_rule.get(str(rule_id), 0) + 1 >= _AUTO_LEARN_MIN_CONSISTENT
