"""The write check: look before acting (LOOP-18).

Spec: ``project-docs/specs/proactive_loop.md`` §4.9, task 3571, owner ask 3570.

When a member asks for a write, one read-only check runs as the member, beside
the work that the write does already. It reads the member's OWN data and finds
a fact that the member may lack: "you sent this file to this person on 12
Nov", "this thread has a newer message", "this task is done", "this task is
already open".

* **The intent** comes from the typed arguments of the write: the act, the
  recipients, the object and the topic words (§4.9.2 step 1). No model reads it.
* **The detectors** are rules in code, one per kind (§4.9.3). Each reads
  through the reader of the member: the one mailbox access seam for mail, and
  the one task visibility clause for tasks. A task or a mailbox that the
  member cannot open gives no finding and no trace.
* **The finding** is a dict with the fields of §6.1. A finding with no
  evidence drops. Its ``visibility_scope`` is the member who asked.
* **The budget** is 800 ms at the 95th percentile. A finding that comes back
  after the budget drops, and the card does not wait for it (§4.9.5).
* **The modes** are ``off``, ``shadow`` and ``on`` (§7.6). In shadow the check
  runs and logs, and it returns nothing. Each kind logs one ``write_check``
  line with the kind, whether it fired, and its time.

**Mail text never reaches the model** (§4.9.2). A finding holds typed facts
only: the kind, a date, a count and a link. Its labels are templates. A label
never holds a subject, a file name or a name from mail that another sender
wrote. The topic words of mail are used for the match, and they leave this
module nowhere. Fence: ``test_a_finding_holds_no_text_of_a_mail`` in
``tests/unit/test_write_check.py``.

This module starts no check by itself. LOOP-20 (task 3573) starts it from
``_agated``, and it never runs before a hand-off to a specialist (KR-2) or
before a capture. Fences: ``tests/unit/test_write_check.py`` (the rules) and
``tests/unit/test_write_check_r8.py`` (the reads, on a real database).
"""

from __future__ import annotations

import asyncio
import logging
import re
import time
from collections.abc import Awaitable, Callable, Iterable, Mapping, Sequence
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from typing import Any, Protocol

logger = logging.getLogger(__name__)

MODES: tuple[str, ...] = ("off", "shadow", "on")
#: The budget of one check, at the 95th percentile (§4.9.5).
BUDGET_S = 0.8
#: At most this many findings show on one card (§4.9.2 step 4).
MAX_SHOWN = 2
#: How far back "already sent" looks (§4.9.3).
SENT_WINDOW = timedelta(days=30)
#: A nudge in this window is a repeat (§4.9.3).
NUDGE_WINDOW = timedelta(hours=24)
#: Mail to one address on one topic this often is a routine, not a resend.
RECURRING_MIN = 3
#: The title match of a new task: the share of words that both titles hold.
TITLE_MATCH = 0.8

KINDS: tuple[str, ...] = (
    "write_already_sent", "write_thread_moved",
    "write_task_settled", "write_duplicate_task",
)

#: §4.9.3. A subject word counts only after this list drops it. Numbers drop by
#: rule (``words``), so the list holds none.
STOP_WORDS: frozenset[str] = frozenset({
    "re", "fwd", "fw", "hi", "hello", "update", "follow", "up",
    "monday", "tuesday", "wednesday", "thursday", "friday", "saturday", "sunday",
    "mon", "tue", "tues", "wed", "thu", "thur", "thurs", "fri", "sat", "sun",
    "january", "february", "march", "april", "may", "june", "july", "august",
    "september", "october", "november", "december",
    "jan", "feb", "mar", "apr", "jun", "jul", "aug", "sep", "sept", "oct", "nov", "dec",
    # Glue words. They match every subject, so they prove nothing.
    "a", "an", "the", "and", "or", "of", "to", "for", "on", "in", "at", "with",
    "from", "is", "are", "be", "by", "this", "that", "your", "our", "my", "you",
})
#: The member said "again" in the typed words, so the member knows (§4.9.3,
#: "a resend that the member asked for").
RESEND_WORDS: frozenset[str] = frozenset({"resend", "resent", "resending", "again", "reminder"})

_WORD = re.compile(r"[a-z0-9]+")
#: Words of a file name that name a version, not a topic: Deck_final, Report_v2.
_FILE_NOISE: frozenset[str] = frozenset({"final", "copy", "draft", "signed", "scan"})
_VERSION = re.compile(r"^v\d+$")
#: Glue words of a task title. A title keeps its numbers and its months:
#: "Pay invoice 1043" is not "Pay invoice 1042", and April is not March.
TITLE_GLUE: frozenset[str] = frozenset({
    "a", "an", "the", "and", "or", "of", "to", "for", "on", "in", "at", "with",
    "from", "is", "are", "be", "by", "this", "that", "your", "our", "my", "you"})
#: A date stamp inside a file name: 2026-11-12, 20261112, 12-11-2026.
_DATE_STAMP = re.compile(r"(?<!\d)(?:\d{4}[-_.]?\d{2}[-_.]?\d{2}|\d{2}[-_.]\d{2}[-_.]\d{4})(?!\d)")
_EXT = re.compile(r"\.[A-Za-z0-9]{1,5}$")


def words(text: str | None) -> frozenset[str]:
    """The topic words of a text: lower case, no stop word, no number."""
    out = set()
    for w in _WORD.findall((text or "").lower()):
        if w in STOP_WORDS or w.isdigit() or len(w) < 2:
            continue
        out.add(w)
    return frozenset(out)


def file_stem_words(name: str | None) -> frozenset[str]:
    """The words of a file name, without its extension, date stamp and version."""
    base = (name or "").replace("\\", "/").rsplit("/", 1)[-1]
    base = _EXT.sub("", base)
    base = _DATE_STAMP.sub(" ", base)
    return frozenset(w for w in words(base.replace("_", " "))
                     if w not in _FILE_NOISE and not _VERSION.match(w))


def title_words(text: str | None) -> frozenset[str]:
    """The words of a task title: numbers and months count, glue does not."""
    return frozenset(w for w in _WORD.findall((text or "").lower())
                     if (w not in TITLE_GLUE and len(w) >= 2) or w.isdigit())


def _file_name(value: str) -> str | None:
    """A file name from an attachment argument, or None for an id form.

    ``send_email`` takes a path, a name, or an id such as ``task-file:<uuid>``
    or ``knowledge:<uuid>``. An id holds no name, so it adds no word.
    """
    v = (value or "").strip()
    if not v or re.match(r"^[A-Za-z][A-Za-z0-9_-]*:", v) or not _EXT.search(v):
        return None
    return v


# ── The intent ──────────────────────────────────────────────────────────────


@dataclass(frozen=True)
class Intent:
    """What the write asks for, read from its typed arguments only."""

    act: str                       # "send_email", "nudge_task", "create_task", "assign_task"
    recipients: tuple[str, ...] = ()
    reply_to: str | None = None    # the local id of the email that a reply answers
    task_id: str | None = None
    project_id: str | None = None
    assignees: tuple[str, ...] = ()
    title: str = ""
    topic: frozenset[str] = field(default_factory=frozenset)
    file_stems: frozenset[str] = field(default_factory=frozenset)
    resend: bool = False


def _split(value: Any) -> tuple[str, ...]:
    if isinstance(value, str):
        value = value.split(",")
    return tuple(sorted({str(v).strip().lower() for v in (value or []) if str(v).strip()}))


def _addresses(values: Iterable[str]) -> tuple[str, ...]:
    out = set()
    for v in values:
        m = re.search(r"<([^<>@\s]+@[^<>@\s]+)>", v)
        addr = m.group(1) if m else v
        if "@" in addr:
            out.add(addr.strip().lower())
    return tuple(sorted(out))


def intent_of(tool: str, args: Mapping[str, Any]) -> Intent | None:
    """The intent of one write, or None when the tool is not one that is checked.

    Only the typed arguments are read. The body of a mail is not, because the
    body is long and the subject and the files say what the mail is about.
    """
    if tool == "send_email":
        subject = str(args.get("subject") or "")
        topic = words(subject)
        recipients = _addresses(
            _split(args.get("to")) + _split(args.get("cc")) + _split(args.get("bcc")))
        stems: set[str] = set()
        for f in args.get("attachments") or []:
            name = _file_name(str(f))
            if name:
                stems |= file_stem_words(name)
        return Intent(
            act="send_email", recipients=recipients,
            reply_to=str(args["reply_to_email_id"]) if args.get("reply_to_email_id") else None,
            topic=topic, file_stems=frozenset(stems),
            resend=bool(RESEND_WORDS & set(_WORD.findall(subject.lower()))),
        )
    if tool == "nudge_task":
        return Intent(act="nudge_task", task_id=str(args.get("task_id") or "") or None)
    if tool == "assign":
        return Intent(act="assign_task", task_id=str(args.get("task_id") or "") or None,
                      assignees=_split(args.get("assignees")))
    if tool == "create_task":
        title = str(args.get("title") or "")
        return Intent(act="create_task", project_id=str(args.get("project_id") or "") or None,
                      title=title, topic=title_words(title))
    return None


# ── The reads. Each is made as the member, behind its one seam ──────────────


class Reader(Protocol):
    """What the detectors read. ``SqlReader`` (``write_check_reader.py``) is the
    real one. A test gives a fake."""

    async def sent_mail(self, recipients: Sequence[str], since: datetime,
                        exclude_thread_of: str | None) -> list[dict[str, Any]]: ...

    async def thread_newer(self, email_id: str) -> list[dict[str, Any]]: ...

    async def task_state(self, task_id: str) -> dict[str, Any] | None: ...

    async def open_tasks(self, project_id: str) -> list[dict[str, Any]]: ...


# ── The finding (§6.1) ──────────────────────────────────────────────────────


def _day(value: Any) -> str:
    return value.date().isoformat() if isinstance(value, datetime) else str(value or "")[:10]


def _finding(*, kind: str, member: str, entity_type: str, entity_id: str,
             facts: Mapping[str, Any], href: str, evidence: dict[str, str],
             fingerprint: str, signal_id: str, act: str) -> dict[str, Any]:
    """One finding. The labels are templates, so no text of a mail is in it."""
    return {
        "kind": kind,
        "entity": {"type": entity_type, "id": entity_id, "label": _LABEL[kind]},
        "signal_id": signal_id,
        "fingerprint": fingerprint,
        "facts": dict(facts),
        "expires_at": None,
        "acts": [],
        "href": href,
        "evidence": [evidence],
        "confidence": 1.0,
        "visibility_scope": [member.lower()],
        "severity_hint": "now",
        "batch_key": f"write_check:{act}",
    }


#: The template of each label (§4.9.2). A word of a template, never a subject.
_LABEL: dict[str, str] = {
    "write_already_sent": "a mail you sent",
    "write_thread_moved": "a new message on this thread",
    "write_task_settled": "a task",
    "write_duplicate_task": "an open task",
}


def _mail_href(row: Mapping[str, Any]) -> str:
    from urllib.parse import urlencode

    return "/email?" + urlencode({"email": str(row["id"]).lower(),
                                  "account": str(row.get("account_id") or "").lower()})


def _task_href(task_id: str) -> str:
    from urllib.parse import quote

    return f"/projects?task={quote(str(task_id))}"


# ── The detectors (§4.9.3) ──────────────────────────────────────────────────


async def _already_sent(intent: Intent, member: str, reader: Reader, now: datetime) -> list[dict[str, Any]]:
    if intent.act != "send_email" or not intent.recipients or intent.resend:
        return []
    if not (intent.topic or intent.file_stems):
        return []  # nothing to match on, so no match
    rows = await reader.sent_mail(intent.recipients, now - SENT_WINDOW, intent.reply_to)
    hits: list[tuple[frozenset[str], dict[str, Any]]] = []
    for r in rows:
        to = {a.lower() for a in r.get("to", ())}
        if not to & set(intent.recipients):
            continue
        matched = (words(r.get("subject")) & intent.topic) | (
            (r.get("file_stems") or frozenset()) & intent.file_stems)
        if matched:
            hits.append((matched, r))
    if not hits:
        return []
    # The best match is the one that shares the most words. A routine (a weekly
    # report) is the SAME match repeated, so only hits that hold all of its
    # words count. Other mail to this address on one shared word does not.
    best = max(hits, key=lambda h: (len(h[0]), h[1]["sent_at"]))[0]
    same = [r for m, r in hits if best <= m]
    if len(same) >= RECURRING_MIN:
        return []
    hits = same
    latest = max(hits, key=lambda r: r["sent_at"])
    return [_finding(
        kind="write_already_sent", member=member, entity_type="thread",
        entity_id=str(latest.get("thread_id") or latest["id"]),
        facts={"sent_on": _day(latest["sent_at"]), "count": len(hits)},
        href=_mail_href(latest),
        evidence={"app": "email", "type": "message", "id": str(latest["id"]),
                  "label": f"a mail you sent on {_day(latest['sent_at'])}", "href": _mail_href(latest)},
        fingerprint=f"{latest['id']}:{len(hits)}", signal_id=f"email:{latest['id']}",
        act=intent.act)]


async def _thread_moved(intent: Intent, member: str, reader: Reader, now: datetime) -> list[dict[str, Any]]:
    if intent.act != "send_email" or not intent.reply_to:
        return []
    rows = await reader.thread_newer(intent.reply_to)
    if not rows:
        return []
    newest = max(rows, key=lambda r: r["received_at"])
    return [_finding(
        kind="write_thread_moved", member=member, entity_type="thread",
        entity_id=str(newest.get("thread_id") or newest["id"]),
        facts={"newer": len(rows), "last_on": _day(newest["received_at"])},
        href=_mail_href(newest),
        evidence={"app": "email", "type": "message", "id": str(newest["id"]),
                  "label": _LABEL["write_thread_moved"], "href": _mail_href(newest)},
        fingerprint=f"{newest['id']}:{len(rows)}", signal_id=f"email:{newest['id']}",
        act=intent.act)]


async def _task_settled(intent: Intent, member: str, reader: Reader, now: datetime) -> list[dict[str, Any]]:
    if intent.act not in ("nudge_task", "assign_task") or not intent.task_id:
        return []
    state = await reader.task_state(intent.task_id)
    if state is None:
        return []  # a task that the member cannot open leaves no trace
    reason = None
    if state.get("done"):
        reason = "done"
    elif intent.act == "assign_task" and intent.assignees and set(intent.assignees) == {
            a.lower() for a in state.get("assignees", ())}:
        # ``assign`` REPLACES the set. A smaller set removes people, which is not
        # "already assigned".
        reason = "assigned"
    elif intent.act == "nudge_task":
        last = state.get("last_nudged_at")
        if isinstance(last, datetime) and now - last < NUDGE_WINDOW:
            reason = "nudged"
    if reason is None:
        return []
    tid = str(state["id"])
    return [_finding(
        kind="write_task_settled", member=member, entity_type="task", entity_id=tid,
        facts={"reason": reason}, href=_task_href(tid),
        evidence={"app": "projects", "type": "task", "id": tid,
                  "label": _LABEL["write_task_settled"], "href": _task_href(tid)},
        fingerprint=f"{tid}:{reason}", signal_id=f"tasks:{tid}", act=intent.act)]


async def _duplicate_task(intent: Intent, member: str, reader: Reader, now: datetime) -> list[dict[str, Any]]:
    if intent.act != "create_task" or not intent.project_id or not intent.topic:
        return []
    for row in await reader.open_tasks(intent.project_id):
        theirs = title_words(row.get("title"))
        if not theirs:
            continue
        share = len(theirs & intent.topic) / len(theirs | intent.topic)
        if share >= TITLE_MATCH:
            tid = str(row["id"])
            return [_finding(
                kind="write_duplicate_task", member=member, entity_type="task", entity_id=tid,
                facts={"match": round(share, 2)}, href=_task_href(tid),
                evidence={"app": "projects", "type": "task", "id": tid,
                          "label": _LABEL["write_duplicate_task"], "href": _task_href(tid)},
                fingerprint=f"{tid}:{intent.project_id}", signal_id=f"tasks:{tid}", act=intent.act)]
    return []


DETECTORS: dict[str, Callable[..., Awaitable[list[dict[str, Any]]]]] = {
    "write_already_sent": _already_sent,
    "write_thread_moved": _thread_moved,
    "write_task_settled": _task_settled,
    "write_duplicate_task": _duplicate_task,
}


# ── The run ─────────────────────────────────────────────────────────────────


async def _timed(kind: str, intent: Intent, member: str, reader: Reader, now: datetime,
                 ) -> tuple[str, list[dict[str, Any]], float, str]:
    start = time.monotonic()
    try:
        found = await DETECTORS[kind](intent, member, reader, now)
        status = "fired" if found else "quiet"
    except Exception as exc:  # a failed read is a quiet check, never a failed write
        found, status = [], f"error:{type(exc).__name__}"
    return kind, found, (time.monotonic() - start) * 1000.0, status


async def check_write(
    tool: str, args: Mapping[str, Any], *, member: str, reader: Reader,
    mode: str = "shadow", budget_s: float = BUDGET_S, now: datetime | None = None,
) -> list[dict[str, Any]]:
    """Run the check of one write and return the findings to show.

    ``off`` runs nothing. ``shadow`` runs, logs one line for each kind, and
    returns nothing, so no member sees a finding. ``on`` returns at most
    ``MAX_SHOWN`` findings. The detectors run side by side inside one budget. A
    detector that has not finished at the budget drops, and so does a read that
    fails. The write never waits for the check, and never fails because of it.
    """
    if mode not in MODES or mode == "off":
        return []
    intent = intent_of(tool, args)
    if intent is None or not member:
        return []
    when = now or datetime.now(UTC)
    started = time.monotonic()
    tasks = {k: asyncio.ensure_future(_timed(k, intent, member, reader, when)) for k in KINDS}
    _, pending = await asyncio.wait(tasks.values(), timeout=budget_s)
    for p in pending:
        p.cancel()
    findings: list[dict[str, Any]] = []
    for kind, task in tasks.items():
        if task in pending:
            logger.info("write_check", extra={"kind": kind, "status": "late", "mode": mode,
                                              "ms": round(budget_s * 1000)})
            continue
        _, found, ms, status = task.result()
        logger.info("write_check", extra={"kind": kind, "status": status, "mode": mode,
                                          "ms": round(ms, 1)})
        findings.extend(found)
    logger.info("write_check_total", extra={"mode": mode, "fired": len(findings),
                                            "ms": round((time.monotonic() - started) * 1000, 1)})
    if mode != "on":
        return []
    return [f for f in findings if f.get("evidence")][:MAX_SHOWN]
