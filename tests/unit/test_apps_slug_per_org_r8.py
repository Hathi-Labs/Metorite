"""WS-STU A3b-1 (task 1310) — the folder of a new app is named by its id (R8).

Code: ``gateway/routes/apps/lifecycle.py`` (``_reserve_app``),
``gateway/studio/packages_store.py`` (``_CREATE_SQL``) and the migration that
adds ``apps_org_slug_key`` (found by CONTENT, never by number, R1).

**The fault.** ``apps.slug`` is unique over all orgs (``apps_slug_key``), and
the folder of an app was ``apps_root()/<slug>``. A slug that is unique for
each org only would then let two orgs share one folder. So A3b-1 moves the
folder of a new app to ``apps_root()/<app id>`` first. A3b-2 drops the global
key one release later (R6, expand then contract).

**What A3b-1 does NOT fix.** Until A3b-2, a slug that org A holds still blocks
the same slug for org B on the INSERT. Org B then takes the next slug. This
suite pins that the answer has the same shape as a clash in the own org: no
500, and nothing that names the other org. The suffix itself is the last
sign, and A3b-2 removes it.

**Acceptance.**

a. The migration adds ``apps_org_slug_key`` on ``(organization_id, slug)``,
   keeps ``apps_slug_key``, and replays with no error.
b. A new app has ``workspace_path == apps_root()/<its id>``, and the path
   holds no slug.
c. A cross-org slug clash gives no 500, and the answer has the same shape as
   a same-org clash. It names no member, folder or id of the other org.
d. The new folder of org B is never a folder of org A, also when org A
   deleted its app and left the folder, and org B then takes the same slug.
e. No INSERT into ``apps`` under ``apps/`` names ``ON CONFLICT (slug)``. Such
   a clause fails once A3b-2 drops ``apps_slug_key``.

**The mutations (R7).** Each one was run on 2026-11-11 and restored with git.

* MA1. ``_reserve_app``: ``workspace = root / slug`` (the old folder).
  b, c and d go red.
* MA2. ``_RESERVE_SQL``: drop ``ON CONFLICT DO NOTHING``. c goes red: the
  cross-org clash raises a unique violation, a 500.
* MA3. ``_CREATE_SQL`` of the package store: ``ON CONFLICT (slug)``. e goes
  red.

Run (a PRIVATE ladder database, one R8 suite at a time, because the
``promoted`` fixture drops ``<db>_h3rls``)::

    export TENANT_LADDER_DATABASE_URL=postgresql+psycopg://acb:acb@127.0.0.1:<port>/acb_tenant
    uv run pytest tests/unit/test_apps_slug_per_org_r8.py -v -rs

Without the database every R8 test here SKIPS, and the run reads green. A
skip is not a pass.
"""

from __future__ import annotations

import asyncio
import json
import re
import uuid
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

pytest.importorskip("sqlalchemy")

from acb_auth import UserContext, UserRole
from acb_common import get_settings
from acb_common.db import bind_tenant, release_tenant
from gateway.routes.apps import lifecycle
from gateway.routes.apps._common import _tenant_session, load_app
from sqlalchemy import text

from tests.unit._tenant_ladder import _exec_file, tenant_engine_scope
from tests.unit.test_h3_rls_promotion_rehearsal import (  # noqa: F401
    _DB_GATE,
    promoted,
)

_ROOT = Path(__file__).resolve().parents[2]
_MIGRATIONS = sorted(
    p for p in (_ROOT / "infra" / "postgres").glob("*.sql")
    if "ADD COLUMN IF NOT EXISTS organization_id UUID"
    in p.read_text(encoding="utf-8")
    and "CREATE UNIQUE INDEX IF NOT EXISTS apps_org_slug_key"
    in p.read_text(encoding="utf-8")
    and "ALTER TABLE apps" in p.read_text(encoding="utf-8"))
#: The contract file (A3b-2). It is found by content too (R1).
_CONTRACT = sorted(
    p for p in (_ROOT / "infra" / "postgres").glob("*.sql")
    if "ALTER TABLE apps DROP CONSTRAINT IF EXISTS apps_slug_key"
    in p.read_text(encoding="utf-8"))

TAG = uuid.uuid4().hex[:8]
#: The two members of this suite. The tag lets teardown find their rows.
ANA = f"ana.{TAG}@a3b1.test"
BEN = f"ben.{TAG}@a3b1.test"
_LIKE = f"%.{TAG}@a3b1.test"


# ── e and a, static: the source and the migration file ──────────────────────


def test_no_apps_insert_names_on_conflict_slug() -> None:
    """Each file under ``apps/`` that inserts into ``apps`` names no
    ``ON CONFLICT (slug)``. The clause needs a unique rule on ``(slug)``
    alone, and A3b-2 drops it. A bare ``ON CONFLICT DO NOTHING`` holds with
    both keys."""
    insert = re.compile(r"INSERT\s+INTO\s+apps\s*\(", re.I)
    conflict = re.compile(r"ON\s+CONFLICT\s*\(\s*slug\s*\)", re.I)
    writers, offenders = [], []
    for path in sorted((_ROOT / "apps").rglob("*.py")):
        if "node_modules" in path.parts:
            continue
        body = path.read_text(encoding="utf-8")
        if not insert.search(body):
            continue
        writers.append(path.relative_to(_ROOT).as_posix())
        if conflict.search(body):
            offenders.append(path.relative_to(_ROOT).as_posix())
    # The positive half, so an empty scan cannot pass.
    assert "apps/services/gateway/gateway/routes/apps/lifecycle.py" in writers
    assert "apps/services/gateway/gateway/studio/packages_store.py" in writers
    assert offenders == [], f"ON CONFLICT (slug) on apps: {offenders}"


def test_the_migration_is_one_file_and_expand_only() -> None:
    assert len(_MIGRATIONS) == 1, [p.name for p in _MIGRATIONS]
    sql = _MIGRATIONS[0].read_text(encoding="utf-8")
    code = "\n".join(
        line for line in sql.splitlines() if not line.lstrip().startswith("--"))
    assert "SET LOCAL lock_timeout = '5s';" in code
    assert "ON apps (organization_id, slug)" in code
    assert "ADD COLUMN IF NOT EXISTS organization_id UUID" in code
    assert not re.search(r"\bDROP\s+(INDEX|CONSTRAINT|TABLE|COLUMN)\b", code, re.I), (
        "the drop of apps_slug_key is A3b-2")
    assert "CONCURRENTLY" not in code.upper()
    assert "%" not in code, "psycopg reads a per-cent sign as a placeholder"


# ── The R8 world ────────────────────────────────────────────────────────────


def _dsn(url: Any) -> str:
    return url.render_as_string(hide_password=False)


@pytest.fixture
def world(promoted, tmp_path: Path, monkeypatch: pytest.MonkeyPatch):  # noqa: F811
    root = tmp_path / "custom_apps"
    monkeypatch.setattr(get_settings(), "custom_apps_root", str(root))
    # The activity feed is Redis, and this suite proves the folders and rows.
    monkeypatch.setattr(lifecycle, "publish_app_activity", lambda *a, **k: None)
    yield SimpleNamespace(p=promoted, root=root, tmp=tmp_path)
    with promoted.admin_engine.begin() as c:
        c.execute(text("DELETE FROM apps WHERE owner_email LIKE :t"), {"t": _LIKE})


def _user(email: str) -> UserContext:
    return UserContext(email=email, role=UserRole.EMPLOYEE)


def _create(world: Any, org: str, name: str, member: str) -> Any:
    """Create an app as *member* of *org*, on the app role. Answers the
    detail, or the exception that the call raised."""
    async def go() -> Any:
        async with tenant_engine_scope(_dsn(world.p.app_url)):
            tok = bind_tenant(org)
            try:
                return await lifecycle.create_app(
                    lifecycle.AppCreate(name=name), user=_user(member))
            except Exception as exc:  # the test reads it
                return exc
            finally:
                release_tenant(tok)
    return asyncio.run(go())


def _row(world: Any, slug: str, org: str) -> tuple[str, str]:
    """The id and the stored folder of the app *slug* of *org*."""
    with world.p.admin_engine.connect() as c:
        r = c.execute(text(
            "SELECT id::text, workspace_path FROM apps "
            "WHERE slug = :s AND organization_id = CAST(:o AS uuid)"),
            {"s": slug, "o": org}).one()
    return r[0], r[1]


def _folders_of(world: Any, org: str) -> set[str]:
    with world.p.admin_engine.connect() as c:
        return {r[0] for r in c.execute(text(
            "SELECT workspace_path FROM apps "
            "WHERE organization_id = CAST(:o AS uuid)"), {"o": org})}


# ── a. The migration, on the server ─────────────────────────────────────────


@_DB_GATE
def test_r8_only_the_org_slug_key_remains_and_the_files_replay(world) -> None:
    with world.p.admin_engine.connect() as c:
        defs = dict(c.execute(text(
            "SELECT indexname, indexdef FROM pg_indexes "
            "WHERE tablename = 'apps' "
            "AND indexname IN ('apps_slug_key', 'apps_org_slug_key')")).all())
    assert set(defs) == {"apps_org_slug_key"}, defs
    assert "UNIQUE INDEX" in defs["apps_org_slug_key"]
    assert defs["apps_org_slug_key"].endswith("(organization_id, slug)")
    with world.p.admin_engine.begin() as c:
        for f in (_MIGRATIONS[0], _CONTRACT[0]):
            _exec_file(c, str(f))
            _exec_file(c, str(f))


def test_the_contract_is_one_file_and_drops_only_the_global_key() -> None:
    assert len(_CONTRACT) == 1, [p.name for p in _CONTRACT]
    assert _CONTRACT[0].name > _MIGRATIONS[0].name, "contract before expand"
    sql = _CONTRACT[0].read_text(encoding="utf-8")
    code = "\n".join(
        line for line in sql.splitlines() if not line.lstrip().startswith("--"))
    assert "SET LOCAL lock_timeout = '5s';" in code
    # The org key is made again BEFORE the global key goes.
    assert code.index("apps_org_slug_key\n    ON apps (organization_id, slug)") \
        < code.index("DROP CONSTRAINT IF EXISTS apps_slug_key")
    drops = re.findall(r"\bDROP\s+\w+(?:\s+IF\s+EXISTS)?\s+(\w+)", code, re.I)
    assert set(drops) <= {"apps_slug_key", "CONSTRAINT"}, drops
    assert not re.search(r"\bDROP\s+(TABLE|COLUMN)\b", code, re.I)
    assert "CONCURRENTLY" not in code.upper()
    assert "%" not in code, "psycopg reads a per-cent sign as a placeholder"


def test_no_reader_finds_an_app_by_slug_without_the_org() -> None:
    """Since A3b-2 two orgs may hold one slug. A statement that reads or
    writes ``apps`` by ``slug`` must name ``organization_id`` too."""
    by_slug = re.compile(
        r"(FROM|UPDATE|JOIN)\s+apps\b(?:(?!text\(|\bdb\.execute)[^;]){0,300}?"
        r"\bslug\s*(?:=\s*(?::|ANY\s*\()|IN\s*\()", re.I | re.S)
    offenders = []
    for path in sorted((_ROOT / "apps").rglob("*.py")):
        if "node_modules" in path.parts or "tests" in path.parts:
            continue
        body = path.read_text(encoding="utf-8")
        for m in by_slug.finditer(body):
            window = body[m.start(): m.end() + 200]
            if "organization_id" not in window and "_BOUND_ORG" not in window:
                offenders.append(f"{path.relative_to(_ROOT).as_posix()}:"
                                 f"{body.count(chr(10), 0, m.start()) + 1}")
    assert offenders == [], f"an app is read by slug with no org: {offenders}"


# ── b. The folder is the id ─────────────────────────────────────────────────


@_DB_GATE
def test_r8_a_new_app_has_its_folder_at_its_id(world) -> None:
    a = world.p.org_a
    out = _create(world, a, f"Folder {TAG}", ANA)

    assert isinstance(out, lifecycle.AppDetail), out
    app_id, path = _row(world, out.slug, a)
    assert path == str(world.root / app_id)
    assert out.slug not in path, "the folder still names the slug"
    assert out.workspace_path == path
    folder = Path(path)
    assert json.loads((folder / "app.json").read_text(encoding="utf-8"))["slug"] == out.slug
    assert not (world.root / out.slug).exists(), "a folder at the slug path appeared"


# ── c. Another org's slug does not block (the contract) ────────────────────


@_DB_GATE
def test_r8_org_b_takes_the_slug_that_org_a_holds(world) -> None:
    a, b = world.p.org_a, world.p.org_b
    theirs = _create(world, a, f"Cross {TAG}", ANA)
    cross = _create(world, b, f"Cross {TAG}", BEN)
    own_first = _create(world, b, f"Same {TAG}", BEN)
    own_second = _create(world, b, f"Same {TAG}", BEN)

    for out in (theirs, cross, own_first, own_second):
        assert isinstance(out, lifecycle.AppDetail), out
    assert cross.slug == theirs.slug, "org B was pushed off a slug it may use"
    assert own_second.slug == f"{own_first.slug}-2", "a clash in the own org"
    assert cross.owner_email == BEN
    text_of_answer = json.dumps(cross.model_dump())
    their_id, their_path = _row(world, theirs.slug, a)
    for secret in (ANA, a, their_id, their_path):
        assert secret not in text_of_answer, f"the answer names org A: {secret}"
    cross_id, cross_path = _row(world, cross.slug, b)
    assert cross_path == str(world.root / cross_id) != their_path
    assert not (_folders_of(world, b) & _folders_of(world, a))


@_DB_GATE
def test_r8_each_org_reads_only_its_own_app_by_slug(world) -> None:
    a, b = world.p.org_a, world.p.org_b
    theirs = _create(world, a, f"Read {TAG}", ANA)
    mine = _create(world, b, f"Read {TAG}", BEN)
    assert theirs.slug == mine.slug
    a_id, _ = _row(world, theirs.slug, a)
    b_id, _ = _row(world, mine.slug, b)
    assert a_id != b_id

    async def read(org: str) -> Any:
        async with tenant_engine_scope(_dsn(world.p.app_url)):
            tok = bind_tenant(org)
            try:
                async with _tenant_session() as db:
                    return await load_app(db, theirs.slug)
            finally:
                release_tenant(tok)

    assert str((asyncio.run(read(a))).id) == a_id
    assert str((asyncio.run(read(b))).id) == b_id


# ── d. Org B never gets a folder of org A ───────────────────────────────────


@_DB_GATE
def test_r8_org_b_never_takes_a_folder_of_org_a(world) -> None:
    """Org A deletes its app and leaves the folder on disk (a failed delete).
    The global slug is free again, so org B takes the same slug. Its folder
    is new, and the folder of org A stays as it was."""
    a, b = world.p.org_a, world.p.org_b
    gone = _create(world, a, f"Gone {TAG}", ANA)
    assert isinstance(gone, lifecycle.AppDetail), gone
    _, a_path = _row(world, gone.slug, a)
    a_folder = Path(a_path)
    (a_folder / "index.html").write_text("org A only\n", encoding="utf-8")
    with world.p.admin_engine.begin() as c:
        c.execute(text("DELETE FROM apps WHERE slug = :s"), {"s": gone.slug})

    out = _create(world, b, f"Gone {TAG}", BEN)

    assert isinstance(out, lifecycle.AppDetail), out
    assert out.slug == gone.slug, "the slug of org A was free again"
    b_id, b_path = _row(world, out.slug, b)
    assert b_path == str(world.root / b_id)
    assert b_path != a_path, "org B took the folder of org A"
    assert (a_folder / "index.html").read_text(encoding="utf-8") == "org A only\n"
    assert not (_folders_of(world, b) & _folders_of(world, a)), (
        "org A and org B share a folder")


def test_this_suite_is_named_in_the_ci_skip_guard() -> None:
    """An R8 suite that CI does not list still skips in silence when the
    database gate is disarmed. Match the argument line, not a mention."""
    for name in ("pr-check.yml", "deploy.yml"):
        workflow = (_ROOT / ".github" / "workflows" / name).read_text(encoding="utf-8")
        assert re.search(r"^\s+tests/unit/test_apps_slug_per_org_r8\.py \\?$", workflow, re.M), name
