"""R8 — the checks read and the reconcile poll (WS-54 GH-5).

Spec: ``project-docs/specs/github_integration.md`` §8.5 and §11. A real
Postgres (the promoted phase-4 catalog, as the NON-privileged role), GitHub
mocked at the HTTP layer, Redis real (``GITHUB_R8_REDIS_URL``) or a fake under
the real ``TenantRedis`` wrapper.

* **K2** a read for an old head writes nothing, also when the head moves
  while the read is at GitHub.
* **K3** no database session is open during a GitHub call (the fence of
  EA12-4, R11): a counting wrapper around ``tenant_session`` sees zero open
  sessions at each request.
* **K4** a poll tick runs in one process only, reads at most 50 references,
  and stops on a rate-limit answer.
* **K5** a poll that finds ``merged`` calls ``on_merged`` once.
* **K6** ``GET /refs`` returns ``checks_state``, ``checks_summary``,
  ``head_ref`` and ``merged_at``.
* **K7** the org list site is registered in ``test_db_engine_seam.py``.
* The webhook's ``check_suite`` event schedules the read, after the answer.

R7, the mutations. Each one turns a test here red (run 2026-10-18):

* K-M5 drop ``AND head_sha = :sha`` from the checks write -> ``test_k2_*``.
* K-M6 the checks read holds its first session across the GitHub call -> ``test_k3_*``.
* K-M7 the tick guard always claims -> ``test_k4_one_process_*``.
* K-M8 ``LIMIT :limit`` dropped -> ``test_k4_at_most_fifty_*``.
* K-M9 the rate limit is not raised -> ``test_k4_a_rate_limit_*``.
* K-M10 the poll skips ``on_merged`` -> ``test_k5_*``.

Fix round 1, item 6 (an unreadable reference), each one red when run:

* 6-M4 ``_REFUSED_SQL`` leaves ``unreadable_since`` alone ->
  ``test_item6_a_refused_read_marks_the_ref_and_a_good_read_clears_it``.
* 6-M5 ``_PR_UPDATE_SQL`` does not clear ``unreadable_since`` -> the same test.
* 6-M6 ``record_refusal`` never calls ``on_unblocked`` ->
  ``test_item6_a_refusal_past_the_bound_runs_the_rule_again``.
* 6-M7 ``read_pull_request`` answers ``None`` for a 5xx too ->
  ``test_item6_a_5xx_is_not_a_refusal``.

Fix round 1, item 7 (the checks reads of one head are joined):

* 7-M1 a job that does not lead reads anyway (no ``return 0``) ->
  ``test_item7_a_burst_of_events_gives_at_most_two_reads``.
* 7-M2 the leader makes no trailing read (``break`` at once) ->
  ``test_item7_the_trailing_read_catches_the_last_completed``.
* 7-M3 ``checks_job`` calls ``read_checks`` and not the joined read ->
  ``test_item7_the_webhook_job_is_the_joined_read``.

Fix round 2, item 2 (a 403 is not a per-PR refusal):

* R2-2-M1 ``REFUSED_STATUSES`` holds 403 again ->
  ``test_r2_2_a_403_never_unblocks_and_the_sibling_merge_waits``.
* R2-2-M2 the poll treats a 403 as a refusal (``record_refusal``) -> the same test.

Fix round 2, item 4 (the last mark of the trailing read):

* R2-4-M1 no GETDEL after the lead goes ->
  ``test_r2_4_a_mark_set_before_the_lead_goes_is_read``.
"""

from __future__ import annotations

import asyncio
import hashlib
import hmac
import json
import uuid
from contextlib import asynccontextmanager
from typing import Any

import httpx
import pytest

pytest.importorskip("sqlalchemy")

from sqlalchemy import text

from tests.unit import _github_r8 as g
from tests.unit._tenant_ladder import tenant_engine_scope
from tests.unit.test_h3_rls_promotion_rehearsal import (  # noqa: F401
    _DB_GATE,
    app_engine,
    promoted,
)

pytestmark = _DB_GATE

REAL_CLIENT = httpx.AsyncClient
INSTALL = 9101
REPO = "acme/widgets"
EMAIL = "dev@gh5-r8.test"
SHA_OLD = "1" * 40
SHA_NEW = "2" * 40


@pytest.fixture(scope="module")
def p(promoted):  # noqa: F811
    g.grant_lookup(promoted)
    return promoted


class FakeGitHub:
    def __init__(self, world: Any) -> None:
        self.w = world
        self.requests: list[str] = []
        self.pulls: dict[int, dict] = {}
        self.runs: dict[str, list] = {}
        self.status: dict[str, dict] = {}
        self.pr_reads = 0
        self.rate_limit_at: int | None = None
        self.on_checks: Any = None
        self.open_at_call: list[int] = []
        self.tokens_gone = False
        self.token_asks = 0
        self.pull_status: dict[int, int] = {}

    async def handle(self, request: httpx.Request) -> httpx.Response:
        self.open_at_call.append(self.w.opened["n"])
        path = request.url.path
        self.requests.append(path)
        if path.endswith("/access_tokens"):
            self.token_asks += 1
            if self.tokens_gone:
                return httpx.Response(404, json={"message": "Not Found"})
            return httpx.Response(201, json={"token": "ghs_R8", "expires_at": "2099-01-01T00:00:00Z"})
        if "/pulls/" in path:
            self.pr_reads += 1
            if self.rate_limit_at is not None and self.pr_reads >= self.rate_limit_at:
                return httpx.Response(403, json={"message": "rate limit"},
                                      headers={"x-ratelimit-remaining": "0"})
            number = int(path.rsplit("/", 1)[1])
            if number in self.pull_status:
                return httpx.Response(self.pull_status[number], json={})
            body = self.pulls.get(number)
            return httpx.Response(200, json=body) if body else httpx.Response(404, json={})
        if path.endswith("/check-runs"):
            sha = path.split("/")[-2]
            if self.on_checks is not None:
                self.on_checks(sha)
            runs = self.runs.get(sha, [])
            return httpx.Response(200, json={"total_count": len(runs), "check_runs": runs})
        if path.endswith("/status"):
            sha = path.split("/")[-2]
            return httpx.Response(200, json=self.status.get(sha, {"state": "pending",
                                                                  "statuses": []}))
        return httpx.Response(404, json={})


@pytest.fixture
def world(p, monkeypatch, tmp_path):
    from gateway import github_reconcile_loop, github_sync
    from gateway.routes.projects import github_merge

    g.configure_app(monkeypatch, g.make_private_key(tmp_path))
    g.use_redis(monkeypatch)
    g.clear_github(p)
    g.seed_github_row(p, p.org_a, INSTALL)

    class W:
        pass

    w = W()
    w.p = p
    w.opened = {"n": 0}
    real = github_sync.tenant_session

    @asynccontextmanager
    async def counted(*args: Any, **kwargs: Any):
        w.opened["n"] += 1
        try:
            async with real(*args, **kwargs) as db:
                yield db
        finally:
            w.opened["n"] -= 1

    monkeypatch.setattr(github_sync, "tenant_session", counted)
    # Item 7: a short join window, so a trailing loop ends inside the test.
    monkeypatch.setattr(github_sync, "JOIN_WINDOW_S", 0.05)
    monkeypatch.setattr(github_reconcile_loop, "tenant_session", counted)
    w.events = []

    async def _emit(name: str, payload: dict) -> None:
        w.events.append((name, payload))

    monkeypatch.setattr(github_merge, "emit", _emit)
    w.gh = FakeGitHub(w)
    g.patch_github(monkeypatch, w.gh.handle, REAL_CLIENT)
    w.project, w.lanes = g.seed_project(p, p.org_a, EMAIL)
    yield w
    g.clear_github(p)


def _ref(w: Any, task: str, number: int, *, state: str = "open", head: str | None = SHA_OLD,
         checks: str | None = None, stale: bool = True) -> str:
    with w.p.admin_engine.begin() as c:
        return str(c.execute(text(
            "INSERT INTO pm_task_refs (organization_id, task_id, kind, provider, ref, url, "
            "state, via, created_by, head_sha, checks_state, fetched_at, checks_fetched_at) "
            "VALUES (CAST(:o AS uuid), CAST(:t AS uuid), 'pull_request', 'github', :ref, :url, "
            ":s, 'ui', :e, :h, :c, CASE WHEN :stale THEN now() - interval '2 hours' ELSE now() "
            "END, CASE WHEN :stale THEN now() - interval '2 hours' ELSE now() END) RETURNING id"),
            {"o": w.p.org_a, "t": task, "ref": f"{REPO}#{number}",
             "url": f"https://github.com/{REPO}/pull/{number}", "s": state, "e": EMAIL,
             "h": head, "c": checks, "stale": stale}).scalar_one())


def _task(w: Any) -> str:
    return g.seed_task(w.p, w.p.org_a, EMAIL, project=w.project, lanes=w.lanes,
                       category="in_progress")


def _pull(number: int, *, state: str = "open", merged: bool = False, sha: str = SHA_OLD,
          updated: str = "2026-10-18T10:00:00Z") -> dict:
    return {"number": number, "title": f"PR {number}", "state": state, "merged": merged,
            "merged_at": "2026-10-18T10:00:00Z" if merged else None, "draft": False,
            "updated_at": updated, "merged_by": {"login": "octo"},
            "head": {"ref": "feat/x", "sha": sha}, "base": {"ref": "main"}}


def _row(w: Any, ref_id: str) -> Any:
    (row,) = g.rows(w.p, "SELECT state, head_sha, checks_state, checks_summary, "
                         "checks_fetched_at FROM pm_task_refs WHERE id = CAST(:i AS uuid)",
                    i=ref_id)
    return row


# ── K2, K3: the checks read ─────────────────────────────────────────────────


async def test_k2_a_read_for_an_old_head_writes_nothing(world) -> None:
    from gateway import github_sync

    w = world
    task = _task(w)
    ref = _ref(w, task, 1, head=SHA_NEW, checks="pending")
    w.gh.runs[SHA_OLD] = [{"status": "completed", "conclusion": "failure"}]
    async with tenant_engine_scope(g.app_dsn(w.p)):
        await github_sync.read_checks(w.p.org_a, REPO, SHA_OLD)
    assert _row(w, ref).checks_state == "pending"
    assert w.gh.requests == [], "no reference holds the old head, so nothing is read"

    # The head moves WHILE the read is at GitHub: the late write must miss.
    with w.p.admin_engine.begin() as c:
        c.execute(text("UPDATE pm_task_refs SET head_sha = :s WHERE id = CAST(:i AS uuid)"),
                  {"s": SHA_OLD, "i": ref})

    def _move(sha: str) -> None:
        with w.p.admin_engine.begin() as c:
            c.execute(text("UPDATE pm_task_refs SET head_sha = :s WHERE id = CAST(:i AS uuid)"),
                      {"s": SHA_NEW, "i": ref})

    w.gh.on_checks = _move
    async with tenant_engine_scope(g.app_dsn(w.p)):
        await github_sync.read_checks(w.p.org_a, REPO, SHA_OLD)
    row = _row(w, ref)
    assert (row.head_sha, row.checks_state) == (SHA_NEW, "pending")


async def test_k3_no_session_is_open_during_a_github_call(world) -> None:
    from gateway import github_reconcile_loop, github_sync

    w = world
    task = _task(w)
    ref = _ref(w, task, 2, head=SHA_OLD, checks="pending")
    w.gh.runs[SHA_OLD] = [{"status": "completed", "conclusion": "success"}]
    w.gh.status[SHA_OLD] = {"state": "success", "statuses": [{"state": "success"}]}
    async with tenant_engine_scope(g.app_dsn(w.p)):
        await github_sync.read_checks(w.p.org_a, REPO, SHA_OLD)
        w.gh.pulls[2] = _pull(2)
        await github_reconcile_loop.run_org(w.p.org_a, "k3-tick")
    row = _row(w, ref)
    assert row.checks_state == "passing"
    assert row.checks_summary == {"passed": 2, "failed": 0, "pending": 0, "skipped": 0,
                                  "total": 2}
    assert w.gh.open_at_call, "GitHub saw no call"
    assert set(w.gh.open_at_call) == {0}, w.gh.open_at_call


# ── K4: one process, fifty references, a rate limit ─────────────────────────


async def test_k4_one_process_runs_a_tick(world) -> None:
    from gateway import github_reconcile_loop

    w = world
    _ref(w, _task(w), 3)
    w.gh.pulls[3] = _pull(3)
    async with tenant_engine_scope(g.app_dsn(w.p)):
        first = await github_reconcile_loop.run_org(w.p.org_a, "tick-one")
        reads = w.gh.pr_reads
        second = await github_reconcile_loop.run_org(w.p.org_a, "tick-one")
    assert first.claimed and not second.claimed
    assert w.gh.pr_reads == reads == 1


async def test_k4_at_most_fifty_references_in_a_tick(world) -> None:
    from gateway import github_reconcile_loop

    w = world
    task = _task(w)
    for n in range(100, 160):
        _ref(w, task, n)
        w.gh.pulls[n] = _pull(n)
    async with tenant_engine_scope(g.app_dsn(w.p)):
        result = await github_reconcile_loop.run_org(w.p.org_a, "tick-fifty")
    assert result.pr_reads == 50 and w.gh.pr_reads == 50
    fresh = g.scalar(w.p, "SELECT count(*) FROM pm_task_refs WHERE fetched_at > now() - "
                          "interval '1 minute'")
    assert fresh == 50


async def test_k4_a_rate_limit_stops_the_tick(world) -> None:
    from gateway import github_reconcile_loop

    w = world
    task = _task(w)
    for n in range(200, 210):
        _ref(w, task, n)
        w.gh.pulls[n] = _pull(n)
    w.gh.rate_limit_at = 3
    async with tenant_engine_scope(g.app_dsn(w.p)):
        result = await github_reconcile_loop.run_org(w.p.org_a, "tick-limit")
    assert result.rate_limited
    assert w.gh.pr_reads == 3


async def test_a_gone_installation_stops_the_tick_after_one_token_request(world) -> None:
    from gateway import github_reconcile_loop

    w = world
    task = _task(w)
    for n in range(300, 306):
        _ref(w, task, n)
        w.gh.pulls[n] = _pull(n)
    w.gh.tokens_gone = True
    async with tenant_engine_scope(g.app_dsn(w.p)):
        result = await github_reconcile_loop.run_org(w.p.org_a, "tick-gone")
    assert result.claimed and w.gh.token_asks == 1, w.gh.token_asks
    assert w.gh.pr_reads == 0


async def test_a_fresh_reference_is_not_read(world) -> None:
    from gateway import github_reconcile_loop

    w = world
    _ref(w, _task(w), 4, stale=False)
    async with tenant_engine_scope(g.app_dsn(w.p)):
        result = await github_reconcile_loop.run_org(w.p.org_a, "tick-fresh")
    assert result.claimed and result.pr_reads == 0 and w.gh.requests == []


# ── K5: a merge that the poll finds ─────────────────────────────────────────


async def test_k5_a_poll_that_finds_merged_calls_on_merged_once(world, monkeypatch) -> None:
    from gateway import github_reconcile_loop
    from gateway.routes.projects import github_merge

    w = world
    task = _task(w)
    with w.p.admin_engine.begin() as c:
        c.execute(text(
            "INSERT INTO pm_agent_runs (organization_id, task_id, member_email, client_via, "
            "status, started_by, created_at) VALUES (CAST(:o AS uuid), CAST(:t AS uuid), :e, "
            "'mcp:claude-code', 'completed', :e, now() - interval '1 day')"),
            {"o": w.p.org_a, "t": task, "e": EMAIL})
    _ref(w, task, 5)
    w.gh.pulls[5] = _pull(5, state="closed", merged=True)
    calls: list[str] = []
    real = github_merge.on_merged

    async def spy(db: Any, org: str, ref: str, *args: Any, **kwargs: Any) -> Any:
        calls.append(ref)
        return await real(db, org, ref, *args, **kwargs)

    monkeypatch.setattr(github_merge, "on_merged", spy)
    async with tenant_engine_scope(g.app_dsn(w.p)):
        first = await github_reconcile_loop.run_org(w.p.org_a, "tick-k5-a")
        await github_reconcile_loop.run_org(w.p.org_a, "tick-k5-b")
    assert calls == [f"{REPO}#5"]
    assert first.merged == 1
    assert g.scalar(w.p, "SELECT s.category FROM pm_tasks t JOIN pm_task_statuses s ON "
                         "s.id = t.status_id WHERE t.id = CAST(:t AS uuid)", t=task) == "done"
    assert ("pm.task.status_changed" in [n for n, _ in w.events])


# ── K6, K7 ──────────────────────────────────────────────────────────────────


async def test_k6_the_refs_route_returns_the_github_fields(world) -> None:
    from gateway.routes import projects as projects_package

    w = world
    task = _task(w)
    ref = _ref(w, task, 6)
    with w.p.admin_engine.begin() as c:
        c.execute(text(
            "UPDATE pm_task_refs SET head_ref = 'feat/x', checks_state = 'failing', "
            "checks_summary = CAST(:s AS jsonb), merged_at = now() WHERE id = CAST(:i AS uuid)"),
            {"s": json.dumps({"passed": 1, "failed": 1, "pending": 0, "skipped": 0,
                              "total": 2}), "i": ref})
    member = g.user(EMAIL, w.p.org_a)
    from acb_auth import UserContext, UserRole, build_access

    member = UserContext(email=EMAIL, role=UserRole.EMPLOYEE, organization_id=w.p.org_a,
                         access=build_access(["feature:projects", "data:org:read"]))
    resp = await g.call(w.p, member, [projects_package.router], "GET",
                        f"/projects/tasks/{task}/refs", params={"refresh": "false"},
                        real_client=REAL_CLIENT)
    assert resp.status_code == 200, resp.text
    (row,) = resp.json()["rows"]
    assert row["checks_state"] == "failing"
    assert row["checks_summary"] == {"passed": 1, "failed": 1, "pending": 0, "skipped": 0,
                                     "total": 2}
    assert row["head_ref"] == "feat/x"
    assert row["merged_at"] is not None


def test_k7_the_org_list_is_a_registered_discovery_site() -> None:
    from tests.unit.test_db_engine_seam import H2_TENANT_DISCOVERY_SITES

    assert ("apps/services/gateway/gateway/github_reconcile_loop.py",
            "_organization_ids") in H2_TENANT_DISCOVERY_SITES


async def test_the_loop_starts_only_with_the_flag(world, monkeypatch) -> None:
    from acb_common import get_settings
    from gateway import github_reconcile_loop

    monkeypatch.setattr(get_settings(), "github_app_enabled", False)
    assert await github_reconcile_loop.start_github_reconcile_loop() is False
    assert not github_reconcile_loop._task


# ── The webhook schedules the read ──────────────────────────────────────────


async def test_a_check_suite_event_reads_the_checks_after_the_answer(world) -> None:
    from fastapi import FastAPI
    from gateway.routes.github import webhook

    w = world
    ref = _ref(w, _task(w), 7, head=SHA_NEW, checks="pending")
    w.gh.runs[SHA_NEW] = [{"status": "completed", "conclusion": "failure"}]
    payload = {"action": "completed", "installation": {"id": INSTALL},
               "repository": {"full_name": REPO},
               "check_suite": {"head_sha": SHA_NEW, "status": "completed"}}
    raw = json.dumps(payload).encode()
    app = FastAPI()
    app.include_router(webhook.router)
    async with tenant_engine_scope(g.app_dsn(w.p)), REAL_CLIENT(
        transport=httpx.ASGITransport(app=app), base_url="http://gateway.test",
    ) as client:
        resp = await client.post("/webhooks/github", content=raw, headers={
            "X-GitHub-Event": "check_suite", "X-GitHub-Delivery": str(uuid.uuid4()),
            "X-Hub-Signature-256": "sha256=" + hmac.new(
                b"r8-webhook-secret", raw, hashlib.sha256).hexdigest()})
        assert resp.status_code == 200, resp.text
    assert _row(w, ref).checks_state == "failing"


# ── Fix round 1, item 6: an unreadable reference ────────────────────────────


def _unreadable(w: Any, ref_id: str) -> Any:
    return g.scalar(w.p, "SELECT unreadable_since FROM pm_task_refs WHERE id = CAST(:i AS uuid)",
                    i=ref_id)


def _stale(w: Any, ref_id: str) -> None:
    with w.p.admin_engine.begin() as c:
        c.execute(text("UPDATE pm_task_refs SET fetched_at = now() - interval '2 hours' "
                       "WHERE id = CAST(:i AS uuid)"), {"i": ref_id})


async def test_item6_a_refused_read_marks_the_ref_and_a_good_read_clears_it(world) -> None:
    from gateway import github_reconcile_loop

    w = world
    ref = _ref(w, _task(w), 501)
    async with tenant_engine_scope(g.app_dsn(w.p)):
        await github_reconcile_loop.run_org(w.p.org_a, "tick-501-a")
        first = _unreadable(w, ref)
        assert first is not None, "a 404 marks the reference"
        _stale(w, ref)
        await github_reconcile_loop.run_org(w.p.org_a, "tick-501-b")
        assert _unreadable(w, ref) == first, "the FIRST refusal time stays"
        w.gh.pulls[501] = _pull(501)
        _stale(w, ref)
        await github_reconcile_loop.run_org(w.p.org_a, "tick-501-c")
    assert _unreadable(w, ref) is None, "a good read clears the mark"


async def test_item6_a_refusal_past_the_bound_runs_the_rule_again(world) -> None:
    """The task merged one pull request and waits on a second that the App
    cannot read. When the refusal is older than the bound, the poll's next
    refused read runs the rule again, and the task moves."""
    from gateway import github_reconcile_loop

    w = world
    task = _task(w)
    with w.p.admin_engine.begin() as c:
        c.execute(text(
            "INSERT INTO pm_agent_runs (organization_id, task_id, member_email, client_via, "
            "status, started_by, created_at) VALUES (CAST(:o AS uuid), CAST(:t AS uuid), :e, "
            "'mcp:claude-code', 'completed', :e, now() - interval '1 day')"),
            {"o": w.p.org_a, "t": task, "e": EMAIL})
    _ref(w, task, 502, state="merged", stale=False)
    blocker = _ref(w, task, 503)
    with w.p.admin_engine.begin() as c:
        c.execute(text("UPDATE pm_task_refs SET unreadable_since = now() - interval '2 hours' "
                       "WHERE id = CAST(:i AS uuid)"), {"i": blocker})
    async with tenant_engine_scope(g.app_dsn(w.p)):
        result = await github_reconcile_loop.run_org(w.p.org_a, "tick-503")
    assert result.merged == 1
    assert g.scalar(w.p, "SELECT s.category FROM pm_tasks t JOIN pm_task_statuses s ON "
                         "s.id = t.status_id WHERE t.id = CAST(:t AS uuid)", t=task) == "done"
    (body,) = [r.body for r in g.rows(
        w.p, "SELECT body FROM pm_activities WHERE task_id = CAST(:t AS uuid) "
             "AND type = 'system' AND created_by = 'system:github'", t=task)]
    assert body == f"GitHub moved this to Done after {REPO}#502 merged"


async def test_item6_a_5xx_is_not_a_refusal(world) -> None:
    from gateway import github_reconcile_loop

    w = world
    ref = _ref(w, _task(w), 504)
    w.gh.pull_status[504] = 502
    async with tenant_engine_scope(g.app_dsn(w.p)):
        await github_reconcile_loop.run_org(w.p.org_a, "tick-504")
    assert _unreadable(w, ref) is None


# ── Fix round 1, item 7: the checks reads of one head are joined ────────────


def _check_reads(w: Any) -> int:
    return sum(1 for path in w.gh.requests if path.endswith("/check-runs"))


async def test_item7_a_burst_of_events_gives_at_most_two_reads(world) -> None:
    from gateway import github_sync

    w = world
    ref = _ref(w, _task(w), 601, head=SHA_NEW, checks="pending")
    w.gh.runs[SHA_NEW] = [{"status": "completed", "conclusion": "success"}]
    w.gh.status[SHA_NEW] = {"state": "success", "statuses": [{"state": "success"}]}
    async with tenant_engine_scope(g.app_dsn(w.p)):
        made = await asyncio.gather(*(
            github_sync.read_checks_joined(w.p.org_a, REPO, SHA_NEW) for _ in range(12)))
    assert 1 <= _check_reads(w) <= 2, w.gh.requests
    assert sum(made) == _check_reads(w)
    assert _row(w, ref).checks_state == "passing"


async def test_item7_the_trailing_read_catches_the_last_completed(world, monkeypatch) -> None:
    from gateway import github_sync

    monkeypatch.setattr(github_sync, "JOIN_WINDOW_S", 0.3)
    w = world
    ref = _ref(w, _task(w), 602, head=SHA_NEW, checks="pending")
    w.gh.runs[SHA_NEW] = [{"status": "in_progress", "conclusion": None}]
    async with tenant_engine_scope(g.app_dsn(w.p)):
        leader = asyncio.ensure_future(github_sync.read_checks_joined(w.p.org_a, REPO, SHA_NEW))
        for _ in range(200):
            if _check_reads(w) >= 1 and _row(w, ref).checks_fetched_at is not None:
                break
            await asyncio.sleep(0.01)
        assert _row(w, ref).checks_state == "pending"
        # The last job completes while the leader waits.
        w.gh.runs[SHA_NEW] = [{"status": "completed", "conclusion": "success"}]
        w.gh.status[SHA_NEW] = {"state": "success", "statuses": [{"state": "success"}]}
        assert await github_sync.read_checks_joined(w.p.org_a, REPO, SHA_NEW) == 0
        assert await leader == 2
    assert _row(w, ref).checks_state == "passing"


def test_item7_the_webhook_job_is_the_joined_read(monkeypatch) -> None:
    from gateway import github_sync

    seen: list[tuple] = []

    async def _joined(org: str, repo: str, sha: str, **kw: Any) -> int:
        seen.append((org, repo, sha, kw))
        return 1

    monkeypatch.setattr(github_sync, "read_checks_joined", _joined)
    asyncio.run(github_sync.checks_job("o", REPO, SHA_NEW)())
    assert seen == [("o", REPO, SHA_NEW, {"trailing_in_background": True})]


# ── Fix round 2, item 2: a 403 keeps the reference blocking ────────────────


async def test_r2_2_a_403_never_unblocks_and_the_sibling_merge_waits(world) -> None:
    from gateway import github_reconcile_loop

    w = world
    task = _task(w)
    with w.p.admin_engine.begin() as c:
        c.execute(text(
            "INSERT INTO pm_agent_runs (organization_id, task_id, member_email, client_via, "
            "status, started_by, created_at) VALUES (CAST(:o AS uuid), CAST(:t AS uuid), :e, "
            "'mcp:claude-code', 'completed', :e, now() - interval '1 day')"),
            {"o": w.p.org_a, "t": task, "e": EMAIL})
    blocked = _ref(w, task, 610)
    with w.p.admin_engine.begin() as c:  # a 403 for two hours already
        c.execute(text("UPDATE pm_task_refs SET fetched_at = now() - interval '2 hours' "
                       "WHERE id = CAST(:i AS uuid)"), {"i": blocked})
    w.gh.pull_status[610] = 403  # no rate-limit header: an IP allow list
    _ref(w, task, 611)
    w.gh.pulls[611] = _pull(611, state="closed", merged=True)
    async with tenant_engine_scope(g.app_dsn(w.p)):
        result = await github_reconcile_loop.run_org(w.p.org_a, "tick-r2-403")
    assert result.pr_reads == 2
    assert _unreadable(w, blocked) is None, "a 403 is never a per-PR refusal"
    fetched = g.scalar(w.p, "SELECT fetched_at > now() - interval '1 minute' FROM "
                            "pm_task_refs WHERE id = CAST(:i AS uuid)", i=blocked)
    assert fetched is True, "the attempt is stamped, so the row waits"
    assert g.scalar(w.p, "SELECT s.category FROM pm_tasks t JOIN pm_task_statuses s ON "
                         "s.id = t.status_id WHERE t.id = CAST(:t AS uuid)", t=task) \
        == "in_progress", "the open sibling still blocks merge-to-Done"


# ── Fix round 2, item 4: a mark set while the lead goes ─────────────────────


async def test_r2_4_a_mark_set_before_the_lead_goes_is_read(world, monkeypatch) -> None:
    """A follower marks ``again`` after the leader's last GETDEL and before
    its DELETE of the lead. The leader must still read once more, or a late
    failing ``check_run`` shows as ``passing`` for ever."""
    from acb_common import tenant_redis
    from gateway import github_sync

    w = world
    ref = _ref(w, _task(w), 620, head=SHA_NEW, checks="pending")
    w.gh.runs[SHA_NEW] = [{"status": "completed", "conclusion": "success"}]
    w.gh.status[SHA_NEW] = {"state": "success", "statuses": [{"state": "success"}]}
    real_get = tenant_redis.get_tenant_redis

    class Racing:
        """The tenant client, with a follower that marks during the DELETE."""

        def __init__(self, inner: Any) -> None:
            self.inner = inner

        def __getattr__(self, name: str) -> Any:
            return getattr(self.inner, name)

        async def delete(self, *keys: Any) -> Any:
            if not injected:
                injected.append(True)
                # The late job: a failing run, and the mark it sets.
                w.gh.runs[SHA_NEW] = [{"status": "completed", "conclusion": "failure"}]
                await self.inner.set(self.inner.key("github", "checks", SHA_NEW, "again"),
                                     "1", ex=60)
            return await self.inner.delete(*keys)

    injected: list[bool] = []
    monkeypatch.setattr(tenant_redis, "get_tenant_redis",
                        lambda **kw: Racing(real_get(**kw)))
    async with tenant_engine_scope(g.app_dsn(w.p)):
        reads = await github_sync.read_checks_joined(w.p.org_a, REPO, SHA_NEW)
    assert injected, "the race was not set up"
    assert reads == 2 and _check_reads(w) == 2, w.gh.requests
    assert _row(w, ref).checks_state == "failing"
