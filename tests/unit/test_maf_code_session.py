"""WS43-F7, the store half — every write and delete of the file tools reaches
the blob store (R8).

Spec ``project-docs/specs/maf_coding_engine.md`` §7.4 ("The file tools") and
§10 WS43-F7, and the done-when 5 of WS-43d. WS-43e adds the ``code_task``
half of this fence (the scope switch, the report, the retry) to this file.

What breaks this half: a write or a delete of ``TenantFileStore`` skips the
blob store, lands under another store key, or keeps a legacy row that the
next rehydrate would bring back. The run data (``.run/``) never reaches the
store.

It runs the REAL ``mirror_to_blob_store`` and ``delete_file`` on the H3
rehearsal's phase-4 catalog, as its NOSUPERUSER NOBYPASSRLS role, with no
stub of the blob store (R8). ``TENANT_LADDER_DATABASE_URL`` must be set, and
a skip is not a pass.

Mutations this suite catches (R7), each run red once by hand:

* ``_after_write`` returns before the mirror: the write test;
* ``delete`` skips ``mirror_delete_from_blob_store``: the delete test;
* ``mirror_delete_from_blob_store`` deletes the run's key only: the legacy
  row test.
"""
from __future__ import annotations

import asyncio
import uuid
from pathlib import Path
from typing import Any

import pytest

pytest.importorskip("sqlalchemy")

from acb_skills.tenant_file_store import TenantFileStore

from tests.unit._sandbox_tools_fakes import PA, short_tmp  # noqa: F401 — fixture by name
from tests.unit.test_chat_write_under_rls import graph_as_app, members  # noqa: F401
from tests.unit.test_h3_rls_promotion_rehearsal import (  # noqa: F401
    _DB_GATE,
    app_engine,
    promoted,
)
from tests.unit.test_h201_tenant_workdirs import _rows


class _Guard:
    def hold(self) -> Any:
        class _Hold:
            async def __aenter__(self) -> None:
                return None

            async def __aexit__(self, *exc: Any) -> None:
                return None

        return _Hold()

    async def prepare(self) -> None:
        return None

    def writes_refused(self) -> bool:
        return False


def _run(org: str, short_tmp: Path, body: Any) -> Any:  # noqa: F811
    """Run *body(store, ws, run_data)* inside a bound run of *org*."""
    from acb_common.db import bind_tenant, release_tenant
    from acb_skills.agent_paths import ensure_state_dir, tenant_instance, thread_slug
    from acb_skills.write_artifact import bind_artifact_context

    thread = str(uuid.uuid4())
    ws = ensure_state_dir(PA, tenant_instance(org)).resolve()
    (ws / "outputs" / thread_slug(thread)).mkdir(parents=True, exist_ok=True)
    run_data = short_tmp / "run-data" / thread_slug(thread)
    run_data.mkdir(parents=True)

    async def go() -> Any:
        token = bind_tenant(org)
        bind_artifact_context(
            session_id=thread, agent_name=PA, run_id=f"r-{uuid.uuid4().hex[:6]}",
            workspace_root=str(ws), instance=tenant_instance(org),
            gateway_url="http://127.0.0.1:9", gateway_token="x",
        )
        try:
            store = TenantFileStore(
                workspace=ws, outputs_rel=f"outputs/{thread_slug(thread)}",
                run_data=run_data, guard=_Guard(), member="member@example.com",
            )
            out = await body(store, ws, run_data, thread_slug(thread))
            rest = [t for t in asyncio.all_tasks() if t is not asyncio.current_task()]
            await asyncio.gather(*rest, return_exceptions=True)
            return out
        finally:
            release_tenant(token)

    return asyncio.run(go())


@pytest.fixture
def clone(monkeypatch: pytest.MonkeyPatch, short_tmp: Path) -> Path:  # noqa: F811
    from acb_common import get_settings

    monkeypatch.setattr(get_settings(), "agents_clone_dir", str(short_tmp / "agents"))
    return short_tmp


@_DB_GATE
def test_a_write_lands_on_disk_and_in_the_store_under_the_runs_key(graph_as_app, clone) -> None:  # noqa: F811
    a = graph_as_app.org_a
    name = f"chart-{uuid.uuid4().hex[:6]}.txt"

    async def body(store: TenantFileStore, ws: Path, run_data: Path, slug: str) -> str:
        await store.write(f"outputs/{name}", "A CHART")
        await store.write(".run/rows.csv", "member rows")
        await store.write("agent-data/skills/chart/SKILL.md", "---\n")
        return slug

    slug = _run(a, clone, body)
    rel = f"outputs/{slug}/{name}"
    assert _rows(graph_as_app, rel, agent=PA) == [(a, f"o:{a}", "A CHART")]
    assert _rows(graph_as_app, "agent-data/skills/chart/SKILL.md", agent=PA)
    with graph_as_app.admin_engine.connect() as c:
        from sqlalchemy import text

        leaked = c.execute(text(
            "SELECT count(*) FROM agent_blob WHERE agent_name = :a AND path LIKE '%rows.csv'"),
            {"a": PA}).scalar()
    assert leaked == 0, "the run data reached the blob store"


@_DB_GATE
def test_a_delete_removes_the_file_and_both_rows(graph_as_app, clone) -> None:  # noqa: F811
    from acb_memory import put_file

    a = graph_as_app.org_a
    name = f"old-{uuid.uuid4().hex[:6]}.txt"
    seen: dict[str, Any] = {}

    async def body(store: TenantFileStore, ws: Path, run_data: Path, slug: str) -> None:
        rel = f"outputs/{slug}/{name}"
        await store.write(f"outputs/{name}", "TO DELETE")
        # A legacy row (instance '') at the same path, as an older run left.
        await put_file(PA, rel, b"LEGACY", instance="", organization_id=a)
        seen["rel"], seen["disk"] = rel, ws / rel
        assert await store.delete(f"outputs/{name}") is True

    _run(a, clone, body)
    assert not seen["disk"].exists()
    assert _rows(graph_as_app, seen["rel"], agent=PA) == []
