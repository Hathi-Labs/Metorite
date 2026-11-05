"""The jobs of the web worker and their directories (WEB-6, R5).

The worker holds no database. The orchestrator names the organization of each
call, and the worker trusts it only behind the token (``main.py``).

* Each job gets a directory that ``tempfile.mkdtemp`` makes with mode
  ``0700``, under a root keyed by job id. The worker deletes it when the job
  ends, also when the job fails.
* The store keys each job by organization and job id. So a job id of org A
  never gives org B a ``409``, and :meth:`JobStore.lookup` finds a job only
  for the organization that started it. A job of another organization and a
  job that does not exist give the same :class:`JobNotFound`.
* No HTTP route reads a job file. ``files`` and ``read`` serve a later
  slice that reads a job while it runs (WEB-8).
* A job takes a slot of its organization and a slot of the container before
  it starts. With no slot it does not wait.

Fence: ``tests/unit/test_web_worker.py``.
"""

from __future__ import annotations

import os
import re
import shutil
import tempfile
import threading
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path

from web_worker.limits import MAX_SESSIONS, MAX_SESSIONS_PER_ORG

#: An organization id or a job id: letters, digits, ``_`` and ``-``. A UUID
#: and a slug fit. A dot, a slash and an empty value do not.
_ID = re.compile(r"[A-Za-z0-9][A-Za-z0-9_-]{0,63}")

#: The name of a file in a job directory: no path part, no leading dot.
_FILE = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]{0,127}")


class BadId(ValueError):
    """An organization id or a job id that the worker does not take."""


class JobNotFound(LookupError):
    """No job of this organization has this id, or the file is not in it."""


class NoSlot(RuntimeError):
    """The organization or the container runs its most jobs now."""


class JobExists(RuntimeError):
    """A job with this id runs now."""


def valid_id(value: object) -> str:
    """``value`` when it is a valid id, or raise :class:`BadId`."""
    if not isinstance(value, str) or not _ID.fullmatch(value):
        raise BadId("bad id")
    return value


@dataclass(frozen=True)
class Job:
    org_id: str
    job_id: str
    dir: Path


class JobStore:
    """The jobs that run now, and the root of their directories."""

    def __init__(self, root: str | os.PathLike[str]) -> None:
        self.root = Path(root)
        self._lock = threading.Lock()
        self._jobs: dict[tuple[str, str], Job] = {}

    def _make_root(self) -> None:
        self.root.mkdir(mode=0o700, parents=True, exist_ok=True)
        os.chmod(self.root, 0o700)

    def running(self, org_id: str | None = None) -> int:
        with self._lock:
            return sum(1 for j in self._jobs.values() if org_id in (None, j.org_id))

    @contextmanager
    def open(self, org_id: str, job_id: str) -> Iterator[Job]:
        """Start a job, and end it when the block exits."""
        org_id, job_id = valid_id(org_id), valid_id(job_id)
        with self._lock:
            key = (org_id, job_id)
            if key in self._jobs:
                raise JobExists("job exists")
            if len(self._jobs) >= MAX_SESSIONS:
                raise NoSlot("container")
            if sum(1 for j in self._jobs.values() if j.org_id == org_id) >= MAX_SESSIONS_PER_ORG:
                raise NoSlot("organization")
            self._make_root()
            job = Job(org_id, job_id, Path(tempfile.mkdtemp(prefix=f"{job_id}.", dir=self.root)))
            self._jobs[key] = job
        try:
            yield job
        finally:
            with self._lock:
                self._jobs.pop(key, None)
            shutil.rmtree(job.dir, ignore_errors=True)

    def lookup(self, org_id: str, job_id: str) -> Job:
        """The running job ``job_id`` of ``org_id``, or :class:`JobNotFound`."""
        try:
            org_id, job_id = valid_id(org_id), valid_id(job_id)
        except BadId:
            raise JobNotFound("not found") from None
        with self._lock:
            job = self._jobs.get((org_id, job_id))
        if job is None:
            raise JobNotFound("not found")
        return job

    def files(self, org_id: str, job_id: str) -> list[str]:
        job = self.lookup(org_id, job_id)
        try:
            return sorted(p.name for p in job.dir.iterdir() if p.is_file())
        except OSError:
            raise JobNotFound("not found") from None

    def read(self, org_id: str, job_id: str, name: str) -> bytes:
        """The bytes of one file of a job. The name must be a plain file name
        that the job directory holds now."""
        job = self.lookup(org_id, job_id)
        if not _FILE.fullmatch(name) or name not in self.files(org_id, job_id):
            raise JobNotFound("not found")
        try:
            return (job.dir / name).read_bytes()
        except OSError:
            raise JobNotFound("not found") from None
