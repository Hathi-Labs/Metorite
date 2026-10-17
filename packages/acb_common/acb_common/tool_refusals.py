"""Gateway refusals that a model can read and act on (the ONE seam).

Spec: ``project-docs/specs/projects_agent_parity.md`` §3.4, §3.5, §7.5, §7.6.

The agent framework (MAF) turns a tool that raises into four words, "Error:
Function failed." (``agent_framework/_tools.py``, unless the agent sets
``include_detailed_errors``). So the model never reads the 422 that named
the bad field, and it guesses again. This module closes that for every tool
family that calls the gateway: the Projects tools (``skill_projects``), the
CRM tools (``acb_skills.crm_tools`` and ``agent-crm``) and the email tools
(``agent-email-assistant``).

Three parts, and each tool family uses all three:

* :class:`GatewayRefusal` is the one exception of a refused call. A refusal
  of the gateway carries its ``status``, its safe ``detail`` and the
  ``fields`` that a 422 names. A refusal of the client itself (a bad id, a
  bad name) has ``status`` ``None``, and its message is text that our code
  wrote.
* :func:`safe_detail` takes the route's own words from a refused response,
  and drops each URL, key, address, path and stack.
* :func:`refusals_as_text` wraps a tool. It turns a :class:`GatewayRefusal`
  into text in one shape::

      Refused: <what failed>
      Gateway said: «<the route's detail>»
      Next: <what to do now>

  Only a refusal becomes text. Any other exception is raised again, so MAF
  still answers "Error: Function failed." and the log keeps the trace. Text
  from an unknown failure can carry a stack, a query or a host.

A :class:`RefusalVoice` holds the few words that differ for each family: the
name of the app, the log event, and the "Next:" line of a 404.

Why not ``include_detailed_errors=True``: it reaches every tool of the run,
also the tools that we do not own, and it prints the exception's own text.

Fences: ``tests/unit/test_projects_agent_refusals.py`` (F4, F5) and
``tests/unit/test_crm_email_tool_refusals.py``.
"""

from __future__ import annotations

import functools
import re
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import Any

from acb_common._log import get_logger

_log = get_logger("acb_common.tool_refusals")

__all__ = [
    "DETAIL_MAX",
    "REFUSED",
    "WRAPPED_ATTR",
    "GatewayRefusal",
    "RefusalVoice",
    "data",
    "is_refusal",
    "refusal_text",
    "refusals_as_text",
    "response_body",
    "route_template",
    "safe_detail",
]

#: The first word of every refusal the model reads. The instructions name it.
REFUSED = "Refused:"

#: Marks a callable that :func:`refusals_as_text` made. Each fence reads it.
WRAPPED_ATTR = "__refusals_as_text__"

#: The longest gateway detail the model may read, unless a family asks for
#: more. A 422 that lists ten fields still fits, and a long body is cut.
DETAIL_MAX = 300


class GatewayRefusal(RuntimeError):
    """A call the client refused, or the gateway refused.

    A refusal of the GATEWAY carries its ``status``, its ``detail`` (already
    made safe by :func:`safe_detail`) and the request ``fields`` a 422 names.
    :func:`refusal_text` builds the model's text from those three, never from
    the message, so the route path in the message does not reach the model.
    A refusal of the client itself has ``status`` ``None``: its message is
    text that our code wrote, so the model reads it.

    ``fixable`` is ``False`` for a refusal that no argument can fix. The
    model then reads a neutral "Next:" line, not "fix the argument".
    ``route`` is the template of the refused call (``/crm/deals/{id}``) for
    the log line, never the concrete path."""

    def __init__(
        self,
        message: str,
        *,
        status: int | None = None,
        detail: str = "",
        fields: tuple[str, ...] = (),
        fixable: bool = True,
        route: str = "",
    ) -> None:
        super().__init__(message)
        self.status = status
        self.detail = detail
        self.fields = fields
        self.fixable = fixable
        self.route = route


def data(value: Any) -> str:
    """Fence a string other people wrote, so an instruction inside it reads
    as data. Guillemets are a firmer boundary than quotes, and any embedded
    ones are stripped so the fence stays unambiguous.

    Line breaks collapse to one space. Some cards parse a tool answer LINE BY
    LINE, so a value with a newline could forge a row. Inside one line the
    fence holds."""
    text = str(value or "").replace("«", "").replace("»", "")
    return "«" + " ".join(text.split()) + "»"


# ── The safe detail ─────────────────────────────────────────────────────────

#: Words that only an exception from the database layer or from Python
#: carries. A detail that holds one is dropped whole, because the rest of
#: it can name a query, a table or a host (WS-46 P2).
_LEAK_MARKERS = (
    "traceback (most recent call last)",
    'file "',
    "psycopg",
    "sqlalchemy",
    "asyncpg",
    "[sql:",
    "background on this error",
    "violates ",
    "detail:  key (",
)
#: A URL or a DSN (``postgresql://user:pass@host/db``), in any scheme.
_URL = re.compile(r"[A-Za-z][A-Za-z0-9+.\-]*://\S+")
#: A bearer value, and the key shapes the platform issues or holds.
_SECRET = re.compile(
    r"(?i)\bbearer\s+\S+"
    r"|\b(?:sk|pk|rk|ghp|gho|ghs|xox[abprs]|cc_live|cc_depl)[-_][A-Za-z0-9_\-]{6,}"
    r"|\beyJ[\w-]+\.[\w-]+\.[\w-]+"
)
#: An email address. Only the acting member's own address stays.
_EMAIL = re.compile(r"[A-Za-z0-9._%+\-]+@[A-Za-z0-9.\-]+\.[A-Za-z]{2,}")
#: An absolute path on a server, or on a Windows box.
_PATH = re.compile(r"(?:/(?:opt|home|srv|var)/|\b[A-Za-z]:\\)\S*")


def _field_of(loc: Any) -> str:
    """The request field a FastAPI validation error points at, or ``""``."""
    if not isinstance(loc, (list, tuple)):
        return ""
    names = [str(part) for part in loc if isinstance(part, str)]
    names = [n for n in names if n not in ("body", "query", "path", "header")]
    return names[-1] if names else ""


def response_body(resp: Any) -> Any:
    """The JSON body of *resp*, or ``None`` when it is not JSON."""
    try:
        return resp.json()
    except Exception:  # a body that is not JSON is not the route's words
        return None


def safe_detail(
    status: int, body: Any, member: str = "", *, limit: int = DETAIL_MAX,
) -> tuple[str, tuple[str, ...]]:
    """The gateway's own words from a refused response, and the fields a 422
    names. Only text the route wrote, and only the safe part of it.

    * A string ``detail`` is the route's sentence (``HTTPException``).
    * A list ``detail`` is FastAPI's 422. Each row becomes ``field: msg``.
      The row's ``input``, ``ctx`` and ``url`` are dropped.
    * A dict ``detail`` gives its ``message``, or its ``error`` code, and
      the names in its ``fields`` (``required_fields_missing``).
    * A body that is not JSON is not the route's words, so it gives nothing.
    * A 5xx other than 503 gives nothing. The 503 that the gateway writes
      for an outage (``gateway.main._tenant_unbound``) is safe text.

    Every result then loses any URL, DSN, bearer, key, JWT and absolute
    path, and each email address except *member*'s own (the acting member).
    It is then cut to *limit*. A detail that names a stack, a query or the
    database layer is dropped whole (:data:`_LEAK_MARKERS`).
    """
    if status >= 500 and status != 503:
        return "", ()
    raw: Any = (body.get("detail") or body.get("error")) if isinstance(body, dict) else None
    fields: list[str] = []
    if isinstance(raw, list):
        parts = []
        for row in raw:
            if not isinstance(row, dict):
                continue
            field = _field_of(row.get("loc"))
            msg = str(row.get("msg") or "").strip()
            if field:
                fields.append(field)
            if msg:
                parts.append(f"{field}: {msg}" if field else msg)
        text = ". ".join(parts)
    elif isinstance(raw, dict):
        text = str(raw.get("message") or raw.get("error") or "")
        for row in raw.get("fields") or []:
            if isinstance(row, dict) and row.get("name"):
                fields.append(str(row["name"]))
    elif isinstance(raw, str):
        text = raw
    else:
        text = ""
    named = tuple(dict.fromkeys(fields))
    if any(marker in text.lower() for marker in _LEAK_MARKERS):
        return "", named
    text = _URL.sub("<link removed>", text)
    text = _SECRET.sub("<secret removed>", text)
    own = member.strip().lower()
    text = _EMAIL.sub(
        lambda m: m.group(0) if own and m.group(0).lower() == own else "<address removed>",
        text,
    )
    text = _PATH.sub("<path removed>", text)
    text = " ".join(text.split())
    if len(text) > limit:
        text = text[: limit - 1].rstrip() + "…"
    return text, named


#: A path segment that is an id, not a word of the route: it holds a digit,
#: or it is long. Module and entity names have no digit as a rule.
_ID_SEGMENT = re.compile(r"\d|^.{41,}$")


def route_template(path: str) -> str:
    """*path* with each id segment as ``{id}``, for a log line.

    ``/crm/modules/Invoices/records/9b1d…`` becomes
    ``/crm/modules/Invoices/records/{id}``. A log line then names the
    route and never the record."""
    head = str(path or "").split("?", 1)[0]
    return "/".join(
        "{id}" if part and _ID_SEGMENT.search(part) else part
        for part in head.split("/")
    )


# ── The text the model reads ────────────────────────────────────────────────


@dataclass(frozen=True)
class RefusalVoice:
    """The words of one tool family.

    ``app`` is what the text calls the service, after "The": "Projects app",
    "CRM", "email app". ``log_event`` is the warning that each refusal
    writes. ``not_found_next`` is the "Next:" line of a 404: it names the
    read tools of the family that find a real id."""

    app: str
    log_event: str
    not_found_next: str


def _by_status(voice: RefusalVoice) -> dict[int, tuple[str, str]]:
    """What failed, and what to do, for each status the gateway sends."""
    app = voice.app
    return {
        400: (
            f"The {app} did not accept this request (400).",
            "Fix the argument that the gateway names, and call the tool once more.",
        ),
        401: (
            f"The assistant could not sign in to the {app} (401).",
            "Tell the member. Do not try again.",
        ),
        403: (
            "Not permitted (403).",
            # Owner directive 2026-10-07 (projects_agent_parity.md §16): the
            # server checked the member's grants and said no. Relay ITS words.
            # The model names no role and no permission of its own.
            "The server refused this for the member. Tell the member what the "
            "gateway said. Name no role or permission that the gateway did not "
            "name, and do not try again.",
        ),
        404: ("Not found, or not visible to you (404).", voice.not_found_next),
        409: (
            "This conflicts with the current state of the row (409).",
            "Read the row again, then call the tool once more.",
        ),
        412: (
            "The row changed since it was read (412).",
            "Read the row again, then call the tool once more.",
        ),
        422: (
            f"The {app} refused an argument (422).",
            "Fix the argument that the gateway names, and call the tool once more.",
        ),
        429: (
            f"Too many requests to the {app} (429).",
            "Wait a moment, then try again once.",
        ),
        503: (
            f"The {app} is not available right now (503).",
            "This is a fault on our side. Try again in a moment. If it fails "
            "again, tell the member to try again later.",
        ),
    }


_CLIENT_NEXT = (
    "Fix the one argument these words name, and call the tool once more. "
    "If it is refused again, tell the member what it said."
)

#: For a refusal that no argument fixes (``GatewayRefusal.fixable`` is
#: ``False``).
_FINAL_NEXT = (
    "Tell the member that this could not be done here. Do not try again, "
    "and do not try another tool for the same act."
)


def _one_line(text: str) -> str:
    return " ".join(str(text or "").split())


def refusal_text(exc: GatewayRefusal, voice: RefusalVoice) -> str:
    """The text the model reads for *exc*, in the shape of the module doc.

    A refusal of the gateway is built from its status, its safe detail and
    its fields, never from its message, so no route path reaches the model.
    A refusal of the client is text that our code wrote, so it is kept.
    """
    status = getattr(exc, "status", None)
    if status is None:
        nxt = _CLIENT_NEXT if getattr(exc, "fixable", True) else _FINAL_NEXT
        return f"{REFUSED} {_one_line(str(exc))}\nNext: {nxt}"
    table = _by_status(voice)
    if status in table:
        what, nxt = table[status]
    elif status >= 500:
        what = f"The {voice.app} had an error ({status})."
        nxt = "Try again once. If it fails again, tell the member."
    else:
        what = f"The {voice.app} refused the request ({status})."
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


def is_refusal(value: Any) -> bool:
    """True when a tool result is a refusal: its text starts with
    :data:`REFUSED`.

    A refused call RETURNS its text, so the model can read it. The stream
    must still mark it ``success: false``. Otherwise a chat card draws a
    refused write as done ("Email sent"). Both stream paths ask this one
    function (``orchestrator.event_translator`` and the Tier 2 shim in
    ``orchestrator.executor``). *value* is a string, or a list of content
    items that each carry ``text``.
    """
    if isinstance(value, str):
        return value.lstrip().startswith(REFUSED)
    if isinstance(value, (list, tuple)) and value:
        first = getattr(value[0], "text", value[0])
        return isinstance(first, str) and first.lstrip().startswith(REFUSED)
    return False


def refusals_as_text(
    fn: Callable[..., Awaitable[Any]], voice: RefusalVoice,
) -> Callable[..., Awaitable[Any]]:
    """*fn*, answering a :class:`GatewayRefusal` with :func:`refusal_text`.

    ``functools.wraps`` keeps the name, the docstring, the signature (MAF
    builds the input model from it) and ``__tool_risk__``, which the egress
    rule reads (H-236). Any other exception is raised again.

    Each refusal it turns into text also writes ONE warning,
    ``voice.log_event``, with the tool, the status and the route template.
    The log line holds no detail and no message, because those can hold
    text that a member typed. A *fn* that is already wrapped comes back as
    it is.
    """
    if getattr(fn, WRAPPED_ATTR, False):
        return fn
    tool = getattr(fn, "__name__", "")

    @functools.wraps(fn)
    async def wrapper(*args: Any, **kwargs: Any) -> Any:
        try:
            return await fn(*args, **kwargs)
        except GatewayRefusal as exc:
            _log.warning(
                voice.log_event,
                tool=tool,
                status=getattr(exc, "status", None),
                route=getattr(exc, "route", "") or "",
            )
            return refusal_text(exc, voice)

    setattr(wrapper, WRAPPED_ATTR, True)
    return wrapper
