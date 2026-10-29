"""The gather of the brief: cards to claims, by rules and no model (WS-BRIEF
BR-2a-1, ``core_brief.md`` §4.3).

Every claim is built by code from one card of the My Day feed:

* ``text`` is ``plan.line(card, now)``, the same words that WhatsApp and
  the chat show (D-CF-6).
* ``section`` is the card's ``group`` (``now`` or ``needs_you``).
* ``source_id`` is the card's ``signal_id``, and ``source_app`` is its
  prefix before the first colon. ⚠️ Not ``card["app"]``: a block-soon card
  has ``app="calendar"`` and ``signal_id="tasks:<id>"``, and the record is
  the task. The claim names the record.
* ``href`` is the card's ``href``, kept only when it is an in-app path.
* ``fingerprint`` is the card's, when it has one.

A card with no in-app href, or a prefix that no provider writes, gets no
claim. The gather logs ``brief.claim_skipped`` with the kind only, and the
other cards keep their claims. The gather invents no link.

:data:`SOURCE_APPS` is the vocabulary of prefixes that the feed's providers
write today (``routes/shell/feed.py`` and ``needs.py``). It is not a second
grammar: the CHECK ``brief_claims_source_id_shape`` holds the shape.
``tests/unit/test_brief_run.py`` lists the prefix and the href of each kind.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any

from acb_common import get_logger

from gateway.brief import store
from gateway.reach import plan

_log = get_logger("brief.gather")

#: The prefixes of ``signal_id`` that the feed's providers write.
SOURCE_APPS: frozenset[str] = frozenset({
    "tasks", "email", "crm", "calendar", "chat", "knowledge", "reminders"})


def source_app(signal_id: Any) -> str | None:
    """The app prefix of *signal_id*, or None when the id has no known
    prefix or no reference after it."""
    if not isinstance(signal_id, str):
        return None
    app, sep, ref = signal_id.partition(":")
    if not sep or not ref or app not in SOURCE_APPS:
        return None
    return app


def in_app_href(value: Any) -> str | None:
    """*value* when it is an in-app path (``/...`` and not ``//...``)."""
    if not isinstance(value, str) or not value.startswith("/") or value.startswith("//"):
        return None
    return value


def claim_of(card: dict[str, Any], position: int, now: datetime) -> store.ClaimIn | None:
    """One claim of *card* at *position*, or None when the card cannot make
    one. The section is the card's group, which must be a brief group."""
    app = source_app(card.get("signal_id"))
    href = in_app_href(card.get("href"))
    section = str(card.get("group") or "")
    kind = str(card.get("kind") or "")
    if app is None or href is None or section not in plan.BRIEF_GROUPS or not kind:
        _log.info("brief.claim_skipped", kind=kind or None,
                  reason="no_source" if app is None else "no_href" if href is None else "shape")
        return None
    text = plan.line(card, now)
    if not text.strip():
        _log.info("brief.claim_skipped", kind=kind, reason="no_text")
        return None
    fingerprint = card.get("fingerprint")
    return store.ClaimIn(
        position=position, section=section, text=text, source_app=app, source_kind=kind,
        source_id=str(card["signal_id"]), href=href,
        fingerprint=str(fingerprint) if fingerprint else None)


def shown(cards: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """The cards that the brief shows: ``plan.brief_items`` cut to
    ``plan.BRIEF_LINES``, in the feed's order."""
    return plan.brief_items(cards)[:plan.BRIEF_LINES]


def claims(cards: list[dict[str, Any]], now: datetime) -> list[store.ClaimIn]:
    """The claims of *cards*: one for each shown card that can make one.
    The position is the index among the shown cards, so a retry with the
    same cards gives the same positions."""
    out: list[store.ClaimIn] = []
    for position, card in enumerate(shown(cards)):
        claim = claim_of(card, position, now)
        if claim is not None:
            out.append(claim)
    return out
