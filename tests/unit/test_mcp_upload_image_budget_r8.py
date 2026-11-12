"""CIV-14b — the 10 MB budget of a task under parallel uploads, on a real database (R8).

Spec: ``project-docs/specs/external_agent_access.md`` §12.9 ("Fences") and
§15 row CIV-14b.

**R8.** The real edge app and the real gateway app, through branch 0, as a
NON-privileged role on a promoted catalog of their own. The upload route
takes ``pg_advisory_xact_lock`` on the task before its SUM, so parallel
uploads to one task count one after the other. An agent's upload is one
image of 3.75 MB at most, so a task of 10 MB holds two images of 3.5 MiB and
refuses the third.

The gate is ``TENANT_LADDER_DATABASE_URL``. A skip is not a pass. Run::

    eval "$(bash scripts/dev_db.sh --export)"
    uv run pytest tests/unit/test_mcp_upload_image_budget_r8.py -v -rs

**The mutation (R7).** Drop the line ``await db.execute(text(_BUDGET_LOCK_SQL),
...)`` of ``attachments.attach_file``, and
``test_b1_three_parallel_uploads_of_3_5_mib_land_two`` can turn red: each
transaction reads the same SUM, and three images land. The race needs the
three statements to run at the same moment, so the red is likely, not sure.
"""
from __future__ import annotations

import asyncio
import base64
import os
import shutil
import tempfile
import uuid
from types import SimpleNamespace
from typing import Any

import pytest

pytest.importorskip("sqlalchemy")

from agent_edge import tools
from sqlalchemy import create_engine, text
from sqlalchemy.engine import make_url

from tests.unit import test_mcp_edge_task_writes as tw
from tests.unit import test_mcp_edge_writes as w
from tests.unit import test_mcp_upload_image_tenant_r8 as tenant
from tests.unit._tenant_ladder import apply_ladder
from tests.unit.test_mcp_upload_image_limits import png_of

_DB_GATE = w._DB_GATE
pytestmark = _DB_GATE

_APP_ROLE = "acb_app_mcpciv14bbud"
_APP_PW = "mcpciv14bbud_scratch_pw"
MIB = 1024 * 1024
BUDGET = 10 * MIB

redis_fake = tw.redis_fake


@pytest.fixture(scope="module")
def world():
    """A private promoted catalog, one tenant, one member, two empty tasks."""
    if not w._URL:
        pytest.skip("TENANT_LADDER_DATABASE_URL unset")
    admin_url = make_url(w._URL)
    dedicated = f"{admin_url.database}_mcpciv14bbud"
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
        c.execute(text("INSERT INTO organization (id, slug, display_name) VALUES (:id, :s, :s)"),
                  {"id": org, "s": "mcpciv14bbud"})
    store = tempfile.mkdtemp(prefix="mcpciv14bbud-store-")
    a = w._Seeder(eng, org)
    a.number = 500_000
    a.member(w.MEMBER, ["feature:projects"])
    team = a.project("Budget", in_space=True)
    todo = a.status(team, "To do", "todo", 0)
    t = {key: a.task(team, todo, key) for key in ("parallel", "edge")}
    world_ = SimpleNamespace(admin=eng, org=org, seeder=a, c={}, tok={}, t=t, f={},
                             store=store)
    world_.c["write"], world_.tok["write"] = a.connection(
        scopes=("projects:read", "projects:write"))
    world_.app_dsn = admin_url.set(
        database=dedicated, username=_APP_ROLE, password=_APP_PW,
    ).render_as_string(hide_password=False)
    yield world_

    eng.dispose()
    w._drop_database(admin_url, dedicated)
    shutil.rmtree(store, ignore_errors=True)


live_app = tenant.live_app
live = tenant.live


def _used(world, task: str) -> int:
    with world.admin.begin() as c:
        return int(c.execute(text(
            "SELECT COALESCE(SUM(a.size_bytes), 0) FROM pm_task_attachments ta"
            " JOIN attachments a ON a.id = ta.attachment_id"
            " WHERE ta.task_id = CAST(:t AS uuid)"), {"t": world.t[task]}).scalar())


def _call(world, task: str, data: bytes, n: int) -> Any:
    return tw._mcp_unscoped(world, "write", "upload_task_image", {
        "task_id": world.t[task], "filename": f"capture-{n}.png",
        "idempotency_key": f"budget-{uuid.uuid4().hex[:12]}",
        "content_base64": base64.b64encode(data).decode("ascii")})


class TestTheBudgetUnderParallelUploads:

    async def test_b1_three_parallel_uploads_of_3_5_mib_land_two(self, live):
        images = [png_of(int(3.5 * MIB) - n) for n in range(3)]

        async def go() -> list[dict[str, Any]]:
            return list(await asyncio.gather(
                *(_call(live, "parallel", data, n) for n, data in enumerate(images))))
        results = await tw._in_scope(live, go)
        landed = [r for r in results if r["isError"] is False]
        refused = [r for r in results if r["isError"] is True]
        assert len(landed) == 2, results
        assert [r["content"][0]["text"] for r in refused] == [tools.TEXT_IMAGE_BUDGET]
        assert _used(live, "parallel") <= BUDGET
        assert _used(live, "parallel") == sum(
            r["structuredContent"]["attachment"]["size"] for r in landed)

    async def test_b2_the_budget_holds_to_the_byte(self, live):
        first = int(3.5 * MIB)

        async def go() -> list[dict[str, Any]]:
            out = []
            for n, size in enumerate((first, first, BUDGET - 2 * first + 1,
                                      BUDGET - 2 * first)):
                out.append(await _call(live, "edge", png_of(size), n))
            return out
        a, b, over, exact = await tw._in_scope(live, go)
        assert a["isError"] is False and b["isError"] is False
        assert over["content"][0]["text"] == tools.TEXT_IMAGE_BUDGET
        assert exact["isError"] is False, exact
        assert _used(live, "edge") == BUDGET
