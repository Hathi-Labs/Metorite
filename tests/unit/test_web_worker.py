"""WEB-6: the web worker container (``web_research.md`` section 5.1).

The fence row of section 14: the worker refuses a private target (top URL,
redirect, subresource), runs as non-root, and keeps one org's job files from
another. These tests need no Chromium. A fake route and a fake request drive
the real route handler, and the real pinned fetch runs over an
``httpx.MockTransport``. The real address rule runs, and only the resolver of
named hosts is a fake. A literal address resolves for real.

The Chromium half (a real page, a real redirect chain, a real subresource and
a rebinding host) runs in the image: see the "As built, WEB-6" note.
"""

from __future__ import annotations

import asyncio
import functools
import gzip
import re
import sys
import threading
import time
from collections.abc import AsyncIterator, Callable
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import Any

import httpx
import pytest

REPO = Path(__file__).resolve().parents[2]
WORKER = REPO / "apps" / "services" / "web_worker"
sys.path.insert(0, str(WORKER))

from acb_common import public_address  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402
from web_worker import egress, fetch, jobs, limits, main, render  # noqa: E402

yaml = pytest.importorskip("yaml")

TOKEN = "t" * 40
AUTH = {"Authorization": f"Bearer {TOKEN}"}

#: The private targets that the acceptance names, and more.
PRIVATE = (
    "http://127.0.0.1/",
    "http://10.0.0.1/",
    "http://10.255.3.4:8080/x",
    "http://169.254.169.254/latest/meta-data/",
    "http://[::1]/",
    "http://[::ffff:127.0.0.1]/",
    "http://localhost/",
    "http://172.18.0.5:5432/",
    "http://postgres:5432/",
)
#: URLs that two parsers can read as two hosts, or that are not http.
BAD_URLS = (
    "http://169.254.169.254\\@example.com/latest/meta-data/",
    "http://127.0.0.1\\@public.example/",
    "http://public.example\\@127.0.0.1/",
    "http://user@public.example/",
    "http://user:pw@public.example/",
    "http://169.254.169.254@public.example/",
    "http://public.example/\x00x",
    "http://public.example/a b",
    "http://public.example\t/",
    "file:///etc/passwd",
    "ftp://public.example/",
    "data:text/html,hi",
    "javascript:alert(1)",
)

#: The fake DNS of named hosts. ``postgres`` is a compose service address.
_NAMES: dict[str, list[str]] = {
    "public.example": ["93.184.215.14"],
    "example.com": ["93.184.215.14"],
    "other.example": ["93.184.215.15"],
    "localhost": ["127.0.0.1", "::1"],
    "postgres": ["172.18.0.5"],
    "rebind.example": ["93.184.215.14", "10.0.0.7"],
}
PUBLIC_IPS = {"93.184.215.14", "93.184.215.15"}


@pytest.fixture(autouse=True)
def _resolver(monkeypatch: pytest.MonkeyPatch) -> None:
    """Named hosts resolve from a table. A literal address resolves for real."""
    real = public_address.resolve

    def fake(host: str, port: int) -> list[str]:
        if host in _NAMES:
            return list(_NAMES[host])
        if re.fullmatch(r"[0-9.]+|[0-9a-fA-F:.]+", host):
            return real(host, port)
        raise OSError("no such host")

    monkeypatch.setattr(public_address, "resolve", fake)


# ── A fake web, over httpx.MockTransport ────────────────────────────────────


class FakeWeb:
    """Answers by (pinned address, Host, path). Each request is recorded, so a
    test can show that no request reached a private address."""

    def __init__(self, pages: dict[str, httpx.Response | Callable[[], httpx.Response]]) -> None:
        self.pages = pages
        self.seen: list[tuple[str, str, str, httpx.Request]] = []

    def handler(self, request: httpx.Request) -> httpx.Response:
        ip = request.url.host
        name = request.headers["host"]
        self.seen.append((ip, name, request.url.path, request))
        assert ip in PUBLIC_IPS, f"a request reached {ip}"
        page = self.pages.get(f"{name}{request.url.path}")
        if page is None:
            return httpx.Response(404, content=b"nope")
        return page() if callable(page) else page

    def transport(self, target: fetch.Target) -> httpx.AsyncBaseTransport:
        return fetch.PinnedTransport(target, inner=StreamingTransport(self))

    def fetcher(self) -> Callable[..., Any]:
        return functools.partial(fetch.pinned_fetch, transport=self.transport)


class StreamingTransport(httpx.AsyncBaseTransport):
    """Like ``httpx.MockTransport``, but it does not read the body first, so
    the cap of the fetch meets the stream as a real socket gives it."""

    def __init__(self, web: FakeWeb) -> None:
        self.web = web

    async def handle_async_request(self, request: httpx.Request) -> httpx.Response:
        resp = self.web.handler(request)
        if not resp.is_stream_consumed:
            return resp
        # httpx reads a bytes body at once. Give its raw bytes as a stream.
        raw = b"".join([chunk async for chunk in resp.stream])

        async def body() -> AsyncIterator[bytes]:
            yield raw

        return httpx.Response(resp.status_code, headers=resp.headers, content=body())


def redirect(status: int, location: str) -> httpx.Response:
    return httpx.Response(status, headers={"location": location})


def ok(body: bytes = b"<p>hi</p>", **headers: str) -> httpx.Response:
    return httpx.Response(200, headers={"content-type": "text/html", **headers}, content=body)


# ── Fakes of Playwright ─────────────────────────────────────────────────────


class FakeFrame:
    parent_frame = None


class FakeRequest:
    def __init__(self, url: str, resource_type: str = "document", method: str = "GET",
                 body: bytes | None = None) -> None:
        self.url = url
        self.resource_type = resource_type
        self.method = method
        self.post_data_buffer = body
        self.frame = FakeFrame()

    async def all_headers(self) -> dict[str, str]:
        return {"user-agent": "x", "cookie": "sid=1", "host": "ignored",
                "accept-encoding": "br, zstd"}

    def is_navigation_request(self) -> bool:
        return self.resource_type == "document"


class FakeRoute:
    def __init__(self) -> None:
        self.aborted: str | None = None
        self.fulfilled: dict[str, Any] | None = None

    async def abort(self, code: str) -> None:
        self.aborted = code

    async def fulfill(self, **kwargs: Any) -> None:
        self.fulfilled = kwargs


def _route(web: FakeWeb, request: FakeRequest, record: egress.EgressLog | None = None,
           scope: fetch.JobScope | None = None) -> tuple[FakeRoute, egress.EgressLog]:
    record = record or egress.EgressLog()
    route = FakeRoute()
    scope = scope or fetch.JobScope()
    handler = egress.route_handler(record, scope, fetch=web.fetcher())
    asyncio.run(handler(route, request))
    return route, record


# ── Strict parse of each hop (P0) ───────────────────────────────────────────


@pytest.mark.parametrize("url", BAD_URLS)
def test_a_hop_url_that_two_parsers_can_read_two_ways_is_refused(url: str) -> None:
    with pytest.raises(fetch.Refused):
        fetch.parse_hop(url)


def test_the_backslash_url_is_the_case_of_the_review() -> None:
    """httpx and urlsplit read ``example.com``, and WHATWG reads the address.
    So the parse refuses the backslash before any parser decides."""
    url = "http://169.254.169.254\\@example.com/latest/meta-data/"
    assert httpx.URL(url).host == "example.com"
    with pytest.raises(fetch.Refused, match="bad url"):
        fetch.parse_hop(url)


def test_a_clean_url_parses() -> None:
    assert fetch.parse_hop("https://public.example/a?b=1").host == "public.example"
    assert fetch.parse_hop("http://[::1]:8080/").host == "::1"


# ── The check ───────────────────────────────────────────────────────────────


def _refusal(url: str) -> str | None:
    return asyncio.run(fetch.AddressChecker().refusal(url))


@pytest.mark.parametrize("url", PRIVATE)
def test_the_check_refuses_a_private_target(url: str) -> None:
    assert _refusal(url) is not None, url


def test_the_check_passes_a_public_host() -> None:
    assert _refusal("https://public.example/a?b=1") is None


def test_one_private_address_refuses_the_host() -> None:
    assert _refusal("https://rebind.example/") == "not public"


def test_the_check_fails_closed(monkeypatch: pytest.MonkeyPatch) -> None:
    def boom(host: str, port: int) -> list[str]:
        raise RuntimeError("resolver fault")

    monkeypatch.setattr(public_address, "resolve", boom)
    assert _refusal("https://public.example/") == "check failed"
    assert _refusal("http://public.example:99999/") is not None


def test_the_check_is_the_one_address_rule() -> None:
    text = (WORKER / "web_worker" / "fetch.py").read_text(encoding="utf-8")
    assert "from acb_common.public_address import PublicAddressRefused, pinned_public_address" in text
    assert "ipaddress" not in text and "is_private" not in text
    assert not list(WORKER.rglob("public_address.py")), "never copy public_address"


# ── The pin: DNS rebinding (P1) ─────────────────────────────────────────────


def test_the_fetch_connects_to_the_address_that_passed(monkeypatch: pytest.MonkeyPatch) -> None:
    """The first answer is public and each later answer is private. The fetch
    resolves once and connects to the first answer, so the private one never
    reaches a socket. Host and the TLS name stay the name."""
    calls: list[str] = []

    def rebinding(host: str, port: int) -> list[str]:
        calls.append(host)
        return ["93.184.215.14"] if len(calls) == 1 else ["10.0.0.7"]

    monkeypatch.setattr(public_address, "resolve", rebinding)
    web = FakeWeb({"rebinder.example/": ok()})
    route, _ = _route(web, FakeRequest("https://rebinder.example/"))
    assert route.fulfilled is not None and route.aborted is None
    assert calls == ["rebinder.example"]
    ip, name, _, req = web.seen[0]
    assert (ip, name) == ("93.184.215.14", "rebinder.example")
    assert req.extensions.get("sni_hostname") == "rebinder.example"


def test_the_pinned_transport_refuses_another_origin() -> None:
    target = fetch.Target(httpx.URL("https://public.example/"), "public.example", "93.184.215.14")
    transport = fetch.PinnedTransport(target, inner=httpx.MockTransport(lambda r: ok()))

    async def go() -> None:
        async with httpx.AsyncClient(transport=transport) as client:
            await client.get("https://other.example/")

    with pytest.raises(fetch.Refused, match="other origin"):
        asyncio.run(go())


# ── The route: top URL, redirect, subresource ───────────────────────────────


@pytest.mark.parametrize("url", PRIVATE + BAD_URLS)
def test_the_route_aborts_a_private_top_url(url: str) -> None:
    web = FakeWeb({})
    route, record = _route(web, FakeRequest(url, "document"))
    assert route.aborted == egress.ABORT_CODE
    assert route.fulfilled is None and web.seen == []
    assert record.refused


@pytest.mark.parametrize("kind", ["script", "image", "stylesheet", "xhr", "fetch", "font"])
@pytest.mark.parametrize("url", PRIVATE)
def test_the_route_aborts_a_private_subresource(url: str, kind: str) -> None:
    web = FakeWeb({})
    route, _ = _route(web, FakeRequest(url, kind))
    assert route.aborted == egress.ABORT_CODE and web.seen == []


@pytest.mark.parametrize("target", PRIVATE + BAD_URLS)
def test_the_route_aborts_a_redirect_to_a_private_target(target: str) -> None:
    web = FakeWeb({"public.example/start": redirect(302, target)})
    route, record = _route(web, FakeRequest("https://public.example/start"))
    assert route.aborted == egress.ABORT_CODE and route.fulfilled is None
    assert [p for _, _, p, _ in web.seen] == ["/start"], "the route fetched a private hop"
    assert record.refused


@pytest.mark.parametrize("target", PRIVATE + BAD_URLS[:3])
def test_the_route_aborts_a_private_target_late_in_a_chain(target: str) -> None:
    web = FakeWeb({
        "public.example/one": redirect(301, "/two"),
        "public.example/two": redirect(302, "https://other.example/three"),
        "other.example/three": redirect(307, target),
    })
    route, _ = _route(web, FakeRequest("https://public.example/one", "script"))
    assert route.aborted == egress.ABORT_CODE
    assert [(n, p) for _, n, p, _ in web.seen] == [
        ("public.example", "/one"), ("public.example", "/two"), ("other.example", "/three")]


def test_the_route_follows_a_public_chain_and_hides_the_3xx() -> None:
    web = FakeWeb({
        "public.example/form": redirect(303, "https://other.example/end"),
        "other.example/end": ok(b"<p>end</p>", **{"set-cookie": "a=1"}),
    })
    route, record = _route(web, FakeRequest("http://public.example/form", "document",
                                            method="POST", body=b"x=1"))
    assert route.aborted is None
    assert route.fulfilled["status"] == 200 and route.fulfilled["body"] == b"<p>end</p>"
    first, second = web.seen[0][3], web.seen[1][3]
    assert first.method == "POST" and first.headers["cookie"] == "sid=1"
    assert second.method == "GET" and "cookie" not in second.headers
    assert first.headers["accept-encoding"] == fetch.ACCEPT_ENCODING
    assert record.top_url == "https://other.example/end"


def test_the_route_stops_after_the_redirect_limit() -> None:
    pages = {f"public.example/{i}": redirect(302, f"/{i + 1}") for i in range(20)}
    web = FakeWeb(pages)
    route, _ = _route(web, FakeRequest("https://public.example/0", "script"))
    assert route.aborted == egress.ABORT_CODE
    assert len(web.seen) == limits.MAX_REDIRECTS + 1


def test_the_route_stops_after_the_page_limit() -> None:
    web = FakeWeb({f"public.example/{i}": ok() for i in range(30)})
    record = egress.EgressLog()
    scope = fetch.JobScope()
    results = [_route(web, FakeRequest(f"https://public.example/{i}"), record, scope)[0].aborted
               for i in range(limits.MAX_PAGES_PER_JOB + 1)]
    assert results[:-1] == [None] * limits.MAX_PAGES_PER_JOB
    assert results[-1] == egress.ABORT_CODE


def test_the_route_stops_after_the_request_limit() -> None:
    web = FakeWeb({"public.example/a.js": lambda: ok(b"x")})
    record = egress.EgressLog()
    scope = fetch.JobScope()
    for _ in range(limits.MAX_REQUESTS_PER_JOB):
        assert _route(web, FakeRequest("https://public.example/a.js", "script"),
                      record, scope)[0].aborted is None
    route, _ = _route(web, FakeRequest("https://public.example/a.js", "script"), record, scope)
    assert route.aborted == egress.ABORT_CODE
    assert len(web.seen) == limits.MAX_REQUESTS_PER_JOB


def test_the_route_skips_a_media_stream() -> None:
    web = FakeWeb({})
    route, _ = _route(web, FakeRequest("https://public.example/v.mp4", "media"))
    assert route.aborted == egress.ABORT_CODE and web.seen == []


def test_the_route_fails_closed() -> None:
    def broken(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("down")

    web = FakeWeb({})
    web.handler = broken  # type: ignore[method-assign]
    route, _ = _route(web, FakeRequest("https://public.example/"))
    assert route.aborted == egress.ABORT_CODE and route.fulfilled is None


# ── Size caps (P1) ──────────────────────────────────────────────────────────


class Source:
    """A streaming body that counts the chunks it gave."""

    def __init__(self, chunk: bytes, count: int) -> None:
        self.chunk, self.count, self.given = chunk, count, 0

    async def __aiter__(self) -> AsyncIterator[bytes]:
        for _ in range(self.count):
            self.given += 1
            yield self.chunk


MIB = 1024 * 1024


@pytest.mark.parametrize(("kind", "cap"), [("document", limits.MAX_DOCUMENT_BYTES),
                                           ("script", limits.MAX_SUBRESOURCE_BYTES)])
def test_an_answer_past_its_cap_gets_an_abort(kind: str, cap: int) -> None:
    source = Source(b"a" * MIB, 100)
    web = FakeWeb({"public.example/big": lambda: httpx.Response(200, content=source)})
    route, record = _route(web, FakeRequest("https://public.example/big", kind))
    assert route.aborted == egress.ABORT_CODE and route.fulfilled is None
    assert source.given <= cap // MIB + 1, "the read did not stop at the cap"
    assert record.refused == ["public.example too large"]


def test_an_answer_under_its_cap_is_fulfilled() -> None:
    body = b"a" * (limits.MAX_SUBRESOURCE_BYTES - 1)
    web = FakeWeb({"public.example/ok.js": lambda: httpx.Response(200, content=body)})
    route, _ = _route(web, FakeRequest("https://public.example/ok.js", "script"))
    assert route.fulfilled["body"] == body


def test_a_gzip_bomb_stops_at_the_decoded_cap() -> None:
    bomb = gzip.compress(b"\0" * (64 * MIB))
    assert len(bomb) < limits.MAX_SUBRESOURCE_BYTES // 10
    web = FakeWeb({"public.example/bomb": lambda: httpx.Response(
        200, headers={"content-encoding": "gzip"}, content=Source(bomb, 1))})
    route, _ = _route(web, FakeRequest("https://public.example/bomb", "script"))
    assert route.aborted == egress.ABORT_CODE


def test_a_small_gzip_answer_is_decoded_for_chromium() -> None:
    web = FakeWeb({"public.example/z": lambda: httpx.Response(
        200, headers={"content-encoding": "gzip", "content-type": "text/html"},
        content=gzip.compress(b"<p>z</p>"))})
    route, _ = _route(web, FakeRequest("https://public.example/z"))
    assert route.fulfilled["body"] == b"<p>z</p>"
    assert "content-encoding" not in route.fulfilled["headers"]


def test_an_encoding_it_cannot_bound_gets_an_abort() -> None:
    web = FakeWeb({"public.example/br": lambda: httpx.Response(
        200, headers={"content-encoding": "br"}, content=b"xx")})
    route, _ = _route(web, FakeRequest("https://public.example/br"))
    assert route.aborted == egress.ABORT_CODE


def test_the_read_cap_counts_wire_bytes_too() -> None:
    async def go() -> None:
        resp = httpx.Response(200, content=Source(b"a" * 1024, 10))
        await fetch.read_capped(resp, 4096)

    with pytest.raises(fetch.TooLarge):
        asyncio.run(go())


# ── The pool of address checks (P2) ─────────────────────────────────────────


def test_a_hanging_resolver_does_not_stall_a_second_job() -> None:
    gate = threading.Event()

    def resolve(host: str, port: int) -> str:
        if host.startswith("hang"):
            gate.wait(10)
        return "93.184.215.14"

    pool = ThreadPoolExecutor(max_workers=4)

    async def go() -> tuple[list[str], float, bool]:
        job_a = fetch.AddressChecker(resolve, org="org-a", pool=pool, per_org=2, timeout=0.3)
        job_b = fetch.AddressChecker(resolve, org="org-b", pool=pool, per_org=2, timeout=0.3)
        urls = [fetch.parse_hop(f"https://hang{i}.example/") for i in (1, 2, 1, 2)]
        results = await asyncio.gather(*(job_a.pin(u) for u in urls), return_exceptions=True)
        reasons = [r.reason for r in results if isinstance(r, fetch.Refused)]
        start = time.monotonic()
        await job_b.pin(fetch.parse_hop("https://public.example/"))
        took = time.monotonic() - start
        # The slots of org A come back only when its threads end.
        held = fetch._slots("org-check:org-a:2", 2).locked()
        return reasons, took, held

    try:
        reasons, took, held = asyncio.run(go())
    finally:
        gate.set()
        pool.shutdown(wait=True)
    assert reasons == ["check timeout"] * 4
    assert took < 0.25, f"job B waited {took:.2f} s"
    assert held, "a timed-out check gave its slot back while its thread still ran"


def test_a_job_checks_each_host_once() -> None:
    calls: list[str] = []

    def resolve(host: str, port: int) -> str:
        calls.append(host)
        return "93.184.215.14"

    async def go() -> set[str]:
        checker = fetch.AddressChecker(resolve)
        targets = await asyncio.gather(*(checker.pin(fetch.parse_hop("https://public.example/x"))
                                         for _ in range(20)))
        return {t.ip for t in targets}

    assert asyncio.run(go()) == {"93.184.215.14"}
    assert calls == ["public.example"]


def test_the_check_pool_is_bounded() -> None:
    assert fetch._POOL._max_workers == limits.MAX_SESSIONS * limits.CHECKS_PER_ORG
    assert limits.CHECK_TIMEOUT_S == 5


# ── The renderer wiring ─────────────────────────────────────────────────────


def test_the_renderer_routes_each_request_and_each_socket() -> None:
    text = (WORKER / "web_worker" / "render.py").read_text(encoding="utf-8")
    assert "new_context(" in text and 'service_workers="block"' in text
    assert "context.route(ALL, route_handler(record, scope))" in text
    assert "context.route_web_socket(ALL, _close_socket)" in text
    assert "await context.close()" in text
    assert render.ALL.fullmatch("wss://anything/at/all")


def test_chromium_has_no_network_of_its_own() -> None:
    """Each request is fulfilled by the route. A request that no route sees
    goes to a proxy where nothing listens, also for loopback."""
    args = render.CHROMIUM_ARGS
    assert "--proxy-server=http://127.0.0.1:9" in args
    assert "--proxy-bypass-list=<-loopback>" in args
    assert "--dns-prefetch-disable" in args
    egress_text = (WORKER / "web_worker" / "egress.py").read_text(encoding="utf-8")
    assert "route.fulfill(" in egress_text
    assert "continue_(" not in egress_text and "route.fetch(" not in egress_text


# ── The token ───────────────────────────────────────────────────────────────


class _Rendered:
    def __init__(self, markdown: str = "# Hi") -> None:
        self.markdown = markdown
        self.final_url = "https://public.example/"
        self.title = "Hi"
        self.egress = egress.EgressLog()


async def _fake_renderer(url: str, job: jobs.Job) -> _Rendered:
    reason = await fetch.AddressChecker().refusal(url)
    if reason is not None:
        raise render.PageRefused(reason)
    (job.dir / "page.md").write_text("# Hi", encoding="utf-8")
    return _Rendered()


def _client(tmp_path: Path, renderer: Any = _fake_renderer,
            store: jobs.JobStore | None = None) -> TestClient:
    app = main.create_app(TOKEN, renderer=renderer,
                          store=store or jobs.JobStore(tmp_path / "jobs"))
    return TestClient(app)


@pytest.mark.parametrize("value", ["", "   ", "\n"])
def test_an_empty_token_refuses_to_start(value: str) -> None:
    with pytest.raises(main.EmptyToken):
        main.create_app(value, renderer=_fake_renderer)


def test_no_token_in_the_env_refuses_to_start(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("WEB_WORKER_TOKEN", raising=False)
    with pytest.raises(main.EmptyToken):
        main.create_app(renderer=_fake_renderer)
    monkeypatch.setenv("WEB_WORKER_TOKEN", "")
    with pytest.raises(main.EmptyToken):
        main.create_app(renderer=_fake_renderer)


def test_the_module_builds_no_app_at_import() -> None:
    assert not hasattr(main, "app")
    dockerfile = (WORKER / "Dockerfile").read_text(encoding="utf-8")
    assert '"--factory", "web_worker.main:create_app"' in dockerfile


def test_each_route_needs_the_token(tmp_path: Path) -> None:
    client = _client(tmp_path)
    body = {"url": "https://public.example/", "org_id": "org-a", "job_id": "j1"}
    for headers in ({}, {"Authorization": "Bearer wrong"}, {"Authorization": TOKEN},
                    {"Authorization": f"Bearer {TOKEN}x"}, {"Authorization": "Bearer "}):
        assert client.post("/render", json=body, headers=headers).status_code == 401
    assert client.post("/render", json=body, headers=AUTH).status_code == 200
    assert client.get("/health").json() == {"status": "ok"}


def test_the_token_compare_is_constant_time() -> None:
    text = (WORKER / "web_worker" / "main.py").read_text(encoding="utf-8")
    assert "hmac.compare_digest(" in text
    assert "!= f\"Bearer" not in text and "== f\"Bearer" not in text


def test_no_route_reads_a_job_file(tmp_path: Path) -> None:
    client = _client(tmp_path)
    paths = {getattr(r, "path", "") for r in client.app.routes}
    assert paths == {"/health", "/render"}, paths
    assert client.get("/jobs/j1/files?org_id=org-a", headers=AUTH).status_code == 404


# ── The render API ──────────────────────────────────────────────────────────


def test_render_answers_with_markdown(tmp_path: Path) -> None:
    r = _client(tmp_path).post(
        "/render", json={"url": "https://public.example/", "org_id": "org-a", "job_id": "j1"},
        headers=AUTH)
    assert r.status_code == 200 and r.json()["markdown"] == "# Hi"


@pytest.mark.parametrize("url", PRIVATE + BAD_URLS[:3])
def test_render_refuses_a_private_top_url(tmp_path: Path, url: str) -> None:
    r = _client(tmp_path).post("/render", json={"url": url, "org_id": "org-a", "job_id": "j1"},
                               headers=AUTH)
    assert r.status_code == 422 and r.json()["error"] == "refused"
    assert "/" not in r.json()["host"], "a refusal names the host, never the path"


@pytest.mark.parametrize("org", ["", " ", "../a", "a/b", "a.b", "-a", "x" * 65, "a b", "ä"])
def test_render_refuses_an_odd_org_id(tmp_path: Path, org: str) -> None:
    r = _client(tmp_path).post(
        "/render", json={"url": "https://public.example/", "org_id": org, "job_id": "j1"},
        headers=AUTH)
    assert r.status_code == 422


def test_render_refuses_an_odd_job_id(tmp_path: Path) -> None:
    for job in ("", "../x", "a/b", ".hidden"):
        r = _client(tmp_path).post(
            "/render", json={"url": "https://public.example/", "org_id": "org-a", "job_id": job},
            headers=AUTH)
        assert r.status_code == 422, job


def test_the_job_dir_is_gone_after_the_job(tmp_path: Path) -> None:
    seen: list[Path] = []

    async def renderer(url: str, job: jobs.Job) -> _Rendered:
        assert job.dir.parent == tmp_path / "jobs"
        assert job.dir.stat().st_mode & 0o777 == 0o700
        (job.dir / "page.md").write_text("x", encoding="utf-8")
        seen.append(job.dir)
        return _Rendered()

    body = {"url": "https://public.example/", "org_id": "org-a", "job_id": "j1"}
    assert _client(tmp_path, renderer).post("/render", json=body, headers=AUTH).status_code == 200
    assert seen and not seen[0].exists()
    assert list((tmp_path / "jobs").iterdir()) == []


def test_the_job_dir_is_gone_after_a_failed_job(tmp_path: Path) -> None:
    seen: list[Path] = []

    async def renderer(url: str, job: jobs.Job) -> _Rendered:
        (job.dir / "page.html").write_text("x", encoding="utf-8")
        seen.append(job.dir)
        raise RuntimeError("the page crashed at /secret/path")

    r = _client(tmp_path, renderer).post(
        "/render", json={"url": "https://public.example/secret/path", "org_id": "org-a",
                         "job_id": "j1"}, headers=AUTH)
    assert r.status_code == 502 and "secret" not in r.text
    assert seen and not seen[0].exists()


def test_a_job_past_the_timeout_ends_and_leaves_no_dir(tmp_path: Path,
                                                       monkeypatch: pytest.MonkeyPatch) -> None:
    seen: list[Path] = []

    async def slow(url: str, job: jobs.Job) -> _Rendered:
        seen.append(job.dir)
        await asyncio.sleep(5)
        return _Rendered()

    monkeypatch.setattr(main, "JOB_TIMEOUT_S", 0.05)
    r = _client(tmp_path, slow).post(
        "/render", json={"url": "https://public.example/", "org_id": "org-a", "job_id": "j1"},
        headers=AUTH)
    assert r.status_code == 504
    assert seen and not seen[0].exists()


# ── Org isolation of jobs ───────────────────────────────────────────────────


def test_org_b_cannot_find_a_job_of_org_a(tmp_path: Path) -> None:
    store = jobs.JobStore(tmp_path / "jobs")
    with store.open("org-a", "job-a") as job:
        assert store.lookup("org-a", "job-a") is job
        for org, job_id in (("org-b", "job-a"), ("org-a", "job-x"), ("../org-a", "job-a")):
            with pytest.raises(jobs.JobNotFound):
                store.lookup(org, job_id)
    with pytest.raises(jobs.JobNotFound):
        store.lookup("org-a", "job-a")
    assert not hasattr(store, "files") and not hasattr(store, "read")


def test_a_job_id_of_org_a_never_gives_org_b_a_409(tmp_path: Path) -> None:
    store = jobs.JobStore(tmp_path / "jobs")
    client = _client(tmp_path, store=store)
    with store.open("org-a", "same-id") as a:
        r = client.post("/render", json={"url": "https://public.example/", "org_id": "org-b",
                                         "job_id": "same-id"}, headers=AUTH)
        assert r.status_code == 200, r.text
        r = client.post("/render", json={"url": "https://public.example/", "org_id": "org-a",
                                         "job_id": "same-id"}, headers=AUTH)
        assert r.status_code == 409
        assert a.dir.exists()


def test_two_jobs_get_two_dirs(tmp_path: Path) -> None:
    store = jobs.JobStore(tmp_path / "jobs")
    with store.open("org-a", "ja") as a, store.open("org-b", "jb") as b:
        assert a.dir != b.dir and a.dir.parent == b.dir.parent
        assert (tmp_path / "jobs").stat().st_mode & 0o777 == 0o700


# ── Limits ──────────────────────────────────────────────────────────────────


def test_the_limits_are_the_numbers_of_the_spec() -> None:
    assert limits.MAX_PAGES_PER_JOB == 25
    assert limits.MAX_SESSIONS_PER_ORG == 2
    assert limits.MAX_SESSIONS == 4
    assert limits.JOB_TIMEOUT_S == 60
    assert limits.MEM_LIMIT == "2g" and limits.CPUS == 2
    assert limits.MAX_REQUESTS_PER_JOB == 200
    assert limits.MAX_DOCUMENT_BYTES == 10 * MIB
    assert limits.MAX_SUBRESOURCE_BYTES == 5 * MIB
    spec = (REPO / "project-docs/specs/web_research.md").read_text(encoding="utf-8")
    for row in ("| Pages per job | 25 |", "| Sessions per organization at once | 2 |",
                "| Sessions in the container at once | 4 |", "| Timeout of one job | 60 s |",
                "| Requests per job | 200 |", "| Bytes of one document | 10 MiB |",
                "| Bytes of one subresource | 5 MiB |", "| Time of one address check | 5 s |"):
        assert row in spec, row


def test_the_slots_do_not_wait(tmp_path: Path) -> None:
    store = jobs.JobStore(tmp_path / "jobs")
    with store.open("org-a", "a1"), store.open("org-a", "a2"):
        with pytest.raises(jobs.NoSlot, match="organization"), store.open("org-a", "a3"):
            pass
        with (store.open("org-b", "b1"), store.open("org-c", "c1"),
              pytest.raises(jobs.NoSlot, match="container"), store.open("org-d", "d1")):
            pass
    assert store.running() == 0


def test_a_full_org_gets_429(tmp_path: Path) -> None:
    store = jobs.JobStore(tmp_path / "jobs")
    client = _client(tmp_path, store=store)
    with store.open("org-a", "a1"), store.open("org-a", "a2"):
        r = client.post("/render", json={"url": "https://public.example/", "org_id": "org-a",
                                         "job_id": "a3"}, headers=AUTH)
        assert r.status_code == 429


# ── The image and the compose block ─────────────────────────────────────────


def _dockerfile() -> str:
    return (WORKER / "Dockerfile").read_text(encoding="utf-8")


def _code(text: str) -> list[str]:
    return [ln.strip() for ln in text.splitlines() if ln.strip() and not ln.strip().startswith("#")]


def test_the_image_runs_as_uid_1000() -> None:
    users = [ln for ln in _code(_dockerfile()) if ln.startswith("USER ")]
    assert users and users[-1] == "USER 1000:1000", users


def test_the_image_installs_acb_common_and_copies_no_module() -> None:
    lines = _code(_dockerfile())
    assert lines[0].startswith("FROM mcr.microsoft.com/playwright/python:v")
    assert "COPY packages/acb_common /tmp/build/acb_common" in lines
    assert not [ln for ln in lines if "public_address" in ln]
    text = " ".join(lines)
    assert "--require-hashes" in text and "requirements.lock.txt" in text


def test_the_base_image_matches_playwright() -> None:
    tag = re.search(r"playwright/python:v([0-9.]+)-", _dockerfile()).group(1)
    assert f"playwright=={tag}" in (WORKER / "requirements.txt").read_text(encoding="utf-8")
    assert f"playwright=={tag}" in (WORKER / "requirements.lock.txt").read_text(encoding="utf-8")


def test_the_requirements_hold_no_docling_and_no_mcp() -> None:
    for name in ("requirements.txt", "requirements.lock.txt"):
        text = (WORKER / name).read_text(encoding="utf-8").lower()
        for banned in ("docling", "playwright-mcp", "mcp=="):
            assert banned not in text, (name, banned)


def _compose() -> dict[str, Any]:
    return yaml.safe_load((REPO / "infra/docker-compose.yml").read_text(encoding="utf-8"))


def test_the_compose_block_holds_the_caps() -> None:
    svc = _compose()["services"]["web-worker"]
    assert svc["profiles"] == ["webworker"]
    assert svc["ports"] == ["127.0.0.1:8096:8080"]
    assert svc["mem_limit"] == limits.MEM_LIMIT
    assert svc["cpus"] == limits.CPUS
    assert svc["cap_drop"] == ["ALL"]
    assert "no-new-privileges:true" in svc["security_opt"]
    assert svc["user"] == "1000:1000"
    assert svc["read_only"] is True
    assert any(t.startswith("/tmp:") for t in svc["tmpfs"])
    assert 0 < svc["pids_limit"] <= 4096
    assert svc["environment"]["WEB_WORKER_TOKEN"] == "${WEB_WORKER_TOKEN:-}"
    assert "extra_hosts" not in svc and "network_mode" not in svc
    assert "privileged" not in svc and "cap_add" not in svc
    assert "build" not in svc, "the root copy of the box has no repo root"


def test_the_worker_network_has_no_other_service() -> None:
    compose = _compose()
    assert compose["services"]["web-worker"]["networks"] == ["web_worker_net"]
    for name, svc in compose["services"].items():
        if name != "web-worker":
            assert "web_worker_net" not in (svc or {}).get("networks", []), name
    assert compose["networks"]["web_worker_net"]["driver"] == "bridge"


# ── Review round 2 ──────────────────────────────────────────────────────────


@pytest.mark.parametrize("location", ["http://public.example/b",           # scheme
                                      "https://public.example:8443/b",      # port
                                      "https://other.example/b"])           # host
def test_a_hop_to_another_origin_gets_no_credential(location: str) -> None:
    web = FakeWeb({"public.example/a": redirect(302, location),
                   "public.example/b": ok(), "other.example/b": ok()})

    class WithAuth(FakeRequest):
        async def all_headers(self) -> dict[str, str]:
            return {"cookie": "sid=1", "authorization": "Bearer x",
                    "proxy-authorization": "Basic y", "user-agent": "x"}

    route, _ = _route(web, WithAuth("https://public.example/a", "script"),
                      scope=fetch.JobScope(top_port=8443))
    assert route.fulfilled is not None, route.aborted
    first, second = web.seen[0][3].headers, web.seen[1][3].headers
    assert first["authorization"] == "Bearer x" and first["cookie"] == "sid=1"
    for name in ("cookie", "authorization", "proxy-authorization"):
        assert name not in second, name
    assert second["user-agent"] == "x"


def test_a_same_origin_hop_keeps_the_cookie() -> None:
    web = FakeWeb({"public.example/a": redirect(302, "/b"), "public.example/b": ok()})
    route, _ = _route(web, FakeRequest("https://public.example/a", "script"))
    assert route.fulfilled is not None
    assert web.seen[1][3].headers["cookie"] == "sid=1"


def test_a_refused_hop_of_the_top_document_is_recorded_for_a_422() -> None:
    web = FakeWeb({"public.example/start": redirect(302, "http://169.254.169.254/")})
    route, record = _route(web, FakeRequest("https://public.example/start", "document"))
    assert route.aborted == egress.ABORT_CODE
    assert record.top_refused == "not public"
    web = FakeWeb({})
    _, record = _route(web, FakeRequest("http://10.0.0.1/x.js", "script"))
    assert record.top_refused is None, "a subresource refusal is not a page refusal"
    text = (WORKER / "web_worker" / "render.py").read_text(encoding="utf-8")
    assert "raise PageRefused(record.top_refused)" in text


def test_the_markdown_pool_has_one_thread_for_each_job_slot() -> None:
    assert render.MARKDOWN_POOL._max_workers == limits.MAX_SESSIONS
    text = (WORKER / "web_worker" / "render.py").read_text(encoding="utf-8")
    assert "asyncio.to_thread(to_markdown" not in text
    assert text.count("MARKDOWN_POOL, to_markdown") == 2


# ── Review round 3: the end of a job, the job budget, IDN and ports ────────


class Drip:
    """A body that gives one chunk and then waits a long time (a slow drip)."""

    def __init__(self) -> None:
        self.started = asyncio.Event()
        self.closed = False

    async def __aiter__(self) -> AsyncIterator[bytes]:
        try:
            yield b"<p>"
            self.started.set()
            await asyncio.sleep(30)
            yield b"never"
        finally:
            self.closed = True


def test_a_slow_drip_stops_when_the_job_ends() -> None:
    drip = Drip()
    web = FakeWeb({"public.example/drip": lambda: httpx.Response(200, content=drip)})

    async def go() -> tuple[float, FakeRoute, fetch.JobScope]:
        scope = fetch.JobScope()
        route = FakeRoute()
        handler = egress.route_handler(egress.EgressLog(), scope, fetch=web.fetcher())
        task = asyncio.ensure_future(handler(route, FakeRequest("https://public.example/drip")))
        await asyncio.wait_for(drip.started.wait(), 2)
        start = time.monotonic()
        scope.close()
        await asyncio.wait_for(task, 2)
        return time.monotonic() - start, route, scope

    took, route, scope = asyncio.run(go())
    assert took < 0.5, f"the fetch ran {took:.2f} s after the end of the job"
    assert route.fulfilled is None
    assert scope.busy() == 0 and drip.closed


def test_a_slow_drip_stops_at_the_deadline_of_the_job() -> None:
    web = FakeWeb({"public.example/drip": lambda: httpx.Response(200, content=Drip())})
    start = time.monotonic()
    route, record = _route(web, FakeRequest("https://public.example/drip"),
                           scope=fetch.JobScope(deadline_s=0.3))
    assert time.monotonic() - start < 2
    assert route.aborted == egress.ABORT_CODE
    assert record.refused == ["public.example deadline"]


def test_a_closed_job_starts_no_fetch() -> None:
    web = FakeWeb({"public.example/": ok()})
    scope = fetch.JobScope()
    scope.close()
    route, record = _route(web, FakeRequest("https://public.example/"), scope=scope)
    assert route.aborted == egress.ABORT_CODE and web.seen == []
    assert record.refused == ["public.example job ended"]


def test_a_hung_resolver_of_an_ended_job_does_not_starve_another_org() -> None:
    gate = threading.Event()
    calls: list[str] = []

    def resolve(host: str, port: int) -> str:
        calls.append(host)
        if host.startswith("hang"):
            gate.wait(10)
        return "93.184.215.14"

    pool = ThreadPoolExecutor(max_workers=4)

    async def go() -> tuple[float, int, int]:
        for round_ in range(5):  # five jobs of org A, each ends at once
            checker = fetch.AddressChecker(resolve, org="org-a", pool=pool, per_org=2,
                                           timeout=limits.CHECK_TIMEOUT_S)
            pins = [asyncio.ensure_future(checker.pin(fetch.parse_hop(f"https://hang{round_}-{i}.example/")))
                    for i in range(3)]
            await asyncio.sleep(0.05)
            checker.close()
            _done, pending = await asyncio.wait(pins, timeout=1)
            assert not pending, "a check of an ended job still waits"
        start = time.monotonic()
        org_b = fetch.AddressChecker(resolve, org="org-b", pool=pool, per_org=2,
                                     timeout=limits.CHECK_TIMEOUT_S)
        await org_b.pin(fetch.parse_hop("https://public.example/"))
        took = time.monotonic() - start
        busy = sum(1 for t in asyncio.all_tasks() if t is not asyncio.current_task())
        return took, sum(1 for c in calls if c.startswith("hang")), busy

    try:
        took, hung, busy = asyncio.run(go())
    finally:
        gate.set()
        pool.shutdown(wait=True)
    assert took < 0.5, f"org B waited {took:.2f} s"
    assert hung == 2, f"org A held {hung} threads, the cap is 2"
    assert busy == 0, "a task of an ended job still runs"


def test_a_job_reads_at_most_its_byte_budget() -> None:
    """Twenty gzip bombs of 4 MiB each, on one host, at once."""
    bomb = gzip.compress(b"\0" * (4 * MIB))
    pages = {f"public.example/b{i}": (lambda: httpx.Response(
        200, headers={"content-encoding": "gzip"}, content=Source(bomb, 1))) for i in range(20)}
    web = FakeWeb(pages)

    async def go() -> tuple[list[FakeRoute], fetch.JobScope]:
        scope = fetch.JobScope()
        handler = egress.route_handler(egress.EgressLog(), scope, fetch=web.fetcher())
        routes = [FakeRoute() for _ in range(20)]
        await asyncio.gather(*(handler(r, FakeRequest(f"https://public.example/b{i}", "image"))
                               for i, r in enumerate(routes)))
        return routes, scope

    routes, scope = asyncio.run(go())
    kept = sum(len(r.fulfilled["body"]) for r in routes if r.fulfilled)
    assert kept <= limits.MAX_JOB_BYTES
    assert scope.budget.bytes <= limits.MAX_JOB_BYTES
    # A decode step never passes the bytes that are left, so the job never
    # holds more than the budget and one byte for each read at once.
    assert scope.budget.peak <= limits.MAX_JOB_BYTES + limits.FETCHES_PER_JOB
    assert sum(1 for r in routes if r.aborted) >= 10


def test_a_queued_check_of_a_closed_job_does_nothing() -> None:
    calls: list[str] = []
    checker = fetch.AddressChecker(lambda h, p: calls.append(h) or "93.184.215.14")
    checker.close()
    with pytest.raises(fetch.Refused, match="job ended"):
        checker._run("public.example", 443)
    assert calls == []


def test_a_job_runs_at_most_its_fetches_at_once() -> None:
    live = {"now": 0, "most": 0}

    class Slow:
        async def __aiter__(self) -> AsyncIterator[bytes]:
            live["now"] += 1
            live["most"] = max(live["most"], live["now"])
            await asyncio.sleep(0.05)
            live["now"] -= 1
            yield b"x"

    web = FakeWeb({f"public.example/{i}": (lambda: httpx.Response(200, content=Slow()))
                   for i in range(20)})

    async def go() -> None:
        scope = fetch.JobScope()
        handler = egress.route_handler(egress.EgressLog(), scope, fetch=web.fetcher())
        await asyncio.gather(*(handler(FakeRoute(), FakeRequest(f"https://public.example/{i}", "script"))
                               for i in range(20)))

    asyncio.run(go())
    assert 1 <= live["most"] <= limits.FETCHES_PER_JOB


class FakeWorld:
    """An isolated world over a fake document. It records each expression,
    so a test can show which slices Python asked for."""

    def __init__(self, html: str, title: str = "T") -> None:
        self.html, self.title = html, title
        self.asked: list[str] = []
        self.doc: str | None = None

    async def evaluate(self, expression: str) -> Any:
        self.asked.append(expression)
        if expression == render.SNAPSHOT_JS:
            self.doc = self.html
            return {"length": len(self.html), "title": self.title[:1000]}
        if expression == render.DROP_JS:
            self.doc = None
            return True
        m = re.fullmatch(r"globalThis.__webWorkerDoc.slice\((\d+), (\d+)\)", expression)
        assert m and self.doc is not None, expression
        return self.doc[int(m.group(1)):int(m.group(2))]


def test_the_document_is_read_in_slices_and_dropped() -> None:
    html = "<html><body>" + "x" * (3 * render.READ_SLICE) + "</body></html>"
    world = FakeWorld(html, "Title")
    got, title = asyncio.run(render.read_document(world.evaluate))
    assert got == html and title == "Title"
    slices = [e for e in world.asked if e.startswith("globalThis.__webWorkerDoc.slice(")]
    assert len(slices) == 4
    assert world.asked[-1] == render.DROP_JS and world.doc is None


def test_a_document_past_the_cap_is_never_read() -> None:
    world = FakeWorld("A" * (limits.MAX_DOCUMENT_BYTES + 1))
    with pytest.raises(render.PageRefused, match="too large"):
        asyncio.run(render.read_document(world.evaluate))
    assert not [e for e in world.asked if e.startswith("globalThis.__webWorkerDoc.slice(")], "a slice was read"
    assert world.asked[-1] == render.DROP_JS


def test_a_document_of_wide_letters_stops_at_the_byte_cap() -> None:
    """Under the cap in UTF-16 units, but three times over in UTF-8. Python
    stops after the slice that passes the cap."""
    cap = 4 * render.READ_SLICE
    world = FakeWorld("€" * (cap - 1))
    with pytest.raises(render.PageRefused, match="too large"):
        asyncio.run(render.read_document(world.evaluate, cap))
    slices = [e for e in world.asked if e.startswith("globalThis.__webWorkerDoc.slice(")]
    assert len(slices) == 2, slices


def test_a_huge_title_stays_in_the_renderer() -> None:
    world = FakeWorld("<p>x</p>", "t" * 10_000_000)
    _, title = asyncio.run(render.read_document(world.evaluate))
    assert len(title) == 1000


def test_the_renderer_never_reads_the_page_in_the_main_world() -> None:
    """A page can patch the main world (``outerHTML``) and grow the DOM. The
    read uses an isolated world over CDP, and never ``page.content()``."""
    text = (WORKER / "web_worker" / "render.py").read_text(encoding="utf-8")
    code = "\n".join(ln for ln in text.splitlines() if not ln.strip().startswith("#"))
    assert "page.content(" not in code and "page.evaluate(" not in code
    assert "page.title(" not in code
    assert '"Page.createIsolatedWorld"' in code and '"contextId": context_id' in code
    assert "await read_document(await isolated_evaluator(context, page))" in code
    assert "scope.close()" in code
    assert f"--js-flags=--max-old-space-size={render.RENDERER_HEAP_MB}" in render.CHROMIUM_ARGS


def test_plain_bytes_never_pass_the_job_budget_by_a_chunk() -> None:
    """Identity bodies of 1 MiB chunks, many at once: each step takes at most
    the bytes left, so the job holds the budget and one byte per fetch."""
    pages = {f"public.example/p{i}": (lambda: httpx.Response(200, content=Source(b"a" * MIB, 4)))
             for i in range(20)}
    web = FakeWeb(pages)

    async def go() -> fetch.JobScope:
        scope = fetch.JobScope()
        handler = egress.route_handler(egress.EgressLog(), scope, fetch=web.fetcher())
        await asyncio.gather(*(handler(FakeRoute(), FakeRequest(f"https://public.example/p{i}", "image"))
                               for i in range(20)))
        return scope

    scope = asyncio.run(go())
    assert scope.budget.peak <= limits.MAX_JOB_BYTES + limits.FETCHES_PER_JOB
    assert scope.budget.bytes <= limits.MAX_JOB_BYTES


@pytest.mark.parametrize("url", ["https://xn--mnchen-3ya.de/", "https://münchen.de/",
                                 "https://Public.Example/", "https://WWW.xn--mnchen-3ya.de/a"])
def test_an_idn_or_mixed_case_host_parses(url: str) -> None:
    host = fetch.parse_hop(url).raw_host.decode("ascii")
    assert host.isascii() and host == host.lower()


def test_an_idn_host_fetches_with_its_ascii_name(monkeypatch: pytest.MonkeyPatch) -> None:
    _NAMES["xn--mnchen-3ya.de"] = ["93.184.215.14"]
    try:
        web = FakeWeb({"xn--mnchen-3ya.de/": ok(b"<p>m</p>")})
        route, _ = _route(web, FakeRequest("https://münchen.de/"))
    finally:
        _NAMES.pop("xn--mnchen-3ya.de")
    assert route.fulfilled is not None and route.fulfilled["body"] == b"<p>m</p>"
    assert web.seen[0][3].extensions["sni_hostname"] == "xn--mnchen-3ya.de"


@pytest.mark.parametrize("url", ["https://public.example:25/", "http://public.example:6379/",
                                 "http://public.example:22/", "http://public.example:5432/"])
def test_an_unsafe_port_is_refused_as_the_top_url(url: str) -> None:
    assert _refusal(url) == "port"
    web = FakeWeb({})
    route, _ = _route(web, FakeRequest(url, "document"),
                      scope=fetch.JobScope(top_port=fetch.port_of(httpx.URL(url))))
    assert route.aborted == egress.ABORT_CODE and web.seen == []


@pytest.mark.parametrize("target", ["http://public.example:25/", "http://other.example:6379/",
                                    "https://public.example:8081/", "http://other.example:22/x"])
def test_a_redirect_to_another_port_is_refused(target: str) -> None:
    web = FakeWeb({"public.example/form": httpx.Response(307, headers={"location": target})})
    route, record = _route(web, FakeRequest("https://public.example/form", "document",
                                            method="POST", body=b"MAIL FROM:<x>"))
    assert route.aborted == egress.ABORT_CODE
    assert len(web.seen) == 1
    assert record.refused[-1].endswith(" port")


def test_a_hop_keeps_the_port_of_the_top_url() -> None:
    web = FakeWeb({"public.example/a": redirect(302, "http://public.example:8080/b"),
                   "public.example/b": ok()})
    route, _ = _route(web, FakeRequest("http://public.example:8080/a", "script"),
                      scope=fetch.JobScope(top_port=8080))
    assert route.fulfilled is not None, route.aborted


@pytest.mark.parametrize("wbits", [-15, 15])
def test_deflate_decodes_in_the_raw_and_the_zlib_form(wbits: int) -> None:
    import zlib

    packer = zlib.compressobj(wbits=wbits)
    body = packer.compress(b"<p>deflated</p>") + packer.flush()
    web = FakeWeb({"public.example/d": lambda: httpx.Response(
        200, headers={"content-encoding": "deflate"}, content=body)})
    route, _ = _route(web, FakeRequest("https://public.example/d"))
    assert route.fulfilled["body"] == b"<p>deflated</p>"


def test_a_body_that_does_not_decode_gets_an_abort() -> None:
    web = FakeWeb({"public.example/g": lambda: httpx.Response(
        200, headers={"content-encoding": "gzip"}, content=Source(b"not gzip at all", 1))})
    route, record = _route(web, FakeRequest("https://public.example/g"))
    assert route.aborted == egress.ABORT_CODE
    assert record.refused == ["public.example encoding"]


def test_the_image_bounds_each_dns_query() -> None:
    assert 'RES_OPTIONS="timeout:2 attempts:1"' in _dockerfile()


def test_the_new_limits_are_in_the_spec() -> None:
    assert limits.MAX_JOB_BYTES == 40 * MIB
    assert limits.FETCHES_PER_JOB == 6 and limits.MAX_FETCHES == 16
    assert {25, 6379, 22} <= limits.UNSAFE_PORTS
    spec = (REPO / "project-docs/specs/web_research.md").read_text(encoding="utf-8")
    for row in ("| Bytes of one job | 40 MiB |", "| Fetches of one job at once | 6 |",
                "| Fetches in the container at once | 16 |",
                "| Address checks of one organization at once | 2 |"):
        assert row in spec, row


def test_a_failed_read_frees_its_buffer_at_once() -> None:
    """A failed read must not wait for the garbage collector. A reference
    cycle through the task and the traceback held each buffer (1 GiB in the
    image for 190 bombs)."""
    import gc

    bomb = gzip.compress(b"\0" * (8 * MIB))
    web = FakeWeb({"public.example/b": lambda: httpx.Response(
        200, headers={"content-encoding": "gzip"}, content=Source(bomb, 1))})
    seen: list[Any] = []
    real = fetch.read_capped

    async def spy(resp: httpx.Response, cap: int, host: str = "",
                  budget: fetch.Budget | None = None) -> bytes:
        try:
            return await real(resp, cap, host, budget)
        except fetch.TooLarge as exc:
            frame = exc.__traceback__.tb_next.tb_frame if exc.__traceback__.tb_next else None
            if frame is not None and "buf" in frame.f_locals:
                seen.append(len(frame.f_locals["buf"]))
            raise

    gc.disable()
    try:
        orig = fetch.read_capped
        fetch.read_capped = spy
        try:
            route, _ = _route(web, FakeRequest("https://public.example/b", "image"))
        finally:
            fetch.read_capped = orig
    finally:
        gc.enable()
    assert route.aborted == egress.ABORT_CODE
    assert seen == [0], f"the failed read kept {seen} bytes"
