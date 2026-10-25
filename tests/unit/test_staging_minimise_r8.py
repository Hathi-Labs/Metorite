"""The staging copy holds the allowlisted organizations, and nothing else.

WS-38 T-9, ``project-docs/specs/staging_and_daily_release.md`` §3 and §5.10.
``infra/staging/minimise.sql`` runs on a restore of the production dump, on the
production box, before the copy travels to the staging box.

R8: this runs the real file with ``psql`` against a production-SHAPED database:
the whole ladder plus the four H3 phases, which is where the
``organization_id`` columns of production come from. Each test works on its
own clone (``CREATE DATABASE … TEMPLATE``), because the file deletes.

What it fences:

1. A customer that is not on the list leaves no row in any org-scoped table.
2. The kept organization keeps its rows.
3. An identity survives only for a member of a kept organization.
4. It refuses, and changes nothing, on an unknown or empty slug list.
5. It refuses a new table that has no ``organization_id`` and no rule.
6. It refuses a credential-shaped column that still holds a value.
"""

from __future__ import annotations

import itertools
import pathlib
import subprocess
import uuid

import pytest
from sqlalchemy import create_engine, text

from tests.unit.test_h3_rls_promotion_rehearsal import _DB_GATE, promoted  # noqa: F401

pytestmark = _DB_GATE

_ROOT = pathlib.Path(__file__).resolve().parents[2]
_SQL = _ROOT / "infra/staging/minimise.sql"
_n = itertools.count()


@pytest.fixture
def clone(promoted):  # noqa: F811
    """A throwaway copy of the promoted database, as a superuser engine."""
    base = promoted.admin_engine.url
    name = f"{base.database}_mz{next(_n)}"
    promoted.admin_engine.dispose()
    maint = create_engine(base.set(database="postgres"), isolation_level="AUTOCOMMIT")
    with maint.connect() as c:
        c.execute(text(f'DROP DATABASE IF EXISTS "{name}"'))
        # A template must have no session. The fixture's own pool is disposed
        # above, and a straggler (a previous clone's engine) is ended here.
        c.execute(text(
            "SELECT pg_terminate_backend(pid) FROM pg_stat_activity "
            "WHERE datname = :d AND pid <> pg_backend_pid()"), {"d": base.database})
        c.execute(text(f'CREATE DATABASE "{name}" TEMPLATE "{base.database}"'))
    eng = create_engine(base.set(database=name))
    eng.dsn = base.set(database=name).render_as_string(hide_password=False).replace("+psycopg", "")
    eng.org_a, eng.org_b = promoted.org_a, promoted.org_b
    try:
        yield eng
    finally:
        eng.dispose()
        with maint.connect() as c:
            c.execute(text(
                "SELECT pg_terminate_backend(pid) FROM pg_stat_activity "
                "WHERE datname = :d AND pid <> pg_backend_pid()"), {"d": name})
            c.execute(text(f'DROP DATABASE IF EXISTS "{name}"'))
        maint.dispose()


def _run(eng, slugs: str) -> subprocess.CompletedProcess:
    return subprocess.run(
        ["psql", eng.dsn, "-X", "-q", "-v", "ON_ERROR_STOP=1", "-v", f"keep_slugs={slugs}",
         "-f", str(_SQL)],
        capture_output=True, text=True, timeout=300,
    )


def _org_rows(eng, org: str) -> int:
    """Rows of ``org`` across every org-scoped public table."""
    with eng.connect() as c:
        tables = c.execute(text(
            "SELECT c.relname FROM pg_class c JOIN pg_namespace n ON n.oid = c.relnamespace "
            "WHERE n.nspname = 'public' AND c.relkind IN ('r','p') AND NOT c.relispartition "
            "AND EXISTS (SELECT 1 FROM pg_attribute a WHERE a.attrelid = c.oid "
            "AND a.attname = 'organization_id' AND NOT a.attisdropped)")).scalars().all()
        return sum(
            c.execute(text(f'SELECT count(*) FROM public."{t}" WHERE organization_id = :o'),
                      {"o": org}).scalar_one()
            for t in tables
        )


def _orgs(eng) -> set[str]:
    with eng.connect() as c:
        return set(c.execute(text("SELECT slug FROM organization")).scalars())


def test_only_the_kept_organization_remains(clone) -> None:
    before_a, before_b = _org_rows(clone, clone.org_a), _org_rows(clone, clone.org_b)
    assert before_a > 0 and before_b > 0, "the fixture seeds rows for both organizations"
    res = _run(clone, "h3rls-a")
    assert res.returncode == 0, res.stdout + res.stderr
    assert _orgs(clone) == {"h3rls-a"}
    assert _org_rows(clone, clone.org_b) == 0, "a customer not on the list left rows behind"
    assert _org_rows(clone, clone.org_a) == before_a, "a kept organization lost rows"
    assert "kept h3rls-a" in res.stdout


def test_an_identity_survives_only_for_a_kept_member(clone) -> None:
    keep_id, drop_id = str(uuid.uuid4()), str(uuid.uuid4())
    with clone.begin() as c:
        c.execute(text("INSERT INTO user_identity (id, email) VALUES (:k, :ke), (:d, :de)"),
                  {"k": keep_id, "ke": "owner@h3rls.test", "d": drop_id, "de": "gone@other.test"})
    res = _run(clone, "h3rls-a")
    assert res.returncode == 0, res.stdout + res.stderr
    with clone.connect() as c:
        left = set(c.execute(text("SELECT email FROM user_identity")).scalars())
    assert "owner@h3rls.test" in left, "the kept organization's member lost the identity"
    assert "gone@other.test" not in left


@pytest.mark.parametrize("slugs", ["h3rls-a,no-such-org", "", "nope"],
                         ids=["one-unknown", "empty", "all-unknown"])
def test_a_bad_slug_list_refuses_and_changes_nothing(clone, slugs: str) -> None:
    before, orgs = _org_rows(clone, clone.org_b), _orgs(clone)
    res = _run(clone, slugs)
    assert res.returncode != 0
    assert _orgs(clone) == orgs
    assert _org_rows(clone, clone.org_b) == before


def test_an_unclassified_table_refuses(clone) -> None:
    with clone.begin() as c:
        c.execute(text("CREATE TABLE public.zz_new_global (id int, note text)"))
    orgs = _orgs(clone)
    res = _run(clone, "h3rls-a")
    assert res.returncode != 0
    assert "zz_new_global" in res.stderr
    assert _orgs(clone) == orgs, "the refusal must roll everything back"


def test_a_credential_left_behind_refuses(clone) -> None:
    with clone.begin() as c:
        c.execute(text("CREATE TABLE public.zz_cred (organization_id uuid, api_key text)"))
        c.execute(text("INSERT INTO public.zz_cred VALUES (:o, 'sk-live-x')"), {"o": clone.org_a})
    orgs = _orgs(clone)
    res = _run(clone, "h3rls-a")
    assert res.returncode != 0
    assert "zz_cred.api_key" in res.stderr
    assert _orgs(clone) == orgs


def test_the_named_credentials_are_blanked(clone) -> None:
    """Every credential column the file names exists on the production shape.
    A rename would turn its UPDATE into an error, so this also catches drift."""
    res = _run(clone, "h3rls-a")
    assert res.returncode == 0, res.stdout + res.stderr
    with clone.connect() as c:
        for table in ("agent_token", "provider_keys", "agent_client", "auth_email_otp_token",
                      "email_detached_purges"):
            assert c.execute(text(f"SELECT count(*) FROM public.{table}")).scalar_one() == 0, table


def test_the_assistant_profile_is_blanked(clone) -> None:
    """WS-63 PZ-9 (``personalization.md`` §7 point 5): the member's profile
    is their own words, and no copy holds it. The row and its other columns
    stay. Mutation: drop the UPDATE in ``minimise.sql``."""
    with clone.begin() as c:
        c.execute(text(
            "INSERT INTO user_settings (organization_id, user_id, assistant_profile, "
            "number_system) VALUES (:o, 'pz9@h3rls.test', "
            "CAST(:p AS jsonb), 'international')"),
            {"o": clone.org_a, "p": '{"v": 1, "facts": [{"id": "f1", "text": "x"}]}'})
    res = _run(clone, "h3rls-a")
    assert res.returncode == 0, res.stdout + res.stderr
    with clone.connect() as c:
        row = c.execute(text(
            "SELECT assistant_profile, number_system FROM user_settings "
            "WHERE user_id = 'pz9@h3rls.test'")).one()
    assert row == (None, "international")


def test_a_varchar_credential_refuses(clone) -> None:
    """format_type prints `character varying(255)`, so a type-name match missed it."""
    with clone.begin() as c:
        c.execute(text("CREATE TABLE public.zz_v (organization_id uuid, foo_secret varchar(255))"))
        c.execute(text("INSERT INTO public.zz_v VALUES (:o, 's3cr3t')"), {"o": clone.org_a})
    res = _run(clone, "h3rls-a")
    assert res.returncode != 0
    assert "zz_v.foo_secret" in res.stderr


def test_the_legacy_mcp_auth_is_blanked(clone) -> None:
    """mcp_servers.headers and env_vars hold PLAINTEXT auth until a server moves
    to the encrypted registry. A verifier proved they left production."""
    with clone.begin() as c:
        c.execute(text(
            "INSERT INTO mcp_servers (name, label, organization_id, url, headers, env_vars) "
            "VALUES ('zz', 'zz', :o, 'https://mcp.example.test', '{\"Authorization\": \"Bearer sk-live-abcdefghijklmnop\"}', "
            "'{\"API\": \"k\"}')"), {"o": clone.org_a})
    res = _run(clone, "h3rls-a")
    assert res.returncode == 0, res.stdout + res.stderr
    with clone.connect() as c:
        row = c.execute(text("SELECT headers::text, env_vars::text FROM mcp_servers "
                             "WHERE name = 'zz'")).one()
    assert row == ("{}", "{}")


@pytest.mark.parametrize("value", [
    '{"auth": "Bearer abcdefghijklmnopqrstu"}', '{"k": "sk-ABCDEFGHIJKLMNOPQRSTUVWX"}',
    '{"k": "ghp_ABCDEFGHIJKLMNOPQRSTUV"}', '{"k": "AKIAABCDEFGHIJKLMNOP"}',
    '{"k": "-----BEGIN RSA PRIVATE KEY-----"}'],
    ids=["bearer", "sk", "github", "aws", "pem"])
def test_a_token_shaped_json_value_refuses(clone, value: str) -> None:
    with clone.begin() as c:
        c.execute(text("CREATE TABLE public.zz_j (organization_id uuid, meta jsonb)"))
        c.execute(text("INSERT INTO public.zz_j VALUES (:o, CAST(:v AS jsonb))"),
                  {"o": clone.org_a, "v": value})
    res = _run(clone, "h3rls-a")
    assert res.returncode != 0
    assert "zz_j.meta" in res.stderr
