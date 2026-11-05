"""WEB-6: the web worker container (``web_research.md`` section 5.1).

The fence row of section 14: the worker refuses a private target (top URL,
redirect, subresource), runs as non-root, and keeps one org's job files from
another. These tests need no Chromium. A fake route, a fake request and a
fake request context drive the real route handler. The real check runs, and
only the resolver of named hosts is a fake. A literal address resolves for
real.

The Chromium half (a real page, a real redirect chain, a real subresource)
runs in the image: see the "As built, WEB-6" note of the spec.
"""

from __future__ import annotations

import asyncio
import re
import sys
from pathlib import Path
from typing import Any

import pytest

REPO = Path(__file__).resolve().parents[2]
WORKER = REPO / "apps" / "services" / "web_worker"
sys.path.insert(0, str(WORKER))

from acb_common import public_address  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402
from web_worker import egress, jobs, limits, main, render  # noqa: E402

yaml = pytest.importorskip("yaml")

TOKEN = "t" * 40
AUTH = {"Authorization": f"Bearer {TOKEN}"}

#: The private targets that the acceptance names, and two more.
PRIVATE = (
    "http://127.0.0.1/",
    "http://10.0.0.1/",
    "http://10.255.3.4:8080/x",
    "http://169.254.169.254/latest/meta-data/",
    "http://[::1]/",
    "http://[::ffff:127.0.0.1]/",
    "http://localhost/",
)
NOT_HTTP = ("file:///etc/passwd", "ftp://public.example/", "data:text/html,hi",
            "javascript:alert(1)", "chrome://version")

_NAMES = {"public.example": ["93.184.215.14"], "other.example": ["93.184.215.15"],
          "localhost": ["127.0.0.1", "::1"], "rebind.example": ["93.184.215.14", "10.0.0.7"]}


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


# ── Fakes of Playwright ─────────────────────────────────────────────────────


class FakeResponse:
    def __init__(self, status: int = 200, location: str | None = None) -> None:
        self.status = status
        self.headers = {"location": location} if location else {}


class FakeFrame:
    parent_frame = None


class FakeRequest:
    def __init__(self, url: str, resource_type: str = "document", method: str = "GET") -> None:
        self.url = url
        self.resource_type = resource_type
        self.method = method
        self.headers = {"user-agent": "x", "cookie": "a=b", "host": "h"}
        self.post_data_buffer = None
        self.frame = FakeFrame()

    def is_navigation_request(self) -> bool:
        return self.resource_type == "document"


class FakeRoute:
    def __init__(self, first: FakeResponse | Exception = FakeResponse()) -> None:
        self.first = first
        self.aborted: str | None = None
        self.fulfilled: Any = None
        self.fetched = 0

    async def fetch(self, max_redirects: int | None = None) -> FakeResponse:
        assert max_redirects == 0, "the route must not let Playwright follow a redirect"
        self.fetched += 1
        if isinstance(self.first, Exception):
            raise self.first
        return self.first

    async def abort(self, code: str) -> None:
        self.aborted = code

    async def fulfill(self, response: Any) -> None:
        self.fulfilled = response


class FakeRequestContext:
    """``context.request``: each later hop of a redirect."""

    def __init__(self, answers: dict[str, FakeResponse] | None = None) -> None:
        self.answers = answers or {}
        self.fetched: list[tuple[str, str, dict[str, str]]] = []

    async def fetch(self, url: str, *, method: str, headers: dict[str, str],
                    max_redirects: int, data: Any = None) -> FakeResponse:
        assert max_redirects == 0
        self.fetched.append((url, method, headers))
        return self.answers.get(url, FakeResponse())


def _run(route: FakeRoute, request: FakeRequest, ctx: FakeRequestContext | None = None,
         check: Any = egress.target_refusal) -> egress.EgressLog:
    record = egress.EgressLog()
    handler = egress.route_handler(record, ctx or FakeRequestContext(), check=check)
    asyncio.run(handler(route, request))
    return record


# ── The check ───────────────────────────────────────────────────────────────


@pytest.mark.parametrize("url", PRIVATE)
def test_the_check_refuses_a_private_target(url: str) -> None:
    assert egress.target_refusal(url) is not None, url


@pytest.mark.parametrize("url", NOT_HTTP)
def test_the_check_refuses_a_scheme_that_is_not_http(url: str) -> None:
    assert egress.target_refusal(url) == "scheme", url


def test_the_check_passes_a_public_host() -> None:
    assert egress.target_refusal("https://public.example/a?b=1") is None
    assert egress.target_refusal("http://public.example:8080/") is None


def test_one_private_address_refuses_the_host() -> None:
    assert egress.target_refusal("https://rebind.example/") == "not public"


def test_the_check_fails_closed(monkeypatch: pytest.MonkeyPatch) -> None:
    def boom(host: str, port: int) -> list[str]:
        raise RuntimeError("resolver fault")

    monkeypatch.setattr(public_address, "resolve", boom)
    assert egress.target_refusal("https://public.example/") == "check failed"
    assert egress.target_refusal("http://public.example:99999/") is not None
    assert egress.target_refusal("https://unknown.example/") is not None


def test_the_check_is_the_one_address_rule() -> None:
    """No second address rule: egress.py calls acb_common.public_address."""
    text = (WORKER / "web_worker" / "egress.py").read_text(encoding="utf-8")
    assert "from acb_common.public_address import" in text
    assert "pinned_public_address(" in text
    assert "ipaddress" not in text and "is_private" not in text
    assert not list(WORKER.rglob("public_address.py")), "never copy public_address"


# ── The route: top URL, redirect, subresource ───────────────────────────────


@pytest.mark.parametrize("url", PRIVATE + NOT_HTTP)
def test_the_route_aborts_a_private_top_url(url: str) -> None:
    route = FakeRoute()
    record = _run(route, FakeRequest(url, "document"))
    assert route.aborted == egress.ABORT_CODE
    assert route.fetched == 0 and route.fulfilled is None
    assert record.refused


@pytest.mark.parametrize("kind", ["script", "image", "stylesheet", "xhr", "fetch", "font"])
@pytest.mark.parametrize("url", PRIVATE)
def test_the_route_aborts_a_private_subresource(url: str, kind: str) -> None:
    route = FakeRoute()
    _run(route, FakeRequest(url, kind))
    assert route.aborted == egress.ABORT_CODE
    assert route.fetched == 0


@pytest.mark.parametrize("target", PRIVATE + NOT_HTTP)
def test_the_route_aborts_a_redirect_to_a_private_target(target: str) -> None:
    route = FakeRoute(FakeResponse(302, target))
    ctx = FakeRequestContext()
    record = _run(route, FakeRequest("https://public.example/start"), ctx)
    assert route.aborted == egress.ABORT_CODE
    assert route.fulfilled is None
    assert ctx.fetched == [], "the route fetched a private hop"
    assert record.refused


@pytest.mark.parametrize("target", PRIVATE)
def test_the_route_aborts_a_private_target_late_in_a_chain(target: str) -> None:
    """Playwright does not route a later hop, so the handler checks each hop."""
    route = FakeRoute(FakeResponse(301, "/two"))
    ctx = FakeRequestContext({
        "https://public.example/two": FakeResponse(302, "https://other.example/three"),
        "https://other.example/three": FakeResponse(307, target),
    })
    _run(route, FakeRequest("https://public.example/one", "script"), ctx)
    assert route.aborted == egress.ABORT_CODE
    assert [u for u, _, _ in ctx.fetched] == ["https://public.example/two",
                                              "https://other.example/three"]


def test_the_route_follows_a_public_chain_and_hides_the_3xx() -> None:
    final = FakeResponse(200)
    route = FakeRoute(FakeResponse(303, "https://other.example/end"))
    ctx = FakeRequestContext({"https://other.example/end": final})
    req = FakeRequest("http://public.example/form", "document", method="POST")
    record = _run(route, req, ctx)
    assert route.aborted is None
    assert route.fulfilled is final
    url, method, headers = ctx.fetched[0]
    assert (url, method) == ("https://other.example/end", "GET")
    assert "cookie" not in headers and "host" not in headers
    assert record.top_url == "https://other.example/end"
    assert record.documents == 2


def test_the_route_stops_after_the_redirect_limit() -> None:
    hops = {f"https://public.example/{i}": FakeResponse(302, f"/{i + 1}") for i in range(1, 20)}
    route = FakeRoute(FakeResponse(302, "/1"))
    ctx = FakeRequestContext(hops)
    _run(route, FakeRequest("https://public.example/0", "script"), ctx)
    assert route.aborted == egress.ABORT_CODE
    assert len(ctx.fetched) == limits.MAX_REDIRECTS


def test_the_route_stops_after_the_page_limit() -> None:
    record = egress.EgressLog()
    handler = egress.route_handler(record, FakeRequestContext())
    results = []
    for i in range(limits.MAX_PAGES_PER_JOB + 1):
        route = FakeRoute()
        asyncio.run(handler(route, FakeRequest(f"https://public.example/{i}", "document")))
        results.append(route.aborted)
    assert results[:-1] == [None] * limits.MAX_PAGES_PER_JOB
    assert results[-1] == egress.ABORT_CODE


def test_the_route_fulfills_a_public_subresource() -> None:
    route = FakeRoute()
    _run(route, FakeRequest("https://public.example/app.js", "script"))
    assert route.aborted is None and route.fulfilled is route.first


def test_the_route_skips_a_media_stream() -> None:
    route = FakeRoute()
    _run(route, FakeRequest("https://public.example/v.mp4", "media"))
    assert route.aborted == egress.ABORT_CODE and route.fetched == 0


def test_the_route_fails_closed() -> None:
    route = FakeRoute(RuntimeError("network fault"))
    _run(route, FakeRequest("https://public.example/"))
    assert route.aborted == egress.ABORT_CODE

    def bad_check(url: str) -> str | None:
        raise RuntimeError("check fault")

    route = FakeRoute()
    _run(route, FakeRequest("https://public.example/"), check=bad_check)
    assert route.aborted == egress.ABORT_CODE and route.fetched == 0


def test_the_renderer_routes_each_request_and_each_socket() -> None:
    """Service workers can go around a route, so each context blocks them.
    Each WebSocket is routed and closed."""
    text = (WORKER / "web_worker" / "render.py").read_text(encoding="utf-8")
    assert "new_context(" in text and 'service_workers="block"' in text
    assert "context.route(ALL, route_handler(record, context.request))" in text
    assert "context.route_web_socket(ALL, _close_socket)" in text
    assert "await context.close()" in text
    assert render.ALL.fullmatch("wss://anything/at/all")


# ── The token ───────────────────────────────────────────────────────────────


class _Rendered:
    def __init__(self, markdown: str = "# Hi") -> None:
        self.markdown = markdown
        self.final_url = "https://public.example/"
        self.title = "Hi"
        self.egress = egress.EgressLog()


async def _fake_renderer(url: str, job: jobs.Job) -> _Rendered:
    reason = await asyncio.to_thread(egress.target_refusal, url)
    if reason is not None:
        raise render.PageRefused(reason)
    (job.dir / "page.md").write_text("# Hi", encoding="utf-8")
    return _Rendered()


def _client(tmp_path: Path, renderer: Any = _fake_renderer) -> TestClient:
    app = main.create_app(TOKEN, renderer=renderer, store=jobs.JobStore(tmp_path / "jobs"))
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
    """uvicorn starts the factory, so an import with no token builds nothing."""
    assert not hasattr(main, "app")
    dockerfile = (WORKER / "Dockerfile").read_text(encoding="utf-8")
    assert '"--factory", "web_worker.main:create_app"' in dockerfile


def test_each_route_needs_the_token(tmp_path: Path) -> None:
    client = _client(tmp_path)
    body = {"url": "https://public.example/", "org_id": "org-a", "job_id": "j1"}
    for headers in ({}, {"Authorization": "Bearer wrong"}, {"Authorization": TOKEN},
                    {"Authorization": f"Bearer {TOKEN}x"}, {"Authorization": "Bearer "}):
        assert client.post("/render", json=body, headers=headers).status_code == 401
        assert client.get("/jobs/j1/files?org_id=org-a", headers=headers).status_code == 401
    assert client.post("/render", json=body, headers=AUTH).status_code == 200
    assert client.get("/health").json() == {"status": "ok"}


def test_the_token_compare_is_constant_time() -> None:
    text = (WORKER / "web_worker" / "main.py").read_text(encoding="utf-8")
    assert "hmac.compare_digest(" in text
    assert "!= f\"Bearer" not in text and "== f\"Bearer" not in text


# ── The render API ──────────────────────────────────────────────────────────


def test_render_answers_with_markdown(tmp_path: Path) -> None:
    r = _client(tmp_path).post(
        "/render", json={"url": "https://public.example/", "org_id": "org-a", "job_id": "j1"},
        headers=AUTH)
    assert r.status_code == 200
    assert r.json()["markdown"] == "# Hi"


@pytest.mark.parametrize("url", PRIVATE)
def test_render_refuses_a_private_top_url(tmp_path: Path, url: str) -> None:
    r = _client(tmp_path).post("/render", json={"url": url, "org_id": "org-a", "job_id": "j1"},
                               headers=AUTH)
    assert r.status_code == 422
    assert r.json()["error"] == "refused"
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
        assert job.dir.name.startswith("j1.")
        assert job.dir.stat().st_mode & 0o777 == 0o700
        (job.dir / "page.md").write_text("x", encoding="utf-8")
        seen.append(job.dir)
        return _Rendered()

    client = _client(tmp_path, renderer)
    body = {"url": "https://public.example/", "org_id": "org-a", "job_id": "j1"}
    assert client.post("/render", json=body, headers=AUTH).status_code == 200
    assert seen and not seen[0].exists()
    assert list((tmp_path / "jobs").iterdir()) == []


def test_the_job_dir_is_gone_after_a_failed_job(tmp_path: Path) -> None:
    seen: list[Path] = []

    async def renderer(url: str, job: jobs.Job) -> _Rendered:
        (job.dir / "page.html").write_text("x", encoding="utf-8")
        seen.append(job.dir)
        raise RuntimeError("the page crashed at /secret/path")

    client = _client(tmp_path, renderer)
    r = client.post("/render", json={"url": "https://public.example/secret/path",
                                     "org_id": "org-a", "job_id": "j1"}, headers=AUTH)
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


# ── Org isolation of job files ──────────────────────────────────────────────


def test_org_b_gets_404_for_a_job_of_org_a(tmp_path: Path) -> None:
    store = jobs.JobStore(tmp_path / "jobs")
    client = TestClient(main.create_app(TOKEN, renderer=_fake_renderer, store=store))
    with store.open("org-a", "job-a") as job:
        (job.dir / "page.md").write_text("# org A only", encoding="utf-8")
        ok = client.get("/jobs/job-a/files?org_id=org-a", headers=AUTH)
        assert ok.status_code == 200 and ok.json() == {"files": ["page.md"]}
        assert client.get("/jobs/job-a/files/page.md?org_id=org-a",
                          headers=AUTH).content == b"# org A only"
        other = client.get("/jobs/job-a/files?org_id=org-b", headers=AUTH)
        missing = client.get("/jobs/job-x/files?org_id=org-b", headers=AUTH)
        assert other.status_code == missing.status_code == 404
        assert other.json() == missing.json(), "a 404 must not say that the job exists"
        assert client.get("/jobs/job-a/files/page.md?org_id=org-b",
                          headers=AUTH).status_code == 404
        for name in ("..%2F..%2Fetc%2Fpasswd", ".hidden", "nope.md"):
            assert client.get(f"/jobs/job-a/files/{name}?org_id=org-a",
                              headers=AUTH).status_code == 404, name
        with pytest.raises(jobs.JobNotFound):
            store.lookup("org-b", "job-a")
    assert client.get("/jobs/job-a/files?org_id=org-a", headers=AUTH).status_code == 404


def test_two_jobs_get_two_dirs(tmp_path: Path) -> None:
    store = jobs.JobStore(tmp_path / "jobs")
    with store.open("org-a", "ja") as a, store.open("org-b", "jb") as b:
        assert a.dir != b.dir and a.dir.parent == b.dir.parent
        assert (tmp_path / "jobs").stat().st_mode & 0o777 == 0o700
    with pytest.raises(jobs.JobExists), store.open("org-a", "j1"), store.open("org-b", "j1"):
        pass


# ── Limits ──────────────────────────────────────────────────────────────────


def test_the_limits_are_the_numbers_of_the_spec() -> None:
    assert limits.MAX_PAGES_PER_JOB == 25
    assert limits.MAX_SESSIONS_PER_ORG == 2
    assert limits.MAX_SESSIONS == 4
    assert limits.JOB_TIMEOUT_S == 60
    assert limits.MEM_LIMIT == "2g" and limits.CPUS == 2
    spec = (REPO / "project-docs/specs/web_research.md").read_text(encoding="utf-8")
    for row in ("| Pages per job | 25 |", "| Sessions per organization at once | 2 |",
                "| Sessions in the container at once | 4 |", "| Timeout of one job | 60 s |"):
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
    client = TestClient(main.create_app(TOKEN, renderer=_fake_renderer, store=store))
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
    req = (WORKER / "requirements.txt").read_text(encoding="utf-8")
    assert f"playwright=={tag}" in req
    assert f"playwright=={tag}" in (WORKER / "requirements.lock.txt").read_text(encoding="utf-8")


def test_the_requirements_hold_no_docling_and_no_mcp() -> None:
    for name in ("requirements.txt", "requirements.lock.txt"):
        text = (WORKER / name).read_text(encoding="utf-8").lower()
        for banned in ("docling", "playwright-mcp", "mcp=="):
            assert banned not in text, (name, banned)


def _service() -> dict[str, Any]:
    compose = yaml.safe_load((REPO / "infra/docker-compose.yml").read_text(encoding="utf-8"))
    return compose["services"]["web-worker"]


def test_the_compose_block_holds_the_caps() -> None:
    svc = _service()
    assert svc["profiles"] == ["webworker"]
    assert svc["ports"] == ["127.0.0.1:8096:8080"]
    assert svc["mem_limit"] == limits.MEM_LIMIT
    assert svc["cpus"] == limits.CPUS
    assert svc["cap_drop"] == ["ALL"]
    assert "no-new-privileges:true" in svc["security_opt"]
    assert svc["user"] == "1000:1000"
    assert svc["environment"]["WEB_WORKER_TOKEN"] == "${WEB_WORKER_TOKEN:-}"
    assert "extra_hosts" not in svc and "network_mode" not in svc
    assert "privileged" not in svc and "cap_add" not in svc
    assert "build" not in svc, "the root copy of the box has no repo root"
