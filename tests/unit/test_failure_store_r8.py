"""TOF-2b — the failure store on a real database (R8, fences P3, P4 and P5).

Spec: ``project-docs/specs/tool_failure_log.md`` §5, §6 and §9. Code: the
migration that creates ``failure_events`` (found by content, never by number,
R1) and ``acb_common.failure_log``.

Each test runs on a phase-4 promoted scratch catalog as the role
``acb_app_h3rls``: not the owner, not a superuser and not BYPASSRLS. The
fixture runs the migration again with that role in place of ``acb_app``, so
the role holds the grants of production: no table grant on a platform table,
and EXECUTE on the five functions.

Run (a private ladder database, one R8 suite at a time)::

    eval "$(bash scripts/dev_db.sh --export)"
    uv run pytest tests/unit/test_failure_store_r8.py -v -rs

Without the database every test here SKIPS, and the run reads green. A skip
is not a pass.

**Acceptance (TOF-2b, §13.2).**

a. A fixture call of ``record_tool_failure`` writes one event row and one
   count (§6).
b. A call with no tenant, a bad field or a made-up fingerprint gets a refusal
   (P3).
c. An org sees only its own tenant rows. The app role cannot read a platform
   table (P4).
d. Another org gets a refusal from the job functions, and so does every org
   while ``failure_config`` has no row (P4).
e. The band shows ``<3`` for 1 or 2 organizations (P5).
f. Two tallies at once of one new fingerprint count 2, and a count at the top
   of ``bigint`` stays there.
"""

from __future__ import annotations

import asyncio
import hashlib
import threading
import uuid
from pathlib import Path
from typing import Any

import pytest

pytest.importorskip("sqlalchemy")

from acb_common import failure_log as fl
from acb_common.db import bind_tenant, release_tenant
from sqlalchemy import text

from tests.unit._tenant_ladder import tenant_engine_scope
from tests.unit.test_h3_rls_promotion_rehearsal import (  # noqa: F401
    _APP_ROLE,
    _DB_GATE,
    app_engine,
    promoted,
)

pytestmark = _DB_GATE

_BIND = "SELECT set_config('app.tenant_id', :o, true)"
_TALLY = text(
    "SELECT failure_tally(:fp, :tool, :code, :exc, :site, :source, :surface, :release, :n)")
_PLATFORM = ("failure_fingerprints", "failure_daily", "failure_config")
_FUNCTIONS = (
    "failure_tally(text, text, text, text, text, text, text, text, integer)",
    "failure_org_band(text)",
    "failure_due(integer)",
    "failure_set(text, text, uuid, timestamp with time zone, timestamp with time zone, "
    "timestamp with time zone, text, bigint, integer)",
    "failure_prune_daily(date)",
)


def _migration() -> str:
    root = Path(__file__).resolve().parents[2] / "infra" / "postgres"
    [hit] = [f for f in root.glob("[0-9]*_*.sql")
             if "CREATE TABLE IF NOT EXISTS failure_events" in f.read_text(encoding="utf-8")]
    # The sync slice adds a column and two function bodies in a second file (TOF-2e,
    # 467): the store the job reads is the two files in order.
    [more] = [f for f in root.glob("[0-9]*_*.sql")
              if "ADD COLUMN IF NOT EXISTS last_release_at" in f.read_text(encoding="utf-8")]
    assert more.name > hit.name
    # The ledger runs each file once. A suite applies both again on a database that
    # kept the later `failure_due`, and 466 makes the old return type again. So the
    # drop of 467 comes first here.
    return ("DROP FUNCTION IF EXISTS public.failure_due(integer);\n"
            + hit.read_text(encoding="utf-8") + "\n" + more.read_text(encoding="utf-8"))


def _fp(tool: str, code: str, exc: str = "-", site: str = "-") -> str:
    line = f"v1|{tool}|{code}|{exc}|{site}"
    return hashlib.sha256(line.encode("utf-8")).hexdigest()[:16]


def _args(tool: str | None = None, code: str = "exception", exc: str = "builtins.KeyError",
          site: str = "acb_skills.crm_tools:crm_get_record", **kw: Any) -> dict[str, Any]:
    tool = tool or f"t_{uuid.uuid4().hex[:12]}"
    args = {"fp": _fp(tool, code, exc, site), "tool": tool, "code": code, "exc": exc,
            "site": site, "source": "chat_tool", "surface": "chat", "release": "abc1234",
            "n": 1}
    args.update(kw)
    return args


@pytest.fixture
def world(promoted, app_engine):  # noqa: F811
    """The grants of production for the app role, and a third org."""
    p = promoted
    body = _migration().replace("acb_app", _APP_ROLE)
    with p.admin_engine.begin() as c, c.connection.dbapi_connection.cursor() as cur:
        cur.execute(body)
    org_c = str(uuid.uuid4())
    with p.admin_engine.begin() as c:
        c.execute(text("INSERT INTO organization (id, slug, display_name) "
                       "VALUES (:id, :s, :s)"), {"id": org_c, "s": f"tof2b-{org_c[:8]}"})
        c.execute(text("DELETE FROM failure_config"))
    with app_engine.connect() as c:
        role = c.execute(text("SELECT rolsuper, rolbypassrls FROM pg_roles "
                              "WHERE rolname = current_user")).one()
    assert not role[0] and not role[1], "the suite must run as a role that RLS binds"
    yield type("W", (), {"p": p, "a": p.org_a, "b": p.org_b, "c": org_c, "app": app_engine})
    with p.admin_engine.begin() as c:
        c.execute(text("DELETE FROM failure_config"))
        c.execute(text("DELETE FROM failure_events"))
        c.execute(text("DELETE FROM failure_org_days"))
        c.execute(text("DELETE FROM failure_daily"))
        c.execute(text("DELETE FROM failure_fingerprints"))
        c.execute(text("DELETE FROM organization WHERE id = :id"), {"id": org_c})


def _tally(engine: Any, org: str | None, args: dict[str, Any]) -> None:
    with engine.connect() as c, c.begin():
        if org:
            c.execute(text(_BIND), {"o": org})
        c.execute(_TALLY, args)


def _admin(world: Any, sql: str, **params: Any) -> list[Any]:
    with world.p.admin_engine.connect() as c:
        return list(c.execute(text(sql), params).fetchall())


def _internal(world: Any, org: str) -> None:
    with world.p.admin_engine.begin() as c:
        c.execute(text("DELETE FROM failure_config"))
        c.execute(text("INSERT INTO failure_config (internal_org) VALUES (:o)"), {"o": org})


# ── a. The write seam end to end ─────────────────────────────────────────────


def test_a_fixture_call_writes_one_event_row_and_one_count(world, monkeypatch) -> None:
    from acb_common import get_settings

    class Down:
        async def incr(self, *a: Any, **k: Any) -> int:
            raise ConnectionError("no redis in this suite")

        expire = incr

    saved = fl.builtin_names()
    fl._reset_for_tests()
    monkeypatch.setattr(get_settings(), "tool_failure_log_orgs", world.a)
    monkeypatch.setattr(fl, "_redis", lambda: Down())
    fl.set_builtin_names({"crm_get_record"})
    fl.set_release("abc1234")

    async def go() -> int:
        url = world.p.app_url.render_as_string(hide_password=False)
        async with tenant_engine_scope(url):
            token = bind_tenant(world.a)
            try:
                try:
                    raise KeyError("Amount for Ravi Kumar")
                except KeyError as exc:
                    fl.record_tool_failure(exc, source="chat_tool", error_code="exception",
                                           tool="crm_get_record", surface="chat",
                                           member_email="ravi@tof2b.test", run_id="run-1")
            finally:
                release_tenant(token)
            took = await fl.flush()
            await fl.stop_drain()
            return took

    try:
        assert asyncio.run(go()) == 1
    finally:
        fl._reset_for_tests()
        fl.set_builtin_names(saved)
    events = _admin(world, "SELECT organization_id::text, tool, error_code, exc_type, "
                    "member_email, run_id, release, fingerprint FROM failure_events")
    assert len(events) == 1
    org, tool, code, exc, member, run, release, fp = events[0]
    assert (org, tool, code, exc, member, run, release) == (
        world.a, "crm_get_record", "exception", "builtins.KeyError", "ravi@tof2b.test",
        "run-1", "abc1234")
    [(total, sources, surfaces)] = _admin(
        world, "SELECT total, sources, surfaces FROM failure_fingerprints WHERE fingerprint = :f",
        f=fp)
    assert (total, sources, surfaces) == (1, ["chat_tool"], ["chat"])
    assert _admin(world, "SELECT occurrences FROM failure_daily WHERE fingerprint = :f",
                  f=fp) == [(1,)]
    assert _admin(world, "SELECT organization_id::text FROM failure_org_days "
                  "WHERE fingerprint = :f", f=fp) == [(world.a,)]
    for table in ("failure_fingerprints", "failure_daily"):
        cols = {r[0] for r in _admin(world, "SELECT column_name FROM information_schema.columns "
                                     "WHERE table_name = :t", t=table)}
        assert "organization_id" not in cols
    text_cols = _admin(world, "SELECT tool, exc_type, code_site FROM failure_fingerprints")
    assert "Ravi" not in repr(text_cols)


def test_the_door_and_mcp_values_of_tof_2d_pass_the_store(world, monkeypatch) -> None:
    """TOF-2d (R8): each value that the door sites D1 and D2 and the MCP sites
    C5 to C7 write passes the CHECKs of the store, through the one writer. A
    route template keeps its braces, and the server stays in the tenant
    column ``tool_name`` only."""
    from acb_common import get_settings

    class Down:
        async def incr(self, *a: Any, **k: Any) -> int:
            raise ConnectionError("no redis in this suite")

        expire = incr

    fl._reset_for_tests()
    monkeypatch.setattr(get_settings(), "tool_failure_log_orgs", world.a)
    monkeypatch.setattr(fl, "_redis", lambda: Down())
    routes = frozenset({"/crm/deals/{deal_id}"})

    async def go() -> int:
        url = world.p.app_url.render_as_string(hide_password=False)
        async with tenant_engine_scope(url):
            token = bind_tenant(world.a)
            try:
                try:
                    raise KeyError("Amount for Ravi Kumar")
                except KeyError as exc:
                    fl.record_tool_failure(exc, source="door_5xx", error_code="unhandled_500",
                                           tool="GET /crm/deals/{deal_id}", kind="route",
                                           route_paths=routes, surface="mcp_edge")
                for code, tool in (("route_500", "POST /crm/deals/{deal_id}"),
                                   ("route_upstream", "GET /crm/deals/ravi-77")):
                    fl.record_tool_failure(None, source="door_5xx", error_code=code, tool=tool,
                                           kind="route", route_paths=routes, surface="app")
                for code in ("mcp_timeout", "mcp_unreachable", "mcp_refused", "mcp_too_large",
                             "mcp_tool_error"):
                    fl.record_tool_failure(None, source="mcp_call", error_code=code,
                                           tool="acme-erp/get_invoice", kind="mcp",
                                           surface="studio")
            finally:
                release_tenant(token)
            took = await fl.flush()
            await fl.stop_drain()
            return took

    try:
        assert asyncio.run(go()) == 8
    finally:
        fl._reset_for_tests()
    events = _admin(world, "SELECT tool, error_code, source, surface, tool_name "
                    "FROM failure_events ORDER BY error_code")
    assert sorted(events) == sorted([
        ("route:GET /crm/deals/{deal_id}", "unhandled_500", "door_5xx", "mcp_edge", None),
        ("route:POST /crm/deals/{deal_id}", "route_500", "door_5xx", "app", None),
        ("route:other", "route_upstream", "door_5xx", "app", None),
        *[("mcp", code, "mcp_call", "studio", "acme-erp/get_invoice") for code in (
            "mcp_timeout", "mcp_unreachable", "mcp_refused", "mcp_too_large",
            "mcp_tool_error")],
    ])
    platform = _admin(world, "SELECT tool, exc_type, code_site FROM failure_fingerprints")
    assert len(platform) == 8
    assert "acme" not in repr(platform) and "ravi" not in repr(platform).lower()


# ── b. Refusals (P3) ─────────────────────────────────────────────────────────


def test_a_call_with_no_tenant_is_refused(world) -> None:
    with pytest.raises(Exception, match="no tenant"):
        _tally(world.app, None, _args())


def test_a_made_up_fingerprint_is_refused(world) -> None:
    with pytest.raises(Exception, match="fingerprint"):
        _tally(world.app, world.a, _args(fp="deadbeefdeadbeef"))
    with pytest.raises(Exception, match="fingerprint"):
        _tally(world.app, world.a, _args(fp=None))


@pytest.mark.parametrize("bad", [
    {"tool": "Ravi Kumar"}, {"tool": "route:GET /crm/deals/ravi@acme.in?x=1"},
    {"code": "boom"}, {"exc": "Ravi Kumar"}, {"site": "acb_skills.crm_tools"},
    {"code": "mcp_timeout"},  # a dash code with a type and a site
    {"tool": "x" * 81},
])
def test_a_field_off_its_grammar_is_refused(world, bad: dict[str, Any]) -> None:
    args = _args(**bad)
    args["fp"] = _fp(args["tool"], args["code"], args["exc"], args["site"])
    with pytest.raises(Exception, match=r"violates check constraint|failure_tally"):
        _tally(world.app, world.a, args)
    assert _admin(world, "SELECT count(*) FROM failure_org_days") == [(0,)]


@pytest.mark.parametrize("bad", [
    {"source": "tenant"}, {"surface": "phone"}, {"release": "not-a-sha"}, {"n": 0},
    {"n": 1001}, {"n": None},
])
def test_a_bad_column_value_is_refused(world, bad: dict[str, Any]) -> None:
    with pytest.raises(Exception, match=r"violates check constraint|failure_tally"):
        _tally(world.app, world.a, _args(**bad))


def test_a_good_call_counts_and_merges(world) -> None:
    args = _args()
    _tally(world.app, world.a, args)
    _tally(world.app, world.a, {**args, "n": 4, "surface": "whatsapp", "release": "def5678"})
    _tally(world.app, world.b, {**args, "source": "door_5xx", "release": None})
    [(total, sources, surfaces, first, last)] = _admin(
        world, "SELECT total, sources, surfaces, first_release, last_release "
        "FROM failure_fingerprints WHERE fingerprint = :f", f=args["fp"])
    assert total == 6
    assert sorted(sources) == ["chat_tool", "door_5xx"]
    assert sorted(surfaces) == ["chat", "whatsapp"]
    assert (first, last) == ("abc1234", "def5678")
    [(occ, rel)] = _admin(world, "SELECT occurrences, releases FROM failure_daily "
                          "WHERE fingerprint = :f", f=args["fp"])
    assert occ == 6 and sorted(rel) == ["abc1234", "def5678"]
    orgs = _admin(world, "SELECT organization_id::text FROM failure_org_days "
                  "WHERE fingerprint = :f ORDER BY 1", f=args["fp"])
    assert sorted(o for (o,) in orgs) == sorted([world.a, world.b])
    [(seen,)] = _admin(world, "SELECT last_seen_at = date_trunc('hour', last_seen_at) "
                       "FROM failure_fingerprints WHERE fingerprint = :f", f=args["fp"])
    assert seen is True


def test_a_tenant_cannot_name_another_org(world) -> None:
    """The tally takes no org argument. A session bound to org A counts org A."""
    args = _args()
    _tally(world.app, world.a, args)
    assert _admin(world, "SELECT organization_id::text FROM failure_org_days "
                  "WHERE fingerprint = :f", f=args["fp"]) == [(world.a,)]


# ── c. Isolation and grants (P4) ─────────────────────────────────────────────


def _insert_event(c: Any, fp: str, org: str | None = None) -> None:
    cols = "fingerprint, source, surface, error_code, tool, exc_type, code_site"
    vals = ":fp, 'chat_tool', 'chat', 'unknown_tool', 'other', '-', '-'"
    if org:
        cols, vals = cols + ", organization_id", vals + ", CAST(:o AS uuid)"
    c.execute(text(f"INSERT INTO failure_events ({cols}) VALUES ({vals})"),
              {"fp": fp, "o": org})


def test_an_org_sees_only_its_own_tenant_rows(world) -> None:
    fp = _fp("other", "unknown_tool")
    for org in (world.a, world.b):
        with world.app.connect() as c, c.begin():
            c.execute(text(_BIND), {"o": org})
            _insert_event(c, fp)
        _tally(world.app, org, _args(tool="other", code="unknown_tool", exc="-", site="-"))
    for org in (world.a, world.b):
        with world.app.connect() as c, c.begin():
            c.execute(text(_BIND), {"o": org})
            for table in ("failure_events", "failure_org_days"):
                seen = c.execute(text(f"SELECT DISTINCT organization_id::text FROM {table}"))
                assert [r[0] for r in seen] == [org], table
            # Org A cannot delete or change a row of org B.
            other = world.b if org == world.a else world.a
            gone = c.execute(text("DELETE FROM failure_events WHERE organization_id = "
                                  "CAST(:o AS uuid)"), {"o": other})
            assert gone.rowcount == 0
    # A third org sees no row of either.
    with world.app.connect() as c, c.begin():
        c.execute(text(_BIND), {"o": world.c})
        for table in ("failure_events", "failure_org_days"):
            assert c.execute(text(f"SELECT count(*) FROM {table}")).scalar_one() == 0


def test_a_tenant_cannot_write_a_row_of_another_org(world) -> None:
    with (pytest.raises(Exception, match="row-level security"),
          world.app.connect() as c, c.begin()):
        c.execute(text(_BIND), {"o": world.a})
        _insert_event(c, _fp("other", "unknown_tool"), org=world.b)


def test_an_unbound_session_writes_no_tenant_row(world) -> None:
    with (pytest.raises(Exception, match=r"row-level security|null value"),
          world.app.connect() as c, c.begin()):
        _insert_event(c, _fp("other", "unknown_tool"))


@pytest.mark.parametrize("table", _PLATFORM)
@pytest.mark.parametrize("sql", ["SELECT * FROM {t}", "DELETE FROM {t}",
                                 "UPDATE {t} SET {col} = {col}"])
def test_the_app_role_cannot_touch_a_platform_table(world, table: str, sql: str) -> None:
    col = {"failure_fingerprints": "total", "failure_daily": "occurrences",
           "failure_config": "internal_org"}[table]
    with (pytest.raises(Exception, match="permission denied"),
          world.app.connect() as c, c.begin()):
        c.execute(text(_BIND), {"o": world.a})
        c.execute(text(sql.format(t=table, col=col)))


def test_each_function_is_a_definer_with_a_fixed_path_and_no_public_grant(world) -> None:
    for sig in _FUNCTIONS:
        [(definer, config, acl)] = _admin(
            world, "SELECT prosecdef, proconfig, proacl::text[] FROM pg_proc "
            "WHERE oid = CAST(:s AS regprocedure)", s=f"public.{sig}")
        assert definer is True, sig
        assert config == ["search_path=pg_catalog, pg_temp"], sig
        grantees = {entry.split("=", 1)[0] for entry in acl}
        assert "" not in grantees, f"{sig} is open to PUBLIC"
        assert _APP_ROLE in grantees, sig
    with world.app.connect() as c:
        for sig in _FUNCTIONS:
            assert c.execute(text("SELECT has_function_privilege(CAST(:s AS regprocedure), "
                                  "'EXECUTE')"), {"s": f"public.{sig}"}).scalar_one(), sig


# ── d. Only the internal org runs the job functions (P4) ─────────────────────


_JOBS = (
    ("SELECT failure_org_band(:fp)", {"fp": "00bc2542a73b3967"}),
    ("SELECT * FROM failure_due(10)", {}),
    ("SELECT failure_set(:fp, 'open', NULL, NULL, NULL, NULL, NULL, 0, 0)",
     {"fp": "00bc2542a73b3967"}),
    ("SELECT failure_prune_daily(CAST(now() AS date) - 500)", {}),
)


@pytest.mark.parametrize(("sql", "params"), _JOBS)
def test_no_config_row_refuses_every_org(world, sql: str, params: dict[str, Any]) -> None:
    for org in (world.a, world.b, None):
        with (pytest.raises(Exception, match=r"failure_config|internal"),
              world.app.connect() as c, c.begin()):
            if org:
                c.execute(text(_BIND), {"o": org})
            c.execute(text(sql), params)


@pytest.mark.parametrize(("sql", "params"), _JOBS)
def test_another_org_is_refused_and_the_internal_org_runs(world, sql: str,
                                                         params: dict[str, Any]) -> None:
    _internal(world, world.a)
    for org in (world.b, None):
        with (pytest.raises(Exception, match="internal"),
              world.app.connect() as c, c.begin()):
            if org:
                c.execute(text(_BIND), {"o": org})
            c.execute(text(sql), params)
    with world.app.connect() as c, c.begin():
        c.execute(text(_BIND), {"o": world.a})
        c.execute(text(sql), params)


# ── e. The band (P5) ─────────────────────────────────────────────────────────


def _band(world: Any, fp: str) -> str | None:
    with world.app.connect() as c, c.begin():
        c.execute(text(_BIND), {"o": world.a})
        return c.execute(text("SELECT failure_org_band(:f)"), {"f": fp}).scalar_one()


def test_the_band_hides_one_and_two_orgs(world) -> None:
    _internal(world, world.a)
    args = _args()
    assert _band(world, args["fp"]) == "0"
    _tally(world.app, world.a, args)
    assert _band(world, args["fp"]) == "<3"
    _tally(world.app, world.a, args)
    assert _band(world, args["fp"]) == "<3"
    _tally(world.app, world.b, args)
    assert _band(world, args["fp"]) == "<3"
    _tally(world.app, world.c, args)
    assert _band(world, args["fp"]) == "3"


def test_the_band_counts_a_fixed_window_of_30_days(world) -> None:
    _internal(world, world.a)
    args = _args()
    for org in (world.a, world.b, world.c):
        _tally(world.app, org, args)
    with world.p.admin_engine.begin() as c:
        c.execute(text("UPDATE failure_org_days SET day = day - 30 WHERE organization_id = "
                       "CAST(:o AS uuid)"), {"o": world.c})
    assert _band(world, args["fp"]) == "<3"


# ── f. The counts under load ─────────────────────────────────────────────────


def test_two_tallies_at_once_count_two(world) -> None:
    args = _args()
    gate = threading.Barrier(2)
    errors: list[BaseException] = []

    def one(org: str) -> None:
        try:
            with world.app.connect() as c, c.begin():
                c.execute(text(_BIND), {"o": org})
                gate.wait(timeout=10)
                c.execute(_TALLY, args)
        except BaseException as exc:
            errors.append(exc)

    threads = [threading.Thread(target=one, args=(org,)) for org in (world.a, world.b)]
    for t in threads:
        t.start()
    for t in threads:
        t.join(timeout=30)
    assert errors == []
    assert _admin(world, "SELECT total FROM failure_fingerprints WHERE fingerprint = :f",
                  f=args["fp"]) == [(2,)]
    assert _admin(world, "SELECT occurrences FROM failure_daily WHERE fingerprint = :f",
                  f=args["fp"]) == [(2,)]


def test_a_count_at_the_top_stays_there(world) -> None:
    args = _args()
    _tally(world.app, world.a, args)
    top = 9223372036854775807
    with world.p.admin_engine.begin() as c:
        c.execute(text("UPDATE failure_fingerprints SET total = :t - 10 WHERE fingerprint = :f"),
                  {"t": top, "f": args["fp"]})
        c.execute(text("UPDATE failure_daily SET occurrences = :t - 10 WHERE fingerprint = :f"),
                  {"t": top, "f": args["fp"]})
    _tally(world.app, world.a, {**args, "n": 1000})
    assert _admin(world, "SELECT total FROM failure_fingerprints WHERE fingerprint = :f",
                  f=args["fp"]) == [(top,)]
    assert _admin(world, "SELECT occurrences FROM failure_daily WHERE fingerprint = :f",
                  f=args["fp"]) == [(top,)]


# ── The job functions do what they say ─────────────────────────────────────


def _job(world: Any, sql: str, **params: Any) -> Any:
    with world.app.connect() as c, c.begin():
        c.execute(text(_BIND), {"o": world.a})
        return c.execute(text(sql), params)


def test_due_gives_platform_rows_and_counts_only(world) -> None:
    _internal(world, world.a)
    args = _args()
    _tally(world.app, world.b, {**args, "n": 3})
    with world.app.connect() as c, c.begin():
        c.execute(text(_BIND), {"o": world.a})
        result = c.execute(text("SELECT * FROM failure_due(10) WHERE fingerprint = :f"),
                           {"f": args["fp"]})
        cols = list(result.keys())
        [row] = result.mappings().all()
    assert "organization_id" not in cols and "tool_name" not in cols
    assert (row["total"], row["n_1d"], row["n_7d"], row["n_30d"]) == (3, 3, 3, 3)
    assert row["days"][0]["n"] == 3 and row["days"][0]["releases"] == ["abc1234"]
    assert world.b not in repr(dict(row))


def test_set_writes_the_state_and_keeps_one_task(world) -> None:
    _internal(world, world.a)
    args = _args()
    _tally(world.app, world.a, args)
    task = str(uuid.uuid4())
    sql = ("SELECT failure_set(:f, :s, CAST(:t AS uuid), NULL, NULL, NULL, NULL, :n, 0)")
    assert _job(world, sql, f=args["fp"], s="open", t=task, n=1).scalar_one() is True
    assert _admin(world, "SELECT state, task_id::text, shown_total FROM failure_fingerprints "
                  "WHERE fingerprint = :f", f=args["fp"]) == [("open", task, 1)]
    with pytest.raises(Exception, match="one task"):
        _job(world, sql, f=args["fp"], s="open", t=str(uuid.uuid4()), n=1)
    with pytest.raises(Exception, match="violates check constraint"):
        _job(world, sql, f=args["fp"], s="gone", t=task, n=1)
    assert _job(world, sql, f="0000000000000000", s="open", t=task, n=1).scalar_one() is False


def test_prune_daily_keeps_400_days(world) -> None:
    _internal(world, world.a)
    args = _args()
    _tally(world.app, world.a, args)
    with world.p.admin_engine.begin() as c:
        c.execute(text("INSERT INTO failure_daily (fingerprint, day, occurrences) "
                       "VALUES (:f, CAST(now() AS date) - 401, 5)"), {"f": args["fp"]})
    with pytest.raises(Exception, match="400"):
        _job(world, "SELECT failure_prune_daily(CAST(now() AS date) - 10)")
    gone = _job(world, "SELECT failure_prune_daily(CAST(now() AS date) - 400)").scalar_one()
    assert gone == 1
    assert _admin(world, "SELECT count(*) FROM failure_daily WHERE fingerprint = :f",
                  f=args["fp"]) == [(1,)]


def test_the_app_role_prunes_its_own_tenant_rows_only(world) -> None:
    """The ground of the TOF-2e prune hook: as the app role, in the session of
    each org, a delete takes old rows of that org only."""
    fp = _fp("other", "unknown_tool")
    for org in (world.a, world.b):
        with world.app.connect() as c, c.begin():
            c.execute(text(_BIND), {"o": org})
            _insert_event(c, fp)
    with world.p.admin_engine.begin() as c:
        c.execute(text("UPDATE failure_events SET occurred_at = now() - interval '31 days'"))
    with world.app.connect() as c, c.begin():
        c.execute(text(_BIND), {"o": world.a})
        gone = c.execute(text("DELETE FROM failure_events "
                              "WHERE occurred_at < now() - interval '30 days'")).rowcount
    assert gone == 1
    assert _admin(world, "SELECT organization_id::text FROM failure_events") == [(world.b,)]


def test_p9_the_prune_hook_keeps_each_retention_in_each_org(world, monkeypatch) -> None:
    """P9 (TOF-2e, §5.4). The prune hook of ``gateway/failures/sync.py`` runs
    in the session of each org, as the app role. ``failure_events`` older than
    30 days and ``failure_org_days`` older than 90 days go, in that org only.
    In the internal org it also takes ``failure_daily`` older than 400 days.

    Mutations: an event bound of 40 days, an org day bound of 100 days, no
    org term on a delete as the owner role (RLS still binds the app role),
    and no daily prune. Each bound and the daily prune turn this test red."""
    from datetime import timedelta

    from acb_common import get_settings
    from gateway.failures import sync

    s = get_settings()
    for name, value in (("tool_failure_tasks", True), ("tool_failure_org_id", world.a),
                        ("tool_failure_project_id", str(uuid.uuid4())),
                        ("tool_failure_run_as", "tool-failures@tof2e.test")):
        monkeypatch.setattr(s, name, value)
    _internal(world, world.a)
    args = _args()
    fp = args["fp"]
    _tally(world.app, world.a, args)
    today = _admin(world, "SELECT (now() AT TIME ZONE 'UTC')::date")[0][0]
    with world.p.admin_engine.begin() as c:
        for org in (world.a, world.b, world.c):
            for age, mark in (("31 days", "old"), ("29 days", "new")):
                c.execute(text(
                    "INSERT INTO failure_events (organization_id, occurred_at, fingerprint, "
                    "source, surface, error_code, tool, exc_type, code_site, run_id) VALUES "
                    "(CAST(:o AS uuid), now() - CAST(:age AS interval), :fp, 'chat_tool', "
                    "'chat', 'exception', :t, :e, :s, :m)"),
                    {"o": org, "age": age, "fp": fp, "t": args["tool"], "e": args["exc"],
                     "s": args["site"], "m": mark})
            for days in (91, 89):
                c.execute(text(
                    "INSERT INTO failure_org_days (organization_id, fingerprint, day) "
                    "VALUES (CAST(:o AS uuid), :fp, :d) ON CONFLICT DO NOTHING"),
                    {"o": org, "fp": fp, "d": today - timedelta(days=days)})
        for days in (401, 399):
            c.execute(text("INSERT INTO failure_daily (fingerprint, day, occurrences) "
                           "VALUES (:fp, :d, 1)"), {"fp": fp, "d": today - timedelta(days=days)})

    def prune(org: str) -> None:
        async def go() -> None:
            from acb_common.db import tenant_session

            url = world.p.app_url.render_as_string(hide_password=False)
            async with tenant_engine_scope(url), tenant_session(org) as db:
                await sync.prune_hook(db, today)
        asyncio.run(go())

    def left(org: str) -> tuple[list[Any], list[Any]]:
        events = sorted(r[0] for r in _admin(
            world, "SELECT run_id FROM failure_events "
                   "WHERE organization_id = CAST(:o AS uuid) AND run_id IS NOT NULL", o=org))
        days = sorted((today - r[0]).days for r in _admin(
            world, "SELECT day FROM failure_org_days WHERE organization_id = CAST(:o AS uuid) "
                   "AND day < :t - 30", o=org, t=today))
        return events, days

    def daily() -> list[int]:
        return sorted((today - r[0]).days for r in _admin(
            world, "SELECT day FROM failure_daily WHERE fingerprint = :f AND day < :t - 300",
            f=fp, t=today))

    prune(world.b)
    assert left(world.b) == (["new"], [89])
    assert left(world.a) == (["new", "old"], [89, 91])  # another org: untouched
    assert daily() == [399, 401]  # org B is not the internal org
    prune(world.a)
    prune(world.c)
    for org in (world.a, world.b, world.c):
        assert left(org) == (["new"], [89]), org
    assert daily() == [399]



def test_both_tenant_tables_force_row_level_security(world) -> None:
    """The mutation: drop FORCE from the migration. The suite role does not own
    the tables, so only the catalogue shows it."""
    rows = _admin(world, "SELECT relname, relrowsecurity, relforcerowsecurity FROM pg_class "
                         "WHERE relname IN ('failure_events', 'failure_org_days') "
                         "AND relkind = 'r'")
    assert {r[0] for r in rows} == {"failure_events", "failure_org_days"}
    assert all(r[1] and r[2] for r in rows), rows
