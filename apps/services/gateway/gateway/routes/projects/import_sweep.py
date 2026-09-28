"""Projects · file import — the sweep of old upload files (WS-41 I-7).

Spec: ``project-docs/specs/project_import.md`` §7.2 · decision D80.

An upload is deleted when its run reaches ``done``, ``failed`` or
``discarded`` (I-2, I-3). Two kinds of file outlive that: an open run nobody
applied or replaced, and the folder of an organization that no longer exists
(its runs CASCADE, its folder does not). This loop deletes any run folder
untouched for ``KEEP_DAYS``, and any organization folder left empty.

It reads the disk only. It needs no database session, so it cannot reach a
tenant's rows, and a run it removes early fails its apply with the
"cannot read the file" reason the writer already gives.
"""

from __future__ import annotations

import asyncio
import shutil
import time
from pathlib import Path

import structlog

_log = structlog.get_logger(__name__)

#: A run folder untouched this long is deleted (§7.2).
KEEP_DAYS = 14
#: How often the loop looks. Twice a day, so "nightly" holds whatever the
#: gateway's restart times are.
SWEEP_EVERY_SECS = 12 * 3600

_task: asyncio.Task[None] | None = None


def _newest(path: Path) -> float:
    """The latest change inside a run folder, or the folder's own time."""
    times = [path.stat().st_mtime]
    times += [p.stat().st_mtime for p in path.rglob("*")]
    return max(times)


def sweep(root: Path, now: float | None = None) -> dict[str, int]:
    """Delete run folders older than ``KEEP_DAYS`` and empty organization
    folders under ``root``. Returns the counts. A missing root is fine."""
    now = time.time() if now is None else now
    cutoff = now - KEEP_DAYS * 86400
    counts = {"runs": 0, "organizations": 0}
    if not root.is_dir():
        return counts
    for org_dir in root.iterdir():
        if not org_dir.is_dir() or org_dir.is_symlink():
            continue
        for run_dir in org_dir.iterdir():
            if run_dir.is_dir() and not run_dir.is_symlink() and _newest(run_dir) < cutoff:
                shutil.rmtree(run_dir, ignore_errors=True)
                counts["runs"] += 1
        if not any(org_dir.iterdir()):
            org_dir.rmdir()
            counts["organizations"] += 1
    return counts


async def _loop() -> None:
    from gateway.routes.projects.imports import import_dir

    while True:
        try:
            counts = await asyncio.to_thread(sweep, import_dir())
            if counts["runs"] or counts["organizations"]:
                _log.info("projects.import.sweep", **counts)
        except asyncio.CancelledError:
            raise
        except Exception as exc:  # never let one bad sweep kill the loop
            _log.warning("projects.import.sweep_failed", error=str(exc)[:160])
        await asyncio.sleep(SWEEP_EVERY_SECS)


async def start_sweep() -> None:
    """Launch the one sweep loop (called from the gateway lifespan)."""
    global _task
    if _task and not _task.done():
        return
    _task = asyncio.create_task(_loop())
    _log.info("projects.import.sweep_started")


async def stop_sweep() -> None:
    """Cancel the sweep loop (gateway shutdown)."""
    global _task
    task, _task = _task, None
    if task and not task.done():
        task.cancel()
