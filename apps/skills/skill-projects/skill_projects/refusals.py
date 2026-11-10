"""Refusals the model can read and act on (WS-46 P2, D91.3).

Spec: ``project-docs/specs/projects_agent_parity.md`` §3.4, §3.5, §7.5, §7.6.

The agent framework turns a tool that raises into four words, "Error:
Function failed." (``agent_framework/_tools.py``, unless the agent sets
``include_detailed_errors``). So the model never read the 422 that named the
bad field, and could not correct itself. Two pieces close that, and both are
applied where the agent registers its tools (``agent-projects/agents.py``):

* :func:`refusals_as_text` wraps each exported tool. It turns a
  :class:`~skill_projects.client.GatewayRefusal` into text in one shape::

      Refused: <what failed>
      Gateway said: «<the route's detail>»
      Next: <what to do now>

  Only a refusal becomes text. Any other exception is raised again, so MAF
  still answers "Error: Function failed." and the log keeps the trace. Text
  from an unknown failure can carry a stack, a query or a host.
* :func:`unknown_argument_text` is the answer to a call with an argument the
  tool does not declare. MAF drops such an argument in silence, and the call
  then succeeds without it. The agent's strict tool refuses it by name.

The generic half lives in ``acb_common.tool_refusals``, the ONE seam that
the CRM and email tools share. This module holds the Projects words (its
:data:`PROJECTS_VOICE`) and the two refusals of the strict tool.

Fences: F4 and F5, ``tests/unit/test_projects_agent_refusals.py``.
"""

from __future__ import annotations

import difflib
from collections.abc import Awaitable, Callable, Iterable
from typing import Any

from acb_common import tool_refusals as _shared
from acb_common.tool_refusals import REFUSED, WRAPPED_ATTR, GatewayRefusal, RefusalVoice

__all__ = [
    "PROJECTS_VOICE",
    "REFUSED",
    "WRAPPED_ATTR",
    "bad_value_text",
    "refusal_text",
    "refusals_as_text",
    "unknown_argument_text",
]

#: The Projects words of the shared refusal text.
PROJECTS_VOICE = RefusalVoice(
    app="Projects app",
    log_event="projects.tool_refused",
    not_found_next=(
        "Find the right id or name with a read tool, for example find_tasks "
        "or projects_tree. Do not guess an id."
    ),
)

#: Argument names a model invents for a repeat rule.
_REPEAT_WORDS = ("recur", "repeat", "rrule", "freq", "cron", "schedule")


def refusal_text(exc: GatewayRefusal) -> str:
    """The text the model reads for *exc*, in the Projects words.

    See :func:`acb_common.tool_refusals.refusal_text`.
    """
    return _shared.refusal_text(exc, PROJECTS_VOICE)


def refusals_as_text(
    fn: Callable[..., Awaitable[Any]],
) -> Callable[..., Awaitable[Any]]:
    """*fn*, answering a :class:`GatewayRefusal` with :func:`refusal_text`.

    Each refusal writes ONE warning, ``projects.tool_refused``, with the
    tool, the status and the route template. See
    :func:`acb_common.tool_refusals.refusals_as_text`.
    """
    return _shared.refusals_as_text(fn, PROJECTS_VOICE)


def bad_value_text(tool: str, fields: Iterable[str]) -> str:
    """The refusal for a call whose value *tool* cannot read. It names the
    arguments and never repeats the value."""
    names = list(dict.fromkeys(str(f) for f in fields)) or ["an argument"]
    return (
        f"{REFUSED} {tool} could not read the value of {', '.join(names)}, "
        "so nothing was done.\n"
        f"Next: Fix the value for {', '.join(names)}, and call the tool once more."
    )


def unknown_argument_text(tool: str, unknown: Iterable[str], known: Iterable[str]) -> str:
    """The refusal for a call that sent arguments *tool* does not declare.

    *known* is the list the model can see, in the tool's order. The text
    names each unknown argument, offers a near name, and lists the real ones.
    A repeat word points at the repeat arguments, or at ``set_recurrence``.
    """
    names = sorted(str(u) for u in unknown)
    real = [str(k) for k in known]
    quoted = ", ".join(f"'{n}'" for n in names)
    noun = "argument" if len(names) == 1 else "arguments"
    lines = [f"{REFUSED} {tool} has no {noun} {quoted}, so nothing was done."]
    near = []
    for name in names:
        match = difflib.get_close_matches(name, real, n=1, cutoff=0.6)
        if match:
            near.append(f"'{match[0]}' for '{name}'")
    hints = []
    if near:
        hints.append(f"Did you mean {', '.join(near)}?")
    if any(word in name.lower() for name in names for word in _REPEAT_WORDS):
        own = [k for k in real if k.startswith("repeat")]
        if own:
            hints.append(f"For a repeat rule, use {', '.join(own)}.")
        elif tool != "set_recurrence":
            hints.append(
                f"{tool} sets no repeat rule. Make or find the task first, "
                "then call set_recurrence with its task_id."
            )
    hints.append(f"The arguments of {tool} are: {', '.join(real) or 'none'}.")
    hints.append("Call it again with only those.")
    lines.append("Next: " + " ".join(hints))
    return "\n".join(lines)
