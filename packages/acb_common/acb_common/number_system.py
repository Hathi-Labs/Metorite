"""The number system of a member: lakh and crore, or million and billion.

Owner, 2026-10-19: "When I ask for graphs and numbers, you use the million
convention, which is not standard in India. Use the user's preferences, and
keep it in the organizational memory."

Two values, one rule:

* ``org_settings`` key ``number_system`` holds the org's choice. An admin
  writes it in Settings, Organization. No row means the default.
* ``user_settings.number_system`` (migration 284) holds a member's override.
  NULL means "use the organization's setting".

:func:`resolve` is the ONE rule: the member's override wins, then the org's
value, then :data:`DEFAULT` (``"indian"``) for every org. The gateway's
settings routes, the chat's per-turn context and the chart stamp all call it,
so the three never disagree.

The chart engine formats with ``workbench/control_plane/src/lib/numberFormat.mjs``.
This module formats nothing. It reads, resolves and stamps.

It opens no connection. :data:`READ_SQL` runs in the caller's session, which
binds the tenant (R5). Fence: ``tests/unit/test_number_system.py`` and
``tests/unit/test_number_system_r8.py``.
"""

from __future__ import annotations

import json
import threading
import time
from decimal import ROUND_HALF_UP, Decimal
from typing import Any, Literal

NumberSystem = Literal["indian", "international"]

#: The two systems. Any other value reads as "not set".
SYSTEMS: tuple[str, ...] = ("indian", "international")
#: The default for every org (owner, 2026-10-19).
DEFAULT: NumberSystem = "indian"
#: The ``org_settings`` key of the org's choice.
ORG_KEY = "number_system"

#: The org's value and the member's override, in one read. ``:org`` is the
#: tenant the caller's session binds (the request's, or the run's), and the
#: SQL names it beside RLS, not instead of it. ``:uid`` is the session's
#: address. Neither is ever a request field (R5). A missing row reads as NULL.
#:
#: WS-47 follow-up of #1154: the member matches in any case. The settings
#: route writes a NEW row in lower case (:func:`member_key`), and the run
#: reads its member in lower case, but an older row can hold the address as
#: the session gave it. When two rows match, the one with a value wins, and
#: then the lower-case row.
READ_SQL = (
    "SELECT "
    "(SELECT value FROM org_settings "
    " WHERE organization_id = CAST(:org AS uuid) AND key = 'number_system') AS org_value, "
    "(SELECT number_system FROM user_settings "
    " WHERE organization_id = CAST(:org AS uuid) AND lower(user_id) = lower(:uid) "
    " ORDER BY (number_system IS NULL), (user_id <> lower(user_id)) "
    " LIMIT 1) AS member_value"
)

#: The member's existing settings row in the bound org, in any case: the
#: one that :data:`READ_SQL` reads first. The settings route writes that row,
#: so a save never adds a second row for one member.
ROW_SQL = (
    "SELECT user_id FROM user_settings "
    " WHERE organization_id = CAST(:org AS uuid) AND lower(user_id) = lower(:uid) "
    " ORDER BY (number_system IS NULL), (user_id <> lower(user_id)) "
    " LIMIT 1"
)


def member_key(email: str | None) -> str:
    """The ``user_settings.user_id`` of a NEW row: the address in lower
    case, as the run reads its member (``executor._verified_run_member``)."""
    return str(email or "").strip().lower()

#: The one line of the chat's per-turn context, for each system.
PROMPT_LINES: dict[str, str] = {
    "indian": "Write amounts in lakh and crore (₹45.3 lakh, ₹1.35 crore), never million or billion.",
    "international": "Write amounts in thousand, million and billion (₹4.53 million, ₹13.5 million), "
                     "never lakh or crore.",
}


def clean(value: Any) -> NumberSystem | None:
    """*value* as a system, or None when it is not one.

    A stored org value is the JSON blob ``{"value": ...}``, or the bare
    string. A member value is the bare string. Anything else is "not set".
    """
    if isinstance(value, str) and value.startswith("{"):
        try:
            value = json.loads(value)
        except ValueError:
            return None
    if isinstance(value, dict):
        value = value.get("value")
    return value if value in SYSTEMS else None  # type: ignore[return-value]


def resolve(org_value: Any = None, member_value: Any = None) -> NumberSystem:
    """The member's system. The member's override wins, then the org's
    value, then :data:`DEFAULT`."""
    return clean(member_value) or clean(org_value) or DEFAULT


def prompt_line(system: Any) -> str:
    """The one line of the chat's context for *system* (resolved again, so a
    bad value gives the default line)."""
    return PROMPT_LINES[resolve(None, system)]


# ── The short number, the Python twin of numberFormat.mjs ───────────────────
#
# The web formats with `workbench/control_plane/src/lib/numberFormat.mjs`.
# The old WhatsApp SVG renderer (`acb_skills.whatsapp_render`) is Python and
# cannot call it, so this is its twin. ONE table of cases,
# `tests/fixtures/number_format_cases.json`, is read by both test suites, so
# the two cannot drift: `numberFormat.test.ts` and `test_number_system.py`.

_NBSP = chr(0xA0)
#: Each system's bands, largest first: (size, suffix). The last is the plain
#: number below a thousand.
_BANDS: dict[str, tuple[tuple[int, str], ...]] = {
    "indian": ((10**7, f"{_NBSP}Cr"), (10**5, f"{_NBSP}L"), (10**3, "K"), (1, "")),
    "international": ((10**9, "B"), (10**6, "M"), (10**3, "K"), (1, "")),
}


def _fixed(x: float, places: int) -> Decimal:
    """*x* to *places* decimals as JavaScript's ``toFixed`` does it: on the
    exact binary value, a tie to the larger."""
    return Decimal(x).quantize(Decimal(1).scaleb(-places), rounding=ROUND_HALF_UP)


def _rounded(x: float, size: int) -> Decimal:
    if size == 1:
        return Decimal(int(x)) if float(x).is_integer() else _fixed(x, 2)
    return _fixed(x, 2 if x < 10 else 1 if x < 100 else 0)


def group(n: Decimal | float, system: Any) -> str:
    """*n* (zero or more) with the system's digit grouping, and at most two
    decimals with no trailing zero: 1,23,456 or 123,456."""
    d = Decimal(n).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP).normalize()
    whole, _, frac = f"{d:f}".partition(".")
    if resolve(None, system) == "indian" and len(whole) > 3:
        head, tail = whole[:-3], whole[-3:]
        pairs = []
        while len(head) > 2:
            pairs.insert(0, head[-2:])
            head = head[:-2]
        whole = ",".join([head, *pairs, tail]) if head else ",".join([*pairs, tail])
    elif len(whole) > 3:
        whole = f"{int(whole):,}"
    return f"{whole}.{frac}" if frac else whole


def compact_number(value: float, system: Any) -> str:
    """A short number: 1.2K, 85K, 12.5 L, 1.35 Cr (indian) or 1.2K, 85K,
    12.5M, 1.35B (international). The rule of ``numberFormat.mjs``
    ``compactNumber``: a value that rounds up into the next band moves up to
    it, so 99,999 is "1 L". The sign is an ASCII "-"."""
    bands = _BANDS[resolve(None, system)]
    a = abs(float(value))
    i = next((k for k, (size, _) in enumerate(bands) if a >= size), len(bands) - 1)
    n = _rounded(a / bands[i][0], bands[i][0])
    if i > 0 and n * bands[i][0] >= bands[i - 1][0]:
        i -= 1
        n = _rounded(a / bands[i][0], bands[i][0])
    body = f"{group(n, system)}{bands[i][1]}"
    return f"-{body}" if value < 0 and n != 0 else body


def big_band(system: Any) -> int:
    """The size where a system stops writing the whole number: a lakh, or a
    million. Below it, ``acb_skills.whatsapp_render.fmt`` writes every digit."""
    return 10**5 if resolve(None, system) == "indian" else 10**6


# ── The chart stamp ─────────────────────────────────────────────────────────

#: The visual kinds of a report that the chart engine draws.
_CHART_VISUALS = frozenset({"chart", "gantt"})
#: The deepest node the walk visits, the renderer's own depth guard.
_MAX_DEPTH = 20


def stamp_chart(spec: dict[str, Any], system: Any) -> dict[str, Any]:
    """*spec* with ``numbering`` set to the resolved *system*.

    It REPLACES a value the model wrote. The preference is the server's, so
    a model cannot get it wrong. It changes *spec* in place and returns it.
    """
    spec["numbering"] = resolve(None, system)
    return spec


def stamp_report(report: dict[str, Any], system: Any) -> dict[str, Any]:
    """Each chart visual of a report with ``numbering``. In place."""
    sections = report.get("sections")
    if isinstance(sections, list):
        for section in sections:
            visual = section.get("visual") if isinstance(section, dict) else None
            if isinstance(visual, dict) and str(visual.get("kind") or "").lower() in _CHART_VISUALS:
                stamp_chart(visual, system)
    return report


def stamp_ui(node: Any, system: Any, depth: int = 0) -> Any:
    """Every chart in a generative-UI tree with ``numbering``. In place.

    A ``chart`` template's ``data`` and each chart visual of a ``report``
    template. It walks ``root``, ``view`` and ``children`` as the renderer
    does, and it never raises on a strange shape.
    """
    if depth > _MAX_DEPTH or not isinstance(node, dict):
        return node
    for key in ("root", "view"):
        if isinstance(node.get(key), dict):
            stamp_ui(node[key], system, depth + 1)
    if node.get("type") == "template" and isinstance(node.get("props"), dict):
        props = node["props"]
        data = props.get("data")
        if isinstance(data, dict):
            if props.get("name") == "chart":
                stamp_chart(data, system)
            elif props.get("name") == "report":
                stamp_report(data, system)
    kids = node.get("children")
    if isinstance(kids, list):
        for kid in kids:
            stamp_ui(kid, system, depth + 1)
    return node


# ── A short cache for the chat ──────────────────────────────────────────────

#: The resolved system by ``(org, member)``, with the time it was read.
#: Process-local. A save calls :func:`forget`, and another process sees the
#: change within :data:`TTL_SECONDS`.
_CACHE: dict[tuple[str, str], tuple[float, NumberSystem]] = {}
_LOCK = threading.Lock()
TTL_SECONDS = 60.0
_CACHE_MAX = 2048


def _key(org: Any, member: Any) -> tuple[str, str]:
    return (str(org or "").strip().lower(), str(member or "").strip().lower())


def cached(org: Any, member: Any) -> NumberSystem | None:
    """The cached system of *org* and *member*, or None when absent or old."""
    with _LOCK:
        hit = _CACHE.get(_key(org, member))
    if hit and time.monotonic() - hit[0] < TTL_SECONDS:
        return hit[1]
    return None


def remember(org: Any, member: Any, system: NumberSystem) -> None:
    """Keep *system* for *org* and *member*."""
    key = _key(org, member)
    with _LOCK:
        _CACHE.pop(key, None)
        _CACHE[key] = (time.monotonic(), system)
        while len(_CACHE) > _CACHE_MAX:
            _CACHE.pop(next(iter(_CACHE)))


def forget(org: Any = None) -> None:
    """Drop the cache of *org* (an org save changes every member), or all."""
    with _LOCK:
        if org is None:
            _CACHE.clear()
            return
        name = _key(org, "")[0]
        for key in [k for k in _CACHE if k[0] == name]:
            _CACHE.pop(key, None)


__all__ = [
    "DEFAULT",
    "ORG_KEY",
    "PROMPT_LINES",
    "READ_SQL",
    "SYSTEMS",
    "NumberSystem",
    "big_band",
    "cached",
    "clean",
    "compact_number",
    "forget",
    "group",
    "prompt_line",
    "remember",
    "resolve",
    "stamp_chart",
    "stamp_report",
    "stamp_ui",
]
