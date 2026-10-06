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

Why not ``include_detailed_errors=True``: it reaches every tool of the run,
also the tools that we do not own, and it prints the exception's own text.
This wrapper reaches only our refusals, and their text is ours or is the
route's detail after :func:`skill_projects.client.safe_detail`.

Fences: F4 and F5, ``tests/unit/test_projects_agent_refusals.py``.
"""

from __future__ import annotations

import difflib
import functools
from collections.abc import Awaitable, Callable, Iterable
from typing import Any

from skill_projects.client import GatewayRefusal, data

__all__ = [
    "REFUSED",
    "refusal_text",
    "refusals_as_text",
    "unknown_argument_text",
]

#: The first word of every refusal the model reads. The instructions name it.
REFUSED = "Refused:"

#: Marks a callable that :func:`refusals_as_text` made. F4 reads it.
WRAPPED_ATTR = "__refusals_as_text__"

#: What failed, and what to do, for each status the gateway sends.
_BY_STATUS: dict[int, tuple[str, str]] = {
    400: (
        "The Projects app did not accept this request (400).",
        "Fix the argument that the gateway names, and call the tool once more.",
    ),
    401: (
        "The assistant could not sign in to the Projects app (401).",
        "Tell the member. Do not try again.",
    ),
    403: (
        "Not permitted (403).",
        "The member may not do this. Tell the member, and do not try again.",
    ),
    404: (
        "Not found, or not visible to you (404).",
        "Find the right id or name with a read tool, for example find_tasks "
        "or projects_tree. Do not guess an id.",
    ),
    409: (
        "This conflicts with the current state of the row (409).",
        "Read the row again, then call the tool once more.",
    ),
    412: (
        "The row changed since it was read (412).",
        "Read the row again, then call the tool once more.",
    ),
    422: (
        "The Projects app refused an argument (422).",
        "Fix the argument that the gateway names, and call the tool once more.",
    ),
    429: (
        "Too many requests to the Projects app (429).",
        "Wait a moment, then try again once.",
    ),
    503: (
        "The Projects app is not available right now (503).",
        "This is a fault on our side. Try again in a moment. If it fails "
        "again, tell the member to try again later.",
    ),
}

_CLIENT_NEXT = (
    "Fix the one argument these words name, and call the tool once more. "
    "If it is refused again, tell the member what it said."
)

#: Argument names a model invents for a repeat rule.
_REPEAT_WORDS = ("recur", "repeat", "rrule", "freq", "cron", "schedule")


def _one_line(text: str) -> str:
    return " ".join(str(text or "").split())


def refusal_text(exc: GatewayRefusal) -> str:
    """The text the model reads for *exc*, in the shape of the module doc.

    A refusal of the gateway is built from its status, its safe detail and
    its fields, never from its message, so no route path reaches the model.
    A refusal of the client is text this package wrote, so it is kept.
    """
    status = getattr(exc, "status", None)
    if status is None:
        return f"{REFUSED} {_one_line(str(exc))}\nNext: {_CLIENT_NEXT}"
    if status in _BY_STATUS:
        what, nxt = _BY_STATUS[status]
    elif status >= 500:
        what = f"The Projects app had an error ({status})."
        nxt = "Try again once. If it fails again, tell the member."
    else:
        what = f"The Projects app refused the request ({status})."
        nxt = "Fix the argument that the gateway names, and call the tool once more."
    fields = tuple(getattr(exc, "fields", ()) or ())
    if fields and status in (400, 422):
        nxt = (
            f"Fix the value for {', '.join(fields)}, and call the tool once more. "
            "If it is refused again, tell the member what the gateway said."
        )
    lines = [f"{REFUSED} {what}"]
    detail = getattr(exc, "detail", "") or ""
    if detail:
        lines.append(f"Gateway said: {data(detail)}")
    lines.append(f"Next: {nxt}")
    return "\n".join(lines)


def refusals_as_text(
    fn: Callable[..., Awaitable[Any]],
) -> Callable[..., Awaitable[Any]]:
    """*fn*, answering a :class:`GatewayRefusal` with :func:`refusal_text`.

    ``functools.wraps`` keeps the name, the docstring, the signature (MAF
    builds the input model from it) and ``__tool_risk__``, which the egress
    rule reads (H-236). Any other exception is raised again.
    """
    if getattr(fn, WRAPPED_ATTR, False):
        return fn

    @functools.wraps(fn)
    async def wrapper(*args: Any, **kwargs: Any) -> Any:
        try:
            return await fn(*args, **kwargs)
        except GatewayRefusal as exc:
            return refusal_text(exc)

    setattr(wrapper, WRAPPED_ATTR, True)
    return wrapper


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
