"""WS-57 TR-1 — the references of a task: the route and the GitHub read.

Spec: ``project-docs/specs/task_references.md`` §5 and §6 TR-1.

Two halves.

**Hermetic** (no database, no network). The GitHub read in
``gateway/routes/projects/refs.py`` is driven with a fake client:

* with no token, no request is built and every row comes back as it was;
* a timeout, a refusal or an unreadable answer leaves each row as it was;
* at most ten rows are read for one call, and only a GitHub pull request or
  issue whose last read is older than 15 minutes;
* the read runs with NO session open, and one new session writes the result;
* the host is ``api.github.com`` and the module does not use
  ``outbound_guard``.

**R8** (a real Postgres, through the real ``/projects`` router, as the
non-privileged role of the phase-4 rehearsal on a database with the whole
ladder and two organizations). A hermetic fake agrees with any SQL it is
handed, so these need the database:

* the route adds, lists and removes a reference, and each add and each
  remove writes exactly one ``link`` activity;
* a second add of the same reference returns the first row and writes
  nothing (the unique key of migration 255);
* another organization's task answers 404, and its rows stay where they are;
* the no-token path and the failure path leave the row intact and the route
  answering;
* ``pm_task_refs`` forces row level security, and the migration replays with
  no change.

The R8 class carries ``_DB_GATE``, so it skips where no ladder URL is set
(the deploy run) and its fixtures never start there.
"""

from __future__ import annotations

import contextlib
import uuid
from datetime import UTC, datetime, timedelta
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import httpx
import pytest

pytest.importorskip("sqlalchemy")
pytest.importorskip("gateway.routes.projects", reason="gateway not installed")

from gateway.routes.projects import refs
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError

from tests.unit._tenant_ladder import tenant_engine_scope
from tests.unit.test_h3_rls_promotion_rehearsal import (  # noqa: F401
    _DB_GATE,
    app_engine,
    promoted,
)

_REPO = Path(__file__).resolve().parents[2]
_MIGRATIONS = _REPO / "infra" / "postgres"
_REFS_SOURCE = (
    _REPO / "apps" / "services" / "gateway" / "gateway" / "routes" / "projects" / "refs.py"
)

PR_URL = "https://github.com/acme/widgets/pull/12"
TASK = "0f8fad5b-d9cb-469f-a165-70867728950e"
#: The real token reader, kept before any test replaces it.
_REAL_TOKEN_READER = refs._github_token


# ── The fakes for the GitHub read ───────────────────────────────────────────


class _Opened:
    """How many sessions are open at this moment. The fake client reads it."""

    def __init__(self) -> None:
        self.now = 0
        self.total = 0


def _count_sessions(monkeypatch: pytest.MonkeyPatch, inner: Any) -> _Opened:
    opened = _Opened()

    @contextlib.asynccontextmanager
    async def counted(*args: Any, **kwargs: Any):
        opened.now += 1
        opened.total += 1
        try:
            async with inner(*args, **kwargs) as db:
                yield db
        finally:
            opened.now -= 1

    monkeypatch.setattr(refs, "_tenant_session", counted)
    return opened


class _FakeGitHub:
    """An ``httpx.AsyncClient`` stand-in. ``answer`` is an exception to raise,
    or ``(status, body)``. It records each URL and asserts no session is open."""

    def __init__(self, answer: Any, calls: list[str], opened: _Opened | None) -> None:
        self.answer = answer
        self.calls = calls
        self.opened = opened

    async def __aenter__(self) -> _FakeGitHub:
        return self

    async def __aexit__(self, *exc: Any) -> bool:
        return False

    async def get(self, url: str, headers: dict | None = None) -> httpx.Response:
        self.calls.append(url)
        if self.opened is not None:
            assert self.opened.now == 0, "the GitHub read ran inside a session"
        assert (headers or {}).get("Authorization", "").startswith("Bearer ")
        if isinstance(self.answer, BaseException):
            raise self.answer
        status, body = self.answer
        return httpx.Response(status, json=body, request=httpx.Request("GET", url))


def _github(
    monkeypatch: pytest.MonkeyPatch, answer: Any, *, token: str | None = "tok",
    opened: _Opened | None = None,
) -> list[str]:
    calls: list[str] = []

    async def _token() -> str | None:
        return token

    monkeypatch.setattr(refs, "_github_token", _token)
    monkeypatch.setattr(
        refs, "httpx",
        SimpleNamespace(AsyncClient=lambda **_kw: _FakeGitHub(answer, calls, opened)),
    )
    return calls


class _FakeSession:
    def __init__(self, log: list[dict]) -> None:
        self.log = log

    async def execute(self, sql: Any, params: dict | None = None) -> Any:
        self.log.append({"sql": str(sql), "params": dict(params or {})})
        return SimpleNamespace(fetchone=lambda: None, fetchall=lambda: [])


def _fake_sessions(monkeypatch: pytest.MonkeyPatch) -> tuple[list[dict], _Opened]:
    log: list[dict] = []

    @contextlib.asynccontextmanager
    async def inner(*_a: Any, **_k: Any):
        yield _FakeSession(log)

    opened = _count_sessions(monkeypatch, inner)
    return log, opened


def _row(n: int, **over: Any) -> dict[str, Any]:
    row = {
        "id": str(uuid.UUID(int=n + 1)), "task_id": TASK, "kind": "pull_request",
        "provider": "github", "ref": f"acme/widgets#{n}", "url": f"{PR_URL[:-2]}{n}",
        "label": f"widgets#{n}", "title": None, "state": None, "fetched_at": None,
    }
    row.update(over)
    return row


# ── Hermetic: the pure helpers ──────────────────────────────────────────────


def test_the_target_is_a_fixed_path_on_api_github_com() -> None:
    assert refs.GITHUB_API == "https://api.github.com"
    assert refs.github_target("acme/widgets#12", "pull_request") == "/repos/acme/widgets/pulls/12"
    assert refs.github_target("acme/widgets#12", "issue") == "/repos/acme/widgets/issues/12"


@pytest.mark.parametrize(
    "ref",
    ["acme/../x#1", "acme/wid%2Fgets#1", "a cme/widgets#1", "acme#1", "acme/widgets",
     "acme/widgets#1x", "../acme/widgets#1", "acme/widgets#12345678901"],
)
def test_a_stored_ref_that_is_not_a_clean_name_builds_no_request(ref: str) -> None:
    """The row is data, so the path is checked again before it is built."""
    assert refs.github_target(ref, "pull_request") is None


@pytest.mark.parametrize(
    ("kind", "body", "state"),
    [
        ("pull_request", {"state": "open"}, "open"),
        ("pull_request", {"state": "open", "draft": True}, "draft"),
        ("pull_request", {"state": "closed", "merged": True}, "merged"),
        ("pull_request", {"state": "closed", "merged_at": "2026-10-01T00:00:00Z"}, "merged"),
        ("pull_request", {"state": "closed"}, "closed"),
        ("issue", {"state": "open"}, "open"),
        ("issue", {"state": "closed"}, "closed"),
        ("issue", {"state": "weird"}, None),
    ],
)
def test_the_state_maps_to_the_four_words(kind: str, body: dict, state: str | None) -> None:
    assert refs.github_state(kind, body) == state


# ── Hermetic: the read ──────────────────────────────────────────────────────


async def test_with_no_token_no_request_is_built_and_the_rows_stay(monkeypatch) -> None:
    log, opened = _fake_sessions(monkeypatch)
    calls = _github(monkeypatch, (200, {"title": "x", "state": "open"}), token=None)
    rows = [_row(1, title="Mine")]
    out = await refs.refresh_from_github(TASK, rows)
    assert out == rows
    assert calls == []
    assert opened.total == 0 and log == []


@pytest.mark.parametrize(
    "answer",
    [
        httpx.ConnectTimeout("slow"),
        TimeoutError(),
        httpx.ConnectError("refused"),
        (404, {"message": "Not Found"}),
        (500, {"message": "boom"}),
        (200, ["not", "an", "object"]),
    ],
)
async def test_a_failed_read_keeps_title_and_state_and_stamps_the_attempt(
    answer: Any, monkeypatch,
) -> None:
    """A failed attempt writes ONLY `fetched_at`, so it is not tried again for
    15 minutes and it does not hold a read slot (review P2, 2026-10-13)."""
    log, opened = _fake_sessions(monkeypatch)
    calls = _github(monkeypatch, answer, opened=opened)
    rows = [_row(1, title="Mine", state="open")]
    out = await refs.refresh_from_github(TASK, rows)
    assert len(calls) == 1
    assert (out[0]["title"], out[0]["state"]) == ("Mine", "open")
    assert out[0]["fetched_at"], "a failed attempt was not stamped"
    assert len(log) == 1
    assert "SET fetched_at = now()" in log[0]["sql"]
    assert "title" not in log[0]["sql"] and "state" not in log[0]["sql"]


@pytest.mark.parametrize("status", [404, 301, 410, 403, 429])
async def test_a_refusal_is_not_read_again_inside_fifteen_minutes(status: int, monkeypatch) -> None:
    _fake_sessions(monkeypatch)
    calls = _github(monkeypatch, (status, {"message": "no"}))
    now = datetime(2026, 10, 13, 12, 0, tzinfo=UTC)
    first = await refs.refresh_from_github(TASK, [_row(1, title="Mine")], now=now)
    assert len(calls) == 1
    later = now + timedelta(minutes=14)
    await refs.refresh_from_github(TASK, first, now=later)
    assert len(calls) == 1, "a refused row was read again inside 15 minutes"
    await refs.refresh_from_github(TASK, first, now=now + timedelta(minutes=15))
    assert len(calls) == 2, "a refused row was never read again"


async def test_failing_rows_cannot_starve_a_readable_one(monkeypatch) -> None:
    """Ten never-read rows that GitHub refuses come first in the order. The
    eleventh, a good row, is read on the NEXT call, because the ten were
    stamped. Unstamped, they would hold all ten slots on every call."""
    _fake_sessions(monkeypatch)
    calls: list[str] = []

    class _Mixed(_FakeGitHub):
        async def get(self, url: str, headers: dict | None = None) -> httpx.Response:
            self.calls.append(url)
            status = 200 if url.endswith("/11") else 404
            body = {"title": "Good", "state": "open"} if status == 200 else {}
            return httpx.Response(status, json=body, request=httpx.Request("GET", url))

    async def _token() -> str:
        return "tok"

    monkeypatch.setattr(refs, "_github_token", _token)
    monkeypatch.setattr(
        refs, "httpx", SimpleNamespace(AsyncClient=lambda **_kw: _Mixed(None, calls, None)),
    )
    now = datetime(2026, 10, 13, 12, 0, tzinfo=UTC)
    rows = [_row(i) for i in range(1, 12)]  # ten that fail, then the good one
    rows = await refs.refresh_from_github(TASK, rows, now=now)
    assert len(calls) == 10 and not any(c.endswith("/11") for c in calls)
    rows = await refs.refresh_from_github(TASK, rows, now=now + timedelta(minutes=1))
    assert calls[-1].endswith("/11") and len(calls) == 11
    good = next(r for r in rows if r["ref"] == "acme/widgets#11")
    assert (good["title"], good["state"]) == ("Good", "open")


async def test_the_read_runs_with_no_session_open_and_one_session_saves(monkeypatch) -> None:
    log, opened = _fake_sessions(monkeypatch)
    calls = _github(
        monkeypatch, (200, {"title": "Fix the login", "state": "closed", "merged": True}),
        opened=opened,
    )
    out = await refs.refresh_from_github(TASK, [_row(1)])
    assert calls == ["https://api.github.com/repos/acme/widgets/pulls/1"]
    assert opened.total == 1, "the result is written in exactly one new session"
    assert len(log) == 1 and log[0]["sql"].lstrip().startswith("UPDATE pm_task_refs")
    assert log[0]["params"]["state"] == "merged"
    assert out[0]["title"] == "Fix the login" and out[0]["state"] == "merged"
    assert out[0]["fetched_at"]


async def test_at_most_ten_rows_are_read_and_only_the_ones_due(monkeypatch) -> None:
    _fake_sessions(monkeypatch)
    calls = _github(monkeypatch, (200, {"title": "t", "state": "open"}))
    now = datetime(2026, 10, 13, 12, 0, tzinfo=UTC)
    fresh = (now - timedelta(minutes=5)).isoformat()
    stale = (now - timedelta(minutes=16)).isoformat()
    rows = [_row(i) for i in range(1, 13)]  # twelve never read
    rows += [
        _row(20, fetched_at=fresh),                                   # read 5 min ago
        _row(21, kind="commit", ref="acme/widgets@abcdef1"),          # no state to read
        _row(22, provider="gitlab", ref="acme/api!3"),                # not GitHub
        _row(23, kind="issue", fetched_at=stale),                     # due again
    ]
    await refs.refresh_from_github(TASK, rows, now=now)
    assert len(calls) == refs.MAX_READS == 10
    assert not any(c.endswith(("/20", "/21", "/22")) for c in calls)


async def test_a_fresh_read_is_not_repeated(monkeypatch) -> None:
    _fake_sessions(monkeypatch)
    calls = _github(monkeypatch, (200, {"title": "t", "state": "open"}))
    now = datetime(2026, 10, 13, 12, 0, tzinfo=UTC)
    await refs.refresh_from_github(
        TASK, [_row(1, fetched_at=(now - timedelta(minutes=14)).isoformat())], now=now,
    )
    assert calls == []


def test_the_module_calls_one_fixed_host_and_not_the_outbound_guard() -> None:
    """The read builds no URL a caller chose, so `outbound_guard` (which is for
    such URLs) is not the control here. The fixed host is."""
    source = _REFS_SOURCE.read_text(encoding="utf-8")
    assert "outbound_guard" not in source.split('"""', 2)[2]
    hosts = {h for h in __import__("re").findall(r"https?://([a-z0-9.-]+)", source)}
    assert hosts == {"api.github.com"}


def test_the_routes_are_mounted() -> None:
    from gateway.routes.projects import router

    mounted = {(m, r.path) for r in router.routes for m in getattr(r, "methods", set())}
    for pair in (
        ("GET", "/projects/tasks/{task_id}/refs"),
        ("POST", "/projects/tasks/{task_id}/refs"),
        ("DELETE", "/projects/tasks/{task_id}/refs/{ref_id}"),
    ):
        assert pair in mounted, pair


# ── R8: the route on a real database ────────────────────────────────────────


def _migration() -> Path:
    """The migration that creates ``pm_task_refs``, found by content (R1)."""
    found = [
        p for p in sorted(_MIGRATIONS.glob("*.sql"))
        if p.name != "schema.generated.sql"
        and "CREATE TABLE IF NOT EXISTS pm_task_refs" in p.read_text(encoding="utf-8")
    ]
    assert len(found) == 1, [p.name for p in found]
    return found[0]


def _principal(email: str, org: str, *, unrestricted: bool = True) -> Any:
    from acb_auth import UserContext, UserRole, build_access

    perms = ["feature:projects", "data:org:read"] if unrestricted else ["feature:projects"]
    return UserContext(
        email=email, role=UserRole.EMPLOYEE, access=build_access(perms),
        organization_id=org,
    )


async def _call(
    p: Any, user: Any, method: str, path: str, body: dict | None = None,
    headers: dict | None = None,
) -> httpx.Response:
    """One request through the real ``/projects`` router, as *user*. The
    override binds the tenant from the user, as ``_with_resolved_access``
    does for a real request. The tenant never comes from the request."""
    from acb_auth import get_current_user
    from acb_common.db import bind_tenant, clear_tenant, release_tenant
    from fastapi import FastAPI
    from gateway.routes import projects as projects_package

    async def _caller() -> Any:
        bind_tenant(user.organization_id)
        return user

    app = FastAPI()
    app.include_router(projects_package.router)
    app.dependency_overrides[get_current_user] = _caller
    scope = clear_tenant()
    try:
        async with (
            tenant_engine_scope(p.app_url.render_as_string(hide_password=False)),
            httpx.AsyncClient(
                transport=httpx.ASGITransport(app=app), base_url="http://projects.test",
            ) as client,
        ):
            return await client.request(method, path, json=body, headers=headers)
    finally:
        release_tenant(scope)


def _seed_task(p: Any, org: str, email: str, *, grant: str | None = None) -> str:
    """A member, a project, a lane and a task in *org*, as the RLS-bypassing
    admin. Every INSERT stamps ``organization_id`` itself. *grant* names the
    one subject the project is granted to, for a member without
    ``data:org:read``."""
    project, status, task = (str(uuid.uuid4()) for _ in range(3))
    with p.admin_engine.begin() as c:
        c.execute(text(
            "INSERT INTO app_user (email, display_name, role, status, organization_id) "
            "VALUES (:e, :e, 'employee', 'active', CAST(:o AS uuid)) "
            "ON CONFLICT DO NOTHING"), {"e": email, "o": org})
        c.execute(text(
            "INSERT INTO pm_projects (id, organization_id, name, created_by, owns_statuses) "
            "VALUES (:id, :o, 'refs', :e, true)"), {"id": project, "o": org, "e": email})
        if grant is not None:
            c.execute(text(
                "INSERT INTO pm_project_grants (organization_id, project_id, subject, "
                "created_by) VALUES (:o, :p, :s, :e)"),
                {"o": org, "p": project, "s": grant, "e": email})
        c.execute(text(
            "INSERT INTO pm_task_statuses (id, organization_id, project_id, name, category, "
            "position) VALUES (:id, :o, :p, 'To do', 'todo', 10)"),
            {"id": status, "o": org, "p": project})
        c.execute(text(
            "INSERT INTO pm_tasks (id, organization_id, project_id, root_project_id, "
            "task_number, status_id, title, created_by) "
            "VALUES (:id, :o, :p, :p, 1, :s, 'Fix the extruder', :e)"),
            {"id": task, "o": org, "p": project, "s": status, "e": email})
    return task


def _count(p: Any, sql: str, **params: Any) -> int:
    with p.admin_engine.connect() as c:
        return int(c.execute(text(sql), params).scalar())


def _links(p: Any, task: str) -> int:
    return _count(
        p, "SELECT count(*) FROM pm_activities WHERE task_id = CAST(:t AS uuid) "
        "AND type = 'link'", t=task,
    )


def _refs_of(p: Any, task: str) -> int:
    return _count(p, "SELECT count(*) FROM pm_task_refs WHERE task_id = CAST(:t AS uuid)", t=task)


@pytest.fixture(scope="module")
def world(promoted):  # noqa: F811 — a fixture
    """One member and one task in each of the two organizations."""
    a_email, b_email = "tr1-a@refs-r8.test", "tr1-b@refs-r8.test"
    m_email = "tr1-m@refs-r8.test"
    ns = SimpleNamespace(
        p=promoted,
        a=_principal(a_email, promoted.org_a),
        b=_principal(b_email, promoted.org_b),
        # A member of org A WITHOUT `data:org:read`: they see a project only
        # through a grant (review note 4, the same-org DELETE scoping).
        m=_principal(m_email, promoted.org_a, unrestricted=False),
        task_a=_seed_task(promoted, promoted.org_a, a_email),
        task_b=_seed_task(promoted, promoted.org_b, b_email),
        task_mine=_seed_task(promoted, promoted.org_a, m_email, grant=m_email),
        task_private=_seed_task(promoted, promoted.org_a, a_email, grant=a_email),
    )
    return ns


@pytest.fixture
def w(world, monkeypatch):
    """The world, with no ref and no link activity left from the last test,
    and with no GitHub token unless a test sets one."""
    async def _no_token() -> None:
        return None

    monkeypatch.setattr(refs, "_github_token", _no_token)
    with world.p.admin_engine.begin() as c:
        c.execute(text("DELETE FROM pm_task_refs"))
        c.execute(text("DELETE FROM pm_activities WHERE type = 'link'"))
    return world


@_DB_GATE
class TestTheRouteOnARealDatabase:
    async def test_the_route_runs_as_a_role_that_cannot_bypass_rls(self, w, app_engine):  # noqa: F811
        with app_engine.connect() as c:
            role = c.execute(text(
                "SELECT rolsuper, rolbypassrls FROM pg_roles "
                "WHERE rolname = current_user")).first()
        assert role is not None and not role[0] and not role[1]

    async def test_add_list_and_remove_each_write_one_link_activity(self, w):
        made = await _call(w.p, w.a, "POST", f"/projects/tasks/{w.task_a}/refs",
                           {"url": PR_URL, "title": "Fix the login"})
        assert made.status_code == 201, made.text
        row = made.json()
        assert (row["kind"], row["provider"], row["ref"], row["label"]) == (
            "pull_request", "github", "acme/widgets#12", "widgets#12")
        assert row["title"] == "Fix the login" and row["via"] == "ui"
        assert row["created_by"] == "tr1-a@refs-r8.test"
        assert _links(w.p, w.task_a) == 1

        listed = await _call(w.p, w.a, "GET", f"/projects/tasks/{w.task_a}/refs")
        assert listed.status_code == 200
        assert [r["id"] for r in listed.json()["rows"]] == [row["id"]]

        gone = await _call(w.p, w.a, "DELETE", f"/projects/tasks/{w.task_a}/refs/{row['id']}")
        assert gone.status_code == 200 and gone.json() == {"deleted": row["id"]}
        assert _refs_of(w.p, w.task_a) == 0
        assert _links(w.p, w.task_a) == 2
        with w.p.admin_engine.connect() as c:
            bodies = [r[0] for r in c.execute(text(
                "SELECT body FROM pm_activities WHERE task_id = CAST(:t AS uuid) "
                "AND type = 'link' ORDER BY created_at, id"), {"t": w.task_a})]
        assert sorted(bodies) == sorted([
            "Added a reference: widgets#12", "Removed a reference: widgets#12"])

        again = await _call(w.p, w.a, "DELETE", f"/projects/tasks/{w.task_a}/refs/{row['id']}")
        assert again.status_code == 404
        assert _links(w.p, w.task_a) == 2

    async def test_a_second_add_of_the_same_reference_returns_the_first_row(self, w):
        first = await _call(w.p, w.a, "POST", f"/projects/tasks/{w.task_a}/refs", {"url": PR_URL})
        second = await _call(w.p, w.a, "POST", f"/projects/tasks/{w.task_a}/refs",
                             {"url": PR_URL + "/files", "title": "Other"})
        assert first.status_code == 201 and second.status_code == 200
        assert second.json()["id"] == first.json()["id"]
        assert second.json()["title"] is None, "the second add changed the first row"
        assert _refs_of(w.p, w.task_a) == 1
        assert _links(w.p, w.task_a) == 1

    async def test_another_organizations_task_is_404_and_its_rows_stay(self, w):
        theirs = await _call(w.p, w.b, "POST", f"/projects/tasks/{w.task_b}/refs", {"url": PR_URL})
        assert theirs.status_code == 201
        ref_b = theirs.json()["id"]

        for method, path, body in (
            ("GET", f"/projects/tasks/{w.task_b}/refs", None),
            ("POST", f"/projects/tasks/{w.task_b}/refs", {"url": "https://example.com/x"}),
            ("DELETE", f"/projects/tasks/{w.task_b}/refs/{ref_b}", None),
        ):
            answer = await _call(w.p, w.a, method, path, body)
            assert answer.status_code == 404, (method, path, answer.text)
        # B's row named through A's own task: RLS hides it, so nothing matches.
        crossed = await _call(w.p, w.a, "DELETE", f"/projects/tasks/{w.task_a}/refs/{ref_b}")
        assert crossed.status_code == 404
        assert _refs_of(w.p, w.task_b) == 1
        assert _links(w.p, w.task_b) == 1
        assert _links(w.p, w.task_a) == 0

    async def test_a_refused_url_is_422_and_writes_nothing(self, w):
        for url in ("javascript:alert(1)", "https://github.com/../../pull/1"):
            answer = await _call(w.p, w.a, "POST", f"/projects/tasks/{w.task_a}/refs",
                                 {"url": url})
            assert answer.status_code == 422, url
        assert _refs_of(w.p, w.task_a) == 0 and _links(w.p, w.task_a) == 0

    async def test_the_chat_header_stamps_via_and_the_timeline(self, w):
        made = await _call(w.p, w.a, "POST", f"/projects/tasks/{w.task_a}/refs",
                           {"url": "https://docs.google.com/document/d/1AbCdEfGhIjK/edit"},
                           headers={"X-Actor-Via": "chat:projects-assistant"})
        assert made.status_code == 201 and made.json()["via"] == "chat"
        with w.p.admin_engine.connect() as c:
            via = c.execute(text(
                "SELECT meta->>'via' FROM pm_activities WHERE task_id = CAST(:t AS uuid) "
                "AND type = 'link'"), {"t": w.task_a}).scalar()
        assert via == "chat:projects-assistant"

    async def test_the_real_no_token_path_keeps_the_row_and_answers(self, w, monkeypatch):
        """No GitHub connection in this organization: the real token reader
        answers ``None``, and the route still answers."""
        monkeypatch.setattr(refs, "_github_token", _REAL_TOKEN_READER)
        calls: list[str] = []
        monkeypatch.setattr(
            refs, "httpx",
            SimpleNamespace(AsyncClient=lambda **_kw: _FakeGitHub((200, {}), calls, None)),
        )
        made = await _call(w.p, w.a, "POST", f"/projects/tasks/{w.task_a}/refs",
                           {"url": PR_URL, "title": "Mine"})
        assert made.status_code == 201, made.text
        assert made.json()["title"] == "Mine" and made.json()["state"] is None
        assert made.json()["fetched_at"] is None
        assert calls == []
        listed = await _call(w.p, w.a, "GET", f"/projects/tasks/{w.task_a}/refs")
        assert listed.status_code == 200 and len(listed.json()["rows"]) == 1

    @pytest.mark.parametrize(
        "answer",
        [httpx.ConnectTimeout("slow"), (502, {"message": "bad gateway"}),
         (404, {"message": "Not Found"}), (429, {"message": "rate limited"})],
    )
    async def test_a_github_failure_keeps_the_row_and_answers(self, w, monkeypatch, answer):
        """The row keeps its title and state, the attempt is stamped, and a
        list inside 15 minutes does not try again (review P2)."""
        calls = _github(monkeypatch, answer)
        made = await _call(w.p, w.a, "POST", f"/projects/tasks/{w.task_a}/refs",
                           {"url": PR_URL, "title": "Mine"})
        assert made.status_code == 201, made.text
        listed = await _call(w.p, w.a, "GET", f"/projects/tasks/{w.task_a}/refs")
        assert listed.status_code == 200
        row = listed.json()["rows"][0]
        assert (row["title"], row["state"]) == ("Mine", None)
        assert row["fetched_at"] is not None, "the failed attempt was not stamped"
        assert len(calls) == 1, "the list read GitHub again inside 15 minutes"
        with w.p.admin_engine.connect() as c:
            title, state, fetched = c.execute(text(
                "SELECT title, state, fetched_at FROM pm_task_refs WHERE id = CAST(:r AS uuid)"),
                {"r": made.json()["id"]}).one()
        assert (title, state) == ("Mine", None) and fetched is not None

    async def test_refresh_false_reads_the_stored_rows_only(self, w, monkeypatch):
        """The chat's reads send `refresh=false`, so they call nothing
        outside and stay `open_world=False`."""
        made = await _call(w.p, w.a, "POST", f"/projects/tasks/{w.task_a}/refs", {"url": PR_URL})
        assert made.status_code == 201
        calls = _github(monkeypatch, (200, {"title": "t", "state": "open"}))
        listed = await _call(w.p, w.a, "GET",
                             f"/projects/tasks/{w.task_a}/refs?refresh=false")
        assert listed.status_code == 200 and len(listed.json()["rows"]) == 1
        assert calls == []

    async def test_a_second_add_in_another_case_returns_the_first_row(self, w):
        """GitHub names are case-insensitive, so the key is lower case. The
        stored URL keeps the case the member typed."""
        first = await _call(w.p, w.a, "POST", f"/projects/tasks/{w.task_a}/refs",
                            {"url": "https://github.com/Acme/Widgets/pull/12"})
        second = await _call(w.p, w.a, "POST", f"/projects/tasks/{w.task_a}/refs",
                             {"url": "https://github.com/acme/widgets/pull/12/files"})
        assert first.status_code == 201 and second.status_code == 200
        assert second.json()["id"] == first.json()["id"]
        assert first.json()["ref"] == "acme/widgets#12"
        assert first.json()["url"] == "https://github.com/Acme/Widgets/pull/12"
        assert _refs_of(w.p, w.task_a) == 1 and _links(w.p, w.task_a) == 1

    async def test_a_task_id_that_is_not_a_uuid_is_404(self, w):
        for method, body in (("GET", None), ("POST", {"url": PR_URL}), ("DELETE", None)):
            path = "/projects/tasks/not-a-uuid/refs"
            if method == "DELETE":
                path += f"/{uuid.uuid4()}"
            answer = await _call(w.p, w.a, method, path, body)
            assert answer.status_code == 404, (method, answer.status_code, answer.text)

    async def test_in_one_org_a_hidden_tasks_ref_cannot_be_removed(self, w):
        """Review note 4. RLS does not separate two tasks of ONE org, so the
        route's own checks are the only guard here. The member sees their own
        task and not the private one."""
        theirs = await _call(w.p, w.a, "POST", f"/projects/tasks/{w.task_private}/refs",
                             {"url": PR_URL})
        assert theirs.status_code == 201
        ref = theirs.json()["id"]
        mine = await _call(w.p, w.m, "GET", f"/projects/tasks/{w.task_mine}/refs")
        assert mine.status_code == 200, "the member cannot see their own task"

        # (1) The hidden task's ref, named through the member's OWN task. The
        # `AND task_id = …` of the DELETE is what refuses it.
        crossed = await _call(w.p, w.m, "DELETE", f"/projects/tasks/{w.task_mine}/refs/{ref}")
        assert crossed.status_code == 404
        # (2) The hidden task named directly. `load_visible_task` refuses it.
        direct = await _call(w.p, w.m, "DELETE", f"/projects/tasks/{w.task_private}/refs/{ref}")
        assert direct.status_code == 404
        for method, body in (("GET", None), ("POST", {"url": "https://example.com/x"})):
            answer = await _call(w.p, w.m, method, f"/projects/tasks/{w.task_private}/refs", body)
            assert answer.status_code == 404, method
        assert _refs_of(w.p, w.task_private) == 1
        assert _links(w.p, w.task_private) == 1 and _links(w.p, w.task_mine) == 0

    async def test_a_github_read_is_saved_outside_the_transaction(self, w, monkeypatch):
        opened = _count_sessions(monkeypatch, refs._tenant_session)
        calls = _github(
            monkeypatch, (200, {"title": "Fix the login", "state": "closed", "merged": True}),
            opened=opened,
        )
        made = await _call(w.p, w.a, "POST", f"/projects/tasks/{w.task_a}/refs", {"url": PR_URL})
        assert made.status_code == 201, made.text
        assert made.json()["state"] == "merged" and made.json()["title"] == "Fix the login"
        assert calls == ["https://api.github.com/repos/acme/widgets/pulls/12"]
        assert opened.total == 2, "one session to add, one to save the read"
        with w.p.admin_engine.connect() as c:
            state, fetched = c.execute(text(
                "SELECT state, fetched_at FROM pm_task_refs WHERE id = CAST(:r AS uuid)"),
                {"r": made.json()["id"]}).one()
        assert state == "merged" and fetched is not None

        # Fresh: a list does not read again.
        await _call(w.p, w.a, "GET", f"/projects/tasks/{w.task_a}/refs")
        assert len(calls) == 1
        # Stale: a list reads again.
        with w.p.admin_engine.begin() as c:
            c.execute(text(
                "UPDATE pm_task_refs SET fetched_at = now() - interval '20 minutes'"))
        await _call(w.p, w.a, "GET", f"/projects/tasks/{w.task_a}/refs")
        assert len(calls) == 2

    async def test_the_table_forces_row_level_security(self, w, app_engine):  # noqa: F811
        made = await _call(w.p, w.b, "POST", f"/projects/tasks/{w.task_b}/refs", {"url": PR_URL})
        assert made.status_code == 201
        assert _refs_of(w.p, w.task_b) == 1
        with app_engine.connect() as c:
            unbound = c.execute(text("SELECT count(*) FROM pm_task_refs")).scalar()
        assert unbound == 0, "an unbound session read a tenant's references"
        with pytest.raises(DBAPIError), app_engine.begin() as c:
            c.execute(text("SELECT set_config('app.tenant_id', :o, true)"), {"o": w.p.org_a})
            c.execute(text(
                "INSERT INTO pm_task_refs (organization_id, task_id, kind, provider, ref, "
                "url, created_by) VALUES (CAST(:b AS uuid), CAST(:t AS uuid), 'url', "
                "'other', 'x', 'https://x.test/', 'x')"),
                {"b": w.p.org_b, "t": w.task_b})

    async def test_the_migration_replays_with_no_change(self, w):
        await _call(w.p, w.a, "POST", f"/projects/tasks/{w.task_a}/refs", {"url": PR_URL})
        with (
            w.p.admin_engine.begin() as c,
            c.connection.dbapi_connection.cursor() as cur,
        ):
            cur.execute(_migration().read_text(encoding="utf-8"))
        assert _refs_of(w.p, w.task_a) == 1
        with w.p.admin_engine.connect() as c:
            forced = c.execute(text(
                "SELECT relrowsecurity AND relforcerowsecurity FROM pg_class "
                "WHERE oid = to_regclass('pm_task_refs')")).scalar()
        assert forced is True
