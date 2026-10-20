"""WS-55 EA-13a — the client has its own name.

Spec: ``project-docs/specs/external_agent_access.md`` §6.1 (EA3-45, EA3-47),
§12.2 item 1 and row EA-13a of §15, acceptance N1 to N7.

The stamp of a connection is ``mcp:<host>:<slug>``. ``<slug>`` comes from the
last path segment of the connection's CIMD client id, so Claude Code's
connection stamps ``mcp:claude.ai:claude-code``, and it reads "Claude Code".
Before EA-13a every client on ``claude.ai`` stamped ``mcp:claude.ai`` and read
"Claude.ai".

Two halves.

**Database-free (always runs).** ``agent_oauth.actor_via_for`` and
``agent_oauth.client_label`` against N1, N2, N3, N6 and N7, and the label map
against the ONE case file ``tests/fixtures/agent_client_names.json``, which
``agentRuns.test.ts`` reads too.

**R8 (a real database, as a NON-privileged role, on a phase-4-promoted
catalog of its own).** The edge calls the REAL gateway app through
``httpx.ASGITransport`` (EA3-20). It proves N1 on the row, N4 at branch 0 and
N5 on the open-run read of EA12-6. Since H-356, N4 and N5 refuse the legacy
host stamp.

Run::

    eval "$(bash scripts/dev_db.sh --export)"
    uv run pytest tests/unit/test_agent_client_stamp.py -v -rs

**The mutations (R7).** Each one turned the named test red. ``A`` is
``packages/acb_auth/acb_auth``, ``E`` is
``apps/services/agent_edge/agent_edge`` and ``G`` is
``apps/services/gateway/gateway/routes/projects``.

* S1. ``A/agent_oauth.py`` ``actor_via_for``: ignore ``client_id`` (the host
  stamp of before). ``test_n1_claude_code_stamps_its_own_slug`` and
  ``test_n1_an_edge_write_stamps_the_row_with_the_slug``.
* S2. ``A/agent_oauth.py`` ``actor_via_for``: return the new stamp without the
  pattern check. ``test_n2_a_stamp_longer_than_the_pattern_falls_back``.
* S3. ``A/agent_oauth.py`` ``client_slug``: no ``is_cimd_client_id`` check.
  ``test_n3_a_client_id_that_is_not_a_url_keeps_the_host_stamp``.
* S4. ``A/agent_oauth.py`` ``actor_via_for``: drop the check that the client
  id is at the origin's host.
  ``test_a_client_id_at_another_host_keeps_the_host_stamp``.
* S5. ``A/deps.py`` branch 0: ``client_id=None``.
  ``test_n4_branch_0_accepts_the_new_stamp_and_refuses_another`` and
  ``test_n1_an_edge_write_stamps_the_row_with_the_slug`` (every edge call is
  a 401).
* S6. ``E/gateway_client.py``: ``client_id=None``.
  ``test_n1_an_edge_write_stamps_the_row_with_the_slug`` (branch 0 refuses
  the host stamp).
* S9. ``G/agent_runs.py`` ``start_agent_run``: the member's start stamps
  ``client_id=None``. ``test_a_member_start_stamps_the_connections_slug``.
* S10. ``A/agent_oauth.py`` ``client_label``: the host fallback returns the
  body, or a title case. ``test_n7_another_host_that_says_claude_code_reads_as_its_host``
  and the case-file test.
* S11. ``A/agent_oauth.py`` ``CLIENT_LABELS``: drop ``claude.ai``.
  ``test_n6_a_legacy_row_reads_claude`` and the map test.

Fix round 1 (the IPv6 host):

* S14. ``A/agent_oauth.py`` ``actor_via_for``: a slug on an IPv6 host.
  ``test_an_ipv6_host_gets_no_slug_and_reads_whole``.
* S15. ``A/agent_oauth.py`` ``client_label``: split an IPv6 body at its first
  colon. ``test_an_ipv6_host_gets_no_slug_and_reads_whole`` and the
  case-file test.

H-356 (2026-10-20) closed the deploy window of fix round 1. Branch 0 and the
open-run read no longer accept the legacy host stamp. S7, S8, S12, S13 and
S16 went with their tests. Two mutations put an arm back:

* H1. ``A/deps.py`` branch 0: accept ``actor_via_for(client_origin,
  client_id=None)`` beside the new stamp.
  ``test_n4_branch_0_refuses_the_legacy_stamp_of_the_row``.
* H2. ``G/agent_runs.py`` ``_OPEN_RUN_SQL``: match the host stamp of the
  caller's stamp too, ``OR r.client_via = split_part(:via, ':', 1) || ':'
  || split_part(:via, ':', 2)``.
  ``test_n5_an_open_legacy_run_is_not_found_and_a_new_run_opens``.
"""
from __future__ import annotations

import json
import os
import uuid
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import httpx
import pytest

pytest.importorskip("sqlalchemy")

from acb_auth import agent_oauth, agent_signing
from acb_common import get_settings
from sqlalchemy import create_engine, text
from sqlalchemy.engine import make_url

from tests.unit import test_mcp_edge_task_writes as tw
from tests.unit import test_mcp_edge_writes as w
from tests.unit._tenant_ladder import apply_ladder

_DB_GATE = w._DB_GATE
KEY = w.KEY
MEMBER = w.MEMBER

#: Claude Code's client id, as the live metadata document names it (fetched
#: 2026-10-18, and the fixture of ``test_agent_oauth_server.py``).
CLAUDE_CODE = "https://claude.ai/oauth/claude-code-client-metadata"
CLAUDE_ORIGIN = "https://claude.ai"
NEW = "mcp:claude.ai:claude-code"
LEGACY = "mcp:claude.ai"

_CASES = Path(__file__).resolve().parents[1] / "fixtures" / "agent_client_names.json"


def _case_file() -> dict[str, Any]:
    return json.loads(_CASES.read_text(encoding="utf-8"))


# ═══════════════════════════════════════════════════════════════════════════
# Database-free: the stamp and the label
# ═══════════════════════════════════════════════════════════════════════════


def test_n1_claude_code_stamps_its_own_slug():
    assert agent_oauth.client_slug(CLAUDE_CODE) == "claude-code"
    assert agent_oauth.actor_via_for(CLAUDE_ORIGIN, client_id=CLAUDE_CODE) == NEW
    assert agent_oauth.client_label(NEW) == "Claude Code"
    assert agent_oauth.ACTOR_VIA_PATTERN.fullmatch(NEW)


@pytest.mark.parametrize("client_id, slug", [
    ("https://client.example/meta.json", "meta"),
    ("https://client.example/oauth/metadata.json", "metadata"),
    ("https://client.example/oauth/tool-metadata.json", "tool"),
    ("https://client.example/oauth/Tool_Client-Metadata", "tool-client"),
    ("https://client.example/a/b/My%20Tool/", "my-20tool"),
    ("https://client.example/" + "x" * 30 + "-y", "x" * 24),
    ("https://client.example/-metadata", ""),
])
def test_the_slug_rule(client_id, slug):
    """EA3-47: the last segment, without ``.json``, then without
    ``-client-metadata`` or ``-metadata``, reduced to ``[a-z0-9-]``, at most
    24 characters."""
    assert agent_oauth.client_slug(client_id) == slug


def test_n2_a_stamp_longer_than_the_pattern_falls_back():
    """N2. The host stamp first, then the hash."""
    host = "a" * 50 + ".example"
    client_id = f"https://{host}/claude-code-client-metadata"
    origin = f"https://{host}"
    assert len(f"mcp:{host}:claude-code") > 64
    assert agent_oauth.actor_via_for(origin, client_id=client_id) == f"mcp:{host}"
    long_host = "a" * 70 + ".example"
    long_id = f"https://{long_host}/claude-code-client-metadata"
    long_origin = f"https://{long_host}"
    import hashlib

    assert agent_oauth.actor_via_for(long_origin, client_id=long_id) == (
        "mcp:" + hashlib.sha256(long_origin.encode()).hexdigest()[:16])


@pytest.mark.parametrize("client_id", [
    "mt_dcr_8f3a2c", "claude-code", "", None,
    "http://claude.ai/oauth/claude-code-client-metadata",
    "https://claude.ai/oauth/claude-code-client-metadata?x=1",
])
def test_n3_a_client_id_that_is_not_a_url_keeps_the_host_stamp(client_id):
    assert agent_oauth.actor_via_for(CLAUDE_ORIGIN, client_id=client_id) == LEGACY


def test_a_client_id_at_another_host_keeps_the_host_stamp():
    """The slug counts only for a client id at the origin's own host."""
    stamp = agent_oauth.actor_via_for(
        "https://client.example", client_id="https://claude.ai/claude-code-client-metadata")
    assert stamp == "mcp:client.example"


def test_an_ipv6_host_gets_no_slug_and_reads_whole():
    """Fix round 1. An IPv6 host has colons of its own, so the stamp keeps
    the host only, and the label reads the whole host back."""
    stamp = agent_oauth.actor_via_for(
        "https://[2001:db8::1]", client_id="https://[2001:db8::1]/tool-metadata")
    assert stamp == "mcp:2001:db8::1"
    assert agent_oauth.client_label(stamp) == "2001:db8::1"


def test_the_client_id_is_a_keyword_with_no_default():
    """A caller names the client id, so none forgets it."""
    with pytest.raises(TypeError):
        agent_oauth.actor_via_for(CLAUDE_ORIGIN)  # type: ignore[call-arg]


def test_n6_a_legacy_row_reads_claude():
    assert agent_oauth.client_label(LEGACY) == "Claude"


def test_n7_another_host_that_says_claude_code_reads_as_its_host():
    stamp = agent_oauth.actor_via_for(
        "https://evil.example", client_id="https://evil.example/claude-code-client-metadata")
    assert stamp == "mcp:evil.example:claude-code"
    assert agent_oauth.client_label(stamp) == "evil.example"


def test_the_label_map_is_the_one_of_the_case_file():
    """``agentRuns.test.ts`` holds its mirror to the same file."""
    assert dict(agent_oauth.CLIENT_LABELS) == _case_file()["labels"]


def test_each_case_of_the_file_reads_its_label():
    cases = _case_file()["cases"]
    assert len(cases) > 5
    for case in cases:
        assert agent_oauth.client_label(case["via"]) == case["label"], case["why"]


def test_each_label_key_is_a_stamp_body_that_the_pattern_accepts():
    for body in agent_oauth.CLIENT_LABELS:
        assert agent_oauth.ACTOR_VIA_PATTERN.fullmatch("mcp:" + body), body


# ═══════════════════════════════════════════════════════════════════════════
# R8: through the real edge and the real gateway
# ═══════════════════════════════════════════════════════════════════════════

_APP_ROLE = "acb_app_mcpstamp"
_APP_PW = "mcpstamp_scratch_pw"


def _claude_connection(a) -> tuple[str, str]:
    """A live connection of Claude Code and one access token for it."""
    from acb_auth import agent_tokens

    cid = str(a.one(
        "INSERT INTO agent_connection (organization_id, member_email, client_id,"
        " client_name, client_origin, scopes, pinned_projects, include_personal,"
        " expires_at) VALUES (CAST(:o AS uuid), :m, :client, 'Claude Code', :origin,"
        " CAST(:scopes AS text[]), CAST('{}' AS uuid[]), false,"
        " now() + interval '1 day') RETURNING id",
        o=a.org, m=MEMBER, client=CLAUDE_CODE, origin=CLAUDE_ORIGIN,
        scopes=list(w.WRITE_SCOPES)))
    raw = agent_tokens.new_token()
    a.one("INSERT INTO agent_token (organization_id, connection_id, kind, token_hash,"
          " expires_at, client_id, resource, scope) VALUES (CAST(:o AS uuid),"
          " CAST(:c AS uuid), 'access', :h, now() + interval '1 hour', :client, :r, :s)"
          " RETURNING id",
          o=a.org, c=cid, h=agent_tokens.hash_token(raw), client=CLAUDE_CODE,
          r=w.RESOURCE, s=" ".join(w.WRITE_SCOPES))
    return cid, raw


@pytest.fixture(scope="module")
def world():
    """A private promoted catalog: one tenant, one member, one project, and
    one connection of Claude Code."""
    if not w._URL:
        pytest.skip("TENANT_LADDER_DATABASE_URL unset")
    admin_url = make_url(w._URL)
    dedicated = f"{admin_url.database}_mcpstamp"
    w._drop_database(admin_url, dedicated)
    with create_engine(admin_url, isolation_level="AUTOCOMMIT").connect() as c:
        c.execute(text(f'CREATE DATABASE "{dedicated}"'))
    ded_admin_url = admin_url.set(database=dedicated)
    eng = create_engine(ded_admin_url, future=True)
    with eng.begin() as conn:
        apply_ladder(conn)
    for phase in ("01_add_columns.sql", "02_backfill.sql", "03_constraints.sql",
                  "04_policies.sql"):
        with eng.begin() as conn, open(os.path.join(w._GENERATED, phase),
                                       encoding="utf-8") as fh:
            w._exec_sql_file(conn, fh.read())
    with create_engine(ded_admin_url, isolation_level="AUTOCOMMIT").connect() as c:
        c.execute(text(
            f"DO $$ BEGIN IF NOT EXISTS (SELECT FROM pg_roles WHERE rolname='{_APP_ROLE}')"
            f" THEN CREATE ROLE {_APP_ROLE} LOGIN; END IF; END $$;"))
        c.execute(text(f"ALTER ROLE {_APP_ROLE} WITH LOGIN PASSWORD '{_APP_PW}' "
                       "NOSUPERUSER NOCREATEDB NOCREATEROLE NOBYPASSRLS INHERIT"))
        c.execute(text(f'GRANT CONNECT ON DATABASE "{dedicated}" TO {_APP_ROLE}'))
        c.execute(text(f"GRANT USAGE ON SCHEMA public TO {_APP_ROLE}"))
        c.execute(text("GRANT SELECT, INSERT, UPDATE, DELETE ON ALL TABLES "
                       f"IN SCHEMA public TO {_APP_ROLE}"))
        c.execute(text(f"GRANT USAGE, SELECT ON ALL SEQUENCES IN SCHEMA public TO {_APP_ROLE}"))

    org = str(uuid.uuid4())
    with eng.begin() as c:
        c.execute(text("INSERT INTO organization (id, slug, display_name) "
                       "VALUES (:id, 'mcpstamp', 'mcpstamp')"), {"id": org})
    a = w._Seeder(eng, org)
    a.number = 400_000
    a.member(MEMBER, ["feature:projects"])
    team = a.project("Team")
    s = SimpleNamespace(
        todo=a.status(team, "To do", "todo", 0),
        doing=a.status(team, "Doing", "in_progress", 1),
    )
    world_ = SimpleNamespace(admin=eng, org=org, seeder=a, team=team, s=s,
                             c={}, tok={}, task=a.task)
    # `write` is the generic connection that `tw.live` warms up with.
    world_.c["write"], world_.tok["write"] = a.connection(scopes=w.WRITE_SCOPES)
    world_.c["claude"], world_.tok["claude"] = _claude_connection(a)
    world_.app_dsn = admin_url.set(
        database=dedicated, username=_APP_ROLE, password=_APP_PW,
    ).render_as_string(hide_password=False)
    yield world_

    eng.dispose()
    w._drop_database(admin_url, dedicated)


redis_fake = tw.redis_fake
live_app = tw.live_app


@pytest.fixture
async def live(live_app):
    """``tw.live``, warmed up by the MEMBER, not by a signed request. A
    mutation of the stamp then fails inside the test that names it, never in
    the warm-up."""
    if not getattr(live_app, "warmed", False):
        r = await tw._send(live_app, "GET", "/projects/tree", signed=None)
        assert r.status_code == 200, r.text
        live_app.warmed = True
    return live_app


async def _signed(world, name: str, method: str, path: str, via: str,
                  body: dict[str, Any] | None = None) -> httpx.Response:
    """One request to the gateway app, signed for connection *name* with the
    stamp *via*, as the edge sends it."""
    from gateway.main import app as gateway_app

    raw = json.dumps(body).encode() if body is not None else b""
    sent = agent_signing.signed_headers(
        KEY, organization_id=world.org, connection_id=world.c[name], actor_via=via,
        method=method, path=path, query="", body=raw)
    if body is not None:
        sent["Content-Type"] = "application/json"

    async def go() -> httpx.Response:
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=gateway_app),
                                     base_url="http://gateway.test") as client:
            return await client.request(method, path, headers=sent, content=raw)
    return await tw._in_scope(world, go)


def _runs(world, task: str) -> list[Any]:
    with world.admin.begin() as c:
        return list(c.execute(text(
            "SELECT * FROM pm_agent_runs WHERE task_id = CAST(:t AS uuid)"
            " ORDER BY created_at"), {"t": task}))


def _legacy_run(world, task: str, connection: str) -> str:
    """An open run as a start before EA-13a wrote it: the host stamp."""
    return str(world.seeder.one(
        "INSERT INTO pm_agent_runs (organization_id, task_id, member_email, connection_id,"
        " client_via, status, started_by, started_at, last_agent_activity_at)"
        " VALUES (CAST(:o AS uuid), CAST(:t AS uuid), :m, CAST(:c AS uuid), :v,"
        " 'in_progress', :v, now(), now()) RETURNING id",
        o=world.org, t=task, m=MEMBER, c=connection, v=LEGACY))


@pytest.fixture
def runs_on(monkeypatch):
    monkeypatch.setattr(get_settings(), "agent_runs_enabled", True)


class TestTheStampOnARealDatabase:
    """N1, N4 and N5 (§15 EA-13a), through the edge and the real gateway."""

    pytestmark = _DB_GATE

    async def test_n1_an_edge_write_stamps_the_row_with_the_slug(self, live):
        task = live.task(live.team, live.s.todo, "n1")
        result = await tw._mcp(live, "claude", "add_comment", {
            "task_id": task, "body": "Starting on this.", "idempotency_key": "n1-comment-1"})
        assert result["isError"] is False, result
        with live.admin.begin() as c:
            via = c.execute(text(
                "SELECT meta->>'via' FROM pm_activities WHERE task_id = CAST(:t AS uuid)"
                " AND type = 'comment'"), {"t": task}).scalar()
        assert via == NEW
        assert agent_oauth.client_label(via) == "Claude Code"

    async def test_n4_branch_0_accepts_the_new_stamp_and_refuses_another(self, live):
        task = live.task(live.team, live.s.todo, "n4")
        path = f"/projects/tasks/{task}"
        assert (await _signed(live, "claude", "GET", path, NEW)).status_code == 200
        for wrong in ("mcp:claude.ai:other", "mcp:evil.example:claude-code",
                      "mcp:client.example", "mcp:client.example:meta"):
            r = await _signed(live, "claude", "GET", path, wrong)
            assert r.status_code == 401, (wrong, r.text)

    async def test_n4_branch_0_refuses_the_legacy_stamp_of_the_row(self, live, runs_on):
        """H-356. The window of EA-13a fix round 1 is closed. A call signed
        with the host stamp OF THE ROW gets 401, a read and a write alike, and
        the write opens no run."""
        task = live.task(live.team, live.s.doing, "n4 legacy refused")
        path = f"/projects/tasks/{task}"
        assert agent_oauth.actor_via_for(CLAUDE_ORIGIN, client_id=None) == LEGACY
        r = await _signed(live, "claude", "GET", path, LEGACY)
        assert r.status_code == 401, r.text
        r = await _signed(live, "claude", "POST", f"{path}/agent-runs", LEGACY,
                          body={"idempotency_key": "legacy-refused-1"})
        assert r.status_code == 401, r.text
        assert _runs(live, task) == []
        # The new stamp of the same row still passes, so the 401 is the stamp.
        assert (await _signed(live, "claude", "GET", path, NEW)).status_code == 200

    async def test_n5_an_open_legacy_run_is_not_found_and_a_new_run_opens(
            self, live, runs_on):
        """H-356. The open-run read matches the caller's stamp only. A legacy
        run of the SAME connection is not found, so the start opens a run under
        the new stamp, and the legacy run stays as it was."""
        task = live.task(live.team, live.s.doing, "n5")
        legacy = _legacy_run(live, task, live.c["claude"])
        result = await tw._mcp(live, "claude", "start_agent_run", {
            "task_id": task, "idempotency_key": "n5-start-1"})
        assert result["isError"] is False, result
        body = result["structuredContent"]
        assert body["created"] is True, body
        assert body["run"]["id"] != legacy
        assert body["run"]["client_via"] == NEW
        runs = _runs(live, task)
        assert sorted((r.client_via, r.status) for r in runs) == sorted(
            [(LEGACY, "in_progress"), (NEW, "in_progress")])

    async def test_a_member_start_stamps_the_connections_slug(self, live, runs_on):
        """A member names the connection. The run's stamp is the one that
        branch 0 gives that connection's calls, so the agent finds the run."""
        task = live.task(live.team, live.s.doing, "member start")
        r = await tw._send(live, "POST", f"/projects/tasks/{task}/agent-runs", signed=None,
                           body={"connection_id": live.c["claude"],
                                 "body": "Fix the extruder.", "idempotency_key": "ms-1"})
        assert r.status_code == 201, r.text
        assert r.json()["run"]["client_via"] == NEW
        again = await tw._mcp(live, "claude", "start_agent_run", {
            "task_id": task, "idempotency_key": "ms-agent-1"})
        assert again["structuredContent"]["created"] is False, again
        assert len(_runs(live, task)) == 1
