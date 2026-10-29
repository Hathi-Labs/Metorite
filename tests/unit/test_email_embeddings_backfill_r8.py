"""R8 — WS-KG KG-2, the email embeddings through the embed door, on a REAL Postgres.

``scripts/backfill_email_embeddings.py`` fills the old mail of the orgs that
the owner names. The embed door (``gateway.kb_index.embed.embed``) is STUBBED:
no test here calls a real embedding provider. Each test runs the app code as
the non-privileged role of the phase-4 rehearsal (NOSUPERUSER, NOBYPASSRLS).

* (a) With the flag OFF, the backfill fills the rows of two orgs. A second run
  writes 0 and calls the door 0 times. The run binds the mailbox owner and
  ``module_slug="email"``.
* (b) A run for org A leaves every row of org B untouched.
* (c) ``--estimate`` prints the counts, calls no embed and writes no row.
* (d) The sweep (``scheduler._embed_messages``) makes no call with the flag
  off, and goes through the door with the flag on.
* (e) A text that the door cannot embed stays pending, and the run stops.

⚠️ It SKIPS without ``TENANT_LADDER_DATABASE_URL``, and a skip is not a pass.
"""

from __future__ import annotations

import importlib.util
import uuid
from pathlib import Path
from typing import Any

import pytest

pytest.importorskip("sqlalchemy")

import email_ingestion.scheduler as sched
from acb_common.db import clear_tenant, release_tenant
from acb_common.settings import get_settings
from sqlalchemy import text

from tests.unit._tenant_ladder import tenant_engine_scope
from tests.unit.test_email_keep_separate import _assert_non_priv
from tests.unit.test_h3_rls_promotion_rehearsal import (  # noqa: F401
    _DB_GATE,
    app_engine,
    promoted,
)

pytestmark = _DB_GATE

_SCRIPT = Path(__file__).resolve().parents[2] / "scripts" / "backfill_email_embeddings.py"
OWNER_A = "owner-a@kg2.test"
OWNER_B = "owner-b@kg2.test"


def _script() -> Any:
    spec = importlib.util.spec_from_file_location("backfill_email_embeddings", _SCRIPT)
    assert spec and spec.loader
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


class _Door:
    """A stub of ``gateway.kb_index.embed.embed``. It records, and it calls no vendor."""

    def __init__(self) -> None:
        self.calls: list[dict[str, Any]] = []
        self.refuse: set[str] = set()

    async def __call__(self, texts: list[str], **_kw: Any) -> list[list[float] | None]:
        from acb_llm.routed import run_attribution

        att = run_attribution()
        self.calls.append({"n": len(texts), "member": att.get("user"),
                           "module": att.get("module_slug")})
        return [None if any(r in t for r in self.refuse) else [0.01 * (i + 1)] * 1536
                for i, t in enumerate(texts)]


@pytest.fixture
def door(monkeypatch: pytest.MonkeyPatch) -> _Door:
    from gateway.kb_index import embed as embed_mod

    stub = _Door()
    monkeypatch.setattr(embed_mod, "embed", stub)
    return stub


def _seed(admin: Any, org: str, owner: str, n: int, tag: str) -> str:
    with admin.begin() as c:
        acct = str(c.execute(text(
            "INSERT INTO email_accounts (user_id, provider, email_address, "
            "credentials_encrypted, organization_id) VALUES (:u, 'microsoft', :m, 'x', "
            "CAST(:o AS uuid)) RETURNING id"),
            {"u": owner, "m": f"{tag}-{uuid.uuid4().hex[:6]}@kg2.test", "o": org}).scalar_one())
        c.execute(text(
            "INSERT INTO email_messages (account_id, provider_message_id, folder, "
            "from_address, to_addresses, subject, body_text, received_at, organization_id) "
            "SELECT CAST(:a AS uuid), :t || '-' || g, 'inbox', "
            "'{\"name\": \"S\", \"email\": \"s@x.test\"}'::jsonb, '[]'::jsonb, "
            "'Subject ' || g, 'body ' || g || E'\\n', now() - make_interval(mins => g), "
            "CAST(:o AS uuid) FROM generate_series(1, :n) g"),
            {"a": acct, "o": org, "n": n, "t": tag})
    return acct


def _count(admin: Any, acct: str) -> int:
    with admin.connect() as c:
        return int(c.execute(text(
            "SELECT count(*) FROM email_embeddings WHERE account_id = CAST(:a AS uuid)"),
            {"a": acct}).scalar_one())


def _purge(admin: Any, accts: list[str]) -> None:
    with admin.begin() as c:
        for a in accts:
            for t in ("email_embeddings", "email_messages"):
                c.execute(text(f"DELETE FROM {t} WHERE account_id = CAST(:a AS uuid)"), {"a": a})
            c.execute(text("DELETE FROM email_accounts WHERE id = CAST(:a AS uuid)"), {"a": a})


@pytest.fixture
async def two_orgs(promoted, app_engine):  # noqa: F811
    _assert_non_priv(app_engine)
    p = promoted
    a = _seed(p.admin_engine, p.org_a, OWNER_A, 5, "a")
    b = _seed(p.admin_engine, p.org_b, OWNER_B, 3, "b")
    token = clear_tenant()
    try:
        async with tenant_engine_scope(p.app_url.render_as_string(hide_password=False)):
            yield {"p": p, "a": a, "b": b}
    finally:
        release_tenant(token)
        _purge(p.admin_engine, [a, b])


async def test_backfill_fills_two_orgs_and_a_second_run_writes_nothing(two_orgs, door) -> None:
    assert get_settings().email_semantic_search_enabled is False, "the flag must stay OFF"
    p, mod = two_orgs["p"], _script()
    first = await mod._run([p.org_a, p.org_b], 2, 0)
    assert first["rows"] == 8 and first["failures"] == 0
    assert _count(p.admin_engine, two_orgs["a"]) == 5
    assert _count(p.admin_engine, two_orgs["b"]) == 3
    # The run is the mailbox owner, and the usage rows carry the app.
    assert {(c["member"], c["module"]) for c in door.calls} == {
        (OWNER_A, "email"), (OWNER_B, "email")}
    assert max(c["n"] for c in door.calls) <= 2
    with p.admin_engine.connect() as c:
        models = c.execute(text(
            "SELECT DISTINCT model FROM email_embeddings WHERE account_id = ANY("
            "ARRAY[CAST(:a AS uuid), CAST(:b AS uuid)])"),
            {"a": two_orgs["a"], "b": two_orgs["b"]}).scalars().all()
    assert models == ["text-embedding-3-small"]

    calls = len(door.calls)
    second = await mod._run([p.org_a, p.org_b], 2, 0)
    assert second["rows"] == 0 and second["batches"] == 0
    assert len(door.calls) == calls, "the second run called the door"


async def test_a_run_for_org_a_leaves_org_b_untouched(two_orgs, door) -> None:
    p, mod = two_orgs["p"], _script()
    got = await mod._run([p.org_a], 64, 0)
    assert got["rows"] == 5
    assert _count(p.admin_engine, two_orgs["a"]) == 5
    assert _count(p.admin_engine, two_orgs["b"]) == 0
    assert {c["member"] for c in door.calls} == {OWNER_A}


async def test_estimate_prints_counts_and_embeds_nothing(two_orgs, door, capsys) -> None:
    p, mod = two_orgs["p"], _script()
    total = await mod._estimate([p.org_a, p.org_b])
    assert total["rows"] == 8 and total["tokens"] > 0
    assert door.calls == []
    assert _count(p.admin_engine, two_orgs["a"]) == 0
    out = capsys.readouterr().out
    assert "rows=5" in out and "rows=3" in out
    assert "body" not in out and "Subject" not in out, "the estimate printed mail text"
    assert mod.main(["--estimate"]) == 2


async def test_the_sweep_is_silent_with_the_flag_off_and_uses_the_door_with_it_on(
    two_orgs, door, monkeypatch,
) -> None:
    p = two_orgs["p"]
    assert await sched._embed_messages(p.org_a, two_orgs["a"]) == 0
    assert door.calls == [] and _count(p.admin_engine, two_orgs["a"]) == 0

    monkeypatch.setattr(get_settings(), "email_semantic_search_enabled", True)
    assert await sched._embed_messages(p.org_a, two_orgs["a"]) == 5
    assert len(door.calls) == 1
    assert door.calls[0]["member"] == OWNER_A and door.calls[0]["module"] == "email"
    assert _count(p.admin_engine, two_orgs["b"]) == 0


async def test_a_text_the_door_refuses_stays_pending_and_the_run_stops(two_orgs, door) -> None:
    p, mod = two_orgs["p"], _script()
    door.refuse = {"Subject 1\n"}  # exactly one message of org A
    got = await mod._run([p.org_a], 64, 0)
    assert got["rows"] == 4 and got["failures"] >= 1
    assert _count(p.admin_engine, two_orgs["a"]) == 4
    assert len(door.calls) <= 2 + mod.MAX_FAILED_IN_A_ROW, "the run did not stop"
