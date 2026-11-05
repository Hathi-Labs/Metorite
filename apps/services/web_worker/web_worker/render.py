"""The render of one page: Playwright Chromium, then Markdown (WEB-6).

One Chromium process serves the container. Each job gets a NEW browser
context, so no job shares a cookie, a cache or a profile with another job.
The context closes when the job ends, also when the job fails.

Each request of the context passes ``egress.route_handler``. Service workers
are blocked, because a service worker request can go around the route. Each
WebSocket is routed and closed, and no WebSocket reaches a server.

Playwright and Crawl4AI load only in the container. A test imports this
module with neither one installed.
"""

from __future__ import annotations

import asyncio
import contextlib
import re
from dataclasses import dataclass
from typing import Any

from web_worker.egress import EgressLog, route_handler, target_refusal
from web_worker.jobs import Job
from web_worker.limits import JOB_TIMEOUT_S, MAX_MARKDOWN_BYTES

#: Chromium flags. WebRTC sends UDP that no route sees, so it gets no
#: interface that is not proxied. The worker sets no proxy, so WebRTC gets
#: no UDP at all.
CHROMIUM_ARGS = (
    "--disable-dev-shm-usage",
    "--force-webrtc-ip-handling-policy=disable_non_proxied_udp",
    "--webrtc-ip-handling-policy=disable_non_proxied_udp",
    "--dns-prefetch-disable",
    "--disable-background-networking",
)

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
        await asyncio.to_thread(to_markdown, "<p>warm</p>", "https://example.com/")

    async def stop(self) -> None:
        if self._browser is not None:
            await self._browser.close()
        if self._pw is not None:
            await self._pw.stop()

    async def __call__(self, url: str, job: Job) -> Rendered:
        reason = await asyncio.to_thread(target_refusal, url)
        if reason is not None:
            raise PageRefused(reason)
        record = EgressLog()
        context = await self._browser.new_context(
            service_workers="block",
            accept_downloads=False,
        )
        try:
            await context.route(ALL, route_handler(record, context.request))
            await context.route_web_socket(ALL, _close_socket)
            page = await context.new_page()
            await page.goto(url, wait_until="load", timeout=JOB_TIMEOUT_S * 1000)
            html = await page.content()
            final_url = record.top_url or page.url
            title = await page.title()
        finally:
            await context.close()
        (job.dir / "page.html").write_text(html, encoding="utf-8")
        markdown = cut(await asyncio.to_thread(to_markdown, html, final_url))
        (job.dir / "page.md").write_text(markdown, encoding="utf-8")
        return Rendered(markdown, final_url, title, record)
