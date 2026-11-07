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
* ``href`` is the card's ``href``, kept only when it is an in-app path by
  the one check, ``orchestrator.in_app_path.is_in_app_path``.
* ``fingerprint`` is the card's, when it has one.

**The close-out** (BR-2b-1, ``core_brief.md`` §4.3) builds the sections
``done`` and ``moved`` with :func:`closeout_claims`. ``done`` is one claim
for each ``Closed`` row of Handled, and ``moved`` is one for each shown open
card. The brief's :func:`claim_of` still refuses a section outside
``plan.BRIEF_GROUPS``: the close-out passes its own section as an argument.

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
from orchestrator.in_app_path import is_in_app_path

from gateway.brief import store
from gateway.reach import plan

_log = get_logger("brief.gather")

#: The prefixes of ``signal_id`` that the feed's providers write.
SOURCE_APPS: frozenset[str] = frozenset({
    "tasks", "email", "approvals", "crm", "calendar", "chat", "knowledge", "reminders"})


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
    """*value* when it is an in-app path, by the one check of the product,
    ``orchestrator.in_app_path.is_in_app_path`` (the twin of ``isInAppPath``
    in the web app). It refuses ``//host``, a scheme, ``/api/...``, a dot
    segment, a backslash and an encoded trick."""
    return value if isinstance(value, str) and is_in_app_path(value) else None


def claim_of(card: dict[str, Any], position: int, now: datetime, *,
             section: str | None = None) -> store.ClaimIn | None:
    """One claim of *card* at *position*, or None when the card cannot make
    one. The section is the card's group, which must be a brief group. The
    close-out passes *section* (``moved``), and then the group is not
    checked: the caller chose the cards."""
    app = source_app(card.get("signal_id"))
    href = in_app_href(card.get("href"))
    if section is None:
        section = str(card.get("group") or "")
        in_section = section in plan.BRIEF_GROUPS
    else:
        in_section = section in store.SECTIONS
    kind = str(card.get("kind") or "")
    if app is None or href is None or not in_section or not kind:
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


#: The ``source_kind`` of a ``done`` claim: the row came from Handled.
HANDLED_KIND = "handled"


def done_claim(closed: Any, position: int) -> store.ClaimIn | None:
    """One ``done`` claim of a Handled row (``needs.Closed``), or None when
    the row cannot make one: no known source prefix in its key, no in-app
    href, or no title. ``source_id`` is the key of the row."""
    app = source_app(closed.key)
    href = in_app_href(closed.href)
    text = str(closed.title or "").strip()
    if app is None or href is None or not text:
        _log.info("brief.claim_skipped", kind=HANDLED_KIND,
                  reason="no_source" if app is None else "no_href" if href is None
                  else "no_text")
        return None
    return store.ClaimIn(
        position=position, section="done", text=text, source_app=str(closed.app or app),
        source_kind=HANDLED_KIND, source_id=str(closed.key), href=href)


def closeout_claims(closed: list[Any], cards: list[dict[str, Any]],
                    now: datetime) -> list[store.ClaimIn]:
    """The claims of the close-out: ``done`` for each Handled row, then
    ``moved`` for each shown open card (:func:`shown`), each cut to
    ``plan.BRIEF_LINES``. The positions are continuous from 0, ``done``
    first, so a retry with the same reads gives the same positions."""
    out: list[store.ClaimIn] = []
    for row in closed[:plan.BRIEF_LINES]:
        claim = done_claim(row, len(out))
        if claim is not None:
            out.append(claim)
    for card in shown(cards):
        claim = claim_of(card, len(out), now, section="moved")
        if claim is not None:
            out.append(claim)
    return out


def closeout_lead(local_date: Any, claims: list[store.ClaimIn]) -> str | None:
    """The template lead of the close-out, from the counts of the claims:
    ``Your close-out, Thu 15 Oct. 2 done and 1 moved to tomorrow.`` A count
    of 0 is left out. None with no claim."""
    done = sum(1 for c in claims if c.section == "done")
    moved = sum(1 for c in claims if c.section == "moved")
    parts = ([f"{done} done"] if done else []) + ([f"{moved} moved to tomorrow"] if moved else [])
    if not parts:
        return None
    return f"Your close-out, {plan._date_words(local_date)}. {' and '.join(parts)}."
