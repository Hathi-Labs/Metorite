"""R8 — a reconnect never freezes the mirror (WS-53 CRM-Q10, ``crm_platform.md`` §18).

The incident of 2026-10-12 (Fracktal, Mirror mode): a reconnect to the SAME
Zoho account dropped the source profile. With no profile the run budget fell
to the 500-credit floor. The org had spent about 950 credits in 24 hours, so
each run stopped at once with ``budget`` and 0 credits. Discovery, the one step
that restores the profile, could not run, and the mirror froze.

What this proves against a REAL Postgres, as the NON-privileged role of the
phase-4 rehearsal:

- a consent that the callback confirms as the same account keeps the profile
  and marks it stale, so the next run discovers again with the new scopes;
- a consent that the callback cannot confirm still drops the profile;
- with no profile and a spent default budget, an incremental run and a
  reconcile run each discover, store the profile, use the edition budget, and
  end ``ok`` (red on the code before CRM-Q10: ``stopped`` with ``budget``);
- the allowance pays for discovery only: an account whose edition stays
  unknown stops after discovery, with the profile stored;
- ``GET /crm/sync/health`` names when a spent window resumes.
"""

# ruff: noqa: F811  (the fixtures are imported)

from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta
from typing import Any

import pytest

pytest.importorskip("sqlalchemy")

from sqlalchemy import text

from tests.unit.test_crm_pipelines_r8 import (  # noqa: F401  (q and ns are fixtures)
    _admin,
    _deal,
    _req,
    _sync,
    ns,
    q,
)
from tests.unit.test_crm_reset_r8 import (  # noqa: F401  (x0 is a fixture)
    OrgZoho,
    _connect,
    _counts,
    _seed,
    x0,
)
from tests.unit.test_crm_sync_engine_r8 import _assert_non_priv, _q, seed_basic
from tests.unit.test_h3_rls_promotion_rehearsal import (  # noqa: F401
    _DB_GATE,
    app_engine,
    promoted,
)

# ⚠️ Every test here needs the real database. Without this gate the suite
# ERRORS where no ladder URL is set (the deploy run), and the deploy stops.
pytestmark = _DB_GATE

#: The credits that the org had spent in the 24 hours before the freeze.
SPENT = 950
#: Professional with 12 licences: 10 % of (50,000 + 12 x 500).
EDITION_BUDGET = 5_600


def _profile_rows(p: Any, org: str) -> list[Any]:
    return _q(p, "SELECT version, fetched_at FROM crm_source_profiles "
                 "WHERE organization_id = CAST(:o AS uuid) ORDER BY version", o=org)


async def _reconsent(org: str, z: Any, *, keep_profile: bool) -> None:
    """A second consent for the same connection, through the store."""
    from acb_common.db import tenant_session
    from gateway.connections import store
    from gateway.crm_sources.base import SourceCredential

    credential = SourceCredential(
        access_token=f"ACCESS-{z.tag}", refresh_token=f"REFRESH2-{z.tag}",
        expires_at=datetime.now(UTC) + timedelta(hours=1), scopes=(),
        provider_meta={
            "api_domain": f"https://{z.api_host}",
            "accounts_server": f"https://{z.accounts_host}",
            "location": "in",
        },
    )
    async with tenant_session(org) as db:
        await store.save_credential(
            db, org, "zoho", "crm", credential, connected_by="admin@crm-sync.test",
            scopes=("ZohoCRM.modules.leads.READ",), keep_profile=keep_profile)


def _spend_the_window(p: Any, org: str, *, gap: bool = False) -> None:
    """The prod state of 2026-10-12: no profile, and 950 credits spent in 24 hours.

    The earlier runs leave the window. With ``gap`` they also end more than 50
    days ago, so the next run is a reconcile (``engine.GAP_RECONCILE``).
    """
    age = "51 days" if gap else "2 days"
    with p.admin_engine.begin() as c:
        c.execute(text("DELETE FROM crm_source_profiles "
                       "WHERE organization_id = CAST(:o AS uuid)"), {"o": org})
        c.execute(text(
            f"UPDATE crm_sync_runs SET started_at = now() - interval '{age}', "
            f"finished_at = now() - interval '{age}' "
            "WHERE organization_id = CAST(:o AS uuid)"), {"o": org})
        conn = c.execute(text(
            "SELECT id FROM integration_connections "
            "WHERE organization_id = CAST(:o AS uuid)"), {"o": org}).scalar()
        c.execute(text(
            "INSERT INTO crm_sync_runs (organization_id, connection_id, kind, status, "
            "started_at, finished_at, credits_used, error) VALUES (CAST(:o AS uuid), "
            ":c, 'incremental', 'stopped', now() - interval '1 hour', "
            "now() - interval '1 hour', :n, 'shutdown')"),
            {"o": org, "c": conn, "n": SPENT})


# ── 1. The consent keeps the profile of the same account ─────────────────────


async def test_a_kept_profile_is_stale_and_the_next_run_discovers_again(q, app_engine) -> None:
    from gateway.crm_sync import engine

    _assert_non_priv(app_engine)
    _deal(q.a, "d1", 1, Pipeline="Standard")
    await _sync(q, q.org_a, q.a)
    assert [r[0] for r in _profile_rows(q.p, q.org_a)] == [1]
    assert q.a.org_reads == 1

    await _reconsent(q.org_a, q.a, keep_profile=True)
    rows = _profile_rows(q.p, q.org_a)
    assert [r[0] for r in rows] == [1], "the same account keeps its profile"
    assert datetime.now(UTC) - rows[0][1] > engine.DISCOVERY_EVERY, "and it is stale"

    # The next run starts on the edition budget and discovers again.
    result = await engine.run_org(q.org_a)
    assert result.status == "ok", result
    assert q.a.org_reads == 2
    assert q.found[-1].read and q.found[-1].cost == 11
    assert datetime.now(UTC) - _profile_rows(q.p, q.org_a)[-1][1] < timedelta(minutes=5)


async def test_a_consent_that_is_not_confirmed_still_drops_the_profile(q, app_engine) -> None:
    _assert_non_priv(app_engine)
    await _sync(q, q.org_a, q.a)
    assert len(_profile_rows(q.p, q.org_a)) == 1
    await _reconsent(q.org_a, q.a, keep_profile=False)
    assert _profile_rows(q.p, q.org_a) == []


async def test_the_callback_keeps_the_profile_of_the_same_account(x0, app_engine) -> None:
    """The real callback: the stored profile names Zoho org Z-A, and so does the
    new token. The profile stays, and it is stale."""
    from gateway.crm_sync import engine

    _assert_non_priv(app_engine)
    _seed(x0, x0.org_a, tag="A", deals=4)
    r = await _connect(x0, OrgZoho("A2", "Z-A"))
    assert r.headers["location"] == "/crm?connected=zoho", r.text
    assert _counts(x0, x0.org_a)["crm_source_profiles"] == 1
    (fetched,) = [row[0] for row in _q(
        x0, "SELECT fetched_at FROM crm_source_profiles "
            "WHERE organization_id = CAST(:o AS uuid)", o=x0.org_a)]
    assert datetime.now(UTC) - fetched > engine.DISCOVERY_EVERY


async def test_the_callback_drops_a_profile_it_cannot_tie_to_the_account(x0, app_engine) -> None:
    """No stored account id, no identity in the profile, no Zoho record: the
    callback cannot confirm the account, so the profile goes, as before."""
    _assert_non_priv(app_engine)
    with x0.admin_engine.begin() as c:
        conn = c.execute(text(
            "INSERT INTO integration_connections (organization_id, provider, "
            "owning_app, status) VALUES (CAST(:o AS uuid), 'zoho', 'crm', "
            "'disconnected') RETURNING id"), {"o": x0.org_a}).scalar()
        c.execute(text(
            "INSERT INTO crm_source_profiles (organization_id, connection_id, "
            "version, profile, profile_hash) VALUES (CAST(:o AS uuid), :c, 1, "
            "CAST(:p AS jsonb), 'h')"),
            {"o": x0.org_a, "c": conn, "p": json.dumps({"org": {}})})
    r = await _connect(x0, OrgZoho("A", "Z-NEW"))
    assert r.headers["location"] == "/crm?connected=zoho", r.text
    assert _counts(x0, x0.org_a)["crm_source_profiles"] == 0


# ── 2. With no profile, discovery still runs ─────────────────────────────────


@pytest.mark.parametrize("kind", ["incremental", "reconcile"])
async def test_no_profile_and_a_spent_default_budget_still_discovers(
    q, app_engine, kind,
) -> None:
    from acb_common.db import tenant_session
    from gateway.crm_sync import engine, profiles

    _assert_non_priv(app_engine)
    seed_basic(q.a, "A")
    await _sync(q, q.org_a, q.a)
    _spend_the_window(q.p, q.org_a, gap=kind == "reconcile")
    async with tenant_session(q.org_a) as db:
        assert await profiles.budget_for(db, q.org_a) is None
    reads = q.a.org_reads

    result = await engine.run_org(q.org_a)

    # Before CRM-Q10 this run stopped at once: ("stopped", "budget", 0 credits).
    assert (result.kind, result.status, result.error) == (kind, "ok", None), result
    assert q.a.org_reads == reads + 1
    discovered = [f for f in q.found if f.read][-1]
    assert discovered.cost == 11
    # Right after discovery, the budget is the edition's share minus the window.
    assert discovered.budget == EDITION_BUDGET - SPENT
    assert result.credits_used > discovered.cost, "the run went on past discovery"
    rows = _q(q.p, "SELECT profile->'org'->>'edition' FROM crm_source_profiles "
                   "WHERE organization_id = CAST(:o AS uuid)", o=q.org_a)
    assert [r[0] for r in rows] == ["professional"]
    async with tenant_session(q.org_a) as db:
        assert await profiles.budget_for(db, q.org_a) == EDITION_BUDGET


async def test_the_allowance_pays_for_discovery_only(q, app_engine) -> None:
    """An account whose edition stays unknown keeps the floor. The run stores the
    profile, then stops on ``budget``. It does not read the records."""
    from gateway.crm_sync import engine

    _assert_non_priv(app_engine)
    seed_basic(q.a, "A")
    await _sync(q, q.org_a, q.a)
    _spend_the_window(q.p, q.org_a)
    q.a.org_error = True
    result = await engine.run_org(q.org_a)
    assert (result.status, result.error) == ("stopped", "budget"), result
    assert 0 < result.credits_used <= engine.DISCOVERY_ALLOWANCE
    rows = _q(q.p, "SELECT profile->'unreadable' FROM crm_source_profiles "
                   "WHERE organization_id = CAST(:o AS uuid)", o=q.org_a)
    assert [r[0] for r in rows] == [["org"]]


# ── 3. Sync health names the pause ───────────────────────────────────────────


async def test_the_health_route_names_when_a_spent_budget_resumes(q, app_engine) -> None:
    _assert_non_priv(app_engine)
    await _sync(q, q.org_a, q.a)
    with q.p.admin_engine.begin() as c:
        c.execute(text("DELETE FROM crm_source_profiles "
                       "WHERE organization_id = CAST(:o AS uuid)"), {"o": q.org_a})
        c.execute(text("DELETE FROM crm_sync_runs "
                       "WHERE organization_id = CAST(:o AS uuid)"), {"o": q.org_a})
        for hours, credits, error in ((23, 400, None), (20, 300, None), (1, 250, "budget")):
            c.execute(text(
                "INSERT INTO crm_sync_runs (organization_id, kind, status, started_at, "
                "finished_at, credits_used, error) VALUES (CAST(:o AS uuid), "
                "'incremental', :s, now() - make_interval(hours => :h), "
                "now() - make_interval(hours => :h), :n, :e)"),
                {"o": q.org_a, "h": hours, "n": credits, "e": error,
                 "s": "stopped" if error else "ok"})
    body = (await _req(_admin(q.org_a), "GET", "/crm/sync/health")).json()
    assert body["credits"]["budget_24h"] == 500 and body["credits"]["used_24h"] == 950
    # 950 - 400 = 550 is still over 500. Without the run of 20 hours ago, 250
    # is under it. So the sync resumes 24 hours after that run started.
    resumes = datetime.fromisoformat(body["credits"]["resumes_at"])
    expected = datetime.now(UTC) + timedelta(hours=4)
    assert abs((resumes - expected).total_seconds()) < 120

    with q.p.admin_engine.begin() as c:
        c.execute(text("UPDATE crm_sync_runs SET credits_used = 10 "
                       "WHERE organization_id = CAST(:o AS uuid)"), {"o": q.org_a})
    body = (await _req(_admin(q.org_a), "GET", "/crm/sync/health")).json()
    assert body["credits"]["resumes_at"] is None
