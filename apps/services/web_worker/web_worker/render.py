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
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from typing import Any

from web_worker.egress import EgressLog, route_handler
from web_worker.fetch import AddressChecker
from web_worker.jobs import Job
from web_worker.limits import JOB_TIMEOUT_S, MAX_MARKDOWN_BYTES, MAX_SESSIONS

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
)

#: The ONE pool of Markdown work. A job that times out stops waiting, and its
#: thread can still run. The pool has one thread for each job slot, so such
#: threads never pass MAX_SESSIONS, and later work waits in the queue.
MARKDOWN_POOL = ThreadPoolExecutor(max_workers=MAX_SESSIONS, thread_name_prefix="web-worker-md")

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
        checker = AddressChecker()
        reason = await checker.refusal(url)
        if reason is not None:
            raise PageRefused(reason)
        record = EgressLog()
        context = await self._browser.new_context(
            service_workers="block",
            accept_downloads=False,
        )
        try:
            await context.route(ALL, route_handler(record, checker))
            await context.route_web_socket(ALL, _close_socket)
            page = await context.new_page()
            try:
                await page.goto(url, wait_until="load", timeout=JOB_TIMEOUT_S * 1000)
            except Exception:
                # A refused hop of the top document is a refusal, not a fault.
                if record.top_refused is not None:
                    raise PageRefused(record.top_refused) from None
                raise
            html = await page.content()
            final_url = record.top_url or page.url
            title = await page.title()
        finally:
            await context.close()
        (job.dir / "page.html").write_text(html, encoding="utf-8")
        markdown = cut(await asyncio.get_running_loop().run_in_executor(
            MARKDOWN_POOL, to_markdown, html, final_url))
        (job.dir / "page.md").write_text(markdown, encoding="utf-8")
        return Rendered(markdown, final_url, title, record)
