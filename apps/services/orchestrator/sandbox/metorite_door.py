"""The door client of the sandbox (SBX-13, ``agent_sandbox.md`` §12.3).

A tool in the coding sandbox imports this module to read Metorite as the
member, through the door of §12.2. The image copies it into site-packages,
root-owned and mode 0444, so ``python3 -I`` finds it.

Rules (SBX-F22, ``tests/unit/test_sandbox_door_client.py``):

* The host is the constant :data:`DOOR_URL`. No input and no environment
  value changes it.
* The module reads one environment value, ``METORITE_API_TOKEN``. The proxy
  settings of the container reach ``requests`` inside the library, and this
  module does not touch them.
* It reads only. A write method is not here (SBX-3 adds the hold first).
* An error never holds the token, a header value or the body of an answer.

It uses the standard library and ``requests`` only.
"""

from __future__ import annotations

import os
import re
import time
from collections.abc import Iterator
from dataclasses import dataclass
from typing import Any

import requests

__all__ = ["DOOR_URL", "PAGED_ROUTES", "PAGE_CAP", "Door", "DoorError", "Paging"]

#: The door on the org's internal network of the box (§12.3 decision 2).
DOOR_URL = "http://metorite-api.sandbox"

#: The one header of the capability (§12.2 decision 3).
CAPABILITY_HEADER = "X-Sandbox-Capability"

#: The shape of a capability: ``msbx_`` and 43 URL-safe characters.
_CAPABILITY_RE = re.compile(r"^msbx_[A-Za-z0-9_-]{43}$")

#: The page cap of the door (§12.2 decision 9).
PAGE_CAP = 100

#: The longest wait before the one retry of a 503, in seconds.
RETRY_WAIT_CAP_S = 5.0

#: The wait before the retry when the 503 has no usable ``Retry-After``.
RETRY_WAIT_DEFAULT_S = 1.0

#: A path segment. No ``?``, ``#``, ``\\``, ``@`` or white space can pass.
_PATH_RE = re.compile(r"^(?:/[A-Za-z0-9._~:%-]+)+/?$")

#: A door code that the answer may give in ``detail``.
_DOOR_CODE_RE = re.compile(r"^sandbox_[a-z_]{1,48}$")

#: The query keys that set a page size or a page position.
_PAGING_KEYS = frozenset({"page", "page_size", "limit", "offset", "per_lane"})


@dataclass(frozen=True)
class Paging:
    """How one list route pages, and the key of its answer that holds the rows."""

    style: str  # "page" sends page and page_size. "offset" sends limit and offset.
    rows_key: str


_PAGE = "page"
_OFFSET = "offset"

#: Each paged route of the door, by its route template. Each entry was read
#: from its handler under ``apps/services/gateway/gateway/routes``. A route
#: that pages by a cursor, by lane or by person is not here, and ``pages``
#: refuses it.
PAGED_ROUTES: dict[str, Paging] = {
    "/crm/leads": Paging(_PAGE, "rows"),
    "/crm/deals": Paging(_PAGE, "rows"),
    "/crm/contacts": Paging(_PAGE, "rows"),
    "/crm/organizations": Paging(_PAGE, "rows"),
    "/crm/modules/{module}/records": Paging(_PAGE, "rows"),
    "/crm/modules/{module}/records/{record_id}/timeline": Paging(_OFFSET, "entries"),
    "/crm/change-history": Paging(_PAGE, "rows"),
    "/crm/aggregate/stage-dwell": Paging(_PAGE, "overdue"),
    "/projects/tasks": Paging(_PAGE, "rows"),
    "/projects/tasks/{task_id}/timeline": Paging(_PAGE, "rows"),
    "/projects/intake": Paging(_PAGE, "rows"),
    "/projects/notifications": Paging(_PAGE, "rows"),
}


def _template_re(template: str) -> re.Pattern[str]:
    parts = re.split(r"(\{[a-z_]+\})", template)
    body = "".join("[^/]+" if p.startswith("{") else re.escape(p) for p in parts)
    return re.compile(f"^{body}/?$")


_PAGED_MATCHERS = tuple((_template_re(t), paging) for t, paging in PAGED_ROUTES.items())

#: The fixed code of each status, when the answer gives no door code.
_FIXED_CODES = {
    401: "not_allowed",
    403: "forbidden",
    404: "not_found",
    422: "bad_input",
    429: "sandbox_call_cap",
    503: "unavailable",
}

#: The statuses whose door code the client takes from the answer.
_DOOR_CODE_STATUSES = frozenset({403, 404, 422, 429})


class DoorError(Exception):
    """A failed call of the door.

    ``status`` is the HTTP status, or 0 when no answer came. ``code`` comes
    from the answer or is a fixed code of the client. ``retry_after`` is the
    wait in seconds that the door asked for, or None. The text holds the
    status, the code and the path only.
    """

    def __init__(
        self,
        code: str,
        *,
        status: int = 0,
        path: str = "",
        retry_after: float | None = None,
    ) -> None:
        self.code = code
        self.status = status
        self.path = path
        self.retry_after = retry_after
        super().__init__(f"door {status} {code} {path}".rstrip())


def _capability() -> str:
    raw = os.environ.get("METORITE_API_TOKEN") or ""
    value = raw.strip()
    if not _CAPABILITY_RE.fullmatch(value):
        raise DoorError("no_capability")
    return value


def _checked_path(path: str) -> str:
    if (
        not isinstance(path, str)
        or not _PATH_RE.fullmatch(path)
        or ".." in path
        or "%2e" in path.lower()
        or "%2f" in path.lower()
        or "%5c" in path.lower()
    ):
        raise ValueError("The path must start with / and hold no scheme, host, query or '..'.")
    return path


def _retry_after(answer: requests.Response) -> float | None:
    value = (answer.headers.get("Retry-After") or "").strip()
    if not re.fullmatch(r"[0-9]{1,6}", value):
        return None
    return float(value)


def _door_code(answer: requests.Response) -> str | None:
    try:
        body = answer.json()
    except ValueError:
        return None
    detail = body.get("detail") if isinstance(body, dict) else None
    if isinstance(detail, dict):
        detail = detail.get("code")
    if isinstance(detail, str) and _DOOR_CODE_RE.fullmatch(detail):
        return detail
    return None


def _error(answer: requests.Response, path: str) -> DoorError:
    status = answer.status_code
    if 300 <= status < 400:
        return DoorError("redirect", status=status, path=path)
    code = _door_code(answer) if status in _DOOR_CODE_STATUSES else None
    fixed = _FIXED_CODES.get(status, "failed")
    return DoorError(code or fixed, status=status, path=path, retry_after=_retry_after(answer))


def _sleep(seconds: float) -> None:
    time.sleep(seconds)


class Door:
    """Reads Metorite through the door, as the member of the capability."""

    def __init__(self, timeout_s: float = 30.0) -> None:
        self._timeout_s = float(timeout_s)
        self._session = requests.Session()

    def _send(self, path: str, params: dict[str, Any] | None) -> requests.Response:
        token = _capability()
        try:
            return self._session.get(
                DOOR_URL + path,
                params=params,
                headers={CAPABILITY_HEADER: token, "Accept": "application/json"},
                timeout=self._timeout_s,
                allow_redirects=False,
            )
        except requests.RequestException:
            # The text of a requests error can hold the proxy URL or a header.
            raise DoorError("unreachable", path=path) from None

    def get(self, path: str, params: dict[str, Any] | None = None) -> dict[str, Any] | list[Any]:
        """Send one GET to the door, and return the JSON of the answer."""
        path = _checked_path(path)
        answer = self._send(path, params)
        if answer.status_code == 503:
            wait = _retry_after(answer)
            wait = RETRY_WAIT_DEFAULT_S if wait is None else wait
            _sleep(min(wait, RETRY_WAIT_CAP_S))
            answer = self._send(path, params)
        if answer.status_code != 200:
            raise _error(answer, path)
        try:
            body = answer.json()
        except ValueError:
            raise DoorError("bad_answer", status=200, path=path) from None
        if not isinstance(body, (dict, list)):
            raise DoorError("bad_answer", status=200, path=path)
        return body

    def pages(
        self,
        path: str,
        params: dict[str, Any] | None = None,
        page_size: int = PAGE_CAP,
        max_pages: int = 50,
    ) -> Iterator[dict[str, Any]]:
        """Read a list route page by page, and yield each row.

        It stops on an empty page, on a page shorter than ``page_size``, or
        after ``max_pages``. It never asks for more than :data:`PAGE_CAP`
        rows in one page.
        """
        path = _checked_path(path)
        paging = next((p for rx, p in _PAGED_MATCHERS if rx.fullmatch(path)), None)
        if paging is None:
            raise ValueError("The route does not page through the door. Use get().")
        if isinstance(page_size, bool) or not isinstance(page_size, int):
            raise ValueError("page_size must be a whole number.")
        if not 1 <= page_size <= PAGE_CAP:
            raise ValueError(f"page_size must be 1 to {PAGE_CAP}.")
        if isinstance(max_pages, bool) or not isinstance(max_pages, int) or max_pages < 1:
            raise ValueError("max_pages must be 1 or more.")
        base = dict(params or {})
        if _PAGING_KEYS & set(base):
            raise ValueError("pages() sets the page keys. Leave them out of params.")
        for n in range(max_pages):
            if paging.style == _PAGE:
                query = {**base, "page": n + 1, "page_size": page_size}
            else:
                query = {**base, "limit": page_size, "offset": n * page_size}
            body = self.get(path, query)
            rows = body.get(paging.rows_key) if isinstance(body, dict) else None
            if not isinstance(rows, list):
                raise DoorError("bad_answer", status=200, path=path)
            yield from rows
            if len(rows) < page_size:
                return
