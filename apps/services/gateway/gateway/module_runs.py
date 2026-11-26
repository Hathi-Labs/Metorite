"""The switch of member code runs on the box (WS-STU STU-Q15a).

Spec: ``project-docs/specs/ai_studio.md`` §12 STU-Q15 and §14 STU-Q15a.

Module Studio runs the Python of a member as a child process on the
production box (``routes/workflows/engine/modules.py::run_module_code``). The
runner is restricted execution, not a sandbox, and D127.2 keeps member code
off the box. So the runner is dark by org until the owner answers STU-Q15.

``MODULE_RUN_ORGS`` is a comma list of organization ids. ``*`` opens every
org. Empty opens none, and empty is the default. :func:`module_runs_allowed`
is the ONE reader. It reads the settings on each call, so a reload of the
settings (SIGHUP, ``docs/flag_reload.md``) takes effect with no restart.

The consumers:

* ``run_module_code`` calls :func:`refuse_unless_allowed` first, before it
  validates or spawns anything. It is the one choke point of the three run
  paths: ``POST /workflows/modules/test``,
  ``POST /workflows/modules/{module_id}/test`` and the workflow module node.
* The two test routes call :func:`refuse_unless_allowed` first too, so a
  refused call opens no session and gets 403 :data:`MODULE_RUNS_OFF`.
* ``GET /auth/me/shell`` returns the answer as ``module_runs``
  (``routes/admin/me.py``), so ``/studio`` hides the Module Studio tab.

The org always comes from the session or from the run, never from request
input (R5).

Fence: ``tests/unit/test_module_runs_off.py``.
"""

from __future__ import annotations

import functools

from acb_common import get_settings

#: The code of a refusal, in the 403 body and in the log.
MODULE_RUNS_OFF = "module_runs_off"

#: What the member reads. It says what happened, why, and who can change it.
MODULE_RUNS_OFF_MESSAGE = (
    "Code modules do not run in this organization yet, because they will run "
    "in a sandbox first. An admin of Metorite can turn them on."
)


class ModuleRunsOff(Exception):
    """The org of the call may not run a code module. Nothing ran.

    It is not a ``ModuleExecutionError`` on purpose. A caller that catches
    only that error gets this one as a fault, never as a quiet ``ok: false``.
    """

    code = MODULE_RUNS_OFF

    def __init__(self) -> None:
        super().__init__(MODULE_RUNS_OFF_MESSAGE)
        self.message = MODULE_RUNS_OFF_MESSAGE


@functools.lru_cache(maxsize=16)
def _parse_orgs(raw: str) -> frozenset[str]:
    """The entries of a comma list, trimmed and in lower case."""
    return frozenset(p.strip().lower() for p in raw.split(",") if p.strip())


def module_run_orgs() -> frozenset[str]:
    """The organization ids in ``MODULE_RUN_ORGS``. ``*`` stays in the set,
    and :func:`module_runs_allowed` reads it as every org."""
    return _parse_orgs(str(getattr(get_settings(), "module_run_orgs", "") or ""))


def module_runs_allowed(organization_id: str | None) -> bool:
    """True when ``MODULE_RUN_ORGS`` holds the org, or holds ``*``. Default
    OFF. No org is always off. The org comes from the session or the run,
    never from request input (R5)."""
    org = str(organization_id or "").strip().lower()
    if not org:
        return False
    listed = module_run_orgs()
    return "*" in listed or org in listed


def refuse_unless_allowed(organization_id: str | None) -> None:
    """Raise :class:`ModuleRunsOff` unless the org may run a code module."""
    if not module_runs_allowed(organization_id):
        raise ModuleRunsOff()
