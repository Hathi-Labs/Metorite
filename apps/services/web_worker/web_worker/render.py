"""The render of one page: Playwright Chromium, then Markdown (WEB-6).

One Chromium process serves the container. Each job gets a NEW browser
context, so no job shares a cookie, a cache or a profile with another job.
The context closes when the job ends, also when the job fails.

Each request of the context passes ``egress.route_handler``, which fetches it
through the pinned fetch and fulfills it. Service workers
are blocked, because a service worker request can go around the route. Each
WebSocket is routed and closed, and no WebSocket reaches a server.

Playwright and Crawl4AI load only in the container. A test imports this
module with neither one installed.
"""

from __future__ import annotations

import asyncio
import contextlib
import re
from collections.abc import AsyncIterator, Awaitable, Callable
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from typing import Any

from web_worker.egress import EgressLog, route_handler
from web_worker.fetch import JobScope, Refused, parse_hop, port_of
from web_worker.jobs import Job
from web_worker.limits import JOB_TIMEOUT_S, MAX_DOCUMENT_BYTES, MAX_MARKDOWN_BYTES, MAX_SESSIONS

#: The JavaScript heap of one renderer, in MiB.
RENDERER_HEAP_MB = 512

#: Chromium flags. The route fulfills each request, so Chromium itself needs
#: no network. The proxy address is one where nothing listens, also for a
#: loopback address. So a connection that no route sees (a preconnect, a
#: favicon) goes nowhere, and Chromium resolves no host. WebRTC gets no
#: interface that is not proxied, so it gets no UDP.
DEAD_PROXY = "http://127.0.0.1:9"
CHROMIUM_ARGS = (
    "--disable-dev-shm-usage",
    f"--proxy-server={DEAD_PROXY}",
    "--proxy-bypass-list=<-loopback>",
    "--force-webrtc-ip-handling-policy=disable_non_proxied_udp",
    "--webrtc-ip-handling-policy=disable_non_proxied_udp",
    "--dns-prefetch-disable",
    "--disable-background-networking",
    # A cap on the JavaScript heap of each renderer. A page that builds a
    # huge string crashes its own renderer, and the job fails alone.
    f"--js-flags=--max-old-space-size={RENDERER_HEAP_MB}",
)

#: The ONE pool of Markdown work. A job that times out stops waiting, and its
#: thread can still run. The pool has one thread for each job slot, so such
#: threads never pass MAX_SESSIONS, and later work waits in the queue.
MARKDOWN_POOL = ThreadPoolExecutor(max_workers=MAX_SESSIONS, thread_name_prefix="web-worker-md")

#: The name of the isolated world that reads the page. Page scripts run in
#: the main world, and they cannot see or patch the objects of this world.
READ_WORLD = "web-worker-read"

#: One snapshot of the document, in the isolated world. The renderer keeps
#: the string in a global of that world, and Python reads it in slices. The
#: title is cut here, so a huge title never leaves the renderer.
SNAPSHOT_JS = """(() => {
  const well = (t) => (typeof t.toWellFormed === 'function')
    ? t.toWellFormed() : t.replace(/[\\uD800-\\uDFFF]/g, '\\uFFFD');
  const d = document;
  const html = well((d.doctype ? '<!DOCTYPE ' + d.doctype.name + '>' : '')
    + (d.documentElement ? d.documentElement.outerHTML : ''));
  globalThis.__webWorkerDoc = html;
  return {length: html.length, title: well(well(String(d.title || '')).slice(0, 1000))};
})()"""

#: UTF-16 units of one slice. A slice is at most 3 times this in UTF-8.
READ_SLICE = 256 * 1024

#: One slice of the snapshot, from unit ``{start}`` to at most ``{end}``.
#: When the unit before the end is a high surrogate, the slice ends one unit
#: earlier, so it never cuts a surrogate pair. It answers ``[end, text]``.
SLICE_JS = """(() => {{
  const d = globalThis.__webWorkerDoc;
  let e = Math.min({end}, d.length);
  if (e < d.length && e > {start} + 1) {{
    const c = d.charCodeAt(e - 1);
    if (c >= 0xD800 && c <= 0xDBFF) e -= 1;
  }}
  return [e, d.slice({start}, e)];
}})()"""

DROP_JS = "delete globalThis.__webWorkerDoc"

Evaluate = Callable[[str], Awaitable[Any]]


def _utf8_size(text: str) -> int:
    """The UTF-8 size of ``text``. A lone surrogate is ``bad text``."""
    try:
        return len(text.encode("utf-8"))
    except UnicodeError:
        raise PageRefused("bad text") from None


async def read_document(evaluate: Evaluate, cap: int = MAX_DOCUMENT_BYTES) -> tuple[str, str]:
    """The HTML and the title of the page, read in the isolated world.

    The snapshot makes the HTML and the title well formed (``toWellFormed``),
    so a lone surrogate becomes U+FFFD. It gives the length in UTF-16 units.
    A UTF-8 byte count is at least that, so a longer snapshot gets ``too
    large`` and no slice is read. Else Python reads slices of READ_SLICE
    units, and no slice cuts a surrogate pair. It stops when the UTF-8 bytes
    pass ``cap``. So Python never holds more than ``cap`` and one slice. A
    lone surrogate that still arrives is ``bad text``, never a fault.

    It never asks the main world for the HTML, because the page controls
    that world and can make its answer unbounded."""
    try:
        snap = await evaluate(SNAPSHOT_JS)
        length = int(snap["length"])
        title = str(snap.get("title", ""))[:1000]
        _utf8_size(title)
        if length > cap:
            raise PageRefused("too large")
        parts: list[str] = []
        size = 0
        start = 0
        while start < length:
            answer = await evaluate(SLICE_JS.format(start=start, end=start + READ_SLICE))
            if not isinstance(answer, list) or len(answer) != 2:
                raise PageRefused("unreadable")
            end, part = int(answer[0]), answer[1]
            if not isinstance(part, str) or not start < end <= start + READ_SLICE:
                raise PageRefused("unreadable")
            size += _utf8_size(part)
            if size > cap:
                raise PageRefused("too large")
            parts.append(part)
            start = end
        return "".join(parts), title
    finally:
        with contextlib.suppress(Exception):
            await evaluate(DROP_JS)


@contextlib.asynccontextmanager
async def isolated_world(context: Any, page: Any) -> AsyncIterator[Evaluate]:
    """An evaluate in a new isolated world of the main frame, over CDP. The
    CDP session detaches when the block ends."""
    session = await context.new_cdp_session(page)
    try:
        tree = await session.send("Page.getFrameTree")
        world = await session.send("Page.createIsolatedWorld", {
            "frameId": tree["frameTree"]["frame"]["id"],
            "worldName": READ_WORLD,
            "grantUniveralAccess": False,
        })
        context_id = world["executionContextId"]

        async def evaluate(expression: str) -> Any:
            answer = await session.send("Runtime.evaluate", {
                "expression": expression,
                "contextId": context_id,
                "returnByValue": True,
            })
            if "exceptionDetails" in answer:
                raise PageRefused("unreadable")
            return answer["result"].get("value")

        yield evaluate
    finally:
        with contextlib.suppress(Exception):
            await session.detach()

#: Every URL, for ``context.route`` and ``context.route_web_socket``.
ALL = re.compile(r".*")


class PageRefused(Exception):
    """The top URL failed the egress check."""

    def __init__(self, reason: str) -> None:
        self.reason = reason
        super().__init__(reason)


@dataclass
class Rendered:
    markdown: str
    final_url: str
    title: str
    egress: EgressLog


def to_markdown(html: str, base_url: str) -> str:
    """HTML to Markdown with Crawl4AI's generator. CPU work: call it from a
    thread."""
    from crawl4ai.markdown_generation_strategy import DefaultMarkdownGenerator

    result = DefaultMarkdownGenerator().generate_markdown(input_html=html, base_url=base_url)
    return result.raw_markdown


def cut(markdown: str) -> str:
    raw = markdown.encode("utf-8")
    if len(raw) <= MAX_MARKDOWN_BYTES:
        return markdown
    return raw[:MAX_MARKDOWN_BYTES].decode("utf-8", errors="ignore")


async def _close_socket(ws: Any) -> None:
    with contextlib.suppress(Exception):
        await ws.close()


class ChromiumRenderer:
    """Owns the one Chromium process. ``start`` and ``stop`` run in the
    lifespan of the app."""

    def __init__(self) -> None:
        self._pw: Any = None
        self._browser: Any = None

    async def start(self) -> None:
        from playwright.async_api import async_playwright

        self._pw = await async_playwright().start()
        self._browser = await self._pw.chromium.launch(headless=True, args=list(CHROMIUM_ARGS))
        # Load the Markdown generator now, so the first job does not pay for it.
        await asyncio.get_running_loop().run_in_executor(
            MARKDOWN_POOL, to_markdown, "<p>warm</p>", "https://example.com/")

    async def stop(self) -> None:
        if self._browser is not None:
            await self._browser.close()
        if self._pw is not None:
            await self._pw.stop()

    async def __call__(self, url: str, job: Job) -> Rendered:
        try:
            top_port = port_of(parse_hop(url))
        except Refused as exc:
            raise PageRefused(exc.reason) from None
        scope = JobScope(org=job.org_id, top_port=top_port)
        try:
            reason = await scope.checker.refusal(url, top_port)
            if reason is not None:
                raise PageRefused(reason)
            record = EgressLog()
            context = await self._browser.new_context(
                service_workers="block",
                accept_downloads=False,
            )
            try:
                html, final_url, title = await self._load(context, url, record, scope)
            finally:
                scope.close()
                await context.close()
        finally:
            scope.close()
        (job.dir / "page.html").write_text(html, encoding="utf-8")
        markdown = cut(await asyncio.get_running_loop().run_in_executor(
            MARKDOWN_POOL, to_markdown, html, final_url))
        (job.dir / "page.md").write_text(markdown, encoding="utf-8")
        return Rendered(markdown, final_url, title, record)

    async def _load(self, context: Any, url: str, record: EgressLog,
                    scope: JobScope) -> tuple[str, str, str]:
        await context.route(ALL, route_handler(record, scope))
        await context.route_web_socket(ALL, _close_socket)
        page = await context.new_page()
        try:
            await page.goto(url, wait_until="load", timeout=JOB_TIMEOUT_S * 1000)
        except Exception:
            # A refused hop of the top document is a refusal, not a fault.
            if record.top_refused is not None:
                raise PageRefused(record.top_refused) from None
            raise
        # A script can grow the DOM past the cap of the answer, and it can
        # patch what the main world sees. So the read runs in an isolated
        # world, in slices, and never through the main world.
        async with isolated_world(context, page) as evaluate:
            html, title = await read_document(evaluate)
        return html, record.top_url or page.url, title
