"""Shared in-memory progress tracking for the email background jobs.

Both the rule runner's "Process past emails" job and the cleaner's sweep publish
per-account progress that the UI polls (``/rules/process-past/status``,
``/cleanup/status``). Each is a dict-per-account in a module global, mutated by a
background task while a request handler reads it — fine, because uvicorn runs the
handler and its BackgroundTasks in the SAME worker process, so they share the
global.

The hazard both share is *clobbering*: a second run for the same account (a
retry, a double-click, a sweep starting while a backfill is mid-flight) would
overwrite the first run's row, and — worse — the FIRST run's late "finished"
write could then land on the SECOND run's row and mark the newer job done while
it is still going. The runner already guarded against this with a monotonic
token; the cleaner did not. This is that guard, in one place, for both.

Ephemeral by design: the state lives only in memory, so a process restart drops
it along with the jobs it describes — losing the tracker with the work is
correct, not a bug.
"""
from __future__ import annotations

import uuid
from typing import Any


def _key(account_id: str) -> str:
    """The key of a row: a mailbox id in canonical form.

    A start route keys the row by the raw ``account_id`` of its body. A
    status route reads its id through ``account_ref``, which gives the
    canonical form. With one key form, an upper-case or a braced id from
    one side still finds the row of the other. A value that is not a UUID
    keeps its text.
    """
    try:
        return str(uuid.UUID(str(account_id).strip()))
    except ValueError:
        return account_id


class JobTracker:
    """Per-account job progress with a monotonic-token guard.

    ``start`` mints a token and seeds the row; the background task threads that
    token back through ``guarded``/``finish`` so a superseded (older) run can
    read the row but never mutate a NEWER run's entry. A token of ``None`` skips
    the check (an unguarded update), matching the pre-token call sites that pass
    no token.
    """

    def __init__(self) -> None:
        self._jobs: dict[str, dict[str, Any]] = {}
        self._seq = 0

    def start(self, account_id: str, **fields: Any) -> int:
        """Seed a fresh row for ``account_id`` and return its guard token. The
        row always carries ``token``; callers add whatever else they track."""
        self._seq += 1
        token = self._seq
        self._jobs[_key(account_id)] = {"token": token, **fields}
        return token

    def guarded(
        self, account_id: str, token: int | None = None,
    ) -> dict[str, Any] | None:
        """Return the account's row for in-place mutation IFF it exists and the
        token still owns it — else None, so a superseded run's writes no-op.

        Mutate the returned dict directly (``job["processed"] += 1``); it IS the
        stored row.
        """
        job = self._jobs.get(_key(account_id))
        if job is None:
            return None
        if token is not None and job.get("token") != token:
            return None
        return job

    def update(
        self, account_id: str, token: int | None = None, **fields: Any
    ) -> None:
        """Merge ``fields`` into the account's row under the token guard."""
        job = self.guarded(account_id, token)
        if job is not None:
            job.update(fields)

    def finish(
        self, account_id: str, token: int | None = None, **fields: Any
    ) -> None:
        """Terminal update (same guard). Callers set status/finished_at here."""
        self.update(account_id, token, **fields)

    def get(self, account_id: str) -> dict[str, Any] | None:
        """The current row for ``account_id`` (no token check) — for the status
        endpoints that only read."""
        return self._jobs.get(_key(account_id))

    def is_running(self, account_id: str) -> bool:
        """Whether a job is currently in-flight for ``account_id`` — the
        concurrency guard the sweep/backfill endpoints check before starting a
        second run."""
        job = self._jobs.get(_key(account_id))
        return bool(job and job.get("status") == "running")

    def set(self, account_id: str, row: dict[str, Any]) -> None:
        """Replace the row wholesale (used by the seed sites that build the
        initial dict themselves). Prefer ``start`` for anything that needs a
        token."""
        self._jobs[_key(account_id)] = row

    def pop(self, account_id: str, default: Any = None) -> Any:
        """Drop an account's row (job ended, or a test tidying up)."""
        return self._jobs.pop(_key(account_id), default)

    def clear(self) -> None:
        """Drop all rows — process shutdown, or test isolation."""
        self._jobs.clear()
