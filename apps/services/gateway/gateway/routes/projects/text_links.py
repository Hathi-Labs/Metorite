"""Projects · links from a task's text (WS-57 TR-5).

Spec: ``project-docs/specs/task_references.md`` §6 TR-5.

When a member or an agent saves a task's title, its description or a comment,
the route that saved it calls :func:`link_from_text`. It finds two things in
the text:

* **Each ``#N``.** For each number that names ONE task the caller can see,
  other than this task, it adds a ``relates_to`` link through
  ``relations.insert_task_link``, the insert of ``POST /tasks/{id}/links``.
* **Each GitHub or document URL.** It adds a reference with ``via = 'text'``
  through ``refs.insert_ref``, the insert of ``POST /tasks/{id}/refs``.

Six rules:

1. **One grammar, two languages.** :func:`find_task_refs` is the Python copy
   of ``findTaskRefs`` in ``workbench/control_plane/src/lib/taskRefs.ts``.
   ``tests/fixtures/task_ref_cases.json`` drives both, so a change to one
   rule fails ``test_task_refs_text_links.py`` or ``taskRefs.test.ts``.
2. **The task's own root first.** ``search.lookup_task_refs`` with the
   task's root is the read the card's pills use, so a pill and a saved link
   name the same task. A number with two candidates links nothing, as the
   pill draws nothing.
3. **Only what the caller can see.** The lookup applies
   ``task_visibility_clause`` for the caller. A task the caller cannot see is
   never linked, and its number is never told apart from a number that names
   nothing.
4. **Never removed by an edit.** This module only adds. A person removes a
   link from the card.
5. **No second link.** A pair that already has a link in either direction,
   of any type, gets no ``relates_to``. A reference is idempotent through
   the unique key of ``pm_task_refs``.
6. **Bounded, and after the save.** At most :data:`MAX_TASK_REFS` numbers and
   :data:`MAX_URL_REFS` URLs for each task in one save. Past the cap the
   rest of the text is not read, and one ``projects.text_links.capped`` line
   says how many were left. The work runs in its own transaction after the
   save commits. A failure logs ``projects.text_links.failed`` and never
   fails the save. It calls no network: a new GitHub reference has no
   ``fetched_at``, so the next list of the card reads its title.
"""

from __future__ import annotations

import re
from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from typing import Any

from acb_common import get_logger
from fastapi import HTTPException
from gateway.routes.projects.core import (
    _tenant_session,
    actor,
    load_visible_task,
    resolve_visibility,
)
from gateway.routes.projects.refs import insert_ref
from gateway.routes.projects.refs_classify import Classified, RefRefused, classify
from gateway.routes.projects.relations import insert_task_link
from gateway.routes.projects.search import lookup_task_refs
from sqlalchemy import text

_log = get_logger("gateway.projects.text_links")

#: The most ``#N`` numbers one save reads for one task.
MAX_TASK_REFS = 20
#: The most URLs one save reads for one task.
MAX_URL_REFS = 20

#: The ``via`` of a reference this module adds (§5 of the spec).
VIA_TEXT = "text"


# ── (a) The ``#N`` finder: the Python copy of ``findTaskRefs`` ──────────────
#
# Each rule here is a rule of `taskRefs.ts`, in its order. The case file holds
# them together. JavaScript's `\s` is spelled out, because Python's `\s` and
# `str.isspace` disagree with it at the edges (`\x1c`, `\x85`, `\ufeff`).

_JS_SPACE = "\t\n\v\f\r \u00a0\u1680\u2000-\u200a\u2028\u2029\u202f\u205f\u3000\ufeff"
_SPACE_RE = re.compile(f"[{_JS_SPACE}]")

#: Rules 1 to 4. `\w` is the Python spelling of `[\p{L}\p{N}_]`.
_CANDIDATE = re.compile(r"(?<![\w/\\#&=?@])#([1-9][0-9]{0,8})(?!\w|[.,][0-9])")
#: Rule 5: the hex run that starts at `#`.
_HEX_RUN = re.compile(r"#[0-9a-fA-F]+")
#: Rule 7: the word before the `#`. `[^\W\d_]` is a letter.
_BEFORE_WORD = re.compile(rf"([^\W\d_]+)\.?[{_JS_SPACE}]*$")
#: Rule 7: the word after the number that makes it a rank. ASCII `\b`, as in
#: JavaScript without the `u` flag.
_RANKS = re.compile(
    rf"[{_JS_SPACE}]+(?:priority|priorities|place|spot|rank|choice|pick|seed|position"
    r"|most|best|top)\b",
    re.IGNORECASE | re.ASCII,
)
_OTHER_KINDS = frozenset({
    "pr", "prs", "pull", "order", "orders", "invoice", "invoices", "po", "receipt", "account",
    "sprint", "build", "release", "version", "v", "step", "phase", "batch", "room", "case",
    "rank", "ranked", "episode", "chapter", "section", "slide", "figure", "channel",
})


@dataclass(frozen=True)
class TaskRefMatch:
    """One reference: ``text[start:end]`` is ``#171``, ``number`` is 171."""

    start: int
    end: int
    number: int


def _is_colour_at(rest: str) -> bool:
    run = _HEX_RUN.match(rest)
    digits = len(run.group(0)) - 1 if run else 0
    return digits in (6, 8)


def _is_inside_url(value: str, index: int) -> bool:
    start = index
    while start > 0 and not _SPACE_RE.match(value[start - 1]):
        start -= 1
    end = index
    while end < len(value) and not _SPACE_RE.match(value[end]):
        end += 1
    word = value[start:end]
    return "://" in word or re.match(r"\(?www\.", word, re.IGNORECASE) is not None


def _is_other_kind(value: str, start: int, end: int) -> bool:
    before = _BEFORE_WORD.search(value[max(0, start - 24):start])
    if before and before.group(1).lower() in _OTHER_KINDS:
        return True
    return _RANKS.match(value[end:end + 24]) is not None


def find_task_refs(value: str) -> list[TaskRefMatch]:
    """Every task reference in one run of plain text, in order. Pure."""
    if "#" not in value:
        return []
    out: list[TaskRefMatch] = []
    for m in _CANDIDATE.finditer(value):
        start = m.start()
        if _is_colour_at(value[start:]):
            continue
        if _is_inside_url(value, start):
            continue
        if _is_other_kind(value, start, m.end()):
            continue
        out.append(TaskRefMatch(start, m.end(), int(m.group(1))))
    return out


# ── (b) Markdown: what the card draws as words ──────────────────────────────
#
# The description and a comment are Markdown. The card draws no pill in code
# and none inside a link's words (`remarkEntityPills.ts`), so the server reads
# neither. A title is plain text and is read as it is.

_FENCE = re.compile(r"^ {0,3}(`{3,}|~{3,})")
_CODE_SPAN = re.compile(r"(`+)(?!`)(.+?)(?<!`)\1(?!`)")
#: `[words](url)` and `![alt](url)`: the words go, the URL stays.
_MD_LINK = re.compile(r"!?\[[^\]\n]*\]\(([^)\s]*)[^)\n]*\)")
#: A URL in text. It stops at a space, a quote, an angle bracket and a
#: bracket, so `[x](https://…)` and `<https://…>` both end at the right mark.
_URL = re.compile(r"https?://[^\s<>()\[\]\"'`]+", re.IGNORECASE)
_URL_TAIL = ".,;:!?*_~"


def without_code(markdown: str) -> str:
    """The Markdown with its fenced blocks and its code spans blanked."""
    lines: list[str] = []
    fence: str | None = None
    for line in markdown.split("\n"):
        mark = _FENCE.match(line)
        if fence is not None:
            if mark and mark.group(1)[0] == fence[0] and len(mark.group(1)) >= len(fence):
                fence = None
            lines.append("")
            continue
        if mark:
            fence = mark.group(1)
            lines.append("")
            continue
        lines.append(_CODE_SPAN.sub(" ", line))
    return "\n".join(lines)


def prose_of(markdown: str) -> str:
    """What the card draws as words: no code, and no link's words."""
    return _MD_LINK.sub(lambda m: f" {m.group(1)} ", without_code(markdown))


def find_urls(value: str) -> list[str]:
    """The ``http(s)`` URLs in a text, in order, with the trailing marks of
    a sentence taken off. Pure."""
    out: list[str] = []
    for m in _URL.finditer(value):
        url = m.group(0).rstrip(_URL_TAIL)
        if url:
            out.append(url)
    return out


def text_ref(found: Classified) -> bool:
    """True for a URL the text adds as a reference: a GitHub pull request,
    issue, commit, branch or comment, or a document. A plain link stays
    text, so a sentence with a web address does not fill the Links list."""
    if found.kind == "doc":
        return True
    return found.provider == "github" and found.kind != "url"


def saved_texts(values: Any) -> list[tuple[str | None, bool]]:
    """The title and the description that a write sets, as ``scan`` takes
    them: the title is plain and the description is Markdown. A write that
    sets neither gives ``[]``, and then nothing is read."""
    if not isinstance(values, dict):
        return []
    return [(values[k], k == "description") for k in ("title", "description") if k in values]


@dataclass(frozen=True)
class Scan:
    """What one save names: the numbers and the references, each capped."""

    numbers: list[int]
    refs: list[Classified]
    numbers_left: int
    refs_left: int


def scan(texts: Iterable[tuple[str | None, bool]]) -> Scan:
    """Find the numbers and the references in one save's texts. Pure.

    Each item is ``(text, markdown)``: a title is plain, a description and a
    comment are Markdown. Each number and each reference counts once, in the
    order the texts give them, up to the two caps.
    """
    numbers: list[int] = []
    refs: list[Classified] = []
    seen_numbers: set[int] = set()
    seen_refs: set[tuple[str, str, str]] = set()
    numbers_left = refs_left = 0
    for value, markdown in texts:
        if not value:
            continue
        words = prose_of(value) if markdown else value
        for match in find_task_refs(words):
            if match.number in seen_numbers:
                continue
            seen_numbers.add(match.number)
            if len(numbers) < MAX_TASK_REFS:
                numbers.append(match.number)
            else:
                numbers_left += 1
        for url in find_urls(without_code(value) if markdown else value):
            try:
                found = classify(url)
            except RefRefused:
                continue
            key = (found.kind, found.provider, found.ref)
            if not text_ref(found) or key in seen_refs:
                continue
            seen_refs.add(key)
            if len(refs) < MAX_URL_REFS:
                refs.append(found)
            else:
                refs_left += 1
    return Scan(numbers, refs, numbers_left, refs_left)


# ── (c) The pick: the Python copy of ``pickTask`` with no title ─────────────


def pick_task(hits: Sequence[Any]) -> Any | None:
    """The one task a number names, or ``None``.

    One distinct task: that task. Else, when exactly one of them is open,
    that one. Else none: two open tasks with one number link to neither, as
    the card's pill draws neither (``pickTask`` in ``taskRefs.ts``).
    """
    if len({str(h.id) for h in hits}) == 1:
        return hits[0]
    open_hits = [h for h in hits if not h.archived]
    if len({str(h.id) for h in open_hits}) == 1:
        return open_hits[0]
    return None


# ── (d) The write ───────────────────────────────────────────────────────────


async def _linked_ids(db: Any, task_id: str, others: list[str]) -> set[str]:
    """The tasks of ``others`` that already have a link with ``task_id``, in
    either direction and of any type."""
    if not others:
        return set()
    rows = (await db.execute(
        text(
            "SELECT source_task_id, target_task_id FROM pm_task_links "
            " WHERE (source_task_id = CAST(:tid AS uuid) "
            "        AND target_task_id = ANY(CAST(:ids AS uuid[]))) "
            "    OR (target_task_id = CAST(:tid AS uuid) "
            "        AND source_task_id = ANY(CAST(:ids AS uuid[])))"
        ),
        {"tid": task_id, "ids": others},
    )).fetchall()
    out: set[str] = set()
    for row in rows:
        out.add(str(row.target_task_id))
        out.add(str(row.source_task_id))
    out.discard(task_id)
    return out


async def _link_one(db: Any, vis: Any, task_id: str, found: Scan, by: str) -> dict[str, int]:
    task = await load_visible_task(db, vis, task_id)
    root = str(task.root_project_id) if getattr(task, "root_project_id", None) else None
    hits: dict[int, list[Any]] = {}
    for row in await lookup_task_refs(db, vis, found.numbers, root):
        hits.setdefault(int(row.task_number), []).append(row)
    targets: list[str] = []
    for number in found.numbers:
        picked = pick_task(hits.get(number, []))
        if picked is None or str(picked.id) == task_id or str(picked.id) in targets:
            continue
        targets.append(str(picked.id))
    already = await _linked_ids(db, task_id, targets)
    linked = 0
    for target in targets:
        if target in already:
            continue
        await insert_task_link(db, task_id, target, "relates_to", by=by)
        linked += 1
    referenced = 0
    for ref in found.refs:
        _row, made = await insert_ref(db, task_id, ref, title=None, via=VIA_TEXT, by=by)
        referenced += int(made)
    return {"links": linked, "refs": referenced}


async def link_from_text(
    user: Any, saved: Sequence[tuple[str, Sequence[tuple[str | None, bool]]]],
) -> None:
    """Add the links and the references that the saved texts name.

    ``saved`` is one item for each task the save wrote: ``(task_id,
    [(text, markdown), …])``. Call it AFTER the save's transaction closes.
    It opens one transaction of its own for the whole save, and it never
    raises: a failure rolls back the links of this save and logs.
    """
    work = [(str(task_id), scan(texts)) for task_id, texts in saved]
    work = [(task_id, found) for task_id, found in work if found.numbers or found.refs]
    if not work:
        return
    for task_id, found in work:
        if found.numbers_left or found.refs_left:
            _log.info(
                "projects.text_links.capped", task_id=task_id,
                numbers_left=found.numbers_left, refs_left=found.refs_left,
            )
    by = actor(user)
    try:
        async with _tenant_session() as db:
            vis = await resolve_visibility(db, user)
            for task_id, found in work:
                try:
                    await _link_one(db, vis, task_id, found, by)
                except HTTPException:
                    # The task left the caller's sight between the save and
                    # this read. Nothing to link from.
                    continue
    except Exception as exc:  # best effort: the save already committed
        _log.warning("projects.text_links.failed", error=type(exc).__name__)


__all__ = [
    "MAX_TASK_REFS",
    "MAX_URL_REFS",
    "VIA_TEXT",
    "Scan",
    "TaskRefMatch",
    "find_task_refs",
    "find_urls",
    "link_from_text",
    "pick_task",
    "prose_of",
    "saved_texts",
    "scan",
    "text_ref",
    "without_code",
]
