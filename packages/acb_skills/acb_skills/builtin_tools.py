"""The set of built-in chat tool names, for the failure fingerprint (TOF-2b).

Spec: ``project-docs/specs/tool_failure_log.md`` §3.2 rule 1. Fence:
``tests/unit/test_failure_fingerprint.py`` (P2).

A chat tool name enters a fingerprint only when this set holds it. Any other
name gives ``other``, because a name that the model wrote is tenant text. The
set comes from the tool names that ``skill_families`` declares, less a name
off the grammar ``^[a-z][a-z0-9_]{0,79}$``. It never holds an F3 skill pack:
``skill_packs`` makes those at run time from a skill name, which is tenant
text, and ``skill_families`` holds none of them.

The module gives the set to ``acb_common.failure_log.set_builtin_names`` once,
at its import. ``acb_skills/__init__.py`` imports it, so each process that
imports a module of ``acb_skills`` has the set before a tool runs. It edits no
``tool_packs`` code. ``skill_families`` imports the standard library only, and
``failure_log`` imports no ``acb_skills`` module, so no cycle can form.

TOF-2c: the module also gives ``failure_log.set_channel_reader`` the reader of
the run's ``channel`` key, so a capture site in ``acb_common`` (C4) reads the
surface of the run with no import of ``acb_skills``.
"""

from __future__ import annotations

from functools import lru_cache

from acb_common import failure_log
from acb_common.failure_fingerprint import TOOL_RE

from acb_skills.skill_families import all_registered_tool_names


@lru_cache(maxsize=1)
def builtin_tool_names() -> frozenset[str]:
    """Every tool name that a family declares, and that matches the grammar."""
    return frozenset(n for n in all_registered_tool_names() if TOOL_RE.match(n))


def run_channel() -> object:
    """The ``channel`` key of the run on this frame, or None (TOF-2c).

    The executor binds it in the run context of ``write_artifact``. The
    import is late, so this module adds no import cycle."""
    from acb_skills.write_artifact import artifact_context

    return artifact_context().get("channel")


failure_log.set_builtin_names(builtin_tool_names())
failure_log.set_channel_reader(run_channel)
