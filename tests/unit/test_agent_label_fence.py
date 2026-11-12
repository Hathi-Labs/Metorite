"""WS-MCP SW-7b — a display label for each agent session.

Spec: ``project-docs/specs/agent_coordination.md`` §3.7 (the trust limits of
SW-6, item 2 and the first row of the fence table) and the slice SW-7b.

``claim_task`` and ``start_agent_run`` take an optional ``agent_label``, for
example ``backend-worker-3``. The gateway cleans it with the rules of
``_clean_body``, drops each control and format character and caps it at 40
characters. Migration ``pm_agent_runs.agent_label`` (expand only) stores it.
The runs wire, the task row, the card, the run panel and the held-by line
show it beside the client name.

**The label is display data only, and it grants nothing.** Three halves:

1. **Structural (always runs).** No module of ``acb_auth`` reads
   ``agent_label``. No visibility or authorization seam reads it: the
   visibility helpers of ``core.py`` and ``filters.py``, ``authority.py``,
   the db seam and the Redis wrapper of ``acb_common``. No run-slot rule of
   ``agent_runs.py`` reads it: the gate, the own-run read ``_OPEN_RUN_SQL``,
   the holder read, the adoption, the takeover, the release and the answer
   rules. No index of ``pm_agent_runs`` names it. The session key never
   meets it: the edge's transport, bearer and gateway client never name it.
2. **The edge schema (always runs).** The argument of the two tools, its
   cap, its pattern and its description. The edge sends it in the body only,
   and the run wire sends it back as untrusted text.
3. **R8** (a real Postgres, the real ``/projects`` router, the
   non-privileged role of the phase-4 rehearsal). Two sessions with two
   labels show two names on the wire. A person's body is 422. A label never
   opens the run of another session.

Run the R8 half on a private database::

    eval "$(bash scripts/dev_db.sh --export)"
    uv run pytest tests/unit/test_agent_label_fence.py -v -rs

Mutations (R7). ``G`` is ``gateway/routes/projects/agent_runs.py``, ``F`` is
``filters.py`` beside it, and ``E`` is ``agent_edge/tools.py``.

* L1. ``G`` ``_OPEN_RUN_SQL``: add ``AND r.agent_label IS NOT DISTINCT FROM
  :label``. ``test_no_run_slot_rule_reads_the_label``.
* L2. ``acb_auth/agent_signing.py``: sign ``agent_label`` into the session.
  ``test_no_module_of_acb_auth_reads_the_label``.
* L3. ``F`` ``unheld_clause``: read ``h.agent_label``.
  ``test_no_visibility_seam_reads_the_label``.
* L4. A migration: an index ``ON pm_agent_runs (..., agent_label)``.
  ``test_no_index_of_the_runs_table_names_the_label``.
* L5. ``G`` ``clean_agent_label``: keep the control characters.
  ``test_the_clean_drops_control_and_format_characters``.
* L6. ``E`` ``_claim_task``: leave the label out of the body.
  ``test_the_claim_sends_the_label_in_the_body``.
* L7. ``E`` ``agent_run``: send the label as a plain string.
  ``test_the_run_wire_sends_the_label_as_untrusted_text``.
* L8. ``G`` ``_runs_out``: drop ``agent_label``.
  ``test_two_sessions_with_two_labels_show_two_names_on_the_wire`` (R8).
* L9. ``G`` ``agent_label_of``: skip the person check.
  ``test_a_person_body_with_a_label_is_refused`` (R8, and the hermetic test).
"""

from __future__ import annotations

import ast
import json
import re
import secrets
import uuid
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

pytest.importorskip("sqlalchemy")
pytest.importorskip("gateway.routes.projects", reason="gateway not installed")

from acb_common import get_settings
from agent_edge import tools
from fastapi import HTTPException
from gateway.routes.projects import agent_runs, filters
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError

from tests.unit.test_agent_run_exclusive import (
    _agent,
    _connection,
    _in_session,
    _member_row,
    _person,
    _seed_task,
)
from tests.unit.test_agent_runs_r8 import CLIENT_ID, _rows, _session
from tests.unit.test_agent_runs_r8 import VIA as R8_VIA
from tests.unit.test_h3_rls_promotion_rehearsal import (  # noqa: F401
    _DB_GATE,
    app_engine,
    promoted,
)
from tests.unit.test_mcp_edge_claim import (  # noqa: F401
    TASK,
    _claim_args,
    edge,
    redis_fake,
)
from tests.unit.test_mcp_edge_writes import _tool

_REPO = Path(__file__).resolve().parents[2]
_MIGRATIONS = _REPO / "infra" / "postgres"
_GATEWAY = _REPO / "apps" / "services" / "gateway" / "gateway" / "routes" / "projects"
_EDGE = _REPO / "apps" / "services" / "agent_edge" / "agent_edge"
_ACB_AUTH = _REPO / "packages" / "acb_auth" / "acb_auth"
_ACB_COMMON = _REPO / "packages" / "acb_common" / "acb_common"

#: The word, and not ``chat_agent_label`` of ``acb_common._log`` (a different
#: name for an in-app chat agent).
_LABEL = re.compile(r"\bagent_label\b")


def _read(path: Path) -> str:
    return path.read_text(encoding="utf-8")


def _named_sources(path: Path, names: tuple[str, ...]) -> dict[str, str]:
    """The source of each top-level function or assignment of *names* in
    *path*. A name that the file no longer defines fails, so the fence
    cannot rot in silence after a rename."""
    source = _read(path)
    found: dict[str, str] = {}
    for node in ast.parse(source).body:
        name = None
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            name = node.name
        elif isinstance(node, ast.Assign) and len(node.targets) == 1 and isinstance(
                node.targets[0], ast.Name):
            name = node.targets[0].id
        if name in names:
            found[name] = ast.get_source_segment(source, node) or ""
    missing = sorted(set(names) - set(found))
    assert not missing, (
        f"{path.relative_to(_REPO).as_posix()} no longer defines {missing}. Update the"
        " SW-7b fence in tests/unit/test_agent_label_fence.py to name the new seam.")
    return found


def _offenders(path: Path, names: tuple[str, ...]) -> list[str]:
    rel = path.relative_to(_REPO).as_posix()
    return [f"{rel}::{name}" for name, body in _named_sources(path, names).items()
            if _LABEL.search(body)]


# ═══════════════════════════════════════════════════════════════════════════
# 1. Structural: a label never reaches an authorization check (§3.7)
# ═══════════════════════════════════════════════════════════════════════════


class TestTheLabelGrantsNothing:

    def test_no_module_of_acb_auth_reads_the_label(self) -> None:
        """``acb_auth`` holds identity, scopes, signing and the session key.
        None of it may read the label."""
        files = sorted(_ACB_AUTH.rglob("*.py"))
        assert files, _ACB_AUTH
        bad = [p.relative_to(_REPO).as_posix() for p in files if _LABEL.search(_read(p))]
        assert not bad, (
            "SW-7b (agent_coordination.md §3.7): agent_label is display data only, and"
            f" no module of acb_auth may read it. Remove it from: {bad}")

    def test_no_visibility_seam_reads_the_label(self) -> None:
        """The visibility, reach, lease and holder rules, the delete and grant
        authority, the db seam and the Redis wrapper."""
        whole = [
            _GATEWAY / "authority.py",
            _ACB_COMMON / "db.py",
            _ACB_COMMON / "tenant_redis.py",
        ]
        bad = [p.relative_to(_REPO).as_posix() for p in whole if _LABEL.search(_read(p))]
        bad += _offenders(_GATEWAY / "core.py", (
            "resolve_visibility", "resolve_visibility_for", "load_visible_task",
            "task_visibility_clause", "_AGENT_REACH_SQL"))
        bad += _offenders(_GATEWAY / "filters.py", (
            "unheld_clause", "holder_sql", "holder_free", "holder_free_sql",
            "connection_gone_sql", "lease_held_sql", "lease_expired",
            "agent_run_lease_s", "agent_run_hold_s", "held_by_of"))
        assert not bad, (
            "SW-7b (agent_coordination.md §3.7): no visibility or authorization seam"
            f" may read agent_label. Remove it from: {bad}")
        rendered = (filters.unheld_clause(), filters.holder_sql(),
                    filters.holder_free_sql("r"), filters.connection_gone_sql("r"))
        assert not [s for s in rendered if _LABEL.search(s)], rendered

    def test_no_run_slot_rule_reads_the_label(self) -> None:
        """The one gate of EA-14a, the own run of SW-13c and SW-7, the holder,
        the adoption, the takeover, the 409, the release and the answer rules."""
        bad = _offenders(_GATEWAY / "agent_runs.py", (
            "_OPEN_RUN_SQL", "_HOLDER_SQL", "_ADOPT_SQL", "_RESUME_KEY_SQL",
            "_open_run_args", "_gate_task", "_same_client", "_task_held", "_free",
            "_take_over", "_agent_session", "_agent_connection", "_move_run",
            "may_end_run", "person_may_end_sql", "may_answer_run",
            "person_may_answer_sql", "holds_answer_role", "_reads_for_member",
            "release_agent_run"))
        assert not bad, (
            "SW-7b (agent_coordination.md §3.7): a label never chooses a run, a holder"
            f" or a session. Remove agent_label from: {bad}")
        for sql in (agent_runs._OPEN_RUN_SQL, agent_runs._HOLDER_SQL, agent_runs._ADOPT_SQL):
            assert not _LABEL.search(sql), sql

    def test_the_session_key_never_meets_the_label_at_the_edge(self) -> None:
        """The edge mints and reads the session key in ``mcp.py``, checks the
        bearer in ``bearer.py`` and signs the headers in ``gateway_client.py``.
        The label is a body field only, never a header and never a key."""
        files = [_EDGE / "mcp.py", _EDGE / "bearer.py", _EDGE / "gateway_client.py"]
        bad = [p.relative_to(_REPO).as_posix() for p in files if _LABEL.search(_read(p))]
        bad += _offenders(_EDGE / "tools.py", ("_yours", "session_text"))
        assert not bad, (
            "SW-7b: the session key comes only from the signed header, and the label"
            f" never reaches it. Remove agent_label from: {bad}")
        heads = [p.relative_to(_REPO).as_posix() for p in (*_EDGE.rglob("*.py"),
                                                           *_GATEWAY.rglob("*.py"))
                 if re.search(r"agent-label", _read(p), re.IGNORECASE)]
        assert not heads, f"agent_label must not travel as a header: {heads}"

    def test_no_index_of_the_runs_table_names_the_label(self) -> None:
        """The run slot is ``pm_agent_runs_one_open_per_task`` and
        ``pm_agent_runs_one_open``. No index of the table names the label."""
        statement = re.compile(r"CREATE\s+(?:UNIQUE\s+)?INDEX[^;]*?\bON\s+pm_agent_runs\b[^;]*;",
                               re.IGNORECASE | re.DOTALL)
        seen, bad = 0, []
        for path in sorted(_MIGRATIONS.glob("[0-9]*_*.sql")):
            for found in statement.finditer(_read(path)):
                seen += 1
                if _LABEL.search(found.group(0)):
                    bad.append(f"{path.name}: {found.group(0)[:80]}")
        assert seen >= 2, "the fence no longer finds the run-slot indexes"
        assert not bad, f"SW-7b: no index of pm_agent_runs may name agent_label: {bad}"


class TestTheMigration:

    def _file(self) -> Path:
        found = [p for p in _MIGRATIONS.glob("[0-9]*_*.sql")
                 if "ADD COLUMN IF NOT EXISTS agent_label" in _read(p)]
        assert len(found) == 1, found
        return found[0]

    def test_it_adds_one_nullable_column_and_its_length_check(self) -> None:
        sql = self._file().read_text(encoding="utf-8")
        assert "ALTER TABLE pm_agent_runs ADD COLUMN IF NOT EXISTS agent_label TEXT NULL;" in sql
        assert "char_length(agent_label) BETWEEN 1 AND 40" in sql
        assert "NOT VALID" in sql and "VALIDATE CONSTRAINT pm_agent_runs_agent_label_length" in sql
        assert "SET lock_timeout = '5s';" in sql
        # Expand only (R6): no index, no policy, no drop, no default, no backfill.
        body = "\n".join(line for line in sql.splitlines() if not line.startswith("--"))
        for word in ("INDEX", "POLICY", "DROP", "DEFAULT", "UPDATE"):
            assert word not in body, word

    def test_the_number_is_unique(self) -> None:
        number = self._file().name.split("_", 1)[0]
        assert [p.name for p in _MIGRATIONS.glob(f"{number}_*.sql")] == [self._file().name]

    def test_the_cap_of_the_check_is_the_cap_of_the_clean(self) -> None:
        assert agent_runs.AGENT_LABEL_MAX == 40 == tools.AGENT_LABEL_MAX


# ═══════════════════════════════════════════════════════════════════════════
# The clean, hermetic
# ═══════════════════════════════════════════════════════════════════════════


class TestTheClean:

    @pytest.mark.parametrize(("raw", "clean"), [
        (None, None),
        ("", None),
        ("   ", None),
        ("\x00\x01\x7f", None),
        ("backend-worker-3", "backend-worker-3"),
        ("  backend\t\tworker\n3  ", "backend worker 3"),
        ("café 中", "café 中"),
    ])
    def test_the_rules_of_clean_body_and_white_space(self, raw: Any, clean: Any) -> None:
        assert agent_runs.clean_agent_label(raw) == clean

    def test_the_clean_drops_control_and_format_characters(self) -> None:
        dirty = "w\x00o\x07r\x1bk\u0085e​r‮-⁦﻿1\U000e0041"
        assert agent_runs.clean_agent_label(dirty) == "work er-1"

    def test_the_clean_caps_at_40_characters_and_trims_the_cut(self) -> None:
        assert agent_runs.clean_agent_label("x" * 60) == "x" * 40
        assert agent_runs.clean_agent_label("a" * 39 + " b") == "a" * 39

    def test_a_person_body_with_a_label_is_refused(self) -> None:
        person = _person("p@label.test", str(uuid.uuid4()))
        with pytest.raises(HTTPException) as refused:
            agent_runs.agent_label_of(person, "worker-1")
        assert refused.value.status_code == 422
        assert agent_runs.agent_label_of(person, None) is None
        agent = _agent("a@label.test", str(uuid.uuid4()), str(uuid.uuid4()), R8_VIA)
        assert agent_runs.agent_label_of(agent, " worker\n1 ") == "worker 1"

    def test_the_body_models_take_the_label_with_one_bound(self) -> None:
        from gateway.routes.projects.tasks import ClaimIn

        for model in (ClaimIn, agent_runs.StartIn):
            field = model.model_fields["agent_label"]
            assert field.default is None and not field.is_required()
            bound = [m.max_length for m in field.metadata if hasattr(m, "max_length")]
            assert bound == [agent_runs.AGENT_LABEL_INPUT_MAX], (model, bound)


# ═══════════════════════════════════════════════════════════════════════════
# 2. The edge schema of the argument
# ═══════════════════════════════════════════════════════════════════════════


_TAKES_LABEL = ("claim_task", "start_agent_run")


class TestTheEdgeArgument:

    def test_the_two_tools_take_the_optional_argument(self) -> None:
        for name in _TAKES_LABEL:
            tool = tools.TOOLS[name]
            assert tool.input_schema["properties"]["agent_label"] is tools._LABEL_INPUT, name
            assert "agent_label" not in tool.input_schema["required"], name
            assert tool.params["agent_label"] == "agent_label", name

    def test_no_other_tool_takes_it(self) -> None:
        others = sorted(name for name, tool in tools.TOOLS.items()
                        if "agent_label" in (tool.input_schema or {}).get("properties", {})
                        and name not in _TAKES_LABEL)
        assert not others, others

    def test_the_schema_caps_it_and_refuses_each_control_character(self) -> None:
        rule = tools._LABEL_INPUT
        assert (rule["type"], rule["minLength"], rule["maxLength"]) == ("string", 1, 40)
        for good in ("backend-worker-3", "w", "café 中", "x" * 40):
            tools._check_value("agent_label", rule, good)
        for bad in ("", "x" * 41, "a\nb", "a\x00b", "a\tb", "a\x7fb", "a\x85b", "a\x1bb"):
            with pytest.raises(tools.ToolError):
                tools._check_value("agent_label", rule, bad)

    def test_the_description_says_display_only_and_names_no_grant(self) -> None:
        words = tools._LABEL_INPUT["description"]
        assert "display data only" in words
        assert "changes no access, no hold and no session" in words
        assert not re.search(r"projects:|scope|admin|permission", words), words

    def test_the_server_instructions_do_not_name_it(self) -> None:
        """Task 1803: the instructions are at their 4096 budget, so the
        guidance lives in the argument description only."""
        from agent_edge import mcp

        assert "agent_label" not in mcp.instructions(("projects:read", "projects:write"))

    def test_the_run_wire_sends_the_label_as_untrusted_text(self) -> None:
        assert tools._RUN_SCHEMA["properties"]["agent_label"] is tools._UNTRUSTED_OR_NULL
        assert "agent_label" in tools.RUN_KEYS
        row = {"id": "r", "task_id": "t", "status": "in_progress", "state": "working"}
        assert tools.agent_run({**row, "agent_label": "worker-1"})["agent_label"] == {
            "untrusted_text": "worker-1"}
        assert tools.agent_run(row)["agent_label"] is None
        assert tools.agent_run({**row, "agent_label": 7})["agent_label"] is None
        assert tuple(tools.agent_run(row)) == tools.RUN_KEYS

    async def test_the_claim_sends_the_label_in_the_body(self, edge) -> None:  # noqa: F811
        result = await _tool("claim_task", {**_claim_args(), "agent_label": "worker-1"})
        assert result["isError"] is False, result
        [post] = edge.gateway.writes()
        assert json.loads(post.body) == {"agent_label": "worker-1"}
        assert not [h for h in post.headers if "label" in h.lower()], post.headers

    async def test_a_claim_with_no_label_sends_no_key(self, edge) -> None:  # noqa: F811
        result = await _tool("claim_task", _claim_args())
        assert result["isError"] is False, result
        [post] = edge.gateway.writes()
        assert json.loads(post.body) == {}

    async def test_a_label_with_a_control_character_is_refused_with_no_write(
            self, edge) -> None:  # noqa: F811
        result = await _tool("claim_task", {**_claim_args(), "agent_label": "worker\n1"})
        assert result["isError"] is True, result
        assert edge.gateway.writes() == []

    async def test_the_start_sends_the_label_in_the_body(self, edge, monkeypatch) -> None:  # noqa: F811
        monkeypatch.setattr(get_settings(), "agent_runs_enabled", True)
        run = {"id": str(uuid.uuid4()), "task_id": TASK, "status": "in_progress",
               "state": "working", "client_via": "mcp:client.example",
               "agent_label": "worker-2"}
        edge.gateway.answers[("POST", f"/projects/tasks/{TASK}/agent-runs")] = (
            201, {"run": run, "created": True})
        result = await _tool("start_agent_run", {
            "task_id": TASK, "idempotency_key": "k-sw7b", "agent_label": "worker-2"})
        assert result["isError"] is False, result
        [post] = edge.gateway.writes()
        assert json.loads(post.body) == {"idempotency_key": "k-sw7b", "agent_label": "worker-2"}
        assert result["structuredContent"]["run"]["agent_label"] == {"untrusted_text": "worker-2"}


# ═══════════════════════════════════════════════════════════════════════════
# 3. R8
# ═══════════════════════════════════════════════════════════════════════════


ALICE, BOB = "sw7b-alice@label.test", "sw7b-bob@label.test"


@pytest.fixture(scope="module")
def world(promoted):  # noqa: F811
    org = promoted.org_a
    ns = SimpleNamespace(p=promoted, org=org)
    with promoted.admin_engine.begin() as c:
        for email, name in ((ALICE, "Alice Label"), (BOB, "Bob Label")):
            _member_row(c, org, email, name)
    ns.conn_alice = _connection(promoted, org, ALICE, CLIENT_ID, "Claude Code")
    ns.alice = _agent(ALICE, org, ns.conn_alice, R8_VIA)
    ns.alice_person = _person(ALICE, org)
    ns.bob_person = _person(BOB, org)
    return ns


@pytest.fixture
def w(world, monkeypatch):
    monkeypatch.setattr(get_settings(), "agent_runs_enabled", True)
    with world.p.admin_engine.begin() as c:
        c.execute(text("DELETE FROM pm_agent_runs"))
    return world


_numbers = iter(range(70_000, 90_000))


def _fresh(w: Any) -> str:
    return _seed_task(w.p, w.org, ALICE, n=next(_numbers))


def _key() -> str:
    """A session key in the shape of SW-7: 22 base64url characters."""
    return secrets.token_urlsafe(16)


def _label_of(w: Any, run_id: str) -> Any:
    [row] = _rows(w.p, "SELECT agent_label FROM pm_agent_runs WHERE id = CAST(:i AS uuid)",
                  i=run_id)
    return row.agent_label


@_DB_GATE
class TestTheLabelOnARealDatabase:

    async def test_the_column_exists_and_its_check_refuses_41_characters(self, w):
        [col] = _rows(w.p, "SELECT data_type, is_nullable FROM information_schema.columns"
                           " WHERE table_name = 'pm_agent_runs' AND column_name = 'agent_label'")
        assert (col.data_type, col.is_nullable) == ("text", "YES")
        task = _fresh(w)
        async with _session(w.p) as call:
            claimed = await call(w.alice, "POST", f"/projects/tasks/{task}/claim", None)
        run_id = claimed.json()["agent_run_id"]
        with pytest.raises(DBAPIError), w.p.admin_engine.begin() as c:
            c.execute(text("UPDATE pm_agent_runs SET agent_label = :l"
                           " WHERE id = CAST(:i AS uuid)"), {"l": "x" * 41, "i": run_id})
        with pytest.raises(DBAPIError), w.p.admin_engine.begin() as c:
            c.execute(text("UPDATE pm_agent_runs SET agent_label = ''"
                           " WHERE id = CAST(:i AS uuid)"), {"i": run_id})

    async def test_two_sessions_with_two_labels_show_two_names_on_the_wire(self, w):
        """The "Done when" of SW-7b. Two terminals of ONE connection, two
        sessions, two tasks: the runs list and the two task rows name each
        session by its own label."""
        one, two = _fresh(w), _fresh(w)
        first, second = _in_session(w.alice, _key()), _in_session(w.alice, _key())
        async with _session(w.p) as call:
            a = await call(first, "POST", f"/projects/tasks/{one}/claim",
                           {"agent_label": "backend-worker-1"})
            b = await call(second, "POST", f"/projects/tasks/{two}/claim",
                           {"agent_label": "backend-worker-2"})
            assert a.status_code == b.status_code == 200, (a.text, b.text)
            listed = await call(w.alice_person, "GET", "/projects/agent-runs", None)
            row_one = await call(w.alice_person, "GET", f"/projects/tasks/{one}", None)
            row_two = await call(w.alice_person, "GET", f"/projects/tasks/{two}", None)
        assert listed.status_code == 200, listed.text
        labels = {r["task_id"]: r["agent_label"] for r in listed.json()["rows"]}
        assert labels == {one: "backend-worker-1", two: "backend-worker-2"}
        names = {r["agent_label"]: r["client_name"] for r in listed.json()["rows"]}
        assert names == {"backend-worker-1": "Claude Code", "backend-worker-2": "Claude Code"}
        assert row_one.json()["agent_label"] == "backend-worker-1"
        assert row_two.json()["agent_label"] == "backend-worker-2"

    async def test_a_label_less_start_keeps_the_label_of_a_keyless_live_run(self, w):
        """An agent with no MCP session claims with a label, so its run has
        no session key. Its own start with no label must keep the label.
        Mutation: clear the label on a call with no label when the run has
        no key (the old `adopted` rule), and the assert fails."""
        task = _fresh(w)
        async with _session(w.p) as call:
            claimed = await call(w.alice, "POST", f"/projects/tasks/{task}/claim",
                                 {"agent_label": "lane-3"})
            run_id = claimed.json()["agent_run_id"]
            started = await call(w.alice, "POST", f"/projects/tasks/{task}/agent-runs",
                                 {"idempotency_key": _key()})
            assert started.status_code == 200, started.text
            assert started.json()["run"]["id"] == run_id
        assert _label_of(w, run_id) == "lane-3"

    async def test_a_resume_clears_the_label_with_the_key(self, w):
        """Resume clears the key of a quiet session, and its label with it. A
        NEW session that adopts the run with no label shows no old name.
        Mutation: drop the `_clear_run_label` call of `resume_agent_run`, and
        both label asserts fail."""
        task = _fresh(w)
        quiet = _in_session(w.alice, _key())
        async with _session(w.p) as call:
            claimed = await call(quiet, "POST", f"/projects/tasks/{task}/claim",
                                 {"agent_label": "lane-3"})
            run_id = claimed.json()["agent_run_id"]
            # An open recovery record, as the second miss of the sweep writes it.
            with w.p.admin_engine.begin() as c:
                c.execute(text(
                    "INSERT INTO pm_agent_run_strands (organization_id, run_id, task_id,"
                    " member_email, reason, seen_activity_at, recovered_at, evidence)"
                    " SELECT r.organization_id, r.id, r.task_id, r.member_email,"
                    " 'agent_quiet', r.last_activity_at, now(), '{}'::jsonb"
                    " FROM pm_agent_runs r WHERE r.id = CAST(:r AS uuid)"), {"r": run_id})
            resumed = await call(w.alice_person, "POST",
                                 f"/projects/agent-runs/{run_id}/resume",
                                 {"idempotency_key": _key()})
            assert resumed.status_code == 200, resumed.text
            assert resumed.json()["run"]["session_key"] is None
            assert _label_of(w, run_id) is None
            later = await call(_in_session(w.alice, _key()), "POST",
                               f"/projects/tasks/{task}/claim", None)
            assert later.json()["agent_run_id"] == run_id
        assert _label_of(w, run_id) is None

    async def test_an_ended_run_keeps_its_label_and_a_reopen_clears_it(self, w):
        """A finished run keeps the name of the session that ran it. A
        member's prompt opens the run again for the next session, and the old
        name goes. Mutation: drop the `_clear_run_label` call of the reopen
        in `_write_activity`, and the last two asserts fail."""
        task = _fresh(w)
        first = _in_session(w.alice, _key())
        async with _session(w.p) as call:
            claimed = await call(first, "POST", f"/projects/tasks/{task}/claim",
                                 {"agent_label": "lane-3"})
            run_id = claimed.json()["agent_run_id"]
            fin = await call(first, "POST", f"/projects/agent-runs/{run_id}/finish", {})
            assert fin.status_code == 200, fin.text
            assert _label_of(w, run_id) == "lane-3"
            prompt = await call(w.alice_person, "POST",
                                f"/projects/agent-runs/{run_id}/activities",
                                {"type": "prompt", "body": "One more thing",
                                 "idempotency_key": _key()})
            assert prompt.status_code == 201, prompt.text
            assert _label_of(w, run_id) is None
            later = await call(_in_session(w.alice, _key()), "POST",
                               f"/projects/tasks/{task}/claim", None)
            assert later.json()["agent_run_id"] == run_id
        assert _label_of(w, run_id) is None

    async def test_a_start_replaces_the_label_and_a_call_with_none_keeps_it(self, w):
        task = _fresh(w)
        session = _in_session(w.alice, _key())
        async with _session(w.p) as call:
            claimed = await call(session, "POST", f"/projects/tasks/{task}/claim",
                                 {"agent_label": "worker-a"})
            run_id = claimed.json()["agent_run_id"]
            started = await call(session, "POST", f"/projects/tasks/{task}/agent-runs",
                                 {"idempotency_key": _key(), "agent_label": "worker-b"})
            assert started.status_code == 200, started.text
            assert started.json()["run"]["agent_label"] == "worker-b"
            again = await call(session, "POST", f"/projects/tasks/{task}/claim", None)
            assert again.json()["agent_run_id"] == run_id
        assert _label_of(w, run_id) == "worker-b"

    async def test_a_new_run_of_a_start_stores_the_cleaned_label(self, w):
        task = _fresh(w)
        async with _session(w.p) as call:
            started = await call(_in_session(w.alice, _key()), "POST",
                                 f"/projects/tasks/{task}/agent-runs", {
                                     "idempotency_key": _key(),
                                     "agent_label": "  lane\t4‮ " + "x" * 50})
        assert started.status_code == 201, started.text
        stored = _label_of(w, started.json()["run"]["id"])
        assert stored == ("lane 4 " + "x" * 50)[:40]
        assert len(stored) == 40 and started.json()["run"]["agent_label"] == stored

    async def test_a_person_body_with_a_label_is_refused(self, w):
        task = _fresh(w)
        async with _session(w.p) as call:
            claim = await call(w.alice_person, "POST", f"/projects/tasks/{task}/claim",
                               {"agent_label": "me"})
            start = await call(w.alice_person, "POST", f"/projects/tasks/{task}/agent-runs", {
                "connection_id": w.conn_alice, "body": "Please take it", "agent_label": "me"})
        assert claim.status_code == 422, claim.text
        assert start.status_code == 422, start.text
        assert _rows(w.p, "SELECT id FROM pm_agent_runs WHERE task_id = CAST(:t AS uuid)",
                     t=task) == []

    async def test_a_label_never_opens_the_run_of_another_session(self, w):
        """Session B sends the label of session A. The gate still finds the
        run of session A as a holder: 409, and the run keeps its label."""
        task = _fresh(w)
        first, second = _in_session(w.alice, _key()), _in_session(w.alice, _key())
        async with _session(w.p) as call:
            a = await call(first, "POST", f"/projects/tasks/{task}/claim",
                           {"agent_label": "same-name"})
            b = await call(second, "POST", f"/projects/tasks/{task}/claim",
                           {"agent_label": "same-name"})
            c = await call(second, "POST", f"/projects/tasks/{task}/agent-runs",
                           {"idempotency_key": _key(), "agent_label": "same-name"})
        assert a.status_code == 200, a.text
        assert b.status_code == 409 and b.json()["detail"]["error"] == "task_held", b.text
        assert c.status_code == 409 and c.json()["detail"]["error"] == "task_held", c.text
        open_runs = _rows(w.p, "SELECT id, agent_label FROM pm_agent_runs"
                               " WHERE task_id = CAST(:t AS uuid)", t=task)
        assert [(str(r.id), r.agent_label) for r in open_runs] == [
            (a.json()["agent_run_id"], "same-name")]
