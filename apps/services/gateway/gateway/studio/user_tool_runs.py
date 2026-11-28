"""The one writer of ``user_tool_runs`` (SBX-4b1, task 3986).

Spec: ``project-docs/specs/agent_sandbox.md`` §7.3 and §12.4 decision 10.
Migration: the file that creates ``user_tool_runs`` (506 at build).
``tests/unit/test_user_tool_registry.py`` runs this SQL on a real Postgres as
the NOBYPASSRLS role.

**One row for each step that ran code.** The step engine of SBX-4b1
(``acb_skills/user_tool_build.py``) calls :func:`record_run` once for each
build and once for each smoke round, a failed or a cancelled round too. A
refusal of ``check_package`` writes a ``smoke`` row with the outcome
``refused`` and the fixed code in ``error_class``. SBX-4b writes no ``run``
row. No production module calls the step engine until SBX-4b2.

**The columns that SBX-4b fills.** ``api_reads``, ``wall_ms``, ``outcome``,
``exit_code`` and ``error_class``. The three write counts are 0, because
SBX-4b holds no write. ``cpu_ms``, ``peak_mem_mb``, ``egress_bytes`` and
``credits`` stay NULL. SBX-7 adds the credits, and no row writes a
``usage_event``.

**Tenancy (R5).** :func:`record_run` takes a session that the caller opened
with ``tenant_session()``, so RLS binds the statement. It takes no org. The
org of a new row comes from the column DEFAULT, the bound tenant. The member,
the run and the thread come from the run binding, never from tool input.

**The chat.** A foreign key check skips RLS. So the INSERT selects the chat
from ``chat_session`` under the bound tenant, as ``tool_gaps.py`` does. A
chat of another org, or no chat, gives no row, and :func:`record_run`
answers None. A Delete of the chat later sets ``thread_id`` to NULL and
keeps the row (ON DELETE SET NULL).
"""

from __future__ import annotations

import re
import uuid
from datetime import datetime
from typing import Any

from sqlalchemy import text

#: The values of ``user_tool_runs.phase`` (§7.3).
PHASES: tuple[str, ...] = ("build", "smoke", "run")
#: The values of ``user_tool_runs.backend`` (§7.3).
BACKENDS: tuple[str, ...] = ("docker-runc", "docker-runsc", "remote-runsc", "e2b")
#: The values of ``user_tool_runs.outcome`` (§7.3).
OUTCOMES: tuple[str, ...] = ("succeeded", "failed", "timeout", "refused", "cancelled")
#: The longest member address, thread id and run id.
EMAIL_MAX = 320
ID_MAX = 200
#: The largest count that fits the INTEGER columns.
INT_MAX = 2_147_483_647

#: A fixed code, never free text (the CHECK ``user_tool_runs_error_class``).
_ERROR_CLASS = re.compile(r"[a-z0-9_]{1,64}")

#: The insert. It selects the chat under the bound tenant, so a chat of
#: another org, or no chat, gives no row. The org comes from the column
#: DEFAULT, and the policy checks it. The write counts take their DEFAULT 0,
#: and the measures of SBX-7 stay NULL. ``started_at`` is clamped to the
#: clock of the database (``LEAST(..., now())``): a host clock that runs
#: ahead would else break the CHECK ``ended_at >= started_at``.
_INSERT_SQL = (
    "INSERT INTO user_tool_runs (member_email, run_id, thread_id, phase, backend, "
    "outcome, exit_code, wall_ms, api_reads, error_class, gap_id, app_id, version, "
    "started_at, ended_at) "
    "SELECT :member, :run, s.id, :phase, :backend, :outcome, :exit_code, :wall_ms, "
    ":api_reads, :error_class, CAST(:gap AS uuid), CAST(:app AS uuid), :version, "
    "LEAST(COALESCE(CAST(:started AS timestamptz), now()), now()), now() "
    "FROM chat_session s WHERE s.id = :thread "
    "RETURNING CAST(id AS text)"
)

_BOUND_ORG = "CAST(NULLIF(current_setting('app.tenant_id', true), '') AS uuid)"

#: Is there a ``build`` row for the gap in the bound org?
_HAS_BUILD_SQL = (
    "SELECT EXISTS (SELECT 1 FROM user_tool_runs "
    f"WHERE organization_id = {_BOUND_ORG} AND gap_id = CAST(:gap AS uuid) "
    "AND phase = 'build')"
)


class ToolRunError(ValueError):
    """A row that the CHECKs of ``user_tool_runs`` would refuse. ``code``
    names the column, and the message never holds the value."""

    def __init__(self, code: str) -> None:
        self.code = code
        super().__init__(f"user_tool_runs refused the value of {code}")


def _short(value: object, limit: int, code: str) -> str:
    if not isinstance(value, str) or not value or len(value) > limit or "\x00" in value:
        raise ToolRunError(code)
    return value


def _count(value: object, code: str, *, nullable: bool) -> int | None:
    if value is None and nullable:
        return None
    if isinstance(value, bool) or not isinstance(value, int) or not 0 <= value <= INT_MAX:
        raise ToolRunError(code)
    return value


def _uuid(value: object, code: str) -> str | None:
    if value is None:
        return None
    try:
        return str(uuid.UUID(str(value)))
    except ValueError:
        raise ToolRunError(code) from None


def check_row(
    *, member: str, run_id: str, thread_id: str, phase: str, backend: str, outcome: str,
    exit_code: int | None = None, wall_ms: int | None = None, api_reads: int = 0,
    error_class: str | None = None, gap_id: str | None = None, app_id: str | None = None,
    version: int | None = None, started_at: datetime | None = None,
) -> dict[str, Any]:
    """The parameters of the INSERT, or :class:`ToolRunError`. Pure. Each
    check is a CHECK of the migration, so the database refuses nothing that
    passes here."""
    email = _short(member, EMAIL_MAX, "member_email")
    if email != email.strip().lower() or "@" not in email:
        raise ToolRunError("member_email")
    run = _short(run_id, ID_MAX, "run_id")
    thread = _short(thread_id, ID_MAX, "thread_id")
    if phase not in PHASES:
        raise ToolRunError("phase")
    if backend not in BACKENDS:
        raise ToolRunError("backend")
    if outcome not in OUTCOMES:
        raise ToolRunError("outcome")
    if exit_code is not None and (isinstance(exit_code, bool) or not isinstance(exit_code, int)
                                  or not -INT_MAX <= exit_code <= INT_MAX):
        raise ToolRunError("exit_code")
    wall = _count(wall_ms, "wall_ms", nullable=True)
    reads = _count(api_reads, "api_reads", nullable=False)
    if error_class is not None and (not isinstance(error_class, str)
                                    or not _ERROR_CLASS.fullmatch(error_class)):
        raise ToolRunError("error_class")
    if version is not None and (isinstance(version, bool) or not isinstance(version, int)
                                or not 1 <= version <= INT_MAX):
        raise ToolRunError("version")
    if started_at is not None and (not isinstance(started_at, datetime)
                                   or started_at.tzinfo is None):
        raise ToolRunError("started_at")
    return {"member": email, "run": run, "thread": thread, "phase": phase,
            "backend": backend, "outcome": outcome, "exit_code": exit_code,
            "wall_ms": wall, "api_reads": reads, "error_class": error_class,
            "gap": _uuid(gap_id, "gap_id"), "app": _uuid(app_id, "app_id"),
            "version": version, "started": started_at}


async def record_run(
    db: Any, *, member: str, run_id: str, thread_id: str, phase: str, backend: str,
    outcome: str, exit_code: int | None = None, wall_ms: int | None = None,
    api_reads: int = 0, error_class: str | None = None, gap_id: str | None = None,
    app_id: str | None = None, version: int | None = None,
    started_at: datetime | None = None,
) -> str | None:
    """Write one ``user_tool_runs`` row in the bound org, and give its id.

    *db* is a session that the caller opened with ``tenant_session()``. The
    member, the thread and the run are the run's own, never a tool argument.
    A bad value raises :class:`ToolRunError` before any statement. A chat
    that the bound tenant cannot see gives no row and None. ``ended_at`` is
    the clock of the database at the write, and ``started_at`` defaults to
    it."""
    params = check_row(member=member, run_id=run_id, thread_id=thread_id, phase=phase,
                       backend=backend, outcome=outcome, exit_code=exit_code,
                       wall_ms=wall_ms, api_reads=api_reads, error_class=error_class,
                       gap_id=gap_id, app_id=app_id, version=version,
                       started_at=started_at)
    row = (await db.execute(text(_INSERT_SQL), params)).first()
    return None if row is None else str(row[0])


async def has_build_row(db: Any, gap_id: str) -> bool:
    """True when the bound org has a ``build`` row for *gap_id*.

    *db* is a session that the caller opened with ``tenant_session()``. A
    *gap_id* that is not a UUID raises :class:`ToolRunError` before any
    statement, so a bad id never reads as "no build"."""
    gap = _uuid(gap_id, "gap_id") if isinstance(gap_id, str) and gap_id else None
    if gap is None:
        raise ToolRunError("gap_id")
    return bool((await db.execute(text(_HAS_BUILD_SQL), {"gap": gap})).scalar())


__all__ = [
    "BACKENDS",
    "OUTCOMES",
    "PHASES",
    "ToolRunError",
    "check_row",
    "has_build_row",
    "record_run",
]
