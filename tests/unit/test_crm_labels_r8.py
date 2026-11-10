"""R8 — the names of the core records in a Native CRM (WS-53 CRM-Z10g).

D133.1 (``project-docs/work_plan.md`` §3): an org that leaves Zoho keeps its
Zoho names, and an admin can rename them later. Migration 311 adds
``crm_settings.labels`` (and the two cutover stamps of CRM-Z10b).
``PUT /crm/settings/labels`` writes the names, and ``GET /crm/mode`` serves
them in Native mode.

What this proves against a REAL Postgres, through the real ``/crm`` router,
as the NON-privileged role of the phase-4 rehearsal (no superuser, no
BYPASSRLS):

- the migration adds three nullable columns with no default, and it runs
  twice with no error and no change;
- a Native admin renames Contacts to "Existing Sites". ``GET /crm/mode``
  serves it to each member, a new engine still reads it, and org B never
  sees it (R5);
- Mirror mode: the route is a 409, and ``GET /crm/mode`` still serves the
  source profile's names;
- a member who is not an admin gets 403;
- an unknown key, an empty name, a name of 61 characters and a NUL are
  each a 422 that writes nothing;
- an empty body clears every name, and a ``null`` entry clears one;
- the start over (``POST /crm/reset``) clears the names and the stamps;
- "Change CRM setup" (``PATCH /crm/settings`` with ``mode: null``) clears
  them too, so Native → none → Native starts with the names of the CRM.

A hermetic fake agrees with any SQL it is handed (the ``mode = 'native'``
guard, the JSONB cast, the RLS policy), so none of this is proved without
the database.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

pytest.importorskip("sqlalchemy")

from sqlalchemy import text

from tests.unit.test_crm_access_r8 import (
    _address,
    _admin,
    _assert_non_priv,
    _call,
    _member,
)
from tests.unit.test_h3_rls_promotion_rehearsal import (  # noqa: F401
    _DB_GATE,
    app_engine,
    promoted,
)

# ⚠️ Every test here needs the real database. Without this gate the suite
# ERRORS where no ladder URL is set (the deploy run), and the deploy stops.
pytestmark = _DB_GATE

_MIGRATIONS = Path(__file__).resolve().parents[2] / "infra" / "postgres"

SITES = {"label": "Existing Sites", "singular_label": "Existing Site"}

#: The tables this suite writes, child first. Each is emptied for both orgs
#: after each test.
_CLEANED = (
    "crm_source_profiles", "crm_settings", "integration_connections",
    "crm_pipeline_stages", "crm_pipelines",
    "crm_deal_statuses", "crm_lead_statuses", "crm_lost_reasons",
)


@pytest.fixture
def zg(promoted, monkeypatch):  # noqa: F811 — a fixture
    """The promoted catalog with the CRM switch on, cleaned after each test."""
    import acb_audit
    from acb_common import get_settings

    from tests.unit._crm_fakes import crm_switch_on

    crm_switch_on(monkeypatch, "*")
    # The source labels of Mirror mode need the fill-rank list (CRM-Q17a-1).
    # The native names must not: they serve with the list empty too.
    monkeypatch.setattr(get_settings(), "crm_fill_rank_orgs", "", raising=False)
    audits: list[Any] = []
    monkeypatch.setattr(acb_audit, "record", audits.append)
    promoted.audits = audits
    yield promoted
    with promoted.admin_engine.begin() as c:
        for org in (promoted.org_a, promoted.org_b):
            for table in _CLEANED:
                c.execute(text(
                    f"DELETE FROM {table} WHERE organization_id = CAST(:o AS uuid)"),
                    {"o": org})


def _set(p: Any, org: str, *, mode: str | None, access: str = "admins",
         labels: dict[str, Any] | None = None) -> None:
    """The settings row of *org*, written as the admin role (no RLS)."""
    with p.admin_engine.begin() as c:
        c.execute(text(
            "INSERT INTO crm_settings (organization_id, mode, access, labels) "
            "VALUES (CAST(:o AS uuid), :m, :a, CAST(:l AS jsonb)) "
            "ON CONFLICT (organization_id) DO UPDATE SET mode = EXCLUDED.mode, "
            "access = EXCLUDED.access, labels = EXCLUDED.labels"),
            {"o": org, "m": mode, "a": access,
             "l": json.dumps(labels) if labels is not None else None})


def _stored(p: Any, org: str) -> Any:
    """``(labels, cutover_at, cutover_by)`` of *org*, or None with no row."""
    with p.admin_engine.connect() as c:
        return c.execute(text(
            "SELECT labels, cutover_at, cutover_by FROM crm_settings "
            "WHERE organization_id = CAST(:o AS uuid)"), {"o": org}).first()


def _profile(p: Any, org: str) -> None:
    """A Zoho profile whose Contacts module reads "Existing Sites"."""
    with p.admin_engine.begin() as c:
        conn = str(c.execute(text(
            "INSERT INTO integration_connections (organization_id, provider, "
            "owning_app, status) VALUES (CAST(:o AS uuid), 'zoho', 'crm', "
            "'disconnected') RETURNING id"), {"o": org}).scalar())
        c.execute(text(
            "INSERT INTO crm_source_profiles (organization_id, connection_id, "
            "version, profile, profile_hash) VALUES (CAST(:o AS uuid), "
            "CAST(:c AS uuid), 1, CAST(:p AS jsonb), 'h')"),
            {"o": org, "c": conn, "p": json.dumps({"modules": [
                {"api_name": "Contacts", "label": "Existing Sites",
                 "singular_label": "Existing Site"},
                {"api_name": "Deals", "label": "Deals", "singular_label": "Deal"},
            ]})})


async def _put(p: Any, body: Any, user: Any = None) -> Any:
    return await _call(p, user or _admin(p.org_a), "PUT", "/crm/settings/labels", body)


async def _mode(p: Any, org: str, user: Any = None) -> dict[str, Any]:
    r = await _call(p, user or _admin(org), "GET", "/crm/mode")
    assert r.status_code == 200, r.text
    return r.json()


# ── The migration ────────────────────────────────────────────────────────────


def _labels_migration() -> Path:
    """The migration that adds ``crm_settings.labels``, found by content (R1)."""
    found = [
        p for p in sorted(_MIGRATIONS.glob("*.sql"))
        if p.name != "schema.generated.sql"
        and "ADD COLUMN IF NOT EXISTS labels" in p.read_text(encoding="utf-8")
        and "crm_settings" in p.read_text(encoding="utf-8")
    ]
    assert len(found) == 1, [p.name for p in found]
    return found[0]


def _columns(p: Any) -> dict[str, tuple[str, str, Any]]:
    with p.admin_engine.connect() as c:
        rows = c.execute(text(
            "SELECT column_name, data_type, is_nullable, column_default "
            "FROM information_schema.columns WHERE table_schema = 'public' "
            "AND table_name = 'crm_settings' "
            "AND column_name IN ('labels', 'cutover_at', 'cutover_by')")).all()
    return {r[0]: (r[1], r[2], r[3]) for r in rows}


def test_the_migration_adds_three_nullable_columns_with_no_default(zg) -> None:
    assert _columns(zg) == {
        "labels": ("jsonb", "YES", None),
        "cutover_at": ("timestamp with time zone", "YES", None),
        "cutover_by": ("text", "YES", None),
    }


def test_the_migration_runs_twice_with_no_error_and_no_change(zg) -> None:
    _set(zg, zg.org_a, mode="native", labels={"contacts": SITES})
    before = _columns(zg)
    sql = _labels_migration().read_text(encoding="utf-8")
    for _ in range(2):
        with zg.admin_engine.begin() as c, c.connection.dbapi_connection.cursor() as cur:
            cur.execute(sql)
    assert _columns(zg) == before
    assert _stored(zg, zg.org_a)[0] == {"contacts": SITES}


# ── A Native admin renames, and the mode route serves it ────────────────────


async def test_a_native_admin_renames_contacts_and_the_mode_route_serves_it(
    zg, app_engine,  # noqa: F811
) -> None:
    _assert_non_priv(app_engine)
    _set(zg, zg.org_a, mode="native", access="everyone")
    _set(zg, zg.org_b, mode="native", access="everyone")
    # Before any rename: the body of before, with no labels key.
    assert await _mode(zg, zg.org_a) == {"mode": "native"}

    r = await _put(zg, {"labels": {"contacts": SITES}})
    assert r.status_code == 200, r.text
    assert r.json() == {"labels": {"contacts": SITES}}

    # A new engine reads it (each `_call` builds and disposes its own), and a
    # member who is not an admin reads it too. The fill-rank list is empty.
    member = _member(_address("m"), zg.org_a)
    for user in (_admin(zg.org_a), member):
        assert await _mode(zg, zg.org_a, user) == {
            "mode": "native", "labels": {"contacts": SITES},
        }
    stored, cutover_at, cutover_by = _stored(zg, zg.org_a)
    assert stored == {"contacts": SITES}
    assert (cutover_at, cutover_by) == (None, None), "nothing writes the stamps yet"

    # R5: org B never sees org A's names, through the route or the policy.
    assert await _mode(zg, zg.org_b) == {"mode": "native"}
    assert _stored(zg, zg.org_b)[0] is None
    with app_engine.begin() as c:
        c.execute(text("SELECT set_config('app.tenant_id', :o, true)"), {"o": zg.org_b})
        seen = c.execute(text("SELECT organization_id, labels FROM crm_settings")).all()
    assert [(str(o), labels) for o, labels in seen] == [(zg.org_b, None)]
    # No audit line: PATCH /crm/settings writes none either.
    assert zg.audits == []


async def test_the_names_are_trimmed_and_sixty_characters_pass(zg, app_engine) -> None:  # noqa: F811
    _assert_non_priv(app_engine)
    _set(zg, zg.org_a, mode="native")
    sixty = "S" * 60
    r = await _put(zg, {"labels": {
        "contacts": {"label": "  Existing Sites  ", "singular_label": " Existing Site "},
        "organizations": {"label": sixty},
    }})
    assert r.status_code == 200, r.text
    want = {
        # In the order of ENTITIES, and with no singular name sent, none.
        "contacts": SITES,
        "organizations": {"label": sixty, "singular_label": None},
    }
    assert r.json()["labels"] == want
    assert (await _mode(zg, zg.org_a))["labels"] == want


async def test_an_empty_body_clears_every_name_and_a_null_clears_one(zg, app_engine) -> None:  # noqa: F811
    _assert_non_priv(app_engine)
    deals = {"label": "Orders", "singular_label": "Order"}
    _set(zg, zg.org_a, mode="native", labels={"contacts": SITES, "deals": deals})

    r = await _put(zg, {"labels": {"contacts": None, "deals": deals}})
    assert r.status_code == 200, r.text
    assert r.json() == {"labels": {"deals": deals}}
    assert (await _mode(zg, zg.org_a))["labels"] == {"deals": deals}

    r = await _put(zg, {})
    assert r.status_code == 200, r.text
    assert r.json() == {"labels": {}}
    assert _stored(zg, zg.org_a)[0] is None, "no name is NULL, never {}"
    assert await _mode(zg, zg.org_a) == {"mode": "native"}

    # No body at all clears too.
    _set(zg, zg.org_a, mode="native", labels={"contacts": SITES})
    r = await _put(zg, None)
    assert r.status_code == 200, r.text
    assert _stored(zg, zg.org_a)[0] is None


# ── Mirror mode and no mode ──────────────────────────────────────────────────


async def test_mirror_mode_is_409_and_the_profile_names_still_serve(
    zg, app_engine, monkeypatch,  # noqa: F811
) -> None:
    from acb_common import get_settings

    _assert_non_priv(app_engine)
    monkeypatch.setattr(get_settings(), "crm_fill_rank_orgs", zg.org_a, raising=False)
    _profile(zg, zg.org_a)
    # A stored name on a Mirror CRM (left from an earlier Native setup) is
    # not served: Zoho owns the names in Mirror mode.
    _set(zg, zg.org_a, mode="mirror", labels={"contacts": {"label": "Old"}})

    r = await _put(zg, {"labels": {"contacts": {"label": "Sites"}}})
    assert r.status_code == 409, r.text
    assert r.json()["detail"]["code"] == "mirror_mode_labels"
    assert _stored(zg, zg.org_a)[0] == {"contacts": {"label": "Old"}}, "nothing written"

    body = await _mode(zg, zg.org_a)
    assert body["mode"] == "mirror"
    assert body["labels"]["contacts"] == SITES
    assert body["labels"]["deals"] == {"label": "Deals", "singular_label": "Deal"}


async def test_no_mode_is_409_not_native(zg, app_engine) -> None:  # noqa: F811
    _assert_non_priv(app_engine)
    r = await _put(zg, {"labels": {"contacts": SITES}})
    assert r.status_code == 409, r.text
    assert r.json()["detail"]["code"] == "not_native"
    assert _stored(zg, zg.org_a) is None, "no row is made"


# ── Who may ─────────────────────────────────────────────────────────────────


async def test_a_member_who_is_not_an_admin_gets_403(zg, app_engine) -> None:  # noqa: F811
    _assert_non_priv(app_engine)
    # `everyone` lets the member through the router's access rule, so the 403
    # comes from the route's own admin floor.
    _set(zg, zg.org_a, mode="native", access="everyone", labels={"contacts": SITES})
    member = _member(_address("m"), zg.org_a)
    r = await _put(zg, {"labels": {}}, member)
    assert r.status_code == 403, r.text
    assert _stored(zg, zg.org_a)[0] == {"contacts": SITES}


# ── Validation ───────────────────────────────────────────────────────────────


@pytest.mark.parametrize("body", [
    # An unknown key: a word that is not a core API slug, and the singular.
    {"labels": {"projects": {"label": "Jobs"}}},
    {"labels": {"contact": {"label": "Sites"}}},
    # An empty name, plural or singular, also after the trim.
    {"labels": {"contacts": {"label": ""}}},
    {"labels": {"contacts": {"label": "   "}}},
    {"labels": {"contacts": {"label": "Sites", "singular_label": " "}}},
    # 61 characters.
    {"labels": {"contacts": {"label": "S" * 61}}},
    {"labels": {"contacts": {"label": "Sites", "singular_label": "S" * 61}}},
    # A NUL, in a name and in a key.
    {"labels": {"contacts": {"label": "Existing\x00Sites"}}},
    {"labels": {"contacts": {"label": "Sites", "singular_label": "Site\x00"}}},
    {"labels": {"cont\x00acts": {"label": "Sites"}}},
    # A field the body does not know, and a tenant in the body (R11).
    {"labels": {"contacts": {"label": "Sites", "color": "red"}}},
    {"labels": {}, "organization_id": "00000000-0000-0000-0000-000000000001"},
    # A name that is not text.
    {"labels": {"contacts": {"label": 5}}},
])
async def test_a_bad_body_is_422_and_writes_nothing(zg, app_engine, body) -> None:  # noqa: F811
    _assert_non_priv(app_engine)
    _set(zg, zg.org_a, mode="native", labels={"contacts": SITES})
    r = await _put(zg, body)
    assert r.status_code == 422, r.text
    assert _stored(zg, zg.org_a)[0] == {"contacts": SITES}


# ── The start over ───────────────────────────────────────────────────────────


async def test_the_start_over_clears_the_names_and_the_stamps(zg, app_engine) -> None:  # noqa: F811
    _assert_non_priv(app_engine)
    _set(zg, zg.org_a, mode="native", access="everyone", labels={"contacts": SITES})
    _set(zg, zg.org_b, mode="native", labels={"contacts": SITES})
    with zg.admin_engine.begin() as c:
        c.execute(text(
            "UPDATE crm_settings SET cutover_at = now(), cutover_by = 'a@x.test' "
            "WHERE organization_id = CAST(:o AS uuid)"), {"o": zg.org_a})

    r = await _call(zg, _admin(zg.org_a), "POST", "/crm/reset",
                    {"confirm": "DELETE", "then": "native"})
    assert r.status_code == 200, r.text
    assert tuple(_stored(zg, zg.org_a)) == (None, None, None)
    assert await _mode(zg, zg.org_a) == {"mode": "native"}
    # The start over keeps the access rule, and org B keeps its names.
    with zg.admin_engine.connect() as c:
        access = c.execute(text(
            "SELECT access FROM crm_settings WHERE organization_id = "
            "CAST(:o AS uuid)"), {"o": zg.org_a}).scalar()
    assert access == "everyone"
    assert _stored(zg, zg.org_b)[0] == {"contacts": SITES}


async def test_change_crm_setup_clears_the_names_and_the_stamps(zg, app_engine) -> None:  # noqa: F811
    """Review P2: Native → none → Native through ``PATCH /crm/settings``.

    Before the fix, ``_reset_setup`` kept ``labels``, ``cutover_at`` and
    ``cutover_by``. The old names came back on the new Native setup, and a
    stale ``cutover_at`` broke CRM-Z10b phase 2 step 3.
    """
    _assert_non_priv(app_engine)
    admin = _admin(zg.org_a)
    _set(zg, zg.org_b, mode="native", labels={"contacts": SITES})
    r = await _call(zg, admin, "PATCH", "/crm/settings", {"mode": "native"})
    assert r.status_code == 200, r.text
    r = await _put(zg, {"labels": {"contacts": SITES}})
    assert r.status_code == 200, r.text
    # Nothing writes the stamps yet (CRM-Z10b will), so the test sets them.
    with zg.admin_engine.begin() as c:
        c.execute(text(
            "UPDATE crm_settings SET cutover_at = now(), cutover_by = 'a@x.test' "
            "WHERE organization_id = CAST(:o AS uuid)"), {"o": zg.org_a})
    assert _stored(zg, zg.org_a)[0] == {"contacts": SITES}

    r = await _call(zg, admin, "PATCH", "/crm/settings", {"mode": None})
    assert r.status_code == 200, r.text
    assert tuple(_stored(zg, zg.org_a)) == (None, None, None)

    r = await _call(zg, admin, "PATCH", "/crm/settings", {"mode": "native"})
    assert r.status_code == 200, r.text
    assert await _mode(zg, zg.org_a) == {"mode": "native"}, "no old names"
    stored, cutover_at, cutover_by = _stored(zg, zg.org_a)
    assert (stored, cutover_at, cutover_by) == (None, None, None)
    # Org B keeps its names.
    assert _stored(zg, zg.org_b)[0] == {"contacts": SITES}
