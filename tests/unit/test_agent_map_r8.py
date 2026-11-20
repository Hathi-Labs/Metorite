"""WS-MCP SW-102 — the map read (``agent_coordination.md`` §3.12, §5.12).

``GET /projects/agents/overview?shape=map`` gives the people, the session
nodes and the open runs that the caller may see. It reads the runs through
the ONE ``seen`` text of the overview (``_SEEN_RUNS_SQL``). ``project_id``
keeps the runs of one project and its sub-projects. ``show_quiet`` lists the
runs that fold. ``AGENT_MAP_ORGS`` keeps it dark, after ``AGENTS_VIEW_ORGS``.

**R8**, a real Postgres, through the real ``/projects`` router as the
non-privileged role (FORCE RLS). Two orgs, and three viewer levels in org A
(admin, lead, member). The world is the one of ``test_agents_overview_r8.py``.

R7, the mutations (each turns a test here red, see the SW-102 report):

* **M1** ``_reach_sql`` drops the lead arm (``:led``) -> the lead test.
* **M2** the answer of a session carries ``session_key`` -> the scan test.
* **M3** ``_SUBTREE_SQL`` keeps only the root (no recursive arm) -> the
  sub-project test.
* **M4** ``agent_map_allowed`` answers True for each org -> the flag tests.
* **M5** the fold drops the ``waiting`` arm -> the fold test.
* **M7** ``count`` comes from a read that skips ``{reach}`` -> the member
  and lead count tests.
* **M6** the route takes ``show_quiet: bool`` again, so FastAPI checks it
  before the flags -> the flag-off malformed test (a 422 echoes the input).
"""

from __future__ import annotations

import json
import secrets
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

ADMIN_A = "sw102-admin@a-map.test"
LEAD_A = "sw102-lead@a-map.test"
MEMBER_A = "sw102-member@a-map.test"
OTHER_A = "sw102-other@a-map.test"
LONER_A = "sw102-loner@a-map.test"
ADMIN_B = "sw102-admin@b-map.test"

VIA = "mcp:acme:map-agent"
VIA_B = "mcp:acme:b-agent"

PATH = "/projects/agents/overview"
MAP = PATH + "?shape=map"

#: Each label is display data. ``worker-1`` names two runs of one connection,
#: so the two runs share one node.
LABEL_1 = "worker-1"
LABEL_B = "b-worker"


def _session_key() -> str:
    """A session key of the shape of migration 302: 22 base64url characters."""
    return secrets.token_urlsafe(16)


def _settings() -> Any:
    from acb_common import get_settings

    return get_settings()


# ── Hermetic: the flag, its reader and its registration ─────────────────────


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

    monkeypatch.setattr(_settings(), "agent_map_orgs", raw)
    assert m.agent_map_allowed(org) is want


def test_the_flag_is_empty_by_default_and_registered() -> None:
    from pathlib import Path

    from acb_common.env_guard import is_platform_env
    from acb_common.settings import Settings
    from gateway import flag_reload as fr

    assert Settings.model_fields["agent_map_orgs"].default == ""
    assert fr.reloadable("AGENT_MAP_ORGS")
    assert is_platform_env("AGENT_MAP_ORGS")
    root = Path(__file__).resolve().parents[2]
    assert "\nAGENT_MAP_ORGS=\n" in (root / ".env.example").read_text(encoding="utf-8")


def test_the_map_reads_the_one_seen_text() -> None:
    """The map wraps ``_SEEN_RUNS_SQL`` and adds no second rule of reach."""
    from gateway.routes.projects import agent_summary as m

    assert m._SEEN_RUNS_SQL in m._MAP_SESSIONS_SQL
    assert m._SEEN_RUNS_SQL in m._MAP_RUNS_SQL
    assert m._SUBTREE_SQL in m._SUMMARY_SQL


@pytest.mark.parametrize(
    ("pid", "quiet", "want"),
    [
        (None, None, (None, False)),
        (None, "true", (None, True)),
        (None, "TRUE", (None, True)),
        (None, "1", (None, True)),
        (None, "False", (None, False)),
        (None, "0", (None, False)),
        (None, "maybe", None),
        (None, "", None),
        ("6a0c6c1e-6f8e-4a51-9a52-2f3b1b2e9d10", None,
         ("6a0c6c1e-6f8e-4a51-9a52-2f3b1b2e9d10", False)),
        ("not-a-uuid", None, None),
        ("", None, None),
        ("6" * 65, None, None),
    ],
)
def test_the_map_query_parse(pid, quiet, want) -> None:
    from gateway.routes.projects.agent_summary import parse_map_query

    assert parse_map_query(pid, quiet) == want


# ── R8: the world ───────────────────────────────────────────────────────────


def _label(p: Any, rid: str, *, label: str | None, key: str | None, conn: str | None,
           waiting: bool = False) -> None:
    with p.admin_engine.begin() as c:
        c.execute(text(
            "UPDATE pm_agent_runs SET agent_label = :l, session_key = :k, "
            "connection_id = CAST(:c AS uuid), "
            "waiting_until = CASE WHEN :w THEN now() + interval '30 minutes' END "
            "WHERE id = CAST(:r AS uuid)"),
            {"l": label, "k": key, "c": conn, "w": waiting, "r": rid})


@pytest.fixture(scope="module")
def world(promoted):  # noqa: F811 — a fixture
    p = promoted
    ns = SimpleNamespace(p=p)
    ns.admin_a = b._principal(ADMIN_A, p.org_a)
    ns.lead_a = b._principal(LEAD_A, p.org_a, unrestricted=False)
    ns.member_a = b._principal(MEMBER_A, p.org_a, unrestricted=False)
    ns.loner_a = b._principal(LONER_A, p.org_a, unrestricted=False)
    ns.admin_b = b._principal(ADMIN_B, p.org_b)
    for email in (ADMIN_A, LEAD_A, MEMBER_A, OTHER_A, LONER_A):
        b.member(p, p.org_a, email)
    b.member(p, p.org_b, ADMIN_B)
    return ns


def _seed(w: Any) -> None:
    p = w.p
    b.clear(p)
    w.keys = []
    w.led = b.project(p, p.org_a, OTHER_A, grant="org")
    w.sub = b.project(p, p.org_a, OTHER_A, parent=w.led)
    w.plain = b.project(p, p.org_a, OTHER_A, grant="org")
    # A project with no grant: only its creator sees it.
    w.private = b.project(p, p.org_a, OTHER_A)
    with p.admin_engine.begin() as c:
        c.execute(text("UPDATE pm_projects SET lead = :l WHERE id = CAST(:p AS uuid)"),
                  {"l": LEAD_A, "p": w.led["id"]})
    conn = b._seed_connection(p, p.org_a, OTHER_A)
    conn_m = b._seed_connection(p, p.org_a, MEMBER_A)

    def key() -> str:
        k = _session_key()
        w.keys.append(k)
        return k

    # The member's own task: a run of OTHER_A, named worker-1.
    w.r_own = b.run(p, p.org_a, b.task(p, p.org_a, MEMBER_A, w.plain), OTHER_A,
                    "in_progress", via=VIA)
    _label(p, w.r_own, label=LABEL_1, key=key(), conn=conn)
    # The led project: the same session name, another session key. One node.
    w.r_led = b.run(p, p.org_a, b.task(p, p.org_a, OTHER_A, w.led), OTHER_A,
                    "in_progress", via=VIA)
    _label(p, w.r_led, label=LABEL_1, key=key(), conn=conn)
    # A sub-project of the led project: no name, so it keys on its session key.
    w.r_sub = b.run(p, p.org_a, b.task(p, p.org_a, OTHER_A, w.sub), OTHER_A,
                    "in_progress", via=VIA)
    _label(p, w.r_sub, label=None, key=key(), conn=conn)
    # Another member's task: only the admin sees it. It waits on a person for
    # 5 hours, and it never folds.
    w.r_await = b.run(p, p.org_a, b.task(p, p.org_a, OTHER_A, w.plain), OTHER_A,
                      "awaiting", via=VIA, last_activity="now() - interval '5 hours'")
    _label(p, w.r_await, label="asker", key=key(), conn=conn)
    # A run with no event for 3 hours: it folds.
    w.r_quiet = b.run(p, p.org_a, b.task(p, p.org_a, OTHER_A, w.plain), OTHER_A,
                      "in_progress", via=VIA, last_activity="now() - interval '3 hours'")
    _label(p, w.r_quiet, label="sleeper", key=key(), conn=conn)
    # A quiet run on the led project: the lead sees it, the member does not.
    w.r_led_quiet = b.run(p, p.org_a, b.task(p, p.org_a, OTHER_A, w.led), OTHER_A,
                          "in_progress", via=VIA, last_activity="now() - interval '3 hours'")
    _label(p, w.r_led_quiet, label="led-sleeper", key=key(), conn=conn)
    # A run that waits on an agent, with no event for 3 hours: it does not fold.
    w.r_wait = b.run(p, p.org_a, b.task(p, p.org_a, OTHER_A, w.plain), OTHER_A,
                     "in_progress", via=VIA, last_activity="now() - interval '3 hours'")
    _label(p, w.r_wait, label="orchestrator", key=key(), conn=conn, waiting=True)
    # An ended run: not on the map.
    w.r_done = b.run(p, p.org_a, b.task(p, p.org_a, OTHER_A, w.plain), OTHER_A,
                     "completed", via=VIA)
    _label(p, w.r_done, label="finisher", key=key(), conn=conn)
    # The member's own run on another member's task, with no name.
    w.r_mine = b.run(p, p.org_a, b.task(p, p.org_a, OTHER_A, w.plain), MEMBER_A,
                     "in_progress", via=VIA)
    _label(p, w.r_mine, label=None, key=key(), conn=conn_m)
    # The member's own quiet run, in a session of its own (no name).
    w.r_mine_quiet = b.run(p, p.org_a, b.task(p, p.org_a, OTHER_A, w.plain), MEMBER_A,
                           "in_progress", via=VIA, last_activity="now() - interval '3 hours'")
    _label(p, w.r_mine_quiet, label=None, key=key(), conn=conn_m)
    # A run on the private project of OTHER_A.
    w.r_private = b.run(p, p.org_a, b.task(p, p.org_a, OTHER_A, w.private), OTHER_A,
                        "in_progress", via=VIA)
    _label(p, w.r_private, label="hidden", key=key(), conn=conn)
    # Org B: one run, under a name that org A uses too.
    w.proj_b = b.project(p, p.org_b, ADMIN_B, grant="org")
    conn_b = b._seed_connection(p, p.org_b, ADMIN_B)
    w.r_b = b.run(p, p.org_b, b.task(p, p.org_b, ADMIN_B, w.proj_b), ADMIN_B,
                  "awaiting", via=VIA_B)
    _label(p, w.r_b, label=LABEL_1, key=key(), conn=conn_b)


@pytest.fixture
def w(world, monkeypatch):
    monkeypatch.setattr(_settings(), "agent_runs_enabled", True)
    monkeypatch.setattr(_settings(), "agents_view_orgs", "*")
    monkeypatch.setattr(_settings(), "agent_map_orgs", "*")
    if not getattr(world, "seeded", False):
        _seed(world)
        world.seeded = True
    return world


async def _get(w: Any, user: Any, query: str = MAP) -> tuple[int, Any, str]:
    async with b._session(w.p) as call:
        got = await call(user, "GET", query)
    try:
        body = got.json()
    except ValueError:
        body = got.text
    return got.status_code, body, got.text


def _keys(value: Any) -> set[str]:
    if isinstance(value, dict):
        return set(value) | {k for v in value.values() for k in _keys(v)}
    if isinstance(value, list):
        return {k for v in value for k in _keys(v)}
    return set()


def _run_ids(body: dict[str, Any]) -> set[str]:
    return {r["run_id"] for r in body["runs"]}


def _sessions(body: dict[str, Any]) -> dict[str, dict[str, Any]]:
    return {s["id"]: s for s in body["sessions"]}


def _people(body: dict[str, Any]) -> set[str]:
    return {p["person"] for p in body["people"]}


@_DB_GATE
class TestTheMap:
    async def test_admin_a_sees_each_open_run_of_org_a(self, w):
        status, body, _ = await _get(w, w.admin_a)
        assert status == 200, body
        assert body["viewer"] == "admin"
        assert _run_ids(body) == {w.r_own, w.r_led, w.r_sub, w.r_await, w.r_wait,
                                  w.r_mine, w.r_private}
        # The three quiet runs count, and the ended run does not.
        assert body["count"] == 10
        assert body["lines"] == [] and body["truncated"] is False
        assert _people(body) == {OTHER_A, MEMBER_A}
        from gateway.routes.projects import agent_summary as m

        assert tuple(body) == m.MAP_KEYS
        assert all(tuple(s) == m.MAP_SESSION_KEYS for s in body["sessions"])
        assert all(tuple(r) == m.MAP_RUN_KEYS for r in body["runs"])

    async def test_one_name_of_one_connection_is_one_node(self, w):
        _, body, _ = await _get(w, w.admin_a)
        by_run = {r["run_id"]: r["session_id"] for r in body["runs"]}
        assert by_run[w.r_own] == by_run[w.r_led]
        assert by_run[w.r_sub] != by_run[w.r_own]
        node = _sessions(body)[by_run[w.r_own]]
        assert node["name"] == LABEL_1 and node["run_count"] == 2
        assert node["client"] == "acme"
        unnamed = _sessions(body)[by_run[w.r_sub]]
        assert unnamed["name"] == "Unnamed session"

    async def test_admin_b_sees_none_of_org_a(self, w):
        status, body, raw = await _get(w, w.admin_b)
        assert status == 200, body
        assert _run_ids(body) == {w.r_b}
        assert _people(body) == {ADMIN_B}
        assert OTHER_A not in raw and MEMBER_A not in raw
        # The same name in two orgs is two nodes.
        _, body_a, _ = await _get(w, w.admin_a)
        assert not set(_sessions(body)) & set(_sessions(body_a))

    async def test_a_member_sees_their_own_tasks_and_their_own_runs(self, w):
        _, body, _ = await _get(w, w.member_a)
        assert body["viewer"] == "member"
        assert _run_ids(body) == {w.r_own, w.r_mine}
        assert _people(body) == {OTHER_A, MEMBER_A}
        # The node of worker-1 counts only the run that the member sees.
        node = next(s for s in body["sessions"] if s["name"] == LABEL_1)
        assert node["run_count"] == 1

    async def test_the_count_of_a_member_holds_only_what_they_see(self, w):
        """Three quiet runs exist in org A. The member sees one of them."""
        _, body, _ = await _get(w, w.member_a)
        assert body["count"] == 3
        quiet = {s["id"]: s["quiet_count"] for s in body["sessions"]}
        assert sorted(quiet.values()) == [0, 0, 1]
        assert sum(s["run_count"] for s in body["sessions"]) == 3

    async def test_a_lead_sees_the_runs_of_the_projects_they_lead(self, w):
        _, body, _ = await _get(w, w.lead_a)
        assert body["viewer"] == "lead"
        assert _run_ids(body) == {w.r_led, w.r_sub}

    async def test_the_count_of_a_lead_holds_only_what_they_see(self, w):
        """The lead sees one quiet run (on the led project) of the three."""
        _, body, _ = await _get(w, w.lead_a)
        assert body["count"] == 3
        by_name = {s["name"]: s for s in body["sessions"]}
        assert by_name["led-sleeper"]["quiet_count"] == 1
        assert by_name[LABEL_1]["quiet_count"] == 0
        assert by_name["Unnamed session"]["quiet_count"] == 0
        assert "sleeper" not in by_name

    async def test_the_run_count_of_each_node_of_a_lead_is_its_open_visible_runs(self, w):
        _, body, _ = await _get(w, w.lead_a, MAP + "&show_quiet=true")
        assert _run_ids(body) == {w.r_led, w.r_sub, w.r_led_quiet}
        listed: dict[str, int] = {}
        for r in body["runs"]:
            listed[r["session_id"]] = listed.get(r["session_id"], 0) + 1
        assert {s["id"]: s["run_count"] for s in body["sessions"]} == listed
        assert all(n == 1 for n in listed.values())

    async def test_a_person_with_no_visible_run_has_no_node(self, w):
        _, body, _ = await _get(w, w.lead_a)
        assert MEMBER_A not in _people(body)
        assert all(s["person"] != MEMBER_A for s in body["sessions"])
        _, body, _ = await _get(w, w.loner_a)
        assert body == {"people": [], "sessions": [], "runs": [], "lines": [],
                        "count": 0, "truncated": False, "viewer": "member"}

    async def test_each_viewer_sees_the_open_runs_that_seen_gives_them(self, w):
        """The drill-down of the overview reads the same ``seen`` text. Its
        open runs are the runs of the map, for each viewer."""
        for user, via in ((w.admin_a, VIA), (w.lead_a, VIA), (w.member_a, VIA),
                          (w.admin_b, VIA_B)):
            _, drill, _ = await _get(w, user, f"/projects/agents/external:{via}/runs")
            open_ids = {r["id"] for r in drill["runs"] if r["ended_at"] is None}
            _, body, _ = await _get(w, user, MAP + "&show_quiet=true")
            assert _run_ids(body) == open_ids, user

    async def test_no_answer_holds_a_session_key(self, w):
        for user in (w.admin_a, w.lead_a, w.member_a, w.admin_b):
            for query in (MAP, MAP + "&show_quiet=true",
                          MAP + f"&project_id={w.led['id']}"):
                _, _, raw = await _get(w, user, query)
                leaked = [k for k in w.keys if k in raw]
                assert not leaked, (query, leaked)
                keys = _keys(json.loads(raw))
                assert not {k for k in keys if "key" in k or "addr" in k or "model" in k}

    async def test_quiet_runs_fold_and_a_waiting_run_does_not(self, w):
        _, body, _ = await _get(w, w.admin_a)
        assert w.r_quiet not in _run_ids(body)
        assert {w.r_await, w.r_wait} <= _run_ids(body)
        quiet = next(s for s in body["sessions"] if s["name"] == "sleeper")
        assert quiet["quiet_count"] == 1 and quiet["state"] == "quiet"
        assert quiet["quiet_last_at"]
        waiter = next(s for s in body["sessions"] if s["name"] == "orchestrator")
        assert waiter["state"] == "waiting" and waiter["quiet_count"] == 0
        asker = next(s for s in body["sessions"] if s["name"] == "asker")
        assert asker["state"] == "waiting_on_person" and asker["quiet_count"] == 0
        # What waits on a person sorts first, and a quiet session last.
        assert body["sessions"][0]["state"] == "waiting_on_person"
        assert body["sessions"][-1]["state"] == "quiet"
        _, opened, _ = await _get(w, w.admin_a, MAP + "&show_quiet=true")
        assert w.r_quiet in _run_ids(opened)
        assert next(r for r in opened["runs"] if r["run_id"] == w.r_quiet)["quiet"] is True
        assert w.r_done not in _run_ids(opened)

    async def test_a_hostile_org_in_the_request_changes_nothing(self, w):
        _, plain, _ = await _get(w, w.admin_b)
        _, hostile, _ = await _get(
            w, w.admin_b, MAP + f"&organization_id={w.p.org_a}&me={MEMBER_A}")
        assert _run_ids(hostile) == _run_ids(plain) == {w.r_b}


@_DB_GATE
class TestTheProjectScope:
    async def test_a_run_on_a_sub_project_shows_in_the_scope_of_its_parent(self, w):
        _, body, _ = await _get(w, w.admin_a, MAP + f"&project_id={w.led['id']}")
        assert _run_ids(body) == {w.r_led, w.r_sub}
        assert body["count"] == 3
        _, sub, _ = await _get(w, w.admin_a, MAP + f"&project_id={w.sub['id']}")
        assert _run_ids(sub) == {w.r_sub}

    async def test_the_scope_keeps_the_reach_of_the_viewer(self, w):
        _, body, _ = await _get(w, w.member_a, MAP + f"&project_id={w.plain['id']}")
        assert _run_ids(body) == {w.r_own, w.r_mine}
        _, body, _ = await _get(w, w.lead_a, MAP + f"&project_id={w.led['id']}")
        assert _run_ids(body) == {w.r_led, w.r_sub}

    async def test_a_project_the_member_cannot_see_is_404_as_the_flag_off(self, w, monkeypatch):
        status, hidden, _ = await _get(w, w.member_a, MAP + f"&project_id={w.private['id']}")
        assert status == 404
        status, foreign, _ = await _get(w, w.admin_b, MAP + f"&project_id={w.led['id']}")
        assert status == 404
        status, bad, _ = await _get(w, w.admin_a, MAP + "&project_id=not-a-uuid")
        assert status == 404
        monkeypatch.setattr(_settings(), "agent_map_orgs", "")
        status, off, _ = await _get(w, w.member_a, MAP)
        assert status == 404
        assert hidden == foreign == bad == off == {"detail": "Not Found"}


@_DB_GATE
class TestTheFlag:
    async def test_with_the_map_flag_empty_the_map_is_404_and_the_overview_is_not(
        self, w, monkeypatch,
    ):
        _, before, _ = await _get(w, w.admin_a, PATH)
        monkeypatch.setattr(_settings(), "agent_map_orgs", "")
        for user in (w.admin_a, w.member_a, w.lead_a, w.admin_b):
            assert (await _get(w, user))[0] == 404
        status, after, _ = await _get(w, w.admin_a, PATH)
        assert status == 200
        assert {r["key"] for r in after["agents"]} == {r["key"] for r in before["agents"]}

    async def test_with_org_a_on_org_b_is_still_404(self, w, monkeypatch):
        monkeypatch.setattr(_settings(), "agent_map_orgs", w.p.org_a)
        assert (await _get(w, w.admin_a))[0] == 200
        assert (await _get(w, w.admin_b))[0] == 404

    async def test_the_view_flag_off_wins(self, w, monkeypatch):
        monkeypatch.setattr(_settings(), "agents_view_orgs", "")
        assert (await _get(w, w.admin_a))[0] == 404

    async def test_an_unknown_shape_is_404(self, w):
        assert (await _get(w, w.admin_a, PATH + "?shape=graph"))[0] == 404

    async def test_the_runs_flag_off_gives_an_empty_map(self, w, monkeypatch):
        monkeypatch.setattr(_settings(), "agent_runs_enabled", False)
        status, body, _ = await _get(w, w.admin_a)
        assert status == 200
        assert body["runs"] == [] and body["sessions"] == [] and body["count"] == 0


#: The one 404 body. It echoes no input.
NOT_FOUND = {"detail": "Not Found"}
LONG_SHAPE = "m" * 17
LONG_PID = "6" * 65


@_DB_GATE
class TestABadValueIsTheOne404:
    async def _assert_404(self, w: Any, user: Any, query: str, needle: str) -> None:
        status, body, raw = await _get(w, user, query)
        assert status == 404, (query, raw)
        assert body == NOT_FOUND, (query, raw)
        assert needle not in raw

    async def test_with_the_flags_off_a_bad_value_is_404_not_422(self, w, monkeypatch):
        monkeypatch.setattr(_settings(), "agents_view_orgs", "")
        monkeypatch.setattr(_settings(), "agent_map_orgs", "")
        for query, needle in (
            (PATH + "?show_quiet=maybe", "maybe"),
            (PATH + "?shape=" + LONG_SHAPE, LONG_SHAPE),
            (MAP + "&show_quiet=maybe", "maybe"),
            (PATH + "?project_id=" + LONG_PID, LONG_PID),
            (MAP + "&project_id=" + LONG_PID, LONG_PID),
        ):
            await self._assert_404(w, w.admin_a, query, needle)

    async def test_with_only_the_map_flag_off_a_bad_map_value_is_404(self, w, monkeypatch):
        monkeypatch.setattr(_settings(), "agent_map_orgs", "")
        await self._assert_404(w, w.admin_a, MAP + "&show_quiet=maybe", "maybe")
        await self._assert_404(w, w.admin_a, MAP + "&project_id=" + LONG_PID, LONG_PID)

    async def test_with_the_flags_on_a_bad_value_is_404(self, w):
        for query, needle in (
            (MAP + "&show_quiet=maybe", "maybe"),
            (PATH + "?shape=bogus", "bogus"),
            (PATH + "?shape=" + LONG_SHAPE, LONG_SHAPE),
            (MAP + "&project_id=" + LONG_PID, LONG_PID),
            (MAP + "&project_id=not-a-uuid", "not-a-uuid"),
        ):
            await self._assert_404(w, w.admin_a, query, needle)

    async def test_the_plain_overview_ignores_the_map_values(self, w):
        """Base behaviour: the plain overview reads no map value."""
        for user in (w.admin_a, w.member_a):
            status, plain, _ = await _get(w, user, PATH)
            assert status == 200
            status, odd, _ = await _get(
                w, user, PATH + "?show_quiet=maybe&project_id=anything-at-all")
            assert status == 200
            assert odd == plain


@_DB_GATE
class TestTheCap:
    async def test_the_301st_run_sets_truncated(self, w):
        """Org B holds 1 run. 300 more make 301, and the map lists 300."""
        p = w.p
        proj = w.proj_b
        with p.admin_engine.begin() as c:
            c.execute(text(
                "INSERT INTO pm_tasks (id, organization_id, project_id, root_project_id, "
                "task_number, status_id, title, created_by) "
                "SELECT gen_random_uuid(), CAST(:o AS uuid), CAST(:p AS uuid), "
                "CAST(:r AS uuid), 900000 + g, CAST(:s AS uuid), 'bulk ' || g, :e "
                "FROM generate_series(1, 300) g"),
                {"o": p.org_b, "p": proj["id"], "r": proj["root"],
                 "s": proj["lanes"]["todo"], "e": ADMIN_B})
            c.execute(text(
                "INSERT INTO pm_agent_runs (organization_id, task_id, member_email, "
                "client_via, status, started_by) "
                "SELECT CAST(:o AS uuid), t.id, :e, :v, 'in_progress', :e "
                "FROM pm_tasks t WHERE t.organization_id = CAST(:o AS uuid) "
                "AND t.title LIKE 'bulk %'"),
                {"o": p.org_b, "e": ADMIN_B, "v": VIA_B})
        try:
            status, body, _ = await _get(w, w.admin_b)
            assert status == 200
            assert len(body["runs"]) == 300
            assert body["truncated"] is True
            assert body["count"] == 301
        finally:
            with p.admin_engine.begin() as c:
                c.execute(text(
                    "DELETE FROM pm_agent_runs WHERE organization_id = CAST(:o AS uuid) "
                    "AND task_id IN (SELECT id FROM pm_tasks WHERE title LIKE 'bulk %')"),
                    {"o": p.org_b})
                c.execute(text(
                    "DELETE FROM pm_tasks WHERE organization_id = CAST(:o AS uuid) "
                    "AND title LIKE 'bulk %'"), {"o": p.org_b})
        _, body, _ = await _get(w, w.admin_b)
        assert body["truncated"] is False and body["count"] == 1
