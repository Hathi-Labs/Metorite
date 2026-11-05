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


def _route(web: FakeWeb, request: FakeRequest,
           record: egress.EgressLog | None = None) -> tuple[FakeRoute, egress.EgressLog]:
    record = record or egress.EgressLog()
    route = FakeRoute()
    handler = egress.route_handler(record, fetch.AddressChecker(), fetch=web.fetcher())
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
    results = [_route(web, FakeRequest(f"https://public.example/{i}"), record)[0].aborted
               for i in range(limits.MAX_PAGES_PER_JOB + 1)]
    assert results[:-1] == [None] * limits.MAX_PAGES_PER_JOB
    assert results[-1] == egress.ABORT_CODE


def test_the_route_stops_after_the_request_limit() -> None:
    web = FakeWeb({"public.example/a.js": lambda: ok(b"x")})
    record = egress.EgressLog()
    for _ in range(limits.MAX_REQUESTS_PER_JOB):
        assert _route(web, FakeRequest("https://public.example/a.js", "script"),
                      record)[0].aborted is None
    route, _ = _route(web, FakeRequest("https://public.example/a.js", "script"), record)
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
        job_a = fetch.AddressChecker(resolve, pool=pool, per_job=2, timeout=0.3)
        job_b = fetch.AddressChecker(resolve, pool=pool, per_job=2, timeout=0.3)
        urls = [fetch.parse_hop(f"https://hang{i}.example/") for i in (1, 2, 1, 2)]
        results = await asyncio.gather(*(job_a.pin(u) for u in urls), return_exceptions=True)
        reasons = [r.reason for r in results if isinstance(r, fetch.Refused)]
        start = time.monotonic()
        await job_b.pin(fetch.parse_hop("https://public.example/"))
        took = time.monotonic() - start
        # The slots of job A come back only when its threads end.
        held = job_a._slots.locked()
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
    assert fetch._POOL._max_workers == limits.MAX_SESSIONS * limits.CHECKS_PER_JOB
    assert limits.CHECK_TIMEOUT_S == 5


# ── The renderer wiring ─────────────────────────────────────────────────────


def test_the_renderer_routes_each_request_and_each_socket() -> None:
    text = (WORKER / "web_worker" / "render.py").read_text(encoding="utf-8")
    assert "new_context(" in text and 'service_workers="block"' in text
    assert "context.route(ALL, route_handler(record, checker))" in text
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


def test_org_b_cannot_find_list_or_read_a_job_of_org_a(tmp_path: Path) -> None:
    store = jobs.JobStore(tmp_path / "jobs")
    with store.open("org-a", "job-a") as job:
        (job.dir / "page.md").write_text("# org A only", encoding="utf-8")
        assert store.files("org-a", "job-a") == ["page.md"]
        assert store.read("org-a", "job-a", "page.md") == b"# org A only"
        for call in (lambda: store.lookup("org-b", "job-a"),
                     lambda: store.files("org-b", "job-a"),
                     lambda: store.read("org-b", "job-a", "page.md"),
                     lambda: store.read("org-a", "job-a", "../page.md"),
                     lambda: store.read("org-a", "job-a", ".hidden")):
            with pytest.raises(jobs.JobNotFound):
                call()
    with pytest.raises(jobs.JobNotFound):
        store.lookup("org-a", "job-a")


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

    route, _ = _route(web, WithAuth("https://public.example/a", "script"))
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
    assert text.count("run_in_executor(\n            MARKDOWN_POOL, to_markdown") == 2
