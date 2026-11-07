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

**The acts** (BR-7b-2, ``core_brief.md`` §8) make the section ``acted``
with :func:`acted_claims`: one claim for each allow-listed ``audit_event``
act (``audit:<id>``) and each sent ``reach_outbox`` row (``reach:<id>``).
Each line names its policy, or says :data:`NO_POLICY`. The link rule is
§8.2 item 4: :func:`audit_href` and :func:`reach_href`.

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

#: The prefix of an act from ``audit_event`` (BR-7b-2): ``audit:<id>``.
AUDIT = "audit"
#: The prefix of an act from ``reach_outbox`` (BR-7b-2): ``reach:<id>``.
REACH = "reach"

#: The prefixes of ``signal_id`` that the feed's providers write, and the
#: two prefixes of an act (``core_brief.md`` §8.2 item 3).
SOURCE_APPS: frozenset[str] = frozenset({
    "tasks", "email", "approvals", "crm", "calendar", "chat", "knowledge", "reminders",
    AUDIT, REACH})


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
    ``Your close-out, Thu 15 Oct. 2 done and 1 moved to tomorrow.`` With
    acts (BR-7b-2): ``2 done, 1 moved to tomorrow and 3 acts for you.`` A
    count of 0 is left out. None with no claim."""
    done = sum(1 for c in claims if c.section == "done")
    moved = sum(1 for c in claims if c.section == "moved")
    acted = sum(1 for c in claims if c.section == ACTED)
    parts = (([f"{done} done"] if done else [])
             + ([f"{moved} moved to tomorrow"] if moved else [])
             + ([f"{acted} act{'' if acted == 1 else 's'} for you"] if acted else []))
    if not parts:
        return None
    words = parts[0] if len(parts) == 1 else f"{', '.join(parts[:-1])} and {parts[-1]}"
    return f"Your close-out, {plan._date_words(local_date)}. {words}."


# ── The acts of the day (BR-7b-2, ``core_brief.md`` §8) ─────────────────────

#: The section of an act.
ACTED = "acted"
#: The ``source_kind`` of a Reach row. An audit act takes its action.
OUTBOX_KIND = "outbox"
#: The words of an act whose writer stored no policy (§8.2 item 2).
NO_POLICY = "no policy recorded"
#: The policy of a Reach row of the kind ``brief`` (§8.2 item 2). Another
#: kind takes ``reach_signal_log.rule_id`` once BR-5 slice 2b-1 stores it.
#: Until then it has no policy.
REACH_BRIEF_POLICY = "brief"
#: The My Day path (``app/page.tsx``): the Handled view lives there. An act
#: whose target has no in-app page links here (§8.2 item 4).
MY_DAY_PATH = "/"
#: The longest part of a line that comes from a row.
_PART_MAX = 60

_REACH_WORDS: dict[str, str] = {
    "brief": "Sent your brief",
    "interrupt": "Sent you an alert",
    "request": "Sent you a request",
    "reminder_due": "Sent you a reminder",
    "reminder_check": "Sent you a reminder check",
    "reminder_unconfirmed": "Sent you a reminder that waits for you",
    "due_summary": "Sent you the summary of what is due",
}
_CHANNEL_WORDS: dict[str, str] = {"whatsapp": "on WhatsApp", "web_push": "as a push"}


def _part(value: Any) -> str:
    """A short, plain part of a line from a row value: one line, cut."""
    text = " ".join(str(value or "").split())
    return text if len(text) <= _PART_MAX else text[:_PART_MAX - 1] + "…"


def _with_policy(what: str, policy: str | None) -> str:
    """The line of an act: what ran, and the policy that allowed it."""
    return f"{what} ({NO_POLICY})" if not policy else f"{what} (policy: {_part(policy)})"


def audit_href(target: Any) -> str:
    """The link of an audit act (§8.2 item 4). A door call's target is
    ``app:<app id>.<op>``, and its page is ``/<app id>`` when that is a live
    pane (``orchestrator.in_app_path.pane_of``). Every other target has no
    in-app page, so it takes :data:`MY_DAY_PATH`."""
    from orchestrator.in_app_path import pane_of

    if isinstance(target, str) and target.startswith("app:"):
        path = "/" + target[len("app:"):].split(".", 1)[0]
        pane = pane_of(path)
        if pane is not None and pane.href == path and in_app_href(path):
            return path
    return MY_DAY_PATH


def reach_href(signal_ids: Any, cards: list[dict[str, Any]], closed: list[Any]) -> str:
    """The link of a Reach act (§8.2 item 4): the href of the card of its
    first signal. The card is an open card of the snapshot, or a Handled row
    with that key, because a card that was closed today is not open now. A
    row with no signal, or whose card is in neither list, takes
    :data:`MY_DAY_PATH`."""
    first = next(iter(signal_ids or ()), None)
    if first:
        for card in cards:
            if card.get("signal_id") == first and (href := in_app_href(card.get("href"))):
                return href
        for row in closed:
            if getattr(row, "key", None) == first and (href := in_app_href(row.href)):
                return href
    return MY_DAY_PATH


def act_line(act: Any) -> str:
    """The line of an audit act (``acb_audit.read.Act``), built by code from
    the action, the target and the policy. It holds no body and no
    argument value."""
    action, payload = str(act.action), act.payload if isinstance(act.payload, dict) else {}
    agent = _part(str(act.target).removeprefix("agent:"))
    if action == "agent_door_call":
        what = f"An outside agent used {_part(payload.get('app'))}: {_part(payload.get('op'))}"
    elif action == "agent_push_by_reviewer":
        what = f"Mira pushed the reviewed changes of the agent {agent}"
    elif action == "mutation_commit_auto_pushed":
        what = f"Mira pushed a repair of the agent {agent}"
    elif action.startswith("execute:"):
        what = f"The Action Broker ran {_part(action.removeprefix('execute:'))}"
    else:
        what = f"Mira ran {_part(action)}"
    return _with_policy(what, act.policy)


def sent_line(row: Any) -> str:
    """The line of a Reach row (``outbox.SentRow``). The body is not read."""
    what = _REACH_WORDS.get(str(row.kind), "Sent you a message")
    channel = _CHANNEL_WORDS.get(str(row.channel))
    what = f"{what} {channel}" if channel else what
    return _with_policy(what, REACH_BRIEF_POLICY if row.kind == "brief" else None)


def acted_claims(acts: list[Any], sent: list[Any], cards: list[dict[str, Any]],
                 closed: list[Any], start: int) -> list[store.ClaimIn]:
    """The ``acted`` claims of the close-out (BR-7b-2): one for each audit
    act and each sent Reach row, the oldest first, cut to
    ``plan.BRIEF_LINES``. The positions start at *start*, after ``done`` and
    ``moved``. A line with no valid href is skipped (``brief.claim_skipped``).
    A line with no policy says :data:`NO_POLICY`, and the run logs the count
    (``brief.acted_no_policy``)."""
    items: list[tuple[Any, str, str, str, str, str]] = []
    for act in acts:
        items.append((act.at, AUDIT, f"{AUDIT}:{act.id}", str(act.action),
                      act_line(act), audit_href(act.target)))
    for row in sent:
        items.append((row.sent_at, REACH, f"{REACH}:{row.id}", OUTBOX_KIND,
                      sent_line(row), reach_href(row.signal_ids, cards, closed)))
    items.sort(key=lambda i: (i[0], i[2]))
    out: list[store.ClaimIn] = []
    for _at, app, source_id, kind, text, href in items[:plan.BRIEF_LINES]:
        if in_app_href(href) is None:
            _log.info("brief.claim_skipped", kind=kind, reason="no_href")
            continue
        out.append(store.ClaimIn(position=start + len(out), section=ACTED, text=text,
                                 source_app=app, source_kind=kind, source_id=source_id,
                                 href=href))
    if len(items) > plan.BRIEF_LINES:
        _log.info("brief.acted_cut", left_out=len(items) - plan.BRIEF_LINES)
    return out


def no_policy_count(claims: list[store.ClaimIn]) -> int:
    """How many ``acted`` claims say :data:`NO_POLICY`. The run logs it."""
    return sum(1 for c in claims if c.section == ACTED and c.text.endswith(f"({NO_POLICY})"))
