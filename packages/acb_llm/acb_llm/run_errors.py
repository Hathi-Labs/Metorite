"""Name a failed agent run with one stable CODE, for the member's chat.

Owner report, 2026-10-08. A Projects chat showed the member this, word for
word, in a red box::

    ("<class 'agent_framework_openai._chat_completion_client.
    OpenAIChatCompletionClient'> service failed to complete the prompt:
    Connection error.", APIConnectionError('Connection error.'))

Then it told them to run ``sudo journalctl -u acb-gateway`` on a server they
cannot reach. The cause was a gateway restart: the agent's model call goes to
our own Router on the same box, and nothing listened for about ten seconds.

## The rule

The server knows what failed, because it holds the exception. The browser holds
only a string. So the server names the failure, and the browser maps the name
to words. **The browser never parses a Python repr.**

:data:`RUN_ERROR_CODES` is the whole vocabulary. ``src/lib/runErrors.ts`` in
the control plane holds the words for each code, and
``tests/unit/test_run_errors.py`` fails when the two lists disagree.

## What a RUN_ERROR carries

:func:`run_error_event` builds the AG-UI ``RUN_ERROR`` frame. ``code`` is one
of the codes. ``ref`` is a short id that the same function writes to the log,
so an operator can find the full failure from what the member reads out.
``message`` keeps the raw text, and the chat shows it only inside a fold.
``error_type`` keeps the class name for logs and tests. The chat route drops
it.

⚠️ AG-UI's ``code`` field carried a Python class name before this module
(``type(exc).__name__``). Nothing read it. It now carries a code from the
vocabulary, which is what the field is for.
"""
from __future__ import annotations

import asyncio
import contextlib
import uuid
from typing import Any

from acb_common import get_logger

__all__ = [
    "RUN_ERROR_CODES",
    "classify_run_error",
    "classify_status",
    "run_error_event",
    "run_ref",
]

_log = get_logger("acb_llm.run_errors")

#: Every code a RUN_ERROR may carry. Append only. A renamed code shows the
#: member the ``unknown`` words until the browser learns the new name.
RUN_ERROR_CODES: tuple[str, ...] = (
    "connection",
    "timeout",
    "rate_limited",
    "credits",
    "model_refused",
    "permission",
    "cancelled",
    "run_in_progress",
    "unknown",
    # The browser's own: the chat route's session ended (a 401 from the
    # gateway to the route). The server never sends it, because a 401 here
    # is our own key to our own Router.
    "signed_out",
)

#: The longest raw text a RUN_ERROR keeps. It is shown inside the fold only.
_MESSAGE_CHARS = 2000

#: How deep the exception walk goes. A loop in a chain must not hang an error
#: path, and twenty links is far past any real wrapper stack.
_CHAIN_LIMIT = 20


def classify_status(status: int) -> str:
    """The code for an HTTP status from the Router or the model.

    ⚠️ **A 401 is ``unknown``, not ``permission``.** It means our own key to
    our own Router is wrong. The member cannot fix that, and "ask an admin"
    would send them to a person who cannot fix it either.
    """
    if status == 402:
        return "credits"
    if status == 403:
        return "permission"
    if status == 429:
        return "rate_limited"
    if status == 409:
        # `_refuse_if_another_run_is_active`: another member's run holds the
        # thread.
        return "run_in_progress"
    if status in (408, 504):
        return "timeout"
    if status in (400, 404, 413, 422):
        return "model_refused"
    if status in (502, 503):
        return "connection"
    return "unknown"


def _chain(exc: BaseException) -> list[BaseException]:
    """*exc* and everything it wraps, outermost first, each once.

    Four links, because the agent framework uses all of them.
    ``inner_exception`` and ``args`` carry the SDK error inside a
    ``ChatClientException`` (``super().__init__(message, inner_exception)``,
    which is why the member saw a tuple). ``__cause__`` is ``raise … from``,
    and ``__context__`` is an exception raised while another was handled.
    """
    out: list[BaseException] = []
    seen: set[int] = set()
    queue: list[BaseException | None] = [exc]
    while queue and len(out) < _CHAIN_LIMIT:
        cur = queue.pop(0)
        if cur is None or id(cur) in seen:
            continue
        seen.add(id(cur))
        out.append(cur)
        inner = getattr(cur, "inner_exception", None)
        queue.append(inner if isinstance(inner, BaseException) else None)
        queue.append(cur.__cause__)
        queue.append(cur.__context__)
        queue.extend(a for a in getattr(cur, "args", ()) if isinstance(a, BaseException))
    return out


def _status_of(exc: BaseException) -> int | None:
    """The HTTP status an exception carries, or None.

    ``openai.APIStatusError`` and FastAPI's ``HTTPException`` carry
    ``status_code``. ``httpx.HTTPStatusError`` carries it on ``response``.
    """
    status = getattr(exc, "status_code", None)
    if isinstance(status, int) and 100 <= status <= 599:
        return status
    response = getattr(exc, "response", None)
    status = getattr(response, "status_code", None)
    if isinstance(status, int) and 100 <= status <= 599:
        return status
    return None


def _code_of(exc: BaseException) -> str | None:
    """The code ONE exception names by itself, or None when it names none."""
    if isinstance(exc, asyncio.CancelledError):
        return "cancelled"
    status = _status_of(exc)
    if status is not None:
        return classify_status(status)
    if "ContentFilter" in type(exc).__name__:
        return "model_refused"
    # The OpenAI SDK's errors, by class NAME in the MRO. Importing the SDK
    # here would be a vendor import (tests/unit/test_no_direct_ai_vendor_calls
    # refuses one in this package), and a name check needs no import.
    # APITimeoutError IS an APIConnectionError, so it is asked first.
    mro = {k.__name__ for k in type(exc).__mro__}
    if "APITimeoutError" in mro:
        return "timeout"
    if "APIConnectionError" in mro:
        return "connection"
    try:
        import httpx

        # TimeoutException IS a TransportError, so it is asked first.
        if isinstance(exc, httpx.TimeoutException):
            return "timeout"
        if isinstance(exc, httpx.TransportError):
            return "connection"
    except ImportError:  # pragma: no cover - httpx is a dependency
        pass
    if isinstance(exc, TimeoutError):
        return "timeout"
    if isinstance(exc, ConnectionError):
        return "connection"
    return None


def classify_run_error(exc: BaseException) -> str:
    """The code for a failed run. Always one of :data:`RUN_ERROR_CODES`.

    It walks the wrappers outermost first, and the first link that names a
    code wins. A failure no link names is ``unknown``. It never raises,
    because it runs inside an error path.
    """
    try:
        for cur in _chain(exc):
            code = _code_of(cur)
            if code is not None:
                return code
    except Exception:
        pass
    return "unknown"


def run_ref(run_id: str | None = None) -> str:
    """A short id the member can read out. Eight hex characters.

    The run id's first eight, when there is a run id, because every executor
    log line already carries the run id. A fresh id otherwise.
    """
    raw = "".join(ch for ch in str(run_id or "") if ch.isalnum())
    return (raw or uuid.uuid4().hex)[:8].lower()


def run_error_event(
    exc: BaseException | None = None,
    *,
    run_id: str | None = None,
    code: str | None = None,
    message: str | None = None,
    where: str = "",
) -> dict[str, Any]:
    """The AG-UI ``RUN_ERROR`` frame for a failed run, with ``code`` and ``ref``.

    Pass the exception and the code comes from :func:`classify_run_error`.
    Pass ``code`` for a failure the caller names itself (the idle watchdog is
    ``timeout``). A code outside the vocabulary becomes ``unknown``.

    It logs one ``run_error`` line with the ref, the code and the exception
    type, so the ref the member reads out finds the failure. It never logs the
    message, because a refused request can quote the member's own words.
    """
    if code is None:
        code = classify_run_error(exc) if exc is not None else "unknown"
    if code not in RUN_ERROR_CODES:
        code = "unknown"
    ref = run_ref(run_id)
    text = message if message is not None else (str(exc) if exc is not None else "")
    # Logging must not fail the error path.
    with contextlib.suppress(Exception):
        _log.warning(
            "run_error",
            code=code,
            ref=ref,
            run_id=run_id or None,
            error_type=type(exc).__name__ if exc is not None else None,
            where=where or None,
        )
    event: dict[str, Any] = {
        "type": "RUN_ERROR",
        "message": text[:_MESSAGE_CHARS],
        "code": code,
        "ref": ref,
    }
    if exc is not None:
        # For logs, tests and replays: the class that failed. The chat route
        # does not forward it, so no member reads a class name.
        event["error_type"] = type(exc).__name__
    if run_id:
        event["runId"] = run_id
    return event
