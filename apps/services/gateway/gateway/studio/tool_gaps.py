"""The one writer and reader of ``tool_gaps`` (SBX-1a, task 3800).

Spec: ``project-docs/specs/agent_sandbox.md`` §6.1, §7.4 and §9.2.
Migration: the file that creates ``tool_gaps`` (504 at merge, 502 at build).
``tests/unit/test_user_tool_registry.py`` runs this SQL on a real Postgres as
the NOBYPASSRLS role, and ``tests/unit/test_tool_gap_detection.py`` holds the
refusals.

**One row for each no-match call of ``find_tools``.** The hook of the
orchestrator (``orchestrator/tool_gap_path.py``) calls :func:`record_gap`
once for each such call. The route ``GET /studio/tool-gaps``
(``routes/studio/tool_gaps.py``) calls :func:`gap_summary`. This module reads
no flag, and the callers read theirs first.

**The build half (SBX-4b2, task 4049, §12.4 decision 14).** ``build_tool``
reads one row with :func:`read_gap`. :func:`set_gap_outcome` moves a row from
``no_match`` to ``declined`` or ``build_failed`` only, in one UPDATE, and it
refuses each other move. ``tests/unit/test_user_tool_registry.py`` holds its
R8 case.

**The text of a row.** ``need`` is the query of the model, 1 to
:data:`NEED_MAX` characters, and it stays in the tenant. Each other column
holds an id, a category key or a fixed word. :func:`record_gap` checks each
CHECK of the migration before the database, so a bad row raises
:class:`ToolGapError` and never drops in silence.

**Tenancy (R5).** Each function takes a session that the caller opened with
``tenant_session()``, so RLS binds each statement, and each statement on
``tool_gaps`` names the bound org too. No function takes an org. The org of a
new row comes from the column DEFAULT, the bound tenant.

**The chat.** A foreign key check skips RLS. So the INSERT selects the chat
from ``chat_session`` under the bound tenant, as ``orchestrator/card_record.py``
does. A chat of another org, or no chat, gives no row, and
:func:`record_gap` answers None.
"""

from __future__ import annotations

import re
import uuid
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import datetime
from typing import Any

from sqlalchemy import text

#: The values of ``tool_gaps.answer`` (``agent_sandbox.md`` §7.4).
ANSWERS: tuple[str, ...] = ("tool", "compose", "none", "unsure")
#: The values of ``tool_gaps.outcome`` (§7.4, §12.1 decision 3). SBX-1a
#: writes the first five, and SBX-4 the last three.
OUTCOMES: tuple[str, ...] = (
    "existing", "reuse", "compose", "no_match", "unsure", "declined", "built", "build_failed",
)
#: The outcomes that :func:`set_gap_outcome` may write (§12.4 decision 14).
#: SBX-5 writes ``built``, so it is not here.
BUILD_OUTCOMES: tuple[str, ...] = ("declined", "build_failed")
#: The one outcome that :func:`set_gap_outcome` moves a row from.
MOVE_FROM = "no_match"
#: The longest need, in characters.
NEED_MAX = 280
#: The most ids in ``candidates``.
CANDIDATES_MAX = 10
#: The most characters of the ids, joined with a comma.
CANDIDATES_CHARS = 2000
#: The longest member address, thread id and run id.
EMAIL_MAX = 320
ID_MAX = 200
#: The days of the counts and the rows of :func:`gap_summary`.
SUMMARY_DAYS = 30
#: The most rows of :func:`gap_summary`.
SUMMARY_ROWS = 50

_CATEGORY = re.compile(r"[a-z0-9-]{1,40}")
_CANDIDATE = re.compile(r"\S{1,200}")

_BOUND_ORG = "CAST(NULLIF(current_setting('app.tenant_id', true), '') AS uuid)"

#: The insert. It selects the chat under the bound tenant, so a chat of
#: another org, or no chat, gives no row. The org comes from the column
#: DEFAULT, and the policy checks it.
_INSERT_SQL = (
    "INSERT INTO tool_gaps (member_email, thread_id, run_id, need, category, "
    "candidates, answer, outcome, app_id) "
    "SELECT :member, s.id, :run, :need, :category, CAST(:candidates AS text[]), "
    ":answer, :outcome, CAST(:app AS uuid) "
    "FROM chat_session s WHERE s.id = :thread "
    "RETURNING CAST(id AS text)"
)

#: The counts of the last *days* days, by outcome and by category.
_COUNTS_SQL = (
    "SELECT 'outcome', outcome, count(*) FROM tool_gaps "
    f"WHERE organization_id = {_BOUND_ORG} "
    "AND created_at > now() - make_interval(days => :days) GROUP BY outcome "
    "UNION ALL "
    "SELECT 'category', category, count(*) FROM tool_gaps "
    f"WHERE organization_id = {_BOUND_ORG} "
    "AND created_at > now() - make_interval(days => :days) GROUP BY category"
)

#: The newest rows of the last *days* days: the need, the category, the
#: outcome and the time only.
_ROWS_SQL = (
    "SELECT need, category, outcome, created_at FROM tool_gaps "
    f"WHERE organization_id = {_BOUND_ORG} "
    "AND created_at > now() - make_interval(days => :days) "
    "ORDER BY created_at DESC, id DESC LIMIT :limit"
)


#: One row of the bound org, by id.
_READ_SQL = (
    "SELECT CAST(id AS text), member_email, thread_id, run_id, need, outcome, created_at "
    f"FROM tool_gaps WHERE id = CAST(:id AS uuid) AND organization_id = {_BOUND_ORG}"
)

#: The one move of the build half. The WHERE holds the old outcome, so a
#: second move, or a move from another outcome, changes no row. It is one
#: statement, so no read comes between the check and the write.
_MOVE_SQL = (
    "UPDATE tool_gaps SET outcome = :outcome "
    f"WHERE id = CAST(:id AS uuid) AND organization_id = {_BOUND_ORG} "
    f"AND outcome = '{MOVE_FROM}' "
    "RETURNING CAST(id AS text)"
)


class ToolGapError(ValueError):
    """A row that the CHECKs of ``tool_gaps`` would refuse. ``code`` names the
    column, and the message never holds the value."""

    def __init__(self, code: str) -> None:
        self.code = code
        super().__init__(f"tool_gaps refused the value of {code}")


@dataclass(frozen=True)
class GapRow:
    """One row of the gap view: no member, no chat and no id."""

    need: str
    category: str
    outcome: str
    created_at: datetime


@dataclass(frozen=True)
class GapRecord:
    """One row of the bound org, for ``build_tool`` (§12.4 decision 12)."""

    id: str
    member_email: str
    thread_id: str
    run_id: str
    need: str
    outcome: str
    created_at: datetime


@dataclass(frozen=True)
class GapSummary:
    """The gap view of the bound org (``agent_sandbox.md`` §9.2)."""

    days: int
    by_outcome: dict[str, int]
    by_category: dict[str, int]
    rows: tuple[GapRow, ...]


def _short(value: object, limit: int, code: str) -> str:
    if not isinstance(value, str) or not value or len(value) > limit or "\x00" in value:
        raise ToolGapError(code)
    return value


def check_row(
    *, member: str, thread_id: str, run_id: str, need: str, category: str,
    candidates: Sequence[str], answer: str, outcome: str, app_id: str | None,
) -> dict[str, Any]:
    """The parameters of the INSERT, or :class:`ToolGapError`. Pure. Each
    check is a CHECK of the migration, so the database refuses nothing that
    passes here."""
    email = _short(member, EMAIL_MAX, "member_email")
    if email != email.strip().lower() or "@" not in email:
        raise ToolGapError("member_email")
    thread = _short(thread_id, ID_MAX, "thread_id")
    run = _short(run_id, ID_MAX, "run_id")
    if not isinstance(need, str) or not 1 <= len(need) <= NEED_MAX or "\x00" in need:
        raise ToolGapError("need")
    if not isinstance(category, str) or not _CATEGORY.fullmatch(category):
        raise ToolGapError("category")
    ids = list(candidates) if isinstance(candidates, list | tuple) else None
    if ids is None or len(ids) > CANDIDATES_MAX:
        raise ToolGapError("candidates")
    if any(not isinstance(i, str) or not _CANDIDATE.fullmatch(i) for i in ids):
        raise ToolGapError("candidates")
    if len(",".join(ids)) > CANDIDATES_CHARS:
        raise ToolGapError("candidates")
    if answer not in ANSWERS:
        raise ToolGapError("answer")
    if outcome not in OUTCOMES:
        raise ToolGapError("outcome")
    app: str | None = None
    if app_id is not None:
        try:
            app = str(uuid.UUID(str(app_id)))
        except ValueError:
            raise ToolGapError("app_id") from None
    return {"member": email, "thread": thread, "run": run, "need": need,
            "category": category, "candidates": ids, "answer": answer,
            "outcome": outcome, "app": app}


async def record_gap(
    db: Any, *, member: str, thread_id: str, run_id: str, need: str, category: str,
    candidates: Sequence[str], answer: str, outcome: str, app_id: str | None = None,
) -> str | None:
    """Write one ``tool_gaps`` row in the bound org, and give its id.

    *db* is a session that the caller opened with ``tenant_session()``. The
    member, the thread and the run are the run's own, never a tool argument.
    A bad value raises :class:`ToolGapError` before any statement. A chat
    that the bound tenant cannot see gives no row and None."""
    params = check_row(member=member, thread_id=thread_id, run_id=run_id, need=need,
                       category=category, candidates=candidates, answer=answer,
                       outcome=outcome, app_id=app_id)
    row = (await db.execute(text(_INSERT_SQL), params)).first()
    return None if row is None else str(row[0])


async def gap_summary(
    db: Any, *, days: int = SUMMARY_DAYS, limit: int = SUMMARY_ROWS,
) -> GapSummary:
    """The gap view of the bound org: the counts by outcome and by category
    of the last *days* days, and the *limit* newest rows of those days.

    *db* is a session that the caller opened with ``tenant_session()``. A
    row holds the need, the category, the outcome and the time only."""
    days = max(1, min(int(days), 366))
    limit = max(1, min(int(limit), SUMMARY_ROWS))
    by_outcome: dict[str, int] = {}
    by_category: dict[str, int] = {}
    for kind, key, count in (await db.execute(text(_COUNTS_SQL), {"days": days})).all():
        (by_outcome if kind == "outcome" else by_category)[str(key)] = int(count)
    rows = (await db.execute(text(_ROWS_SQL), {"days": days, "limit": limit})).all()
    return GapSummary(
        days=days, by_outcome=by_outcome, by_category=by_category,
        rows=tuple(GapRow(need=str(r[0]), category=str(r[1]), outcome=str(r[2]),
                          created_at=r[3]) for r in rows))


def _gap_uuid(gap_id: object) -> str | None:
    """The canonical text of a UUID, or None for each other value."""
    if not isinstance(gap_id, str) or not gap_id or len(gap_id) > 64:
        return None
    try:
        return str(uuid.UUID(gap_id))
    except ValueError:
        return None


async def read_gap(db: Any, gap_id: str) -> GapRecord | None:
    """The row *gap_id* of the bound org, or None.

    *db* is a session that the caller opened with ``tenant_session()``.
    *gap_id* can come from the model, so a value that is not a UUID gives
    None with no statement. A row of another org gives None too."""
    gid = _gap_uuid(gap_id)
    if gid is None:
        return None
    row = (await db.execute(text(_READ_SQL), {"id": gid})).first()
    if row is None:
        return None
    return GapRecord(id=str(row[0]), member_email=str(row[1]), thread_id=str(row[2]),
                     run_id=str(row[3]), need=str(row[4]), outcome=str(row[5]),
                     created_at=row[6])


async def set_gap_outcome(db: Any, gap_id: str, outcome: str) -> bool:
    """Move the row *gap_id* of the bound org from ``no_match`` to *outcome*.
    True when a row moved.

    *db* is a session that the caller opened with ``tenant_session()``. An
    *outcome* that is not in :data:`BUILD_OUTCOMES`, or a *gap_id* that is
    not a UUID, raises :class:`ToolGapError` before any statement. A row in
    another outcome, a row of another org, or no row gives False."""
    if outcome not in BUILD_OUTCOMES:
        raise ToolGapError("outcome")
    gid = _gap_uuid(gap_id)
    if gid is None:
        raise ToolGapError("id")
    row = (await db.execute(text(_MOVE_SQL), {"id": gid, "outcome": outcome})).first()
    return row is not None
