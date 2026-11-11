"""The ONE client of the web worker (WS-WEB WEB-7, ``web_research.md`` §3.2 and §5.1).

``web_tools.fetch_page`` calls :func:`render_page` when a page needs a browser.
The worker is ``apps/services/web_worker``. Its API is ``POST /render`` with
``{url, org_id, job_id}`` and a bearer token, and it answers with Markdown and a
title. The rules, in order:

1. **Ready.** ``WEB_WORKER_URL`` and ``WEB_WORKER_TOKEN`` are operator settings.
   With either one empty, the client sends nothing and answers ``not ready``.
2. **The caller checks first.** ``fetch_page`` sends a page URL only after its
   own guard passed the URL (``web_tools._walk``). The worker checks each hop
   again. This module never resolves or checks a page host itself.
3. **Who asks.** The caller passes the org of the run binding
   (``web_tools._run_org``), never a value of the tool input (R5). An empty
   org sends nothing. Each call gets a new ``job_id`` (``uuid4().hex``).
4. **The request.** The client posts to the operator's URL only. The page URL
   goes in the JSON body, never in the request line. The client follows no
   redirect, reads no proxy from the env and keeps no cookie. It connects in
   :data:`CONNECT_TIMEOUT_S` and ends in :data:`TOTAL_TIMEOUT_S`, because the
   job timeout of the worker is 60 s.
5. **The answer.** The client reads at most :data:`MAX_ANSWER_BYTES` as it
   streams. Each fault is one code of :data:`FAULTS`, and the client never
   raises. The code never holds the token, the page URL or an exception text.
6. **Slots.** :data:`MAX_RENDERS` renders run in the process, and
   :data:`MAX_RENDERS_PER_ORG` for each org. With no slot, the call answers
   ``busy`` at once and does not wait.

🔴 The token is a secret. It goes in the ``Authorization`` header and in no
other place: no log line, no answer and no fault code.

Fence: ``tests/unit/test_web_render.py``.
"""
from __future__ import annotations

import asyncio
import contextlib
import json
import threading
import uuid
from collections import Counter
from dataclasses import dataclass

import httpx
from acb_common import get_logger

_log = get_logger("acb_skills.web_render_client")

#: Seconds to open the connection to the worker.
CONNECT_TIMEOUT_S = 3.0
#: Seconds for one render call. The job timeout of the worker is 60 s, and
#: this leaves it 10 s to answer.
TOTAL_TIMEOUT_S = 70.0
#: The most bytes of one worker answer. The worker caps the Markdown at
#: 2 MiB, and JSON can write a line break or a quote as two bytes.
MAX_ANSWER_BYTES = 4 * 1024 * 1024
#: The renders that run at one time in the process, and for one org.
MAX_RENDERS = 2
MAX_RENDERS_PER_ORG = 1
#: The longest title and final URL that the client keeps.
MAX_TITLE_CHARS = 1000
MAX_URL_CHARS = 2048

NOT_READY = "not ready"
BUSY = "busy"
REFUSED = "refused"
TOO_LARGE = "too large"
BAD_TEXT = "bad text"
TIMEOUT = "timeout"
UNAVAILABLE = "unavailable"
EMPTY = "empty"
#: The fault codes of a render. None holds a value of the page or the token.
FAULTS = frozenset({NOT_READY, BUSY, REFUSED, TOO_LARGE, BAD_TEXT, TIMEOUT, UNAVAILABLE,
                    EMPTY})

_SLOT_LOCK = threading.Lock()
#: The renders that run now, by org. It is process-wide, never per event loop.
_SLOTS_IN_USE: Counter[str] = Counter()


@dataclass(frozen=True)
class Rendered:
    """One answer of the worker. ``fault`` is ``""`` on success, else a code of
    :data:`FAULTS`. ``url`` is the final URL that the worker reports. The
    caller parses it before it shows it."""

    markdown: str = ""
    title: str = ""
    url: str = ""
    fault: str = ""


def _config() -> tuple[str, str]:
    """``(base URL, token)`` from the settings, read now. A fault gives none."""
    try:
        from acb_common import get_settings

        settings = get_settings()
        base = str(getattr(settings, "web_worker_url", "") or "").strip()
        token = str(getattr(settings, "web_worker_token", "") or "").strip()
    except Exception:  # never call the worker after a fault of the settings
        return "", ""
    return base, token


def ready() -> bool:
    """True when the operator set both the worker URL and its token."""
    base, token = _config()
    return bool(base and token)


def _take_slot(org: str) -> bool:
    """One render slot for ``org``, or False. It never waits."""
    with _SLOT_LOCK:
        if sum(_SLOTS_IN_USE.values()) >= MAX_RENDERS or _SLOTS_IN_USE[org] >= MAX_RENDERS_PER_ORG:
            return False
        _SLOTS_IN_USE[org] += 1
        return True


def _give_slot(org: str) -> None:
    with _SLOT_LOCK:
        _SLOTS_IN_USE[org] -= 1
        if _SLOTS_IN_USE[org] <= 0:
            del _SLOTS_IN_USE[org]


def _transport() -> httpx.AsyncBaseTransport:
    """The transport that opens the socket to the worker. A test replaces it."""
    return httpx.AsyncHTTPTransport(retries=0)


def _client() -> httpx.AsyncClient:
    """The client of one render: no redirect, no proxy from the env, no cookie."""
    return httpx.AsyncClient(
        transport=_transport(),
        follow_redirects=False,
        trust_env=False,
        timeout=httpx.Timeout(TOTAL_TIMEOUT_S, connect=CONNECT_TIMEOUT_S),
    )


def _fault(code: str, status: int = 0) -> Rendered:
    """The one log line of a failed render. It holds no URL and no token."""
    with contextlib.suppress(Exception):  # a log fault is no render fault
        _log.warning("web_render.fault", fault=code, status=status)
    return Rendered(fault=code)


def _encoded(resp: httpx.Response) -> bool:
    """True when the answer is encoded. The client asks for no encoding, so it
    decodes nothing, and an encoded answer is a fault."""
    return resp.headers.get("content-encoding", "").strip().lower() not in ("", "identity")


async def _read_capped(resp: httpx.Response) -> bytes | None:
    """The raw answer, or None past :data:`MAX_ANSWER_BYTES`."""
    buf = bytearray()
    async for chunk in resp.aiter_raw():
        buf += chunk
        if len(buf) > MAX_ANSWER_BYTES:
            return None
    return bytes(buf)


def _error_fault(status: int, body: bytes) -> str:
    """The fault code of a worker answer that is not 200."""
    if status == 504:
        return TIMEOUT
    if status == 429:
        return BUSY
    if status != 422:
        return UNAVAILABLE
    try:
        item = json.loads(body)
    except ValueError:
        return UNAVAILABLE
    if not isinstance(item, dict) or item.get("error") != "refused":
        return UNAVAILABLE
    reason = item.get("reason")
    if reason == TOO_LARGE:
        return TOO_LARGE
    if reason == BAD_TEXT:
        return BAD_TEXT
    return REFUSED


def _success(body: bytes) -> Rendered:
    """The Markdown, the title and the final URL of a 200 answer."""
    try:
        item = json.loads(body)
    except ValueError:
        return _fault(UNAVAILABLE, 200)
    if not isinstance(item, dict):
        return _fault(UNAVAILABLE, 200)
    markdown, title, url = item.get("markdown", ""), item.get("title", ""), item.get("url", "")
    if not all(isinstance(field, str) for field in (markdown, title, url)):
        return _fault(UNAVAILABLE, 200)
    if not markdown.strip():
        return _fault(EMPTY, 200)
    return Rendered(markdown=markdown.strip(), title=title[:MAX_TITLE_CHARS],
                    url=url if len(url) <= MAX_URL_CHARS else "")


async def render_page(url: str, org: str) -> Rendered:
    """Ask the worker to render ``url`` for ``org``. It never raises.

    ``url`` passed the guard of ``fetch_page`` first. ``org`` is the org of the
    run binding. An empty org, an empty setting or no slot sends nothing."""
    base, token = _config()
    org_key = str(org or "").strip()
    if not base or not token or not org_key:
        return Rendered(fault=NOT_READY)
    slot = org_key.lower()
    if not _take_slot(slot):
        return _fault(BUSY)
    status = 0
    try:
        payload = {"url": url, "org_id": org_key, "job_id": uuid.uuid4().hex}
        headers = {"Authorization": f"Bearer {token}", "Accept": "application/json",
                   "Accept-Encoding": "identity"}
        async with (
            asyncio.timeout(TOTAL_TIMEOUT_S),
            _client() as client,
            client.stream("POST", base.rstrip("/") + "/render", json=payload,
                          headers=headers) as resp,
        ):
            status = resp.status_code
            if _encoded(resp):
                return _fault(UNAVAILABLE, status)
            body = await _read_capped(resp)
        if body is None:
            return _fault(TOO_LARGE if status == 200 else UNAVAILABLE, status)
        if status != 200:
            return _fault(_error_fault(status, body), status)
        return _success(body)
    except (TimeoutError, httpx.TimeoutException):
        return _fault(TIMEOUT, status)
    except Exception:  # the worker is never a raise of the tool, and no text of it leaks
        return _fault(UNAVAILABLE, status)
    finally:
        _give_slot(slot)
