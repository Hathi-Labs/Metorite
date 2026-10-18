"""R8 — the GitHub App migration on a real Postgres (WS-54 GH-2).

Spec: ``project-docs/specs/github_integration.md`` §8.3 and §9.1 to §9.3.
The migration is found by CONTENT (``_github_r8.migration``), never by number
(R1). Every assertion runs on the promoted phase-4 catalog of
``test_h3_rls_promotion_rehearsal``, as its NOSUPERUSER NOBYPASSRLS role where
the rule is about the app.

* **M1** the eight columns exist and are nullable.
* **M2** ``via = 'github'`` inserts, and ``via = 'gitlab'`` fails.
* **M3** a second live ``github`` row with the same installation id in another
  org fails, and a ``disconnected`` one passes.
* **M4** ``github_installation_org`` returns the org with no tenant bound,
  returns no row for an unknown or disconnected id, and ``anon`` cannot run it.
* **M5** the migration runs twice with no error.
* **M6** a pre-check with a planted duplicate refuses with the count.

R7, the mutations. Each one turns a test here red (run 2026-10-18):

* G1 drop ``github|`` from the new ``via`` CHECK -> ``test_m2_via_github_inserts_and_gitlab_fails``.
* G2 drop ``AND status <> 'disconnected'`` from the index ->
  ``test_m3_one_live_installation_in_one_org``.
* G3 drop ``AND c.status <> 'disconnected'`` from the function ->
  ``test_m4_the_lookup_returns_the_org_and_nothing_else``.
* G4 remove ``SECURITY DEFINER`` -> ``test_m4_the_lookup_returns_the_org_and_nothing_else``.
* G5 drop the ``anon`` revoke loop -> ``test_m4_only_the_app_role_may_run_the_lookup``.
* G6 ``head_sha TEXT NOT NULL DEFAULT ''`` -> ``test_m1_the_eight_columns_are_nullable``.

Run (real Postgres)::

    eval "$(bash scripts/dev_db.sh --export)"
    uv run pytest tests/unit/test_github_migration_r8.py -v -rs
"""

from __future__ import annotations

import json
import re
import uuid

import pytest

pytest.importorskip("sqlalchemy")

import psycopg
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError, IntegrityError

from tests.unit import _github_r8 as g
from tests.unit.test_h3_rls_promotion_rehearsal import (  # noqa: F401
    _DB_GATE,
    app_engine,
    promoted,
)

pytestmark = _DB_GATE

EIGHT = {
    "head_sha": "text", "head_ref": "text", "checks_state": "text",
    "checks_summary": "jsonb", "checks_fetched_at": "timestamp with time zone",
    "pr_updated_at": "timestamp with time zone", "merged_at": "timestamp with time zone",
    "unreadable_since": "timestamp with time zone",
}


@pytest.fixture(scope="module")
def p(promoted):  # noqa: F811
    g.grant_lookup(promoted)
    return promoted


@pytest.fixture(autouse=True)
def _clean(p):
    g.clear_github(p)
    yield
    g.clear_github(p)


def _apply_migration(p) -> None:
    with p.admin_engine.connect() as c:
        with c.connection.dbapi_connection.cursor() as cur:
            cur.execute(g.migration().read_text(encoding="utf-8"))
        c.connection.dbapi_connection.commit()


def _insert_ref_as_app(app_engine, org: str, task: str, via: str) -> None:  # noqa: F811
    with app_engine.begin() as c:
        c.execute(text("SELECT set_config('app.tenant_id', :o, true)"), {"o": org})
        c.execute(text(
            "INSERT INTO pm_task_refs (task_id, kind, provider, ref, url, via, created_by) "
            "VALUES (CAST(:t AS uuid), 'pull_request', 'github', :ref, :url, :via, 'x@gh.test')"),
            {"t": task, "ref": f"acme/w#{uuid.uuid4().int % 10000}",
             "url": "https://github.com/acme/w/pull/1", "via": via})


# ── M1 ──────────────────────────────────────────────────────────────────────


def test_m1_the_eight_columns_are_nullable(p) -> None:
    cols = {
        r.column_name: (r.data_type, r.is_nullable, r.column_default)
        for r in g.rows(p,
                        "SELECT column_name, data_type, is_nullable, column_default "
                        "FROM information_schema.columns WHERE table_name = 'pm_task_refs'")
    }
    for name, kind in EIGHT.items():
        assert name in cols, f"{name} is missing"
        assert cols[name] == (kind, "YES", None), (name, cols[name])


def test_m1_the_head_sha_and_checks_state_are_checked(p, app_engine) -> None:  # noqa: F811
    task = g.seed_task(p, p.org_a, "m1@gh.test")
    _insert_ref_as_app(app_engine, p.org_a, task, "ui")
    for column, value in (("head_sha", "XYZ"), ("checks_state", "green"),
                          ("head_ref", "b" * 256)):
        with pytest.raises(IntegrityError), p.admin_engine.begin() as c:
            c.execute(text(f"UPDATE pm_task_refs SET {column} = :v"), {"v": value})
    with p.admin_engine.begin() as c:
        c.execute(text(
            "UPDATE pm_task_refs SET head_sha = :s, checks_state = 'passing', "
            "checks_summary = CAST(:j AS jsonb)"),
            {"s": "a" * 40, "j": json.dumps({"passed": 1, "total": 1})})


def test_m1_the_two_read_indexes_exist(p) -> None:
    names = {r.indexname for r in g.rows(
        p, "SELECT indexname FROM pg_indexes WHERE tablename = 'pm_task_refs'")}
    assert {"pm_task_refs_provider_ref", "pm_task_refs_head_sha"} <= names


# ── M2 ──────────────────────────────────────────────────────────────────────


def test_m2_via_github_inserts_and_gitlab_fails(p, app_engine) -> None:  # noqa: F811
    task = g.seed_task(p, p.org_a, "m2@gh.test")
    _insert_ref_as_app(app_engine, p.org_a, task, "github")
    for via in ("ui", "chat", "text", "mcp:claude-code"):
        _insert_ref_as_app(app_engine, p.org_a, task, via)
    with pytest.raises(IntegrityError):
        _insert_ref_as_app(app_engine, p.org_a, task, "gitlab")
    validated = g.scalar(
        p, "SELECT convalidated FROM pg_constraint WHERE conname = 'pm_task_refs_via_check'")
    assert validated is True


# ── M3 ──────────────────────────────────────────────────────────────────────


def test_m3_one_live_installation_in_one_org(p) -> None:
    g.seed_github_row(p, p.org_a, 4242)
    with pytest.raises(IntegrityError):
        g.seed_github_row(p, p.org_b, 4242)
    # A disconnected row leaves the index, so it passes.
    g.seed_github_row(p, p.org_b, 4242, status="disconnected")
    # And once A disconnects, B may connect it.
    g.seed_github_row(p, p.org_a, 4242, status="disconnected")
    g.seed_github_row(p, p.org_b, 4242, status="connected")
    assert g.scalar(
        p, "SELECT count(*) FROM integration_connections WHERE provider = 'github' "
        "AND status <> 'disconnected' AND provider_meta->>'installation_id' = '4242'") == 1


def test_m3_a_token_row_has_no_installation_and_never_clashes(p) -> None:
    g.seed_github_row(p, p.org_a, None, auth="token")
    g.seed_github_row(p, p.org_b, None, auth="token")
    assert g.scalar(p, "SELECT count(*) FROM integration_connections "
                       "WHERE provider = 'github'") == 2


# ── M4 ──────────────────────────────────────────────────────────────────────


def _lookup_as_app(app_engine, installation_id: int) -> list[str]:  # noqa: F811
    with app_engine.connect() as c:
        bound = c.execute(text("SELECT current_setting('app.tenant_id', true)")).scalar()
        assert not bound, "the lookup must run with no tenant bound"
        return [str(r[0]) for r in c.execute(
            text("SELECT organization_id FROM public.github_installation_org(:i)"),
            {"i": installation_id}).all()]


def test_m4_the_lookup_returns_the_org_and_nothing_else(p, app_engine) -> None:  # noqa: F811
    g.seed_github_row(p, p.org_a, 5001)
    g.seed_github_row(p, p.org_b, 5002, status="disconnected")
    # The mechanism proof: an unbound read of the table as the app role sees nothing.
    with app_engine.connect() as c:
        assert c.execute(text(
            "SELECT count(*) FROM integration_connections WHERE provider = 'github'"
        )).scalar() == 0
    assert _lookup_as_app(app_engine, 5001) == [p.org_a]
    assert _lookup_as_app(app_engine, 5002) == []      # disconnected
    assert _lookup_as_app(app_engine, 999999) == []    # unknown
    assert _lookup_as_app(app_engine, 0) == []


def test_m4_only_the_app_role_may_run_the_lookup(p) -> None:
    with p.admin_engine.begin() as c:
        for r in g.SUPABASE_ROLES:
            c.execute(text(
                f"DO $$ BEGIN CREATE ROLE {r} NOLOGIN; "
                f"EXCEPTION WHEN duplicate_object OR unique_violation THEN NULL; END $$;"))
            c.execute(text(f"GRANT EXECUTE ON FUNCTION {g.FN} TO {r}"))
    _apply_migration(p)
    g.grant_lookup(p)
    with p.admin_engine.connect() as c:
        public = c.execute(text(
            f"SELECT has_function_privilege('public', '{g.FN}', 'EXECUTE')")).scalar_one()
        for r in g.SUPABASE_ROLES:
            held = c.execute(text(
                f"SELECT has_function_privilege('{r}', '{g.FN}', 'EXECUTE')")).scalar_one()
            assert held is False, f"{r} may run the cross-tenant lookup"
        definer, path = c.execute(text(
            "SELECT p.prosecdef, p.proconfig FROM pg_proc p "
            "WHERE p.oid = CAST(:f AS regprocedure)"), {"f": g.FN}).one()
    assert public is False
    assert definer is True
    assert path == ["search_path=pg_catalog, pg_temp"]


def test_m4_anon_cannot_run_it(p) -> None:
    with p.admin_engine.begin() as c:
        c.execute(text(
            "DO $$ BEGIN CREATE ROLE anon NOLOGIN; "
            "EXCEPTION WHEN duplicate_object OR unique_violation THEN NULL; END $$;"))
    _apply_migration(p)
    with pytest.raises(DBAPIError) as err, p.admin_engine.begin() as c:
        c.execute(text("SET LOCAL ROLE anon"))
        c.execute(text("SELECT * FROM public.github_installation_org(1)"))
    assert "permission denied" in str(err.value).lower()


# ── M5 and M6 ───────────────────────────────────────────────────────────────


def test_m5_the_migration_runs_twice(p) -> None:
    g.seed_github_row(p, p.org_a, 6001)
    _apply_migration(p)
    _apply_migration(p)
    g.grant_lookup(p)
    assert g.scalar(p, "SELECT count(*) FROM pg_constraint "
                       "WHERE conname = 'pm_task_refs_via_check'") == 1


def test_m6_the_precheck_refuses_a_duplicate_with_the_count(p) -> None:
    sql = g.migration().read_text(encoding="utf-8")
    block = re.search(r"DO \$precheck\$.*?\$precheck\$;", sql, re.S)
    assert block, "the pre-check DO block is gone from the migration"
    with p.admin_engine.connect() as c:
        tx = c.begin()
        try:
            c.execute(text("DROP INDEX integration_connections_github_installation"))
            for org in (p.org_a, p.org_b):
                c.execute(text(
                    "INSERT INTO integration_connections (organization_id, provider, "
                    "owning_app, scope_kind, status, provider_meta) VALUES "
                    "(CAST(:o AS uuid), 'github', 'integrations', 'org', 'connected', "
                    "CAST(:m AS jsonb))"),
                    {"o": org, "m": json.dumps({"auth": "app", "installation_id": "7007"})})
            with pytest.raises(psycopg.Error) as err, \
                    c.connection.dbapi_connection.cursor() as cur:
                cur.execute(block.group(0))
            assert "1 GitHub installation id(s) are connected more than once (2 rows)" \
                in str(err.value)
        finally:
            tx.rollback()
