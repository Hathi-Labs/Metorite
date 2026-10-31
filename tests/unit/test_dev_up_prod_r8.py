"""R8: ``dev_up.sh --data prod`` restores production's copy, and RLS still binds.

LD-8b, ``project-docs/specs/engineering_practice.md`` §1.3.1.

No real copy comes near this suite. It builds a FAKE copy the same way the
nightly export does (``scripts/staging_export.sh``): a pgvector:pg17 server
with the whole ladder and the RLS promotion, two organizations, then
``pg_dump -Fc --schema=public --no-owner --no-acl``, ``roles.txt``,
``extensions.txt``, ``flags.env`` and ``SHA256SUMS``. Then it runs the real
``scripts/dev_up.sh --data prod --db-only`` against that copy, in a private
stack, and reads the restored database.

What it fences:

1. With no ``--as``, it refuses and lists the active members only.
2. An unknown email and an inactive member refuse. An active member passes.
3. ``relforcerowsecurity`` holds on every table that forced RLS in the copy.
4. ``push_subscriptions`` has 0 rows after the restore, and after each start.
5. ``acb_app`` is NOSUPERUSER NOBYPASSRLS, and reads 0 rows of another organization.
6. The roles and extensions of the copy arrive, and ``apply_migrations.sh`` ran.

Every container is private to this run, and the suite removes each one. It
never touches the database of ``TENANT_LADDER_DATABASE_URL``, because another
session may use it. The gate arms the suite as an R8 suite. It also needs a
Docker daemon for the two pg17 servers.
"""
from __future__ import annotations

import hashlib
import os
import re
import shutil
import subprocess
import uuid
from pathlib import Path

import pytest
from sqlalchemy import create_engine, text

from tests.unit.test_h3_rls_promotion_rehearsal import _DB_GATE

pytestmark = _DB_GATE

_ROOT = Path(__file__).resolve().parents[2]
_SCRIPT = _ROOT / "scripts" / "dev_up.sh"
_IMAGE = "pgvector/pgvector:pg17"
_STAMP = "20261101T023814Z"
_ORG_A = str(uuid.uuid4())
_ORG_B = str(uuid.uuid4())
_OWNER_A = "owner@ld8b-a.test"
_GONE_A = "gone@ld8b-a.test"
_MEMBER_B = "member@ld8b-b.test"
_DATA_FILES = ("staging.dump", "roles.txt", "extensions.txt", "flags.env")


def _bash() -> str | None:
    if os.name == "nt":
        # Git's bash, not WSL's bash.exe, as in test_dev_up.py.
        git_bash = Path(os.environ.get("PROGRAMFILES", r"C:\Program Files")) / "Git/bin/bash.exe"
        return str(git_bash) if git_bash.is_file() else None
    return shutil.which("bash")


def _dk(*args: str, stdin: str | None = None, timeout: int = 900) -> str:
    res = subprocess.run(["docker", *args], input=stdin, capture_output=True, text=True,
                         encoding="utf-8", timeout=timeout)
    assert res.returncode == 0, f"docker {args[:3]} failed:\n{res.stdout[-2000:]}\n{res.stderr[-2000:]}"
    return res.stdout


_SEED = f"""
INSERT INTO organization (id, slug, display_name) VALUES
  ('{_ORG_A}', 'ld8b-a', 'LD-8b A'), ('{_ORG_B}', 'ld8b-b', 'LD-8b B');
INSERT INTO app_user (email, display_name, role, status, organization_id) VALUES
  ('{_OWNER_A}', 'Owner A', 'executive', 'active', '{_ORG_A}'),
  ('{_GONE_A}', 'Gone A', 'employee', 'suspended', '{_ORG_A}'),
  ('{_MEMBER_B}', 'Member B', 'employee', 'active', '{_ORG_B}');
INSERT INTO user_identity (email, display_name) VALUES
  ('{_OWNER_A}', 'Owner A'), ('{_GONE_A}', 'Gone A'), ('{_MEMBER_B}', 'Member B')
  ON CONFLICT DO NOTHING;
INSERT INTO org_membership (organization_id, user_id, status)
  SELECT '{_ORG_A}', id, 'active' FROM user_identity WHERE email = '{_OWNER_A}'
  ON CONFLICT DO NOTHING;
INSERT INTO org_membership (organization_id, user_id, status)
  SELECT '{_ORG_A}', id, 'suspended' FROM user_identity WHERE email = '{_GONE_A}'
  ON CONFLICT DO NOTHING;
INSERT INTO org_membership (organization_id, user_id, status)
  SELECT '{_ORG_B}', id, 'active' FROM user_identity WHERE email = '{_MEMBER_B}'
  ON CONFLICT DO NOTHING;
INSERT INTO push_subscriptions (organization_id, member_email, endpoint, p256dh, auth) VALUES
  ('{_ORG_A}', '{_OWNER_A}', 'https://push.example/a', 'BKEY-A', 'AUTH-A'),
  ('{_ORG_B}', '{_MEMBER_B}', 'https://push.example/b', 'BKEY-B', 'AUTH-B');
INSERT INTO apps (slug, name, owner_email, workspace_path, organization_id) VALUES
  ('ld8b-a-1', 'ld8b', '{_OWNER_A}', '/tmp/ld8b-a-1', '{_ORG_A}'),
  ('ld8b-a-2', 'ld8b', '{_OWNER_A}', '/tmp/ld8b-a-2', '{_ORG_A}'),
  ('ld8b-b-1', 'ld8b', '{_MEMBER_B}', '/tmp/ld8b-b-1', '{_ORG_B}'),
  ('ld8b-b-2', 'ld8b', '{_MEMBER_B}', '/tmp/ld8b-b-2', '{_ORG_B}'),
  ('ld8b-b-3', 'ld8b', '{_MEMBER_B}', '/tmp/ld8b-b-3', '{_ORG_B}');
-- A policy that names a role of the copy: the restore fails unless the roles
-- loop makes that role first.
CREATE POLICY ld8b_probe ON public.apps TO ld8b_probe USING (false);
"""

_FORCED_SQL = ("SELECT relname FROM pg_class WHERE relkind IN ('r', 'p') "
               "AND relnamespace = 'public'::regnamespace AND relforcerowsecurity ORDER BY 1")


def _build_source(name: str) -> tuple[set[str], int]:
    """A production-shaped pg17 server: the ladder, the promotion, the seed."""
    _dk("run", "-d", "--name", name, "-e", "POSTGRES_USER=acb", "-e", "POSTGRES_PASSWORD=acb",
        "-e", "POSTGRES_DB=acb_src",
        "-v", f"{(_ROOT / 'infra').as_posix()}:/app/infra:ro",
        "-v", f"{(_ROOT / 'scripts').as_posix()}:/app/scripts:ro", _IMAGE)
    _dk("exec", name, "bash", "-c",
        "for i in $(seq 1 90); do pg_isready -h 127.0.0.1 -U acb -d acb_src && exit 0; sleep 1; done; exit 1",
        timeout=120)
    # The same build as dev_up.sh build_database, so the copy holds the whole
    # ladder and the RLS promotion, as production does.
    _dk("exec", name, "bash", "-c", """set -e
      q() { psql -X -q -v ON_ERROR_STOP=1 -U acb -d acb_src "$@"; }
      q -c "CREATE ROLE acb_app LOGIN NOSUPERUSER NOBYPASSRLS; CREATE ROLE ld8b_probe NOLOGIN;"
      q -c 'CREATE EXTENSION IF NOT EXISTS "uuid-ossp"; CREATE EXTENSION IF NOT EXISTS pgcrypto;'
      q -f /app/infra/postgres/01_schema.sql >/dev/null
      APP_DIR=/app PG_MODE=local PG_USER=acb PG_DB=acb_src SKIP_PRE_MIGRATION_BACKUP=1 \
        bash /app/scripts/apply_migrations.sh >/dev/null
      for p in 01_add_columns 02_backfill 03_constraints 04_policies; do
        q -f /app/infra/postgres/generated/$p.sql >/dev/null
      done""")
    _dk("exec", "-i", name, "psql", "-X", "-q", "-v", "ON_ERROR_STOP=1", "-U", "acb", "-d", "acb_src",
        stdin=_SEED)
    forced = set(_dk("exec", name, "psql", "-X", "-U", "acb", "-d", "acb_src", "-tAc",
                     _FORCED_SQL).split())
    pushes = int(_dk("exec", name, "psql", "-X", "-U", "acb", "-d", "acb_src", "-tAc",
                     "SELECT count(*) FROM push_subscriptions").strip())
    return forced, pushes


def _export(name: str, folder: Path) -> None:
    """The files of scripts/staging_export.sh, by its own queries."""
    folder.mkdir(parents=True)
    with (folder / "staging.dump").open("wb") as out:
        res = subprocess.run(["docker", "exec", name, "pg_dump", "-U", "acb", "-d", "acb_src", "-Fc",
                              "--no-owner", "--no-acl", "--schema=public"],
                             stdout=out, stderr=subprocess.PIPE, timeout=600)
    assert res.returncode == 0, res.stderr.decode("utf-8", "replace")
    head = (folder / "staging.dump").read_bytes()[:8]
    # "PGDMP", then the archive version: 1.16 is what pg_dump 17 writes.
    assert head[:5] == b"PGDMP" and tuple(head[5:7]) == (1, 16), head
    roles = _dk("exec", name, "psql", "-X", "-U", "acb", "-d", "acb_src", "-tAc",
                "select rolname from pg_roles where rolname !~ '^pg_' "
                "and rolname <> 'postgres' order by 1")
    (folder / "roles.txt").write_text(roles, encoding="utf-8", newline="\n")
    exts = _dk("exec", name, "psql", "-X", "-U", "acb", "-d", "acb_src", "-tAc",
               "select e.extname || '|' || n.nspname from pg_extension e "
               "join pg_namespace n on n.oid = e.extnamespace "
               "where e.extname <> 'plpgsql' order by 1")
    # Production's Supabase lists an extension that the image does not ship.
    (folder / "extensions.txt").write_text(exts + "pg_graphql|graphql\n",
                                           encoding="utf-8", newline="\n")
    (folder / "flags.env").write_text(
        "root:IDENTITY_CUTOVER=true\nroot:REACH_ENABLED=true\n"
        "workbench:NEXT_PUBLIC_PUSH_ENABLED=1\n", encoding="utf-8", newline="\n")
    sums = "".join(f"{hashlib.sha256((folder / f).read_bytes()).hexdigest()}  {f}\n"
                   for f in _DATA_FILES)
    (folder / "SHA256SUMS").write_text(sums, encoding="utf-8", newline="\n")


class _Stack:
    def __init__(self, repo: Path, copies: Path, bash: str) -> None:
        self.repo, self.copies, self.bash = repo, copies, bash

    def run(self, *args: str) -> subprocess.CompletedProcess:
        env = {**os.environ, "DEV_UP_REPO": self.repo.as_posix(),
               "PROD_COPY_DIR": self.copies.as_posix()}
        for name in ("DEV_UP_FLAGS_FILE", "DEV_UP_FLAGS_HOST"):
            env.pop(name, None)
        return subprocess.run([self.bash, _SCRIPT.as_posix(), *args], capture_output=True,
                              text=True, encoding="utf-8", timeout=1200, env=env)

    def state(self, key: str) -> str:
        for line in (self.repo / ".dev_up" / "state").read_text(encoding="utf-8").splitlines():
            if line.startswith(f"{key}="):
                return line.split("=", 1)[1]
        return ""

    def url(self, user: str = "acb", password: str = "acb") -> str:
        return f"postgresql+psycopg://{user}:{password}@127.0.0.1:{6400 + int(self.state('SLOT'))}/acb_dev"

    def log(self, name: str) -> str:
        return (self.repo / ".dev_up" / f"{name}.log").read_text(encoding="utf-8", errors="replace")


@pytest.fixture(scope="module")
def restored(tmp_path_factory):
    """The fake copy, restored by the real dev_up.sh into a private stack."""
    bash = _bash()
    if bash is None or shutil.which("docker") is None:
        pytest.skip("needs bash and Docker: the copy is pg_dump 17 output in a pg17 container")
    if subprocess.run(["docker", "info"], capture_output=True, timeout=120).returncode != 0:
        pytest.skip("needs a running Docker daemon for the two pg17 servers")
    uid = uuid.uuid4().hex[:8]
    # resolve(): a Windows temp path can hold an 8.3 short name, and a Docker
    # mount of a short name comes up empty.
    base = tmp_path_factory.mktemp(f"ld8b{uid}").resolve()
    src = f"ld8b-r8-src-{uid}"
    repo = base / f"ld8b-{uid}"
    copies = base / "copies"
    stack = _Stack(repo, copies, bash)
    try:
        forced, pushes = _build_source(src)
        _export(src, copies / _STAMP)
        # A pull that did not finish. It is newer, and it must never count.
        (copies / ".20261102T000000Z").mkdir()
        (copies / ".20261102T000000Z" / "staging.dump").write_bytes(b"partial")
        # The stack mounts infra/ and scripts/ of its repo, so give it a copy of both.
        shutil.copytree(_ROOT / "infra", repo / "infra")
        shutil.copytree(_ROOT / "scripts", repo / "scripts")
        first = stack.run("--data", "prod", "--db-only")
        stack.forced, stack.source_pushes, stack.first = forced, pushes, first
        yield stack
    finally:
        if repo.exists():
            stack.run("--down")
        subprocess.run(["docker", "rm", "-f", src], capture_output=True, timeout=120)
        left = subprocess.run(["docker", "ps", "-aq", "--filter", f"label=metorite.dev_up={repo.as_posix()}"],
                              capture_output=True, text=True, timeout=60).stdout.split()
        if left:
            subprocess.run(["docker", "rm", "-f", *left], capture_output=True, timeout=120)


def test_no_as_refuses_and_lists_the_active_members(restored) -> None:
    res = restored.first
    assert res.returncode == 1, res.stdout + res.stderr
    assert f"restoring the copy {_STAMP}" in res.stdout, "the newest whole copy was not the one restored"
    assert "--data prod needs --as <email>" in res.stderr
    assert f"{_OWNER_A}  ld8b-a" in res.stderr
    assert f"{_MEMBER_B}  ld8b-b" in res.stderr
    assert _GONE_A not in res.stderr, "a suspended member is listed as active"


def test_an_unknown_email_refuses(restored) -> None:
    res = restored.run("--data", "prod", "--db-only", "--as", "nobody@ld8b.test")
    assert res.returncode == 1
    assert "nobody@ld8b.test is not an active member in this copy" in res.stderr
    assert f"{_OWNER_A}  ld8b-a" in res.stderr


def test_an_inactive_member_refuses(restored) -> None:
    res = restored.run("--data", "prod", "--db-only", "--as", _GONE_A)
    assert res.returncode == 1
    assert f"{_GONE_A} is not an active member in this copy" in res.stderr


def test_an_active_member_passes(restored) -> None:
    res = restored.run("--data", "prod", "--db-only", "--as", _OWNER_A.upper())
    assert res.returncode == 0, res.stdout + res.stderr
    assert "Database only" in res.stdout


def test_a_switch_to_the_demo_needs_reset(restored) -> None:
    res = restored.run("--data", "demo", "--db-only")
    assert res.returncode == 1
    assert "holds the prod data" in res.stderr


def test_force_rls_holds_on_every_tenant_table_of_the_copy(restored) -> None:
    assert len(restored.forced) > 50 and {"apps", "app_user", "push_subscriptions"} <= restored.forced
    eng = create_engine(restored.url())
    try:
        with eng.connect() as c:
            after = set(c.execute(text(_FORCED_SQL)).scalars())
            marker = c.execute(text("SELECT shobj_description(oid, 'pg_database') "
                                    "FROM pg_database WHERE datname = 'acb_dev'")).scalar_one()
            probe = c.execute(text("SELECT count(*) FROM pg_policies "
                                   "WHERE policyname = 'ld8b_probe'")).scalar_one()
    finally:
        eng.dispose()
    assert after == restored.forced, f"lost FORCE RLS: {sorted(restored.forced - after)}"
    assert marker == f"dev_up: prod copy {_STAMP}"
    assert probe == 1, "the policy that names a role of the copy did not arrive"


def test_the_restore_made_the_roles_and_extensions_of_the_copy(restored) -> None:
    log = restored.log("restore")
    assert "extension pg_graphql is not in the image, so it is skipped" in log
    assert "pg_restore: error" not in log
    eng = create_engine(restored.url())
    try:
        with eng.connect() as c:
            roles = set(c.execute(text("SELECT rolname FROM pg_roles")).scalars())
            exts = set(c.execute(text("SELECT extname FROM pg_extension")).scalars())
    finally:
        eng.dispose()
    assert "ld8b_probe" in roles
    assert {"vector", "uuid-ossp", "pgcrypto"} <= exts


def test_the_branch_migrations_ran_on_the_copy(restored) -> None:
    counts = re.findall(r"\((\d+) applied, (\d+) already recorded\)", restored.log("migrate"))
    assert counts, "apply_migrations.sh did not run on the copy"
    applied, recorded = map(int, counts[0])
    assert applied == 0 and recorded > 300, counts[0]


def test_push_subscriptions_is_empty_after_the_restore_and_each_start(restored) -> None:
    assert restored.source_pushes == 2, "the copy must hold push keys for this test to mean anything"
    eng = create_engine(restored.url())
    try:
        with eng.connect() as c:
            assert c.execute(text("SELECT count(*) FROM push_subscriptions")).scalar_one() == 0
        with eng.begin() as c:
            c.execute(text("INSERT INTO push_subscriptions (organization_id, member_email, endpoint, "
                           "p256dh, auth) VALUES (:o, :e, 'https://push.example/late', 'K', 'A')"),
                      {"o": _ORG_A, "e": _OWNER_A})
        res = restored.run("--data", "prod", "--db-only", "--as", _OWNER_A)
        assert res.returncode == 0, res.stdout + res.stderr
        with eng.connect() as c:
            assert c.execute(text("SELECT count(*) FROM push_subscriptions")).scalar_one() == 0
    finally:
        eng.dispose()


def test_the_app_role_reads_no_row_of_another_organization(restored) -> None:
    admin = create_engine(restored.url())
    app = create_engine(restored.url("acb_app", "acb_app"))
    try:
        with admin.connect() as c:
            super_, bypass = c.execute(text("SELECT rolsuper, rolbypassrls FROM pg_roles "
                                            "WHERE rolname = 'acb_app'")).one()
        assert (super_, bypass) == (False, False)
        with app.connect() as c:
            unbound = c.execute(text("SELECT count(*) FROM apps WHERE slug LIKE 'ld8b-%'")).scalar_one()
            assert unbound == 0, "an unbound read of acb_app sees rows"
            c.execute(text("SELECT set_config('app.tenant_id', :o, false)"), {"o": _ORG_A})
            mine = c.execute(text("SELECT count(*) FROM apps WHERE slug LIKE 'ld8b-%'")).scalar_one()
            theirs = c.execute(text("SELECT count(*) FROM apps WHERE organization_id = :b"),
                               {"b": _ORG_B}).scalar_one()
            members = c.execute(text("SELECT count(*) FROM app_user WHERE organization_id = :b"),
                                {"b": _ORG_B}).scalar_one()
        assert mine == 2, "the bound read must see its own organization's rows"
        assert theirs == 0 and members == 0, "acb_app read another organization's rows"
    finally:
        app.dispose()
        admin.dispose()


def test_the_held_switch_of_the_copy_arrives_off(restored) -> None:
    res = restored.run("--print-env", "--as", _OWNER_A)
    assert res.returncode == 0, res.stderr
    lines = set(res.stdout.splitlines())
    assert "REACH_ENABLED=false" in lines
    assert "NEXT_PUBLIC_PUSH_ENABLED=0" in lines
    assert "IDENTITY_CUTOVER=true" in lines
    assert f"DEV_SIGNED_IN_EMAIL={_OWNER_A}" in lines
