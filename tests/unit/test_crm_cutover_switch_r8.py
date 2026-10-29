"""R8 — the switch from the Zoho mirror to Native (WS-53 CRM-Z10b).

Spec: ``project-docs/specs/crm_platform.md`` §3.4 and §3.4.1. Decisions: D133
(and its two amendments) and D137. The route module is
``gateway/routes/crm/cutover.py``.

What this proves against a REAL Postgres, through the real ``/crm`` router, as
the NON-privileged role of the phase-4 rehearsal. Each test names the item of
the audit's acceptance list that it holds (1 to 21):

- the switch keeps every record and every ``(ext_source, ext_id)``, makes a
  Native org, closes Zoho with one revoke, and never seeds the Native
  pipeline (1, 2, 13);
- the source's names serve in Native mode with no fill-rank flag (3);
- THE FOLD keeps the board order and the hue of each column, for the five
  cases of ``tests/fixtures/crm_cutover_board_order.json`` (4, 6);
- the watermark rewind, and its race with an auto-lead cycle (7, 8);
- a second call, an open run, the refusals and a rollback write nothing
  (9, 10, 11, 12, 19);
- phase 1 always starts its own run, and phase 2 takes only that run as the
  final sync: a one-module run or a run that ended after it fails closed
  (review P0);
- two closes of Zoho at one time revoke once and write one line (F2);
- a run that raced the commit stops with ``mode_changed`` (14);
- a module that is not complete blocks unless the admin leaves it out (15);
- a revoke that Zoho does not confirm still switches (16);
- two calls at one time switch once (17);
- an overlay edit that holds the mode is folded, and Native refuses a
  ``ui_*`` write after it (18);
- a Mirror PATCH of a Zoho record is 409 before and 200 after (20);
- the start over still works after the switch, and clears the stamps (21).

A hermetic fake agrees with any SQL it is handed (``FOR SHARE``, a CTE with a
window function, the RLS policy), so none of this is proved without the
database.
"""

from __future__ import annotations

import asyncio
import json
from pathlib import Path
from typing import Any

import pytest

pytest.importorskip("sqlalchemy")

from sqlalchemy import text

from tests.unit._tenant_ladder import tenant_engine_scope
from tests.unit.test_crm_access_r8 import (
    _admin,
    _app_dsn,
    _assert_non_priv,
    _call,
    _member,
)
from tests.unit.test_crm_cutover_preview_r8 import _hashes
from tests.unit.test_crm_reset_r8 import (  # noqa: F401
    OrgZoho,
    _connect,
    _q,
    _seed,
    x0,
)
from tests.unit.test_h3_rls_promotion_rehearsal import (  # noqa: F401
    _DB_GATE,
    app_engine,
    promoted,
)

# ⚠️ Every test here needs the real database. Without this gate the suite
# ERRORS where no ladder URL is set (the deploy run), and the deploy stops.
pytestmark = _DB_GATE

FIXTURE = Path(__file__).resolve().parents[1] / "fixtures" / "crm_cutover_board_order.json"
CASES: list[dict[str, Any]] = json.loads(FIXTURE.read_text(encoding="utf-8"))["cases"]

SWITCH = {"confirm": "SWITCH"}
DEALS = 40

#: The core cursors of a finished import (``engine.ENTITY_ORDER`` less the
#: optional ``task``).
_CORE_CURSORS = ("company", "contact", "lead", "deal", "note", "call", "meeting")

#: The record tables. The switch writes none of them.
_RECORD_TABLES = (
    "crm_leads", "crm_deals", "crm_contacts", "crm_companies", "crm_activities",
    "crm_module_records",
)

_STRANGER_BEFORE = "early-prospect@prospect-z10b.test"
_STRANGER_AFTER = "late-prospect@prospect-z10b.test"


@pytest.fixture
def zb(x0, monkeypatch):  # noqa: F811 — a fixture
    """``x0`` (switches on, one fake Zoho, audits caught, cleaned after), the
    cutover list on, the fill-rank list OFF, and the final run recorded."""
    from acb_common import get_settings
    from gateway.routes.crm import cutover

    settings = get_settings()
    monkeypatch.setattr(settings, "crm_cutover_orgs", "*", raising=False)
    # The Native names must serve with no fill-rank flag (item 3).
    monkeypatch.setattr(settings, "crm_fill_rank_orgs", "", raising=False)
    handles: list[Any] = []
    monkeypatch.setattr(cutover, "_start_final_run", handles.append)
    x0.final_runs = handles
    return x0


# ── Helpers ──────────────────────────────────────────────────────────────────


def _one(p: Any, sql: str, **params: Any) -> Any:
    rows = _q(p, sql, **params)
    return rows[0] if rows else None


def _exec(p: Any, sql: str, **params: Any) -> None:
    with p.admin_engine.begin() as c:
        c.execute(text(sql), params)


def _write(p: Any, sql: str, **params: Any) -> Any:
    """One write that returns a row, committed."""
    with p.admin_engine.begin() as c:
        return c.execute(text(sql), params).first()


def _done_cursors(p: Any, org: str) -> None:
    with p.admin_engine.begin() as c:
        for entity in _CORE_CURSORS:
            c.execute(text(
                "INSERT INTO crm_sync_cursors (organization_id, module, "
                "initial_done_at) VALUES (CAST(:o AS uuid), :m, now()) "
                "ON CONFLICT (organization_id, module) DO NOTHING"),
                {"o": org, "m": f"zoho:{entity}"})


def _final_run(p: Any, org: str, *, start_min: int = 5, end_min: int | None = 4,
               status: str = "ok", counts: dict[str, Any] | None = None) -> str:
    """One run of *org*, the newest. ``end_min`` None leaves it open."""
    with p.admin_engine.begin() as c:
        conn = c.execute(text(
            "SELECT id FROM integration_connections WHERE organization_id = "
            "CAST(:o AS uuid) LIMIT 1"), {"o": org}).scalar()
        return str(c.execute(text(
            "INSERT INTO crm_sync_runs (organization_id, connection_id, kind, "
            "status, started_at, finished_at, heartbeat_at, counts) VALUES "
            "(CAST(:o AS uuid), :c, 'incremental', :s, "
            "now() - make_interval(mins => :a), "
            "CASE WHEN CAST(:open AS boolean) THEN NULL "
            "ELSE now() - make_interval(mins => :b) END, now(), "
            "CAST(:k AS jsonb)) RETURNING id"),
            {"o": org, "c": conn, "s": status, "a": start_min,
             "b": end_min or 0, "open": end_min is None,
             "k": json.dumps(counts or {})}).scalar())


async def _ready(p: Any, *, deals: int = DEALS, final: bool = True) -> None:
    """Org A: a Mirror org with records, a live Zoho token, a finished
    import and, with *final*, a good final run 4 minutes old. Its id is
    ``p.final_run_id``, as if phase 1 had started it."""
    _seed(p, p.org_a, tag="A", deals=deals)
    connected = await _connect(p, OrgZoho("A1", "Z-A"))
    assert connected.status_code == 302, connected.text
    _done_cursors(p, p.org_a)
    p.final_run_id = _final_run(p, p.org_a) if final else None
    p.active["zoho"].requests.clear()


def _body(p: Any, **extra: Any) -> dict[str, Any]:
    """The body of phase 2: the word, and the run that phase 1 started."""
    return {**SWITCH, "run_id": getattr(p, "final_run_id", None), **extra}


async def _switch(p: Any, body: dict[str, Any] | None = None, user: Any = None) -> Any:
    """Phase 2. A body with no ``run_id`` gets the run of phase 1."""
    sent = _body(p) if body is None else {"run_id": getattr(p, "final_run_id", None), **body}
    return await _call(p, user or _admin(p.org_a), "POST", "/crm/cutover", sent)


def _settings(p: Any, org: str) -> Any:
    return _one(p, "SELECT mode, connection_id, cutover_at, cutover_by, labels, "
                   "access FROM crm_settings WHERE organization_id = CAST(:o AS uuid)",
                o=org)


def _connection(p: Any, org: str) -> Any:
    return _one(p, "SELECT status, credentials_encrypted FROM integration_connections "
                   "WHERE organization_id = CAST(:o AS uuid)", o=org)


def _records(p: Any, org: str) -> tuple[dict[str, int], list[tuple[Any, ...]]]:
    """The count of each record table, and every ``(ext_source, ext_id)``."""
    counts: dict[str, int] = {}
    keys: list[tuple[Any, ...]] = []
    for table in _RECORD_TABLES:
        rows = _q(p, f"SELECT ext_source, ext_id FROM {table} "
                     "WHERE organization_id = CAST(:o AS uuid)", o=org)
        counts[table] = len(rows)
        keys += [(table, r[0], r[1]) for r in rows]
    links = _q(p, "SELECT deal_id, contact_id, ext_source FROM crm_deal_contacts "
                  "WHERE organization_id = CAST(:o AS uuid)", o=org)
    counts["crm_deal_contacts"] = len(links)
    keys += [("crm_deal_contacts", *map(str, r)) for r in links]
    return counts, sorted(keys, key=repr)


def _setup_counts(p: Any, org: str) -> dict[str, int]:
    return {
        t: int(_one(p, f"SELECT count(*) FROM {t} WHERE organization_id = "
                       "CAST(:o AS uuid)", o=org)[0])
        for t in ("crm_pipelines", "crm_pipeline_stages", "crm_deal_statuses",
                  "crm_lead_statuses", "crm_lost_reasons")
    }


def _revokes(p: Any) -> list[Any]:
    return [r for r in p.active["zoho"].requests
            if r.url.path.endswith("/token/revoke")]


def _actions(p: Any, action: str) -> list[Any]:
    return [a for a in p.audits if getattr(a, "action", None) == action]


async def _request(p: Any, user: Any, method: str, path: str,
                   body: dict[str, Any] | None = None) -> Any:
    """``_call`` with no engine scope of its own: the caller holds ONE scope
    for two actors that run at once (the scope is process-wide)."""
    import httpx
    from acb_auth import get_current_user
    from acb_common.db import bind_tenant
    from fastapi import FastAPI
    from gateway.routes import crm as crm_package

    async def _caller() -> Any:
        bind_tenant(user.organization_id)
        return user

    app = FastAPI()
    app.include_router(crm_package.router)
    app.dependency_overrides[get_current_user] = _caller
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://crm.test",
    ) as client:
        return await client.request(method, path, json=body)


async def _waits_on_a_lock(p: Any, seconds: float = 15.0) -> bool:
    """True once a session of this database waits on a lock."""
    for _ in range(int(seconds / 0.1)):
        await asyncio.sleep(0.1)
        with p.admin_engine.connect() as c:
            waiting = c.execute(text(
                "SELECT count(*) FROM pg_stat_activity WHERE datname = "
                "current_database() AND wait_event_type = 'Lock'")).scalar()
        if waiting:
            return True
    return False


def _seed_case(p: Any, case: dict[str, Any], *, settings: bool = True) -> dict[str, str]:
    """A Mirror org A with the rows and pipelines of one fixture case.

    Returns the real id of each fixture id."""
    o = {"o": p.org_a}
    table = "crm_deal_statuses" if case["kind"] == "deal" else "crm_lead_statuses"
    ids: dict[str, str] = {}
    with p.admin_engine.begin() as c:
        if settings:
            conn = c.execute(text(
                "INSERT INTO integration_connections (organization_id, provider, "
                "owning_app, status) VALUES (CAST(:o AS uuid), 'zoho', 'crm', "
                "'disconnected') RETURNING id"), o).scalar()
            c.execute(text(
                "INSERT INTO crm_settings (organization_id, mode, connection_id, "
                "access) VALUES (CAST(:o AS uuid), 'mirror', :c, 'everyone')"),
                {**o, "c": conn})
        for row in case["rows"]:
            ids[row["id"]] = str(c.execute(text(
                f"INSERT INTO {table} (organization_id, name, type, position, "
                "probability, color, hidden, ui_position, ui_color, "
                "ui_probability, source_gone_at) VALUES (CAST(:o AS uuid), :name, "
                ":type, :position, :probability, 'gray', :hidden, :ui_position, "
                ":ui_color, :ui_probability, "
                "CASE WHEN CAST(:gone AS boolean) THEN now() END) RETURNING id"),
                {**o, "name": row["name"], "type": row["type"],
                 "position": row["position"], "probability": row["probability"],
                 "hidden": bool(row.get("hidden")),
                 "ui_position": row.get("ui_position"),
                 "ui_color": row.get("ui_color"),
                 "ui_probability": row.get("ui_probability"),
                 "gone": bool(row.get("source_gone"))}).scalar())
        for pipe in case["pipelines"]:
            pid = c.execute(text(
                "INSERT INTO crm_pipelines (organization_id, name, position, "
                "is_default, ext_source, ext_id) VALUES (CAST(:o AS uuid), :n, "
                ":pos, :d, 'zoho', :e) RETURNING id"),
                {**o, "n": pipe["name"], "pos": pipe["position"],
                 "d": pipe["is_default"], "e": pipe["id"]}).scalar()
            for place, sid in enumerate(pipe["stages"]):
                c.execute(text(
                    "INSERT INTO crm_pipeline_stages (organization_id, pipeline_id, "
                    "status_id, position) VALUES (CAST(:o AS uuid), :p, "
                    "CAST(:s AS uuid), :pl)"),
                    {**o, "p": pid, "s": ids[sid], "pl": place})
    if settings:
        _done_cursors(p, p.org_a)
        p.final_run_id = _final_run(p, p.org_a)
    return ids


def _native_rows(p: Any, table: str) -> list[Any]:
    return _q(p, f"SELECT id::text AS id, position, color, probability, ui_position, "
                 f"ui_color, ui_probability, hidden, source_gone_at FROM {table} "
                 "WHERE organization_id = CAST(:o AS uuid) ORDER BY position, name",
              o=p.org_a)


# ── Items 1, 2 and 13: the records stay, the org is Native, Zoho closes ─────


async def test_the_switch_keeps_every_record_and_closes_zoho(zb, app_engine) -> None:  # noqa: F811
    _assert_non_priv(app_engine)
    await _ready(zb)
    _seed(zb, zb.org_b, tag="B", deals=3, zoho_org="Z-B")
    counts, keys = _records(zb, zb.org_a)
    setup = _setup_counts(zb, zb.org_a)
    org_b = _records(zb, zb.org_b)
    access = _settings(zb, zb.org_a).access
    member = _member("m@crm-t5-r8.test", zb.org_a)
    # F3: a Mirror CRM's mode body has no cutover fact.
    assert "cutover_done" not in (await _call(zb, member, "GET", "/crm/mode")).json()

    r = await _switch(zb)
    assert r.status_code == 200, r.text
    assert r.json() == {"status": "switched", "revoked": "confirmed",
                        "zoho_connection": "disconnected"}
    # F3: the mode body says the switch is done, so the card may read the
    # status. A Native CRM with no stamp leaves it out
    # (``test_crm_aggregate_r8.py`` pins ``{"mode": "native"}``).
    mode = (await _call(zb, member, "GET", "/crm/mode")).json()
    assert (mode["mode"], mode.get("cutover_done")) == ("native", True)

    # Item 1: every record, link and source key, as before.
    assert _records(zb, zb.org_a) == (counts, keys)
    assert counts["crm_deals"] == DEALS and counts["crm_deal_contacts"] == 1
    assert _records(zb, zb.org_b) == org_b
    # Item 2: a Native org, its connection closed and its blob gone.
    row = _settings(zb, zb.org_a)
    assert row.mode == "native" and row.connection_id is None
    assert row.cutover_at is not None and row.cutover_by == "admin@crm-t5-r8.test"
    assert row.access == access
    conn = _connection(zb, zb.org_a)
    assert conn.status == "disconnected" and conn.credentials_encrypted is None
    assert len(_revokes(zb)) == 1
    # Org B stays a mirror.
    assert _settings(zb, zb.org_b).mode == "mirror"
    # Item 13: no Native seed. The pipelines, stages and lost reasons stay.
    assert _setup_counts(zb, zb.org_a) == setup
    # One audit line for the switch, one for the disconnect.
    (line,) = _actions(zb, "crm.cutover")
    assert line.payload["counts"]["deals"] == DEALS
    assert line.payload["leave_out"] == [] and line.payload["t0"]
    (closed,) = _actions(zb, "crm.cutover.disconnect")
    assert closed.payload["revoked"] == "confirmed"
    # The status read says done.
    status = await _call(zb, _admin(zb.org_a), "GET", "/crm/cutover/status")
    assert status.status_code == 200, status.text
    assert status.json()["state"] == "done"
    assert status.json()["zoho_connection"] == "disconnected"


# ── Item 3: the names of the profile serve in Native mode ───────────────────


async def test_the_source_names_serve_in_native_with_no_fill_rank_flag(zb, app_engine) -> None:  # noqa: F811
    _assert_non_priv(app_engine)
    await _ready(zb)
    _exec(zb, "UPDATE crm_source_profiles SET profile = profile || CAST(:m AS jsonb) "
              "WHERE organization_id = CAST(:o AS uuid)",
          o=zb.org_a, m=json.dumps({"modules": [
              {"api_name": "Contacts", "label": "Existing Sites",
               "singular_label": "Existing Site"},
              {"api_name": "Deals", "label": "Projects", "singular_label": "Project"},
          ]}))
    r = await _switch(zb)
    assert r.status_code == 200, r.text
    stored = _settings(zb, zb.org_a).labels
    assert stored["contacts"] == {"label": "Existing Sites", "singular_label": "Existing Site"}
    mode = await _call(zb, _member("m@crm-t5-r8.test", zb.org_a), "GET", "/crm/mode")
    assert mode.status_code == 200, mode.text
    body = mode.json()
    assert body["mode"] == "native"
    assert body["labels"]["contacts"]["label"] == "Existing Sites"
    assert body["labels"]["deals"]["label"] == "Projects"


async def test_no_profile_label_stores_no_names(zb, app_engine) -> None:  # noqa: F811
    _assert_non_priv(app_engine)
    await _ready(zb)  # the seeded profile holds only the org section
    assert (await _switch(zb)).status_code == 200
    assert _settings(zb, zb.org_a).labels is None


# ── Items 4 and 6: THE FOLD keeps the board ─────────────────────────────────


@pytest.mark.parametrize("case", CASES, ids=[c["name"] for c in CASES])
async def test_the_fold_keeps_the_board_order_and_the_hues(zb, app_engine, case) -> None:  # noqa: F811
    _assert_non_priv(app_engine)
    ids = _seed_case(zb, case)
    names = {real: fid for fid, real in ids.items()}
    table = "crm_deal_statuses" if case["kind"] == "deal" else "crm_lead_statuses"
    preview = await _call(zb, _admin(zb.org_a), "GET", "/crm/cutover/preview")
    assert preview.status_code == 200, preview.text
    assert preview.json()["pipelines_in_another_order"] == case["other_order"]
    assert preview.json()["can_switch"] is True

    r = await _switch(zb)
    assert r.status_code == 200, r.text
    rows = _native_rows(zb, table)
    # Native order (position, then name) is the Mirror order of the fixture.
    assert [names[row.id] for row in rows] == case["mirror_order"]
    for row in rows:
        want = case["after"][names[row.id]]
        assert (row.position, row.color, row.probability) == (
            want["position"], want["color"], want["probability"]), names[row.id]
        assert (row.ui_position, row.ui_color, row.ui_probability) == (None, None, None)
    # The fold keeps the retired marks.
    by_id = {fid: row for fid, row in ((names[r_.id], r_) for r_ in rows)}
    for fixture_row in case["rows"]:
        kept = by_id[fixture_row["id"]]
        assert kept.hidden is bool(fixture_row.get("hidden"))
        assert (kept.source_gone_at is not None) is bool(fixture_row.get("source_gone"))
    # The pipelines keep their own stage order.
    for pipe in case["pipelines"]:
        listed = _q(zb, "SELECT ps.status_id::text FROM crm_pipeline_stages ps JOIN "
                        "crm_pipelines p ON p.id = ps.pipeline_id WHERE p.ext_id = :e "
                        "AND p.organization_id = CAST(:o AS uuid) ORDER BY ps.position",
                    e=pipe["id"], o=zb.org_a)
        assert [names[r_[0]] for r_ in listed] == pipe["stages"]


async def test_the_fold_moves_probability_and_keeps_each_effective_value(zb, app_engine) -> None:  # noqa: F811
    """Item 6: through the read API, before and after, on both tables."""
    _assert_non_priv(app_engine)
    deals = next(c for c in CASES if c["kind"] == "deal" and any(
        r.get("ui_probability") is not None for r in c["rows"]))
    leads = next(c for c in CASES if c["kind"] == "lead")
    _seed_case(zb, deals)
    _seed_case(zb, leads, settings=False)
    admin = _admin(zb.org_a)
    before = {k: (await _call(zb, admin, "GET", f"/crm/statuses/{k}")).json()
              for k in ("deal", "lead")}
    assert (await _switch(zb)).status_code == 200
    after = {k: (await _call(zb, admin, "GET", f"/crm/statuses/{k}")).json()
             for k in ("deal", "lead")}
    for kind in ("deal", "lead"):
        old = {s["id"]: s for s in before[kind]}
        new = {s["id"]: s for s in after[kind]}
        assert set(old) == set(new)
        for sid, s in new.items():
            assert s["probability"] == old[sid]["probability"], (kind, s["name"])
            assert s["ui_position"] is None and s["ui_color"] is None
            assert s["ui_probability"] is None
    for table in ("crm_deal_statuses", "crm_lead_statuses"):
        left = _one(zb, f"SELECT count(*) FROM {table} WHERE organization_id = "
                        "CAST(:o AS uuid) AND (ui_position IS NOT NULL OR ui_color "
                        "IS NOT NULL OR ui_probability IS NOT NULL)", o=zb.org_a)[0]
        assert left == 0, table


# ── Items 7 and 8: the watermark of auto-lead ───────────────────────────────


def _mailbox(p: Any, org: str) -> str:
    return str(_one(p, "SELECT id FROM email_accounts WHERE organization_id = "
                       "CAST(:o AS uuid) LIMIT 1", o=org)[0])


def _inbound(p: Any, org: str, account: str, sender: str, processed_min: float) -> None:
    _exec(p, "INSERT INTO email_messages (account_id, provider_message_id, folder, "
             "subject, from_address, to_addresses, received_at, rules_processed_at, "
             "organization_id) VALUES (CAST(:a AS uuid), :pm, 'inbox', 'Quote', "
             "CAST(:f AS jsonb), '[]'::jsonb, now() - interval '30 minutes', "
             "now() - make_interval(secs => :s), CAST(:o AS uuid))",
          a=account, pm=f"pm-{sender}-{processed_min}", o=org,
          f=json.dumps({"email": sender, "name": "Pat Prospect"}), s=processed_min * 60)


def _watermark(p: Any, account: str) -> Any:
    return _one(p, "SELECT processed_watermark, last_run_at, activated_at FROM "
                   "crm_auto_lead_cursors WHERE account_id = CAST(:a AS uuid)", a=account)


def _t0(p: Any) -> Any:
    return _one(p, "SELECT started_at FROM crm_sync_runs WHERE organization_id = "
                   "CAST(:o AS uuid) ORDER BY started_at DESC LIMIT 1", o=p.org_a)[0]


async def _cycle(org: str, account: str) -> dict[str, int]:
    """One auto-lead cycle with the tenant bound, as the email hook does."""
    from acb_common.db import bind_tenant, release_tenant
    from gateway.routes.crm import auto_lead

    token = bind_tenant(org)
    try:
        return await auto_lead.create_leads_from_new_mail(account)
    finally:
        release_tenant(token)


async def test_the_rewind_makes_a_lead_for_mail_after_t0_only(zb, app_engine) -> None:  # noqa: F811
    """Item 7. A Mirror cycle stamped the watermark past both mails. The switch
    rewinds it to T0, so the next cycle makes a lead for the mail after T0
    and none for the mail before it."""
    _assert_non_priv(app_engine)
    await _ready(zb)
    _seed(zb, zb.org_b, tag="B", deals=1, zoho_org="Z-B")
    box = _mailbox(zb, zb.org_a)
    _exec(zb, "UPDATE crm_auto_lead_cursors SET activated_at = now() - interval "
              "'2 hours', processed_watermark = now(), last_run_at = now() "
              "WHERE account_id = CAST(:a AS uuid)", a=box)
    # A second mailbox whose cursor is already below T0, and org B's cursor.
    low = str(_write(zb, "INSERT INTO email_accounts (user_id, provider, email_address, "
                       "credentials_encrypted, sync_enabled, organization_id) VALUES "
                       "('low@crm-x0.test', 'microsoft', 'low@crm-x0.test', 'x', true, "
                       "CAST(:o AS uuid)) RETURNING id", o=zb.org_a)[0])
    _exec(zb, "INSERT INTO crm_auto_lead_cursors (account_id, activated_at, "
              "processed_watermark, last_run_at, organization_id) VALUES "
              "(CAST(:a AS uuid), now() - interval '2 hours', now() - interval "
              "'10 minutes', now(), CAST(:o AS uuid))", a=low, o=zb.org_a)
    low_before = _watermark(zb, low)
    other = _mailbox(zb, zb.org_b)
    other_before = _watermark(zb, other)
    _inbound(zb, zb.org_a, box, _STRANGER_BEFORE, 6)   # before T0 (5 min ago)
    _inbound(zb, zb.org_a, box, _STRANGER_AFTER, 4)    # after T0
    before = _watermark(zb, box)

    assert (await _switch(zb)).status_code == 200
    t0 = _t0(zb)
    after = _watermark(zb, box)
    assert after.processed_watermark == t0
    assert (after.last_run_at, after.activated_at) == (before.last_run_at, before.activated_at)
    assert _watermark(zb, low) == low_before
    assert _watermark(zb, other) == other_before

    async with tenant_engine_scope(_app_dsn(zb)):
        stats = await _cycle(zb.org_a, box)
    assert stats["created"] == 1, stats
    made = {r[0] for r in _q(zb, "SELECT lower(email) FROM crm_leads WHERE "
                                 "organization_id = CAST(:o AS uuid) AND email IS NOT NULL",
                             o=zb.org_a)}
    assert _STRANGER_AFTER in made and _STRANGER_BEFORE not in made


async def test_a_mirror_cycle_that_holds_the_mode_stamps_before_the_rewind(
    zb, app_engine, monkeypatch,  # noqa: F811
) -> None:
    """Item 8. A Mirror cycle read the mode FOR SHARE and has not stamped yet.
    The switch waits for it, its stamp lands first, and the rewind lands
    after it. Without the share lock the switch would commit first, and the
    cycle's stamp would skip the mail after T0."""
    _assert_non_priv(app_engine)
    from gateway.routes.crm import auto_lead

    await _ready(zb)
    box = _mailbox(zb, zb.org_a)
    _exec(zb, "UPDATE crm_auto_lead_cursors SET activated_at = now() - interval "
              "'2 hours', processed_watermark = now() - interval '10 minutes', "
              "last_run_at = now() WHERE account_id = CAST(:a AS uuid)", a=box)
    _inbound(zb, zb.org_a, box, _STRANGER_AFTER, 1)  # after T0
    reached, go = asyncio.Event(), asyncio.Event()
    real = auto_lead._load_candidates

    async def _paused(*args: Any, **kwargs: Any) -> Any:
        if not reached.is_set():
            reached.set()
            await go.wait()
        return await real(*args, **kwargs)

    monkeypatch.setattr(auto_lead, "_load_candidates", _paused)
    async with tenant_engine_scope(_app_dsn(zb)):
        cycle = asyncio.create_task(_cycle(zb.org_a, box))
        await asyncio.wait_for(reached.wait(), 30)
        switch = asyncio.create_task(_request(zb, _admin(zb.org_a), "POST",
                                              "/crm/cutover", _body(zb)))
        waited = await _waits_on_a_lock(zb)
        go.set()
        stats = await cycle
        r = await switch
    assert waited, "the switch did not wait for the cycle that holds the mode"
    assert stats["candidates"] == 1 and stats["created"] == 0
    assert r.status_code == 200, r.text
    assert _watermark(zb, box).processed_watermark == _t0(zb)


# ── Items 9 and 10: a second call, and an open run ──────────────────────────


async def test_a_second_call_changes_nothing(zb, app_engine) -> None:  # noqa: F811
    _assert_non_priv(app_engine)
    await _ready(zb)
    assert (await _switch(zb)).status_code == 200
    before, audits = _hashes(zb), len(zb.audits)
    r = await _switch(zb)
    assert r.status_code == 200, r.text
    assert r.json() == {"status": "already_done", "revoked": None,
                        "zoho_connection": "disconnected"}
    assert _hashes(zb) == before
    assert len(zb.audits) == audits


async def test_a_failed_close_of_zoho_completes_on_the_second_call(
    zb, app_engine, monkeypatch,  # noqa: F811
) -> None:
    """Item 9, second half, and item 14, first half: a failure in phase 3
    leaves a Native org and a live token. The sync refuses it for the mode,
    and the next call closes Zoho."""
    _assert_non_priv(app_engine)
    from gateway.crm_sync import engine
    from gateway.routes.crm import cutover

    await _ready(zb)
    real = cutover.disconnect_zoho_reporting

    async def _broken(org: str) -> Any:
        raise RuntimeError("the revoke broke")

    monkeypatch.setattr(cutover, "disconnect_zoho_reporting", _broken)
    first = await _switch(zb)
    assert first.status_code == 200, first.text
    assert first.json() == {"status": "switched", "revoked": None,
                            "zoho_connection": "connected"}
    assert _settings(zb, zb.org_a).mode == "native"
    assert _connection(zb, zb.org_a).status == "connected"
    status = await _call(zb, _admin(zb.org_a), "GET", "/crm/cutover/status")
    assert status.json()["state"] == "done"
    assert status.json()["zoho_connection"] == "connected"
    # Item 14: the token is live, and the sync still refuses the org.
    async with tenant_engine_scope(_app_dsn(zb)):
        with pytest.raises(engine.NotConnected) as refused:
            await engine.acquire(zb.org_a)
    assert refused.value.args == ("mode",)

    monkeypatch.setattr(cutover, "disconnect_zoho_reporting", real)
    second = await _switch(zb)
    assert second.status_code == 200, second.text
    assert second.json() == {"status": "already_done", "revoked": "confirmed",
                             "zoho_connection": "disconnected"}
    assert _connection(zb, zb.org_a).credentials_encrypted is None
    assert len(_actions(zb, "crm.cutover")) == 1
    assert len(_actions(zb, "crm.cutover.disconnect")) == 1


async def test_two_closes_of_zoho_at_one_time_revoke_once(
    zb, app_engine, monkeypatch,  # noqa: F811
) -> None:
    """F2 (verifier): phase 3 failed, so the token is live. Two admins press
    "Finish closing Zoho" at one time. The first holds the connect lock while
    Zoho answers the revoke. The second waits, then reads ``disconnected``.
    So Zoho gets one revoke, and one ``crm.cutover.disconnect`` line lands.
    Without the lock, both read ``connected`` and both revoke."""
    _assert_non_priv(app_engine)
    from gateway.routes.crm import connection, cutover

    await _ready(zb)
    real_close = cutover.disconnect_zoho_reporting

    async def _broken(org: str) -> Any:
        raise RuntimeError("the revoke broke")

    monkeypatch.setattr(cutover, "disconnect_zoho_reporting", _broken)
    assert (await _switch(zb)).json()["zoho_connection"] == "connected"
    monkeypatch.setattr(cutover, "disconnect_zoho_reporting", real_close)

    reached, go = asyncio.Event(), asyncio.Event()
    real_revoke = connection._best_effort_revoke

    async def _slow_revoke(*args: Any, **kwargs: Any) -> Any:
        if not reached.is_set():
            reached.set()
            await go.wait()
        return await real_revoke(*args, **kwargs)

    monkeypatch.setattr(connection, "_best_effort_revoke", _slow_revoke)
    admin = _admin(zb.org_a)
    async with tenant_engine_scope(_app_dsn(zb)):
        first = asyncio.create_task(_request(zb, admin, "POST", "/crm/cutover", SWITCH))
        await asyncio.wait_for(reached.wait(), 30)
        second = asyncio.create_task(_request(zb, admin, "POST", "/crm/cutover", SWITCH))
        waited = await _waits_on_a_lock(zb)
        go.set()
        answers = [await first, await second]
    assert waited, "the second close did not wait for the connect lock"
    assert [a.status_code for a in answers] == [200, 200], [a.text for a in answers]
    assert [a.json()["status"] for a in answers] == ["already_done", "already_done"]
    assert sorted(str(a.json()["revoked"]) for a in answers) == ["None", "confirmed"]
    assert len(_revokes(zb)) == 1
    assert len(_actions(zb, "crm.cutover.disconnect")) == 1
    assert _connection(zb, zb.org_a).credentials_encrypted is None


async def test_an_open_run_refuses_and_writes_nothing(zb, app_engine) -> None:  # noqa: F811
    _assert_non_priv(app_engine)
    await _ready(zb)
    _final_run(zb, zb.org_a, start_min=1, end_min=None, status="running")
    before = _hashes(zb)
    r = await _switch(zb)
    assert r.status_code == 409, r.text
    assert r.json()["detail"]["code"] == "run_open"
    assert _hashes(zb) == before
    assert not _actions(zb, "crm.cutover")


# ── Items 11 and 19: the refusals ───────────────────────────────────────────


async def test_the_refusals(zb, app_engine, monkeypatch) -> None:  # noqa: F811
    _assert_non_priv(app_engine)
    from acb_common import get_settings

    await _ready(zb)
    before = _hashes(zb)
    member = _member("m@crm-t5-r8.test", zb.org_a)
    paths = (("POST", "/crm/cutover/start"), ("GET", "/crm/cutover/status"),
             ("POST", "/crm/cutover"))
    for method, path in paths:
        body = SWITCH if method == "POST" else None
        assert (await _call(zb, member, method, path, body)).status_code == 403, path
    for body in ({"confirm": "switch"}, {}, {"confirm": " SWITCH"}):
        r = await _switch(zb, body)
        assert r.status_code == 422, body
        assert r.json()["detail"]["code"] == "confirm_required"
    r = await _switch(zb, {"confirm": "SWITCH", "leave_out": ["module:Nope"]})
    assert r.status_code == 422 and r.json()["detail"]["code"] == "unknown_leave_out"
    # Phase 1 takes only a module that is not complete. Products is complete.
    r = await _call(zb, _admin(zb.org_a), "POST", "/crm/cutover/start",
                    {"confirm": "SWITCH", "leave_out": ["module:Products"]})
    assert r.status_code == 422 and r.json()["detail"]["code"] == "unknown_leave_out"
    r = await _switch(zb, {"confirm": "SWITCH", "organization_id": zb.org_b})
    assert r.status_code == 422, "an unknown body field is refused (R11)"
    # PATCH /crm/settings never moves Mirror to Native.
    r = await _call(zb, _admin(zb.org_a), "PATCH", "/crm/settings", {"mode": "native"})
    assert r.status_code == 422, r.text
    assert _hashes(zb) == before

    # The flag off: 404 on each switch route. The preview says no switch.
    monkeypatch.setattr(get_settings(), "crm_cutover_orgs", "", raising=False)
    for method, path in paths:
        body = SWITCH if method == "POST" else None
        r = await _call(zb, _admin(zb.org_a), method, path, body)
        assert r.status_code == 404, path
    preview = await _call(zb, _admin(zb.org_a), "GET", "/crm/cutover/preview")
    assert preview.status_code == 200 and preview.json()["can_switch"] is False
    monkeypatch.setattr(get_settings(), "crm_cutover_orgs", "*", raising=False)

    # P0 (review): phase 2 needs the run of phase 1. No id, or an id of no
    # run, is not a final sync.
    for run_id in (None, "", "00000000-0000-0000-0000-000000000000"):
        r = await _switch(zb, {"confirm": "SWITCH", "run_id": run_id})
        assert r.status_code == 409, (run_id, r.text)
        assert r.json()["detail"]["code"] == "final_sync_missing"
    assert _hashes(zb) == before

    # Item 19: a final run that ended 16 minutes ago is too old.
    _exec(zb, "UPDATE crm_sync_runs SET started_at = now() - interval '20 minutes', "
              "finished_at = now() - interval '16 minutes' WHERE organization_id = "
              "CAST(:o AS uuid) AND started_at > now() - interval '1 hour'", o=zb.org_a)
    r = await _switch(zb)
    assert r.status_code == 409 and r.json()["detail"]["code"] == "final_sync_missing"
    # Native with no stamp is not a mirror.
    _exec(zb, "UPDATE crm_settings SET mode = 'native' WHERE organization_id = "
              "CAST(:o AS uuid)", o=zb.org_a)
    r = await _switch(zb)
    assert r.status_code == 409 and r.json()["detail"]["code"] == "not_mirror"
    assert not _actions(zb, "crm.cutover")


# ── Item 12: a failure rolls back all of phase 2 ────────────────────────────


@pytest.mark.parametrize("step", ["_rewind_watermarks", "_set_native"])
async def test_a_failure_in_phase_two_rolls_back_everything(
    zb, app_engine, monkeypatch, step,  # noqa: F811
) -> None:
    """A failure after step 10 (the watermark), then after step 11 (the mode)."""
    _assert_non_priv(app_engine)
    from gateway.routes.crm import cutover

    await _ready(zb)
    _exec(zb, "UPDATE crm_source_profiles SET profile = profile || CAST(:m AS jsonb) "
              "WHERE organization_id = CAST(:o AS uuid)",
          o=zb.org_a, m=json.dumps({"modules": [
              {"api_name": "Contacts", "label": "Existing Sites"}]}))
    _exec(zb, "UPDATE crm_deal_statuses SET ui_position = 3, ui_color = 'blue', "
              "ui_probability = 33 WHERE organization_id = CAST(:o AS uuid)", o=zb.org_a)
    box = _mailbox(zb, zb.org_a)
    before = _hashes(zb)
    stamp = _watermark(zb, box)
    real = getattr(cutover, step)

    async def _after_the_step(*args: Any, **kwargs: Any) -> Any:
        await real(*args, **kwargs)
        raise RuntimeError(f"a failure after {step}")

    monkeypatch.setattr(cutover, step, _after_the_step)
    with pytest.raises(RuntimeError):
        await _switch(zb)
    assert _hashes(zb) == before
    row = _settings(zb, zb.org_a)
    assert row.mode == "mirror" and row.labels is None and row.cutover_at is None
    ui = _one(zb, "SELECT ui_position, ui_color, ui_probability FROM crm_deal_statuses "
                  "WHERE organization_id = CAST(:o AS uuid)", o=zb.org_a)
    assert tuple(ui) == (3, "blue", 33)
    sources = {r[0] for r in _q(zb, "SELECT source FROM crm_field_defs WHERE "
                                    "organization_id = CAST(:o AS uuid)", o=zb.org_a)}
    assert "zoho" in sources
    assert _watermark(zb, box) == stamp
    assert not _actions(zb, "crm.cutover")
    assert _revokes(zb) == []


# ── Item 14: a run that raced the commit ────────────────────────────────────


async def test_a_run_that_raced_the_commit_stops_on_the_mode(
    zb, app_engine, monkeypatch,  # noqa: F811
) -> None:
    """The run's acquire read ``mirror`` before the commit, after the check
    for an open run. Its first page reads the mode FOR SHARE, finds
    ``native``, and stops with ``mode_changed``. It writes no record."""
    _assert_non_priv(app_engine)
    from gateway.crm_sync import engine
    from gateway.routes.crm import cutover

    await _ready(zb)
    records = _records(zb, zb.org_a)
    reached, go = asyncio.Event(), asyncio.Event()
    real = cutover.fold_cutover_overlay

    async def _paused(*args: Any, **kwargs: Any) -> Any:
        reached.set()
        await go.wait()
        return await real(*args, **kwargs)

    monkeypatch.setattr(cutover, "fold_cutover_overlay", _paused)
    async with tenant_engine_scope(_app_dsn(zb)):
        switch = asyncio.create_task(_request(zb, _admin(zb.org_a), "POST",
                                              "/crm/cutover", _body(zb)))
        await asyncio.wait_for(reached.wait(), 30)
        handle = await engine.acquire(zb.org_a)
        go.set()
        r = await switch
        assert r.status_code == 200, r.text
        result = await engine.execute(handle)
    assert (result.status, result.error) == ("stopped", "mode_changed")
    run = _one(zb, "SELECT status, error FROM crm_sync_runs WHERE id = CAST(:i AS uuid)",
               i=handle.run_id)
    assert tuple(run) == ("stopped", "mode_changed")
    assert _records(zb, zb.org_a) == records


# ── Item 15: a module that is not complete ──────────────────────────────────


async def test_a_held_module_blocks_unless_it_is_left_out(zb, app_engine) -> None:  # noqa: F811
    _assert_non_priv(app_engine)
    await _ready(zb)
    _exec(zb, "UPDATE crm_settings SET sync_holds = CAST(:h AS jsonb) "
              "WHERE organization_id = CAST(:o AS uuid)", o=zb.org_a,
          h=json.dumps({"module:Products": {"held": True, "by": "admin", "count": 1,
                                            "at": "2026-10-28T00:00:00+00:00"}}))
    products = _one(zb, "SELECT count(*) FROM crm_module_records WHERE organization_id "
                        "= CAST(:o AS uuid) AND module = 'Products'", o=zb.org_a)[0]
    r = await _switch(zb)
    assert r.status_code == 409, r.text
    detail = r.json()["detail"]
    assert detail["code"] == "module_incomplete"
    assert {"key": "module:Products", "label": "Products", "reason": "held"} in detail["modules"]
    r = await _switch(zb, {"confirm": "SWITCH", "leave_out": ["module:Products"]})
    assert r.status_code == 200, r.text
    assert _one(zb, "SELECT count(*) FROM crm_module_records WHERE organization_id "
                    "= CAST(:o AS uuid) AND module = 'Products'", o=zb.org_a)[0] == products
    (line,) = _actions(zb, "crm.cutover")
    assert line.payload["leave_out"] == ["module:Products"]


async def test_records_to_retry_and_a_short_read_each_block(zb, app_engine) -> None:  # noqa: F811
    _assert_non_priv(app_engine)
    await _ready(zb)
    _exec(zb, "UPDATE crm_sync_cursors SET cursor_state = CAST(:s AS jsonb) "
              "WHERE organization_id = CAST(:o AS uuid) AND module = 'zoho:deal'",
          o=zb.org_a, s=json.dumps({"in_progress": False,
                                    "failed": {"910000001": ["2026-10-28", 1]}}))
    r = await _switch(zb)
    assert r.status_code == 409, r.text
    assert {(m["key"], m["reason"]) for m in r.json()["detail"]["modules"]} == {
        ("deals", "failed")}
    _exec(zb, "UPDATE crm_sync_cursors SET cursor_state = NULL WHERE organization_id = "
              "CAST(:o AS uuid) AND module = 'zoho:deal'", o=zb.org_a)
    _exec(zb, "UPDATE crm_sync_cursors SET cursor_state = CAST(:s AS jsonb) "
              "WHERE organization_id = CAST(:o AS uuid) AND module = 'zoho:contact'",
          o=zb.org_a, s=json.dumps({"in_progress": False, "short_by": 3}))
    r = await _switch(zb)
    assert r.status_code == 409, r.text
    assert {(m["key"], m["reason"]) for m in r.json()["detail"]["modules"]} == {
        ("contacts", "short")}
    preview = (await _call(zb, _admin(zb.org_a), "GET", "/crm/cutover/preview")).json()
    assert preview["incomplete"] == [
        {"key": "contacts", "label": "Contacts", "reason": "short"}]
    assert _settings(zb, zb.org_a).mode == "mirror"


# ── Item 16: Zoho does not confirm the revoke ───────────────────────────────


async def test_an_unconfirmed_revoke_still_switches_and_says_so(zb, app_engine) -> None:  # noqa: F811
    _assert_non_priv(app_engine)
    await _ready(zb)
    zb.active["zoho"].revoke_body = {"status": "failure"}
    r = await _switch(zb)
    assert r.status_code == 200, r.text
    assert r.json() == {"status": "switched", "revoked": "not_confirmed",
                        "zoho_connection": "disconnected"}
    assert _settings(zb, zb.org_a).mode == "native"
    assert _connection(zb, zb.org_a).credentials_encrypted is None
    (closed,) = _actions(zb, "crm.cutover.disconnect")
    assert closed.payload["revoked"] == "not_confirmed"


# ── Item 17: two calls at one time ──────────────────────────────────────────


async def test_two_calls_at_one_time_switch_once(zb, app_engine) -> None:  # noqa: F811
    _assert_non_priv(app_engine)
    await _ready(zb)
    admin = _admin(zb.org_a)
    async with tenant_engine_scope(_app_dsn(zb)):
        answers = await asyncio.gather(
            _request(zb, admin, "POST", "/crm/cutover", _body(zb)),
            _request(zb, admin, "POST", "/crm/cutover", _body(zb)),
        )
    assert [a.status_code for a in answers] == [200, 200], [a.text for a in answers]
    assert sorted(a.json()["status"] for a in answers) == ["already_done", "switched"]
    assert len(_actions(zb, "crm.cutover")) == 1
    assert _settings(zb, zb.org_a).mode == "native"
    # F2: one revoke and one close line, whichever call closed Zoho.
    assert len(_revokes(zb)) == 1
    assert len(_actions(zb, "crm.cutover.disconnect")) == 1


# ── Item 18: an overlay edit that holds the mode ────────────────────────────


async def test_an_order_edit_that_holds_the_mode_is_folded(
    zb, app_engine, monkeypatch,  # noqa: F811
) -> None:
    """The order route read ``mirror`` FOR SHARE and has not written. The
    switch waits, the order commits, and the fold takes it. Without the share
    lock the switch would fold first, and the order would write ``ui_position``
    into a Native CRM after it."""
    _assert_non_priv(app_engine)
    from gateway.routes.crm import admin as crm_admin

    case = CASES[0]
    ids = _seed_case(zb, case)
    wanted = list(reversed([row["id"] for row in case["rows"]]))
    reached, go = asyncio.Event(), asyncio.Event()
    real = crm_admin.assert_writable

    async def _paused(*args: Any, **kwargs: Any) -> Any:
        await real(*args, **kwargs)
        if not reached.is_set():
            reached.set()
            await go.wait()

    monkeypatch.setattr(crm_admin, "assert_writable", _paused)
    admin = _admin(zb.org_a)
    async with tenant_engine_scope(_app_dsn(zb)):
        order = asyncio.create_task(_request(
            zb, admin, "PUT", "/crm/statuses/deal/order",
            {"ids": [ids[i] for i in wanted]}))
        await asyncio.wait_for(reached.wait(), 30)
        switch = asyncio.create_task(_request(zb, admin, "POST", "/crm/cutover", _body(zb)))
        waited = await _waits_on_a_lock(zb)
        go.set()
        ordered = await order
        r = await switch
    assert waited, "the switch did not wait for the edit that holds the mode"
    assert ordered.status_code == 200, ordered.text
    assert r.status_code == 200, r.text
    names = {real_id: fid for fid, real_id in ids.items()}
    rows = _native_rows(zb, "crm_deal_statuses")
    assert [names[row.id] for row in rows] == wanted
    assert all(row.ui_position is None for row in rows)
    # Native refuses a later overlay write.
    monkeypatch.setattr(crm_admin, "assert_writable", real)
    late = await _call(zb, admin, "PATCH", f"/crm/statuses/deal/{ids['a']}",
                       {"ui_position": 5})
    assert late.status_code == 422, late.text
    assert _one(zb, "SELECT ui_position FROM crm_deal_statuses WHERE id = "
                    "CAST(:i AS uuid)", i=ids["a"])[0] is None


# ── Item 20: a Zoho record becomes editable ─────────────────────────────────


async def test_a_zoho_record_is_locked_before_and_open_after(zb, app_engine) -> None:  # noqa: F811
    _assert_non_priv(app_engine)
    await _ready(zb)
    deal = str(_one(zb, "SELECT id FROM crm_deals WHERE organization_id = "
                        "CAST(:o AS uuid) AND ext_source = 'zoho' ORDER BY ext_id LIMIT 1",
                    o=zb.org_a)[0])
    admin = _admin(zb.org_a)
    before = await _call(zb, admin, "PATCH", f"/crm/deals/{deal}", {"name": "Renamed"})
    assert before.status_code == 409, before.text
    assert before.json()["detail"]["code"] == "mirrored_record"
    assert (await _switch(zb)).status_code == 200
    after = await _call(zb, admin, "PATCH", f"/crm/deals/{deal}", {"name": "Renamed"})
    assert after.status_code == 200, after.text
    assert _one(zb, "SELECT name, ext_source FROM crm_deals WHERE id = CAST(:i AS uuid)",
                i=deal) == ("Renamed", "zoho")


# ── Item 21: the start over after the switch ────────────────────────────────


async def test_the_start_over_after_the_switch_clears_the_stamps(zb, app_engine) -> None:  # noqa: F811
    _assert_non_priv(app_engine)
    await _ready(zb)
    _exec(zb, "UPDATE crm_source_profiles SET profile = profile || CAST(:m AS jsonb) "
              "WHERE organization_id = CAST(:o AS uuid)",
          o=zb.org_a, m=json.dumps({"modules": [
              {"api_name": "Contacts", "label": "Existing Sites"}]}))
    assert (await _switch(zb)).status_code == 200
    assert _settings(zb, zb.org_a).labels is not None
    r = await _call(zb, _admin(zb.org_a), "POST", "/crm/reset",
                    {"confirm": "DELETE", "then": "none"})
    assert r.status_code == 200, r.text
    row = _settings(zb, zb.org_a)
    assert (row.mode, row.labels, row.cutover_at, row.cutover_by) == (None, None, None, None)
    assert _records(zb, zb.org_a)[0]["crm_deals"] == 0


# ── Phase 1: the final sync ─────────────────────────────────────────────────


async def test_phase_one_starts_the_final_run_and_the_status_follows_it(zb, app_engine) -> None:  # noqa: F811
    _assert_non_priv(app_engine)
    await _ready(zb, final=False)
    admin = _admin(zb.org_a)
    r = await _call(zb, admin, "POST", "/crm/cutover/start", SWITCH)
    assert r.status_code == 202, r.text
    run_id = r.json()["run_id"]
    assert r.json() == {"run_id": run_id}
    (handle,) = zb.final_runs
    assert handle.run_id == run_id
    status = (await _call(zb, admin, "GET", "/crm/cutover/status")).json()
    assert (status["state"], status["run_id"]) == ("running", run_id)
    # A second start while it runs: the run lock refuses it.
    again = await _call(zb, admin, "POST", "/crm/cutover/start", SWITCH)
    assert again.status_code == 409 and again.json()["detail"]["code"] == "sync_running"
    # A body of phase 1 takes no run id (R11).
    extra = await _call(zb, admin, "POST", "/crm/cutover/start", {**SWITCH, "run_id": run_id})
    assert extra.status_code == 422, extra.text
    _exec(zb, "UPDATE crm_sync_runs SET status = 'ok', finished_at = now() "
              "WHERE id = CAST(:i AS uuid)", i=run_id)
    status = (await _call(zb, admin, "GET", "/crm/cutover/status")).json()
    assert (status["state"], status["run_id"]) == ("ready", run_id)
    # P0 (review): a fresh good run is no shortcut. Each start runs its own.
    second = await _call(zb, admin, "POST", "/crm/cutover/start", SWITCH)
    assert second.status_code == 202, second.text
    newer = second.json()["run_id"]
    assert newer != run_id and len(zb.final_runs) == 2
    _exec(zb, "UPDATE crm_sync_runs SET status = 'ok', finished_at = now() "
              "WHERE id = CAST(:i AS uuid)", i=newer)
    # The first run is not the newest now, so it is not the final sync.
    before = _hashes(zb)
    stale = await _switch(zb, _body(zb, run_id=run_id))
    assert stale.status_code == 409 and stale.json()["detail"]["code"] == "final_sync_missing"
    assert _hashes(zb) == before
    # Phase 2 takes the run of the last start as the final one.
    assert (await _switch(zb, _body(zb, run_id=newer))).status_code == 200
    (line,) = _actions(zb, "crm.cutover")
    assert line.payload["final_run"] == newer


async def test_a_fresh_one_module_run_is_not_the_final_sync(zb, app_engine) -> None:  # noqa: F811
    """P0 (review). "Sync it on its own" ran one module a minute ago, and the
    run ended ``ok``. The row does not say that the run read one module. So
    phase 1 starts its own full run, and phase 2 refuses the one-module run,
    with every table as before."""
    _assert_non_priv(app_engine)
    await _ready(zb, final=False)
    admin = _admin(zb.org_a)
    one_module = _final_run(zb, zb.org_a, start_min=2, end_min=1)
    r = await _call(zb, admin, "POST", "/crm/cutover/start", SWITCH)
    assert r.status_code == 202, r.text
    final = r.json()["run_id"]
    assert final != one_module and len(zb.final_runs) == 1
    # The final run is still open.
    r = await _switch(zb, _body(zb, run_id=one_module))
    assert r.status_code == 409 and r.json()["detail"]["code"] == "run_open"
    _exec(zb, "UPDATE crm_sync_runs SET status = 'ok', finished_at = now() "
              "WHERE id = CAST(:i AS uuid)", i=final)
    before = _hashes(zb)
    for run_id in (one_module, None):
        r = await _switch(zb, _body(zb, run_id=run_id))
        assert r.status_code == 409, (run_id, r.text)
        assert r.json()["detail"]["code"] == "final_sync_missing"
    assert _hashes(zb) == before
    assert _settings(zb, zb.org_a).mode == "mirror"
    assert not _actions(zb, "crm.cutover") and _revokes(zb) == []
    r = await _switch(zb, _body(zb, run_id=final))
    assert r.status_code == 200, r.text
    (line,) = _actions(zb, "crm.cutover")
    assert line.payload["final_run"] == final


async def test_a_run_that_ends_after_the_final_run_fails_closed(zb, app_engine) -> None:  # noqa: F811
    """P0 (review). A scheduled run starts after the final run and ends
    ``ok`` before the commit. Its changes are not the final sync's, and T0
    would be wrong. Phase 2 answers ``final_sync_missing`` and writes
    nothing. The admin starts the switch again."""
    _assert_non_priv(app_engine)
    await _ready(zb)
    _final_run(zb, zb.org_a, start_min=2, end_min=1)
    before = _hashes(zb)
    r = await _switch(zb)
    assert r.status_code == 409, r.text
    assert r.json()["detail"]["code"] == "final_sync_missing"
    assert _hashes(zb) == before
    assert _settings(zb, zb.org_a).mode == "mirror"
    assert not _actions(zb, "crm.cutover") and _revokes(zb) == []


async def test_phase_one_refuses_a_dead_token_an_open_import_and_a_failed_run(
    zb, app_engine,  # noqa: F811
) -> None:
    _assert_non_priv(app_engine)
    await _ready(zb, final=False)
    admin = _admin(zb.org_a)
    _exec(zb, "UPDATE integration_connections SET status = 'needs_reconnect' "
              "WHERE organization_id = CAST(:o AS uuid)", o=zb.org_a)
    r = await _call(zb, admin, "POST", "/crm/cutover/start", SWITCH)
    assert r.status_code == 409 and r.json()["detail"]["code"] == "needs_reconnect"
    _exec(zb, "UPDATE integration_connections SET status = 'connected' "
              "WHERE organization_id = CAST(:o AS uuid)", o=zb.org_a)
    _exec(zb, "UPDATE crm_sync_cursors SET initial_done_at = NULL WHERE "
              "organization_id = CAST(:o AS uuid) AND module = 'zoho:lead'", o=zb.org_a)
    r = await _call(zb, admin, "POST", "/crm/cutover/start", SWITCH)
    assert r.status_code == 409 and r.json()["detail"]["code"] == "import_open"
    _exec(zb, "UPDATE crm_sync_cursors SET initial_done_at = now() WHERE "
              "organization_id = CAST(:o AS uuid) AND module = 'zoho:lead'", o=zb.org_a)
    _final_run(zb, zb.org_a, start_min=3, end_min=2, status="failed")
    _exec(zb, "UPDATE crm_sync_runs SET error = 'budget' WHERE organization_id = "
              "CAST(:o AS uuid) AND status = 'failed'", o=zb.org_a)
    status = (await _call(zb, admin, "GET", "/crm/cutover/status")).json()
    assert (status["state"], status["error"]) == ("failed", "budget")
    assert zb.final_runs == []


async def test_a_module_that_the_final_sync_completed_does_not_block(zb, app_engine) -> None:  # noqa: F811
    """Item 15, the other side. The admin left a module out at phase 1, and
    the final sync then completed it. Phase 2 drops it and switches. It does
    not refuse the same body that phase 1 took."""
    _assert_non_priv(app_engine)
    await _ready(zb)
    body = {"confirm": "SWITCH", "leave_out": ["module:Products"]}
    r = await _switch(zb, body)
    assert r.status_code == 200, r.text
    (line,) = _actions(zb, "crm.cutover")
    assert line.payload["leave_out"] == []
