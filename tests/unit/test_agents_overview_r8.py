"""WS-AGA AGA-6 and AGA-7 — the member view of agents (``agents_view.md``).

``GET /projects/agents/overview`` lists every agent that the caller may see:
the built-in agents ("shared by Metorite"), the agents that the org
registered, and the outside agents of the task runs in ``pm_agent_runs``.
Each row has a state (working, idle, waiting on a person), the last run and
the last error in plain words. ``AGENTS_VIEW_ORGS`` keeps it dark.

**R8**, a real Postgres, through the real ``/projects`` router as the
non-privileged role (FORCE RLS). Two orgs, each with agents and runs of its
own, and three viewer levels in org A (admin, lead, member).

R7, the mutations (each turns a test here red, see the AGA-7 report):

* **M1** ``agents_view_allowed`` answers True for each org -> the flag tests.
* **M2** the route takes the org of the presence and of the flag from the
  query string, not the session -> the hostile-input test.
* **M3** ``_reach_sql`` drops the member arm (``lower(r.member_email) = :me``)
  -> the member test.
* **M4** ``_reach_sql`` drops the lead arm (``:led``) -> the lead test.
* **M5** the row name of an outside agent reads the self-declared
  ``agent_connection.client_name`` -> the spoofed-name test.
* **M6** ``_reach_sql`` drops ``lower()`` in one arm -> the mixed-case test.
"""

from __future__ import annotations

import json
from types import SimpleNamespace
from typing import Any

import pytest

pytest.importorskip("sqlalchemy")
pytest.importorskip("gateway.routes.projects", reason="gateway not installed")

from sqlalchemy import text

from tests.unit import _agent_board_r8 as b
from tests.unit.test_h3_rls_promotion_rehearsal import (  # noqa: F401
    _DB_GATE,
    app_engine,
    promoted,
)

ADMIN_A = "aga7-admin@a-overview.test"
LEAD_A = "aga7-lead@a-overview.test"
MEMBER_A = "aga7-member@a-overview.test"
OTHER_A = "aga7-other@a-overview.test"
ADMIN_B = "aga7-admin@b-overview.test"
#: A member whose session email is in mixed case. Each stored value below is
#: in another case, so each arm of the reach matches only through lower().
MIXED_SESSION = "AGA7-Mixed.Member@A-Overview.TEST"
MIXED_CREATOR = "aga7-MIXED.member@a-overview.test"
MIXED_ASSIGNEE = "Aga7-Mixed.MEMBER@a-overview.test"
MIXED_RUNNER = "aga7-mixed.Member@A-OVERVIEW.test"
MIXED_LEAD = "AGA7-MIXED.MEMBER@A-OVERVIEW.TEST"

#: One stamp for each outside agent.
VIA_X = "mcp:acme:x-agent"  # on the member's own task; org B has one too
VIA_Y = "mcp:acme:y-agent"  # awaiting on another member's task
VIA_Z = "mcp:acme:z-agent"  # failed on a task of the led project
VIA_W = "mcp:acme:w-agent"  # the member's own run on another member's task
VIA_C = "mcp:acme:c-agent"  # an open run on a revoked connection
VIA_M1 = "mcp:acme:m1-agent"  # on a task the mixed-case member created
VIA_M2 = "mcp:acme:m2-agent"  # on a task assigned to the mixed-case member
VIA_M3 = "mcp:acme:m3-agent"  # the mixed-case member's own run
VIA_M4 = "mcp:acme:m4-agent"  # on a task of the project the mixed-case member leads
MIXED_VIAS = {VIA_M1, VIA_M2, VIA_M3, VIA_M4}
#: An outside agent whose connection declares the name "Claude Code".
VIA_E = "mcp:evil.example:claude-code"
SPOOF = "Claude Code"

ORG_AGENT_A = "aga7-a-sales-helper"
ORG_AGENT_B = "aga7-b-only-agent"
ERROR_BODY = "The build step timed out after 30 minutes"

PATH = "/projects/agents/overview"


# ── Hermetic: the flag, its reader and its registration ─────────────────────


def _settings() -> Any:
    from acb_common import get_settings

    return get_settings()


@pytest.mark.parametrize(
    ("raw", "org", "want"),
    [
        ("", "org-1", False),
        ("   ", "org-1", False),
        ("org-1", "org-1", True),
        ("ORG-1", "org-1", True),
        ("org-2, org-1", "org-1", True),
        ("org-2", "org-1", False),
        ("*", "org-1", True),
        ("*", "", False),
        ("*", None, False),
    ],
)
def test_the_flag_reader(monkeypatch, raw, org, want) -> None:
    from gateway.routes.projects import agent_summary as m

    monkeypatch.setattr(_settings(), "agents_view_orgs", raw)
    assert m.agents_view_allowed(org) is want


def test_the_flag_is_empty_by_default_and_registered() -> None:
    from pathlib import Path

    from acb_common.env_guard import is_platform_env
    from acb_common.settings import Settings
    from gateway import flag_reload as fr

    assert Settings.model_fields["agents_view_orgs"].default == ""
    assert fr.reloadable("AGENTS_VIEW_ORGS")
    assert is_platform_env("AGENTS_VIEW_ORGS")
    root = Path(__file__).resolve().parents[2]
    assert "\nAGENTS_VIEW_ORGS=\n" in (root / ".env.example").read_text(encoding="utf-8")


def test_the_one_line_job_of_a_registry_description() -> None:
    from gateway.routes.projects.agent_summary import _split_description

    assert _split_description(
        "projects-assistant",
        "Projects Assistant — the AI chat inside the Projects app. Reads the tree.",
    ) == ("Projects Assistant", "The AI chat inside the Projects app.")
    assert _split_description("x", "") == ("x", "No job description yet.")
    assert _split_description("x", "Reads mail.\nWrites\tdrafts.") == ("x", "Reads mail.")


def _keys(value: Any) -> set[str]:
    if isinstance(value, dict):
        return set(value) | {k for v in value.values() for k in _keys(v)}
    if isinstance(value, list):
        return {k for v in value for k in _keys(v)}
    return set()


# ── R8: the world ───────────────────────────────────────────────────────────


@pytest.fixture(scope="module")
def world(promoted):  # noqa: F811 — a fixture
    p = promoted
    ns = SimpleNamespace(p=p)
    ns.admin_a = b._principal(ADMIN_A, p.org_a)
    ns.lead_a = b._principal(LEAD_A, p.org_a, unrestricted=False)
    ns.member_a = b._principal(MEMBER_A, p.org_a, unrestricted=False)
    ns.admin_b = b._principal(ADMIN_B, p.org_b)
    ns.mixed_a = b._principal(MIXED_SESSION, p.org_a, unrestricted=False)
    b.member(p, p.org_a, MIXED_SESSION.lower())
    for email in (ADMIN_A, LEAD_A, MEMBER_A, OTHER_A):
        b.member(p, p.org_a, email)
    b.member(p, p.org_b, ADMIN_B)
    return ns


def _seed(w: Any) -> None:
    p = w.p
    b.clear(p)
    with p.admin_engine.begin() as c:
        c.execute(text("DELETE FROM dynamic_agents WHERE name LIKE 'aga7-%'"))
        for org, name in ((p.org_a, ORG_AGENT_A), (p.org_b, ORG_AGENT_B)):
            c.execute(text(
                "INSERT INTO dynamic_agents (name, description, organization_id) "
                "VALUES (:n, :d, CAST(:o AS uuid))"),
                {"n": name, "d": "Sales Helper — drafts the weekly pipeline note.", "o": org})
    w.led = b.project(p, p.org_a, OTHER_A, grant="org")
    w.plain = b.project(p, p.org_a, OTHER_A, grant="org")
    with p.admin_engine.begin() as c:
        c.execute(text("UPDATE pm_projects SET lead = :l WHERE id = CAST(:p AS uuid)"),
                  {"l": LEAD_A, "p": w.led["id"]})
    own = b.task(p, p.org_a, MEMBER_A, w.plain)
    theirs = b.task(p, p.org_a, OTHER_A, w.plain)
    theirs_2 = b.task(p, p.org_a, OTHER_A, w.plain)
    led_task = b.task(p, p.org_a, OTHER_A, w.led)
    gone_task = b.task(p, p.org_a, OTHER_A, w.plain)
    b.run(p, p.org_a, own, OTHER_A, "in_progress", via=VIA_X)
    b.run(p, p.org_a, theirs, OTHER_A, "awaiting", via=VIA_Y)
    failed = b.run(p, p.org_a, led_task, OTHER_A, "failed", via=VIA_Z)
    b.run(p, p.org_a, theirs_2, MEMBER_A, "completed", via=VIA_W)
    gone = b.run(p, p.org_a, gone_task, OTHER_A, "in_progress", via=VIA_C)
    conn = b._seed_connection(p, p.org_a, OTHER_A)
    with p.admin_engine.begin() as c:
        c.execute(text("UPDATE pm_agent_runs SET error_metadata = CAST(:e AS jsonb), "
                       "ended_at = now() WHERE id = CAST(:r AS uuid)"),
                  {"e": json.dumps({"body": ERROR_BODY + "\n\nstack line 2"}), "r": failed})
        c.execute(text("UPDATE agent_connection SET revoked_at = now() WHERE id = CAST(:c AS uuid)"),
                  {"c": conn})
        c.execute(text("UPDATE pm_agent_runs SET connection_id = CAST(:c AS uuid) "
                       "WHERE id = CAST(:r AS uuid)"), {"c": conn, "r": gone})
    # The connection declares "Claude Code" (``_seed_connection``), and its
    # stamp is of another host. The name is the host's, never the declared one.
    spoof_task = b.task(p, p.org_a, OTHER_A, w.plain)
    spoof = b.run(p, p.org_a, spoof_task, OTHER_A, "awaiting", via=VIA_E)
    spoof_conn = b._seed_connection(p, p.org_a, OTHER_A)
    with p.admin_engine.begin() as c:
        c.execute(text("UPDATE pm_agent_runs SET connection_id = CAST(:c AS uuid) "
                       "WHERE id = CAST(:r AS uuid)"), {"c": spoof_conn, "r": spoof})
    # The mixed-case member: one run for each arm of the reach.
    mixed_led = b.project(p, p.org_a, OTHER_A, grant="org")
    with p.admin_engine.begin() as c:
        c.execute(text("UPDATE pm_projects SET lead = :l WHERE id = CAST(:p AS uuid)"),
                  {"l": MIXED_LEAD, "p": mixed_led["id"]})
    b.run(p, p.org_a, b.task(p, p.org_a, MIXED_CREATOR, w.plain), OTHER_A, "completed", via=VIA_M1)
    b.run(p, p.org_a, b.task(p, p.org_a, OTHER_A, w.plain, assignee=MIXED_ASSIGNEE),
          OTHER_A, "completed", via=VIA_M2)
    b.run(p, p.org_a, b.task(p, p.org_a, OTHER_A, w.plain), MIXED_RUNNER, "completed", via=VIA_M3)
    b.run(p, p.org_a, b.task(p, p.org_a, OTHER_A, mixed_led), OTHER_A, "completed", via=VIA_M4)
    w.proj_b = b.project(p, p.org_b, ADMIN_B, grant="org")
    task_b = b.task(p, p.org_b, ADMIN_B, w.proj_b)
    b.run(p, p.org_b, task_b, ADMIN_B, "awaiting", via=VIA_X)


@pytest.fixture
def w(world, monkeypatch):
    from gateway.routes.projects import agent_summary as m

    monkeypatch.setattr(_settings(), "agent_runs_enabled", True)
    monkeypatch.setattr(_settings(), "agents_view_orgs", "*")
    presence = {
        world.p.org_a: [{"agent": "projects-assistant", "user": MEMBER_A, "ts": "2026-11-11T09:00:00Z"}],
        world.p.org_b: [{"agent": "crm-assistant", "user": ADMIN_B, "ts": "2026-11-11T09:01:00Z"}],
    }

    async def _fake_active_runs(*, organization_id: str | None) -> list[dict[str, Any]]:
        # The real reader scans the tenant's own Redis prefix only.
        return [dict(r) for r in presence.get(str(organization_id or ""), [])]

    monkeypatch.setattr(m, "active_runs", _fake_active_runs)
    if not getattr(world, "seeded", False):
        _seed(world)
        world.seeded = True
    return world


async def _get(w: Any, user: Any, query: str = "") -> tuple[int, Any]:
    async with b._session(w.p) as call:
        got = await call(user, "GET", PATH + query)
    try:
        body = got.json()
    except ValueError:
        body = got.text
    return got.status_code, body


def _by_key(body: dict[str, Any]) -> dict[str, dict[str, Any]]:
    return {row["key"]: row for row in body["agents"]}


def _external(body: dict[str, Any]) -> dict[str, dict[str, Any]]:
    return {row["agent"]: row for row in body["agents"] if row["source"] == "external"}


@_DB_GATE
class TestTheOverview:
    async def test_admin_a_sees_every_agent_of_org_a_and_none_of_org_b(self, w):
        status, body = await _get(w, w.admin_a)
        assert status == 200, body
        assert body["viewer"] == "admin"
        ext = _external(body)
        assert set(ext) == {VIA_X, VIA_Y, VIA_Z, VIA_W, VIA_C, VIA_E} | MIXED_VIAS
        # Org B's run of the same agent is not counted, and its state is not A's.
        assert ext[VIA_X]["runs"] == 1 and ext[VIA_X]["state"] == "working"
        rows = _by_key(body)
        assert f"org:{ORG_AGENT_A}" in rows and f"org:{ORG_AGENT_B}" not in rows
        assert rows[f"org:{ORG_AGENT_A}"]["source"] == "org"
        assert rows[f"org:{ORG_AGENT_A}"]["label"] is None
        assert rows["built_in:crm-assistant"]["state"] == "idle"  # org B's live run

    async def test_admin_b_sees_none_of_org_a(self, w):
        status, body = await _get(w, w.admin_b)
        assert status == 200, body
        ext = _external(body)
        assert set(ext) == {VIA_X}
        assert ext[VIA_X]["state"] == "waiting_on_person" and ext[VIA_X]["runs"] == 1
        rows = _by_key(body)
        assert f"org:{ORG_AGENT_B}" in rows and f"org:{ORG_AGENT_A}" not in rows
        assert rows["built_in:crm-assistant"]["state"] == "working"
        assert rows["built_in:projects-assistant"]["state"] == "idle"  # org A's live run
        assert ERROR_BODY not in json.dumps(body)

    async def test_built_in_agents_are_shared_by_metorite(self, w):
        _, body = await _get(w, w.member_a)
        built = [r for r in body["agents"] if r["source"] == "built_in"]
        assert built, body
        assert {r["label"] for r in built} == {"shared by Metorite"}
        assert "built_in:projects-assistant" in _by_key(body)
        row = _by_key(body)["built_in:projects-assistant"]
        assert row["name"] == "Projects Assistant" and row["job"]
        assert all(r["label"] is None for r in body["agents"] if r["source"] != "built_in")

    async def test_the_three_states_and_the_last_run(self, w):
        _, body = await _get(w, w.admin_a)
        ext = _external(body)
        assert ext[VIA_Y]["state"] == "waiting_on_person"
        assert ext[VIA_X]["state"] == "working"
        assert ext[VIA_W]["state"] == "idle"
        assert ext[VIA_W]["last_run"]["state"] == "completed"
        assert ext[VIA_W]["last_run"]["at"]
        assert {r["state"] for r in body["agents"]} <= {"working", "idle", "waiting_on_person"}
        live = _by_key(body)["built_in:projects-assistant"]
        assert live["state"] == "working" and live["last_run"]["at"]

    async def test_the_last_error_says_what_happened_and_the_next_step(self, w):
        _, body = await _get(w, w.admin_a)
        ext = _external(body)
        err = ext[VIA_Z]["last_error"]
        assert err["what"] == f"The last run failed. The agent said: {ERROR_BODY} stack line 2"
        assert err["next_step"].startswith("Open the task")
        assert "\n" not in err["what"]
        gone = ext[VIA_C]["last_error"]
        assert "revoked or has expired" in gone["what"]
        assert "Integrations, AI agents" in gone["next_step"]
        assert ext[VIA_X]["last_error"] is None

    async def test_a_declared_client_name_never_names_an_outside_agent(self, w):
        from acb_auth import agent_oauth

        _, body = await _get(w, w.admin_a)
        ext = _external(body)
        assert ext[VIA_E]["name"] == agent_oauth.client_label(VIA_E) == "evil.example"
        assert ext[VIA_E]["state"] == "waiting_on_person"
        assert ext[VIA_C]["name"] == agent_oauth.client_label(VIA_C)
        assert SPOOF not in json.dumps(body)

    async def test_no_cost_and_no_credit_field(self, w):
        _, body = await _get(w, w.admin_a)
        assert not {k for k in _keys(body) if "cost" in k.lower() or "credit" in k.lower()}

    async def test_a_member_sees_their_own_tasks_and_their_own_runs(self, w):
        _, body = await _get(w, w.member_a)
        assert body["viewer"] == "member"
        assert set(_external(body)) == {VIA_X, VIA_W}
        # The live presence counts the member's own run.
        assert _by_key(body)["built_in:projects-assistant"]["state"] == "working"
        assert f"org:{ORG_AGENT_A}" in _by_key(body)

    async def test_a_lead_sees_the_agents_of_the_project_they_lead(self, w):
        _, body = await _get(w, w.lead_a)
        assert body["viewer"] == "lead"
        assert set(_external(body)) == {VIA_Z}
        # Another member's live run is not the lead's.
        assert _by_key(body)["built_in:projects-assistant"]["state"] == "idle"

    async def test_the_reach_ignores_the_case_of_an_email(self, w):
        _, body = await _get(w, w.mixed_a)
        assert body["viewer"] == "lead"
        assert set(_external(body)) == MIXED_VIAS

    async def test_a_hostile_org_in_the_request_changes_nothing(self, w, monkeypatch):
        monkeypatch.setattr(_settings(), "agents_view_orgs", w.p.org_b)
        _, plain = await _get(w, w.admin_b)
        status, hostile = await _get(
            w, w.admin_b, f"?organization_id={w.p.org_a}&org={w.p.org_a}&me={MEMBER_A}")
        assert status == 200
        assert hostile == plain
        assert set(_external(hostile)) == {VIA_X}
        assert _by_key(hostile)["built_in:projects-assistant"]["state"] == "idle"
        # Org A is off, and a name in the request does not turn it on.
        status, _ = await _get(w, w.admin_a, f"?organization_id={w.p.org_b}")
        assert status == 404


@_DB_GATE
class TestTheFlag:
    async def test_with_the_flag_empty_every_route_is_404(self, w, monkeypatch):
        monkeypatch.setattr(_settings(), "agents_view_orgs", "")
        for user in (w.admin_a, w.member_a, w.lead_a, w.admin_b):
            status, _ = await _get(w, user)
            assert status == 404

    async def test_with_org_a_on_org_b_is_still_404(self, w, monkeypatch):
        monkeypatch.setattr(_settings(), "agents_view_orgs", w.p.org_a)
        assert (await _get(w, w.admin_a))[0] == 200
        assert (await _get(w, w.member_a))[0] == 200
        assert (await _get(w, w.admin_b))[0] == 404

    async def test_the_runs_flag_off_keeps_the_cast_and_no_run(self, w, monkeypatch):
        monkeypatch.setattr(_settings(), "agent_runs_enabled", False)
        status, body = await _get(w, w.admin_a)
        assert status == 200
        assert _external(body) == {}
        assert any(r["source"] == "built_in" for r in body["agents"])
