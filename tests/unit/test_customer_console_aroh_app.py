"""CP-APP — an organization gets the Aroh app with N seats (Metorite task 1105).

Spec: ``project-docs/specs/customer_console.md`` §6 "CP-APP · The Aroh app for
an organization" · migration ``038_app_seats.sql`` · ``customer_console/apps.py``.

The world under test, stated once:

* An organization lives on its METORITE box (``org_placement``).
* The operator turns Aroh on for it with N seats (``POST /orgs/apps``). The org
  is then visible to the AROH box through ``org_app``, never through a second
  ``org_placement`` row.
* The Aroh box holds ONE deployment key with ``{resolve, seat_admin, serve}``.
  A member signs in, the box calls ``resolve``, then takes the member's own
  seat at ``POST /registry/seats``. The (N+1)th member gets the seat cap 409.
* The box calls the Router with ``X-CC-Member`` + ``X-CC-Member-Proven: 1``,
  and the call is metered on the member's organization, under ``aroh``.

⚠️ **R8, and a fake would agree with anything.** Every subject is a join over
``org_app``, ``org_membership`` and ``deployment``, a CHECK the migration
widens, the capacity lock, or a ledger sum. So this suite skips LOUDLY without
a server, and ``pr-check.yml`` names it in its must-run list.

⚠️ **One box serves the Aroh app** (``deployment_app_uniq``). This suite never
moves that designation outside a transaction it rolls back, because another
shard may run against the same database at the same time.

Run::

    export CUSTOMER_CONSOLE_DATABASE_URL=postgresql+psycopg://cc:cc@127.0.0.1:5442/cc_platform
    uv run pytest tests/unit/test_customer_console_aroh_app.py
"""
from __future__ import annotations

import io
import json
import os
import uuid
from decimal import Decimal
from pathlib import Path

import pytest

pytest.importorskip("fastapi")

import httpx
from customer_console import router as router_mod
from customer_console.auth import (
    RESOLVE_CAPABILITY,
    SEAT_ADMIN_CAPABILITY,
    SERVE_CAPABILITY,
)
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, text

from tests.unit._customer_console_ladder import (
    apply_ladder,
    ensure_deployment,
    mint_deployment_key,
)

_URL = os.environ.get("CUSTOMER_CONSOLE_DATABASE_URL", "").strip()

pytestmark = pytest.mark.skipif(
    not _URL,
    reason=(
        "CUSTOMER_CONSOLE_DATABASE_URL unset — R8 requires a REAL Postgres. "
        "A skip here is not a pass; CI must set it."
    ),
)

_ROOT = Path(__file__).resolve().parents[2]

TOKEN = "test-operator-token"
INTERNAL = "test-internal-token"
ENC_KEY = "test-encryption-key-not-a-real-one"
OP = {"Authorization": f"Bearer {TOKEN}"}

#: Where this suite's organizations LIVE: their Metorite box.
METORITE_BOX = "aroh-suite-metorite-box"
#: The label this suite gives the Aroh box when no box serves Aroh yet.
AROH_BOX = "aroh-suite-aroh-box"

AROH_CAPS = [RESOLVE_CAPABILITY, SEAT_ADMIN_CAPABILITY, SERVE_CAPABILITY]

PROVIDER_RESPONSE = {
    "id": "chatcmpl-aroh",
    "object": "chat.completion",
    "created": 1_755_000_000,
    "model": "deepseek/deepseek-v4-pro",
    "choices": [{
        "index": 0,
        "message": {"role": "assistant", "content": "Done."},
        "finish_reason": "stop",
    }],
    "usage": {"prompt_tokens": 1200, "completion_tokens": 40,
              "prompt_tokens_details": {"cached_tokens": 900}},
}


# ── Fixtures ─────────────────────────────────────────────────────────────────

@pytest.fixture(scope="module", autouse=True)
def _schema():
    eng = create_engine(_URL, future=True)
    with eng.begin() as conn:
        apply_ladder(conn)
    eng.dispose()


@pytest.fixture
def db():
    eng = create_engine(_URL, future=True)
    yield eng
    eng.dispose()


@pytest.fixture
def calls():
    seen: list[dict] = []

    async def _stub(**kwargs):
        seen.append(kwargs)
        return dict(PROVIDER_RESPONSE)

    router_mod.set_provider_call(_stub)
    yield seen


@pytest.fixture
def client(monkeypatch, calls):
    monkeypatch.setenv("CUSTOMER_CONSOLE_OPERATOR_TOKEN", TOKEN)
    monkeypatch.setenv("CUSTOMER_CONSOLE_INTERNAL_TOKEN", INTERNAL)
    monkeypatch.setenv("CUSTOMER_CONSOLE_ENCRYPTION_KEY", ENC_KEY)
    from customer_console.main import app

    return TestClient(app)


def _aroh_box_id(conn) -> str:
    """The box that serves Aroh: the one that exists, else this suite's."""
    row = conn.execute(text("SELECT id FROM deployment WHERE app = 'aroh'")).first()
    if row is not None:
        return str(row[0])
    dep = ensure_deployment(conn, label=AROH_BOX, base_url="https://aroh.invalid")
    conn.execute(text("UPDATE deployment SET app = 'aroh' WHERE id = :d"), {"d": dep})
    return dep


@pytest.fixture(autouse=True)
def boxes(db):
    """Both boxes, per TEST: a sibling suite empties `deployment`."""
    with db.begin() as c:
        metorite = ensure_deployment(c, label=METORITE_BOX)
        aroh = _aroh_box_id(c)
    return {"metorite": metorite, "aroh": aroh}


@pytest.fixture
def aroh_key(db, boxes):
    with db.begin() as c:
        return mint_deployment_key(c, deployment_id=boxes["aroh"], capabilities=AROH_CAPS)


@pytest.fixture
def metorite_key(db, boxes):
    with db.begin() as c:
        return mint_deployment_key(
            c, deployment_id=boxes["metorite"], capabilities=AROH_CAPS
        )


# ── Helpers ──────────────────────────────────────────────────────────────────

def _org(client, *, core_seats: int = 5) -> dict:
    slug = f"aroh-{uuid.uuid4().hex[:8]}"
    owner = f"owner@{slug}.example"
    r = client.post("/orgs/provision", headers=OP, json={
        "slug": slug, "name": "N", "owner_email": owner,
        "core_seats": core_seats, "deployment_label": METORITE_BOX,
    })
    assert r.status_code == 200, r.text
    return {"slug": slug, "owner": owner, "id": r.json()["organization_id"]}


def _member(client, org: dict, name: str) -> str:
    email = f"{name}@{org['slug']}.example"
    r = client.post("/registry/members", headers=OP,
                    json={"org_slug": org["slug"], "member_email": email})
    assert r.status_code == 200, r.text
    return email


def _set_app(client, org: dict, *, enabled: bool = True, seats: int = 2):
    return client.post("/orgs/apps", headers=OP, json={
        "org_slug": org["slug"], "app": "aroh", "enabled": enabled,
        "seats": seats, "reason": "pilot for task 1105",
    })


def _bearer(key: str) -> dict:
    return {"Authorization": f"Bearer {key}"}


def _resolve(client, key: str, email: str):
    return client.post("/registry/resolve", headers=_bearer(key), json={"email": email})


def _take(client, key: str, member: str, *, actor: str | None = None, **extra):
    return client.post("/registry/seats", headers=_bearer(key), json={
        "actor_email": actor or member, "member_email": member,
        "plan_slug": "aroh", **extra,
    })


def _free(client, key: str, member: str, *, actor: str | None = None):
    return client.post("/registry/seats/release", headers=_bearer(key), json={
        "actor_email": actor or member, "member_email": member, "plan_slug": "aroh",
    })


def _apps(client, org: dict) -> dict:
    r = client.get(f"/orgs/apps?org_slug={org['slug']}", headers=OP)
    assert r.status_code == 200, r.text
    (aroh,) = r.json()["apps"]
    return aroh


def _set_org_status(db, org: dict, status: str) -> None:
    """Force a lifecycle state: the PRECONDITION, never the subject."""
    with db.begin() as c:
        c.execute(text("UPDATE organization SET status = :s WHERE id = :o"),
                  {"s": status, "o": org["id"]})


def _live_seats(db, org: dict) -> dict[str, int]:
    with db.begin() as c:
        return {
            r[0]: int(r[1]) for r in c.execute(text(
                "SELECT plan_slug, count(*) FROM seat_assignment "
                "WHERE organization_id = :o AND released_at IS NULL GROUP BY 1"),
                {"o": org["id"]})
        }


# ── The migration ────────────────────────────────────────────────────────────

class TestTheMigration:
    def test_the_aroh_plan_is_an_app_that_nobody_can_buy(self, db):
        with db.begin() as c:
            row = c.execute(text(
                "SELECT kind, active, price_inr FROM plan_catalog WHERE slug = 'aroh'"
            )).one()
        assert row[0] == "app"
        assert row[1] is False, "an active app plan would be sold in the checkout"
        assert row[2] == Decimal("0.00")

    def test_the_ladder_replays(self, db):
        """Every migration here is replay-safe; 038's two CHECK swaps too."""
        with db.begin() as c:
            apply_ladder(c)
            names = {r[0] for r in c.execute(text(
                "SELECT conname FROM pg_constraint WHERE conname IN "
                "('plan_catalog_kind_known', 'seat_assignment_source_known')"))}
        assert names == {"plan_catalog_kind_known", "seat_assignment_source_known"}

    def test_the_seat_source_app_is_legal_and_a_typo_is_not(self, db, client):
        org = _org(client)
        with db.connect() as c:
            trans = c.begin()
            try:
                ident = c.execute(text(
                    "SELECT user_identity_id FROM org_membership "
                    "WHERE organization_id = :o"), {"o": org["id"]}).scalar_one()
                c.execute(text(
                    "INSERT INTO seat_assignment (organization_id, plan_slug, "
                    " user_identity_id, source) VALUES (:o, 'aroh', :i, 'app')"),
                    {"o": org["id"], "i": ident})
                with (
                    pytest.raises(Exception, match="seat_assignment_source_known"),
                    c.begin_nested(),
                ):
                    c.execute(text(
                        "INSERT INTO seat_assignment (organization_id, plan_slug, "
                        " user_identity_id, source) VALUES (:o, 'core', :i, 'apps')"),
                        {"o": org["id"], "i": ident})
            finally:
                trans.rollback()

    def test_one_box_serves_one_app(self, db, boxes):
        with db.connect() as c:
            trans = c.begin()
            try:
                other = ensure_deployment(c, label=f"second-{uuid.uuid4().hex[:6]}")
                with pytest.raises(Exception, match="deployment_app_uniq"):
                    c.execute(text("UPDATE deployment SET app = 'aroh' WHERE id = :d"),
                              {"d": other})
            finally:
                trans.rollback()


# ── The operator door ────────────────────────────────────────────────────────

class TestTheOperatorDoor:
    def test_a_new_org_has_aroh_off_with_no_seats(self, client):
        aroh = _apps(client, _org(client))
        assert aroh["app"] == "aroh" and aroh["name"] == "Aroh"
        assert aroh["enabled"] is False and aroh["configured"] is False
        assert aroh["seats"] == {"purchased": 0, "assigned": 0, "available": 0,
                                 "oversubscribed": False}
        assert aroh["holders"] == []
        assert aroh["usage"]["calls"] == 0
        # The box that would serve it is named, so the operator sees where.
        assert aroh["deployment"] is not None

    def test_turning_aroh_on_with_two_seats(self, client, db):
        org = _org(client)
        r = _set_app(client, org, seats=2)
        assert r.status_code == 200, r.text
        assert r.json()["enabled"] is True and r.json()["seats"] == 2

        aroh = _apps(client, org)
        assert aroh["enabled"] is True and aroh["configured"] is True
        assert aroh["seats"]["purchased"] == 2 and aroh["seats"]["available"] == 2
        with db.begin() as c:
            row = c.execute(text(
                "SELECT d.app FROM org_app a JOIN deployment d ON d.id = a.deployment_id "
                "WHERE a.organization_id = :o"), {"o": org["id"]}).one()
            placement = c.execute(text(
                "SELECT d.label FROM org_placement p JOIN deployment d "
                "ON d.id = p.deployment_id WHERE p.organization_id = :o"),
                {"o": org["id"]}).scalar_one()
            audit = c.execute(text(
                "SELECT count(*) FROM control_audit WHERE organization_id = :o "
                "AND action = 'app.set'"), {"o": org["id"]}).scalar_one()
        assert row[0] == "aroh"
        # 🔴 The org stays on its Metorite box. The Aroh box never takes it.
        assert placement == METORITE_BOX
        assert audit == 1

    def test_the_count_cannot_go_below_the_seats_in_use(self, client, aroh_key):
        org = _org(client)
        assert _set_app(client, org, seats=2).status_code == 200
        assert _take(client, aroh_key, org["owner"]).status_code == 200
        r = _set_app(client, org, seats=0)
        assert r.status_code == 409
        assert "1 seats are in use" in r.text

    def test_off_hides_the_org_and_keeps_the_seats(self, client, aroh_key):
        org = _org(client)
        _set_app(client, org, seats=2)
        assert _take(client, aroh_key, org["owner"]).status_code == 200

        assert _set_app(client, org, enabled=False, seats=2).status_code == 200
        assert _resolve(client, aroh_key, org["owner"]).json()["organizations"] == []
        aroh = _apps(client, org)
        assert aroh["enabled"] is False and aroh["seats"]["assigned"] == 1

        _set_app(client, org, enabled=True, seats=2)
        (o,) = _resolve(client, aroh_key, org["owner"]).json()["organizations"]
        assert o["seat"] == "already_held"

    def test_an_unknown_app_and_an_extra_field_are_refused(self, client):
        org = _org(client)
        r = client.post("/orgs/apps", headers=OP, json={
            "org_slug": org["slug"], "app": "nope", "enabled": True,
            "seats": 1, "reason": "why not"})
        assert r.status_code == 400
        r = client.post("/orgs/apps", headers=OP, json={
            "org_slug": org["slug"], "app": "aroh", "enabled": True, "seats": 1,
            "reason": "why not", "deployment_label": "elsewhere"})
        assert r.status_code == 422

    def test_a_locked_org_cannot_change_its_seats(self, client, db):
        org = _org(client)
        _set_org_status(db, org, "suspended")
        assert _set_app(client, org).status_code == 403

    def test_no_aroh_box_is_a_409(self, client, monkeypatch):
        from customer_console import main as main_mod

        org = _org(client)
        monkeypatch.setattr(main_mod.store, "app_deployment", lambda conn, *, app: None)
        r = _set_app(client, org)
        assert r.status_code == 409
        assert "no deployment serves the aroh app" in r.text

    def test_the_operator_frees_a_seat_with_the_staff_release(self, client, aroh_key):
        org = _org(client)
        _set_app(client, org, seats=1)
        assert _take(client, aroh_key, org["owner"]).status_code == 200
        r = client.post("/billing/seats/release", headers=OP, json={
            "org_slug": org["slug"], "email": org["owner"], "plan_slug": "aroh"})
        assert r.status_code == 200 and r.json()["released"] is True
        assert _apps(client, org)["seats"]["assigned"] == 0

    def test_the_staff_seat_door_refuses_an_app_seat(self, client):
        org = _org(client)
        _set_app(client, org, seats=1)
        r = client.post("/billing/seats", headers=OP, json={
            "org_slug": org["slug"], "email": org["owner"], "plan_slug": "aroh"})
        assert r.status_code == 400


# ── Resolve on the Aroh box ──────────────────────────────────────────────────

class TestResolveOnTheArohBox:
    def test_a_member_of_an_entitled_org_resolves_with_the_aroh_seat_state(
            self, client, aroh_key):
        org = _org(client)
        member = _member(client, org, "ada")
        _set_app(client, org, seats=2)

        body = _resolve(client, aroh_key, member).json()
        assert body["app"] == "aroh"
        (o,) = body["organizations"]
        # 📌 How the hub learns the org: it is the answer, never the request.
        assert o["organization_id"] == org["id"] and o["slug"] == org["slug"]
        assert o["seat"] == "not_allocated"
        assert o["app_seats"] == {"purchased": 2, "assigned": 0, "available": 2}
        assert o["capabilities"]["use_ai"] is True

    def test_resolve_on_the_aroh_box_writes_nothing(self, client, db, aroh_key):
        """🔴 No Core seat. Signing in to Aroh is not signing in to Metorite."""
        org = _org(client)
        member = _member(client, org, "bea")
        _set_app(client, org, seats=2)
        before = _live_seats(db, org)
        assert _resolve(client, aroh_key, member).status_code == 200
        assert _live_seats(db, org) == before
        with db.begin() as c:
            status = c.execute(text(
                "SELECT m.status FROM org_membership m JOIN user_identity u "
                "ON u.id = m.user_identity_id WHERE m.organization_id = :o "
                "AND u.email = :e"), {"o": org["id"], "e": member}).scalar_one()
        assert status == "invited", "the Aroh resolve must not promote an invitee"

    def test_an_org_without_aroh_is_invisible(self, client, aroh_key):
        org = _org(client)
        assert _resolve(client, aroh_key, org["owner"]).json() == {
            "organizations": [], "app": "aroh"}

    def test_a_removed_member_is_invisible(self, client, db, aroh_key):
        org = _org(client)
        member = _member(client, org, "cy")
        _set_app(client, org)
        with db.begin() as c:
            c.execute(text(
                "UPDATE org_membership SET status = 'removed' WHERE organization_id = :o "
                "AND user_identity_id = (SELECT id FROM user_identity WHERE email = :e)"),
                {"o": org["id"], "e": member})
        assert _resolve(client, aroh_key, member).json()["organizations"] == []

    def test_a_deleted_org_is_refused_by_name(self, client, db, aroh_key):
        org = _org(client)
        _set_app(client, org)
        _set_org_status(db, org, "deleted")
        r = _resolve(client, aroh_key, org["owner"])
        assert r.status_code == 403 and "deleted" in r.text

    def test_the_metorite_box_still_resolves_through_placement(
            self, client, metorite_key):
        """Regression: a Metorite box key is untouched by the app path."""
        org = _org(client)
        _set_app(client, org)
        body = _resolve(client, metorite_key, org["owner"]).json()
        assert "app" not in body
        (o,) = body["organizations"]
        assert o["seat"] in ("allocated", "already_held")


# ── The Aroh seat ────────────────────────────────────────────────────────────

class TestTheArohSeat:
    def test_two_seats_two_members_and_the_third_is_told_they_are_full(
            self, client, aroh_key):
        org = _org(client)
        a, b, c = (_member(client, org, n) for n in ("a1", "b1", "c1"))
        _set_app(client, org, seats=2)

        r = _take(client, aroh_key, a)
        assert r.status_code == 200, r.text
        assert r.json()["seat"] == "allocated"
        assert r.json()["app_seats"] == {"purchased": 2, "assigned": 1, "available": 1}
        assert _take(client, aroh_key, b).status_code == 200

        r = _take(client, aroh_key, c)
        assert r.status_code == 409
        detail = r.json()["detail"]
        assert detail["reason"] == "seat_cap_exceeded"
        assert detail["buy_more"]["plan_slug"] == "aroh"
        assert detail["buy_more"]["purchased"] == 2
        assert detail["buy_more"]["assigned"] == 2

    def test_taking_a_held_seat_again_consumes_nothing(self, client, aroh_key):
        org = _org(client)
        _set_app(client, org, seats=1)
        assert _take(client, aroh_key, org["owner"]).json()["seat"] == "allocated"
        r = _take(client, aroh_key, org["owner"])
        assert r.status_code == 200 and r.json()["seat"] == "already_held"
        assert r.json()["app_seats"]["assigned"] == 1

    def test_an_org_without_aroh_is_refused_as_not_entitled(self, client, aroh_key):
        org = _org(client)
        r = _take(client, aroh_key, org["owner"])
        assert r.status_code == 403
        assert r.json()["detail"] == {"reason": "app_not_entitled", "app": "aroh"}

    def test_a_plain_member_cannot_take_a_seat_for_somebody_else(
            self, client, aroh_key):
        org = _org(client)
        a, b = _member(client, org, "a2"), _member(client, org, "b2")
        _set_app(client, org, seats=2)
        assert _take(client, aroh_key, b, actor=a).status_code == 403
        # The OWNER may: an active owner or admin acts on any member's seat.
        assert _take(client, aroh_key, b, actor=org["owner"]).status_code == 200

    def test_the_box_names_no_org_no_other_plan_and_no_other_source(
            self, client, aroh_key):
        org = _org(client)
        _set_app(client, org)
        assert _take(client, aroh_key, org["owner"],
                     org_slug=org["slug"]).status_code == 400
        r = client.post("/registry/seats", headers=_bearer(aroh_key), json={
            "actor_email": org["owner"], "member_email": org["owner"],
            "plan_slug": "core"})
        assert r.status_code == 400
        assert _take(client, aroh_key, org["owner"], source="center").status_code == 400
        assert _take(client, aroh_key, org["owner"], source="app").status_code == 200
        # The live hub sends the model default. The door still writes `app`.
        r = _take(client, aroh_key, org["owner"], source="alacarte")
        assert r.status_code == 200 and r.json()["seat"] == "already_held"

    def test_the_seat_the_hub_takes_is_written_with_source_app(self, client, db, aroh_key):
        org = _org(client)
        _set_app(client, org, seats=1)
        assert _take(client, aroh_key, org["owner"], source="alacarte").status_code == 200
        with db.begin() as c:
            src = c.execute(text(
                "SELECT source FROM seat_assignment WHERE organization_id = :o "
                "AND plan_slug = 'aroh' AND released_at IS NULL"), {"o": org["id"]}).scalar_one()
        assert src == "app"

    def test_releasing_frees_the_seat_for_the_next_member(self, client, aroh_key):
        org = _org(client)
        a, b = _member(client, org, "a3"), _member(client, org, "b3")
        _set_app(client, org, seats=1)
        assert _take(client, aroh_key, a).status_code == 200
        assert _take(client, aroh_key, b).status_code == 409

        r = _free(client, aroh_key, a)
        assert r.status_code == 200 and r.json()["released"] is True
        assert _take(client, aroh_key, b).status_code == 200
        assert _free(client, aroh_key, a).json()["released"] is False

    def test_a_suspended_org_cannot_take_but_can_free(self, client, db, aroh_key):
        org = _org(client)
        a, b = _member(client, org, "a4"), _member(client, org, "b4")
        _set_app(client, org, seats=2)
        assert _take(client, aroh_key, a).status_code == 200
        _set_org_status(db, org, "suspended")
        r = _take(client, aroh_key, b)
        assert r.status_code == 403 and "seats are locked" in r.text
        assert _free(client, aroh_key, a).json()["released"] is True

    def test_a_metorite_box_cannot_take_an_aroh_seat(self, client, metorite_key):
        org = _org(client)
        _set_app(client, org)
        r = _take(client, metorite_key, org["owner"])
        assert r.status_code == 400

    def test_the_holder_shows_on_the_operator_read(self, client, aroh_key):
        org = _org(client)
        member = _member(client, org, "dee")
        _set_app(client, org, seats=2)
        _take(client, aroh_key, member)
        aroh = _apps(client, org)
        assert [h["email"] for h in aroh["holders"]] == [member]
        assert aroh["seats"]["assigned"] == 1


# ── Serving through the Aroh box ─────────────────────────────────────────────

@pytest.fixture
def platform_credential(db):
    with db.begin() as c:
        c.execute(
            text("INSERT INTO provider_credential (provider, secret_enc, label) "
                 "VALUES ('deepseek', :s, 'platform') ON CONFLICT DO NOTHING"),
            {"s": router_mod.encrypt_secret("sk-provider-secret")},
        )


@pytest.fixture
def priced_card(db):
    """A fixture price for one test, removed afterwards (the seed stays at 0)."""
    tier = "tier-balanced"
    with db.begin() as c:
        c.execute(text(
            "INSERT INTO tier_rate_card (tier, task, input_credits_per_1m, "
            " output_credits_per_1m, cached_input_credits_per_1m, "
            " pricing_mode, effective_from) "
            "VALUES (:t, 'chat', 2000, 6000, 500, 'priced', now()) "
            "ON CONFLICT DO NOTHING"), {"t": tier})
    yield tier
    with db.begin() as c:
        c.execute(text(
            "DELETE FROM tier_rate_card WHERE tier = :t "
            "  AND (input_credits_per_1m <> 0 OR output_credits_per_1m <> 0)"),
            {"t": tier})


def _chat(client, key: str, member: str, *, proven: bool = True, **headers):
    h = {**_bearer(key), "X-CC-Member": member, **headers}
    if proven:
        h["X-CC-Member-Proven"] = "1"
    return client.post("/v1/chat/completions", headers=h, json={
        "model": "tier-balanced", "client_ref": f"aroh-{uuid.uuid4().hex}",
        "messages": [{"role": "user", "content": "hi"}]})


def _balance(db, org: dict) -> Decimal:
    with db.begin() as c:
        return Decimal(c.execute(text(
            "SELECT COALESCE(SUM(delta), 0) FROM credit_ledger "
            "WHERE organization_id = :o"), {"o": org["id"]}).scalar_one())


class TestServingThroughTheArohBox:
    def test_a_seated_member_is_served_and_metered_on_their_org_as_aroh(
            self, client, db, aroh_key, platform_credential):
        org = _org(client)
        member = _member(client, org, "eve")
        _set_app(client, org, seats=1)
        _take(client, aroh_key, member)

        r = _chat(client, aroh_key, member, **{"X-CC-Agent": "aroh"})
        assert r.status_code == 200, r.text
        with db.begin() as c:
            row = c.execute(text(
                "SELECT organization_id, user_email, agent, module_slug "
                "FROM usage_event WHERE organization_id = :o ORDER BY created_at DESC "
                "LIMIT 1"), {"o": org["id"]}).one()
        assert str(row[0]) == org["id"]
        assert row[1] == member
        assert row[2] == "aroh"
        assert row[3] == "aroh", "the module defaults to the app's name"

    def test_with_no_agent_header_the_call_is_still_aroh(
            self, client, db, aroh_key, platform_credential):
        org = _org(client)
        _set_app(client, org, seats=1)
        _take(client, aroh_key, org["owner"])
        assert _chat(client, aroh_key, org["owner"]).status_code == 200
        with db.begin() as c:
            agent = c.execute(text(
                "SELECT agent FROM usage_event WHERE organization_id = :o "
                "ORDER BY created_at DESC LIMIT 1"), {"o": org["id"]}).scalar_one()
        assert agent == "aroh"

    def test_the_org_s_credits_go_down_by_the_aroh_use(
            self, client, db, aroh_key, platform_credential, priced_card):
        org = _org(client)
        _set_app(client, org, seats=1)
        _take(client, aroh_key, org["owner"])
        assert client.post("/credits/grant", headers=OP, json={
            "org_slug": org["slug"], "credits": "100"}).status_code == 200
        before = _balance(db, org)

        assert _chat(client, aroh_key, org["owner"]).status_code == 200
        after = _balance(db, org)
        # 300 fresh @2/1k + 900 cached @0.5/1k + 40 out @6/1k = 1.29 credits.
        assert before - after == Decimal("1.29")

        aroh = _apps(client, org)
        assert aroh["usage"]["calls"] == 1
        assert Decimal(aroh["usage"]["credits"]) == Decimal("1.29")
        assert [m["member"] for m in aroh["usage"]["members"]] == [org["owner"]]

    def test_an_unseated_member_is_refused(self, client, aroh_key, platform_credential):
        org = _org(client)
        member = _member(client, org, "fay")
        _set_app(client, org, seats=1)
        r = _chat(client, aroh_key, member)
        assert r.status_code == 403 and "holds no aroh seat" in r.text

    def test_a_member_of_an_org_without_aroh_is_refused(self, client, aroh_key):
        org = _org(client)
        r = _chat(client, aroh_key, org["owner"])
        assert r.status_code == 403

    def test_an_unproven_member_is_refused(self, client, aroh_key):
        org = _org(client)
        _set_app(client, org, seats=1)
        _take(client, aroh_key, org["owner"])
        r = _chat(client, aroh_key, org["owner"], proven=False)
        assert r.status_code == 403 and "PROVEN" in r.text


# ── The mint CLI ─────────────────────────────────────────────────────────────

class TestTheMintCli:
    def test_mint_makes_the_aroh_key_with_exactly_its_three_capabilities(
            self, db, boxes):
        from customer_console import deployment_keys as dk

        with db.connect() as c:
            trans = c.begin()
            try:
                label = c.execute(text("SELECT label FROM deployment WHERE id = :d"),
                                  {"d": boxes["aroh"]}).scalar_one()
                made = dk.mint(c, deployment_label=label, app="aroh",
                               capabilities=dk.AROH_CAPABILITIES)
                stored = c.execute(text(
                    "SELECT capabilities, deployment_id FROM deployment_key "
                    "WHERE prefix = :p"), {"p": made.prefix}).one()
            finally:
                trans.rollback()
        assert made.token.startswith(made.prefix + "_")
        assert list(stored[0]) == ["resolve", "seat_admin", "serve"]
        assert str(stored[1]) == boxes["aroh"]

    def test_mint_registers_a_new_box_and_names_its_app(self, db):
        """Inside a rolled-back transaction, so the shared designation stays."""
        from customer_console import deployment_keys as dk

        label = f"cli-box-{uuid.uuid4().hex[:6]}"
        with db.connect() as c:
            trans = c.begin()
            try:
                c.execute(text("UPDATE deployment SET app = NULL WHERE app = 'aroh'"))
                made = dk.mint(c, deployment_label=label, base_url="https://x.invalid",
                               app="aroh", capabilities=dk.AROH_CAPABILITIES)
                app = c.execute(text("SELECT app FROM deployment WHERE label = :l"),
                                {"l": label}).scalar_one()
            finally:
                trans.rollback()
        assert made.created_deployment is True
        assert app == "aroh"

    def test_mint_refuses_an_unknown_box_and_a_second_box_for_aroh(self, db):
        from customer_console import deployment_keys as dk

        with db.connect() as c:
            trans = c.begin()
            try:
                with pytest.raises(dk.KeyRefused, match="pass --base-url"):
                    dk.mint(c, deployment_label=f"nope-{uuid.uuid4().hex[:6]}",
                            capabilities=("resolve",))
                with pytest.raises(dk.KeyRefused, match="already served"):
                    dk.mint(c, deployment_label=METORITE_BOX, app="aroh",
                            capabilities=("resolve",))
            finally:
                trans.rollback()

    def test_grant_only_grows_the_set(self, db, boxes):
        from customer_console import deployment_keys as dk
        from customer_console.keys import split_key

        with db.connect() as c:
            trans = c.begin()
            try:
                token = mint_deployment_key(c, deployment_id=boxes["metorite"])
                # split_key, never rsplit: the secret's alphabet holds "_" (keys.py).
                prefix = split_key(token)[0]
                assert dk.grant(c, prefix=prefix, capabilities=("serve", "resolve")) == [
                    "resolve", "serve"]
                with pytest.raises(dk.KeyRefused, match="no live deployment key"):
                    dk.grant(c, prefix="cc_depl_000000000000", capabilities=("serve",))
                with pytest.raises(dk.KeyRefused, match="not a deployment key"):
                    dk.grant(c, prefix="cc_live_000000000000", capabilities=("serve",))
            finally:
                trans.rollback()

    def test_main_prints_the_token_once_on_stdout(self, db, boxes):
        from customer_console import deployment_keys as dk

        with db.begin() as c:
            label = c.execute(text("SELECT label FROM deployment WHERE id = :d"),
                              {"d": boxes["metorite"]}).scalar_one()
        out, err = io.StringIO(), io.StringIO()
        rc = dk.main(["mint", "--deployment-label", label,
                      "--capabilities", "resolve", "--label", "suite"],
                     out=out, err=err, engine=db)
        assert rc == 0
        token = out.getvalue().strip()
        assert token.startswith("cc_depl_") and "\n" not in token
        assert token not in err.getvalue(), "the secret must reach stdout only"

        out, err = io.StringIO(), io.StringIO()
        assert dk.main(["mint", "--deployment-label", label, "--capabilities", "rezolve"],
                       out=out, err=err, engine=db) == 2
        assert out.getvalue() == "" and "unknown capabilities" in err.getvalue()


# ── "Connect Aroh": mint, push, rotate, disconnect (owner request 2026-10-30) ─

HUB_TOKEN = "hub-operator-token-0123456789"
HUB_URL = "https://aroh-hub.invalid"


class _MockHub:
    """A mock Aroh hub on `httpx.MockTransport`. It records each request."""

    def __init__(self) -> None:
        self.requests: list[httpx.Request] = []
        self.put_status = 200
        self.put_body: dict | None = None
        self.unreachable = False
        self.held: str | None = None

    def handler(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        if self.unreachable:
            raise httpx.ConnectError("no route", request=request)
        if request.headers.get("authorization") != f"Bearer {HUB_TOKEN}":
            return httpx.Response(401, json={"ok": False, "error": "unauthorized"})
        if request.url.path != "/hub/api/router-key":
            return httpx.Response(404, json={"ok": False})
        if request.method == "PUT":
            key = json.loads(request.content)["key"]
            if self.put_status == 200 and self.put_body is None:
                self.held = key
            body = self.put_body or {"ok": True, "kind": "deployment", "last4": key[-4:]}
            return httpx.Response(self.put_status, json=body)
        if request.method == "GET":
            return httpx.Response(200, json={
                "ok": True, "configured": self.held is not None, "kind": "deployment",
                "last4": self.held[-4:] if self.held else None})
        if request.method == "DELETE":
            self.held = None
            return httpx.Response(200, json={"ok": True})
        return httpx.Response(405)

    def calls(self, method: str) -> list[httpx.Request]:
        return [r for r in self.requests if r.method == method]


@pytest.fixture
def hub(db):
    from customer_console import app_hub

    mock = _MockHub()
    app_hub.set_transport(httpx.MockTransport(mock.handler))
    # Each test starts from no connection. Only this suite writes the table.
    with db.begin() as c:
        c.execute(text("DELETE FROM app_connection WHERE app = 'aroh'"))
    yield mock
    app_hub.set_transport(None)


def _settings(client, **body):
    return client.post("/apps/connection/settings", headers=OP,
                       json={"app": "aroh", **body})


def _connect(client, confirm: str = "aroh"):
    return client.post("/apps/connection/connect", headers=OP,
                       json={"app": "aroh", "confirm": confirm})


def _disconnect(client):
    return client.post("/apps/connection/disconnect", headers=OP,
                       json={"app": "aroh", "confirm": "aroh"})


def _view(client, probe: bool = False) -> dict:
    r = client.get(f"/apps/connection?app=aroh{'&probe=true' if probe else ''}",
                   headers=OP)
    assert r.status_code == 200, r.text
    return r.json()


def _key_live(db, prefix: str) -> bool:
    with db.begin() as c:
        return c.execute(text(
            "SELECT revoked_at IS NULL FROM deployment_key WHERE prefix = :p"),
            {"p": prefix}).scalar_one()


class TestConnectAroh:
    def test_a_fresh_view_names_the_default_url_and_no_token(self, client, hub):
        view = _view(client)
        assert view["base_url"] == "https://aroh.dev.metorite.com"
        assert view["token_set"] is False and view["connected"] is False
        assert view["capabilities"] == ["resolve", "seat_admin", "serve"]

    def test_the_token_is_stored_encrypted_and_never_returned(self, client, db, hub):
        r = _settings(client, base_url=HUB_URL + "/", operator_token=HUB_TOKEN)
        assert r.status_code == 200, r.text
        assert HUB_TOKEN not in r.text
        assert r.json()["token_set"] is True
        assert r.json()["token_last4"] == HUB_TOKEN[-4:]
        assert r.json()["base_url"] == HUB_URL, "the trailing slash is cut"
        with db.begin() as c:
            enc = c.execute(text(
                "SELECT operator_token_enc FROM app_connection WHERE app = 'aroh'"
            )).scalar_one()
            audit = c.execute(text(
                "SELECT detail::text FROM control_audit "
                "WHERE action = 'app.connection.settings' ORDER BY created_at DESC LIMIT 1"
            )).scalar_one()
        assert enc and HUB_TOKEN not in enc
        assert router_mod.decrypt_secret(enc) == HUB_TOKEN
        assert HUB_TOKEN not in audit

    def test_a_url_change_keeps_the_token(self, client, hub):
        _settings(client, base_url=HUB_URL, operator_token=HUB_TOKEN)
        view = _settings(client, base_url="https://other-hub.invalid").json()
        assert view["token_set"] is True and view["base_url"] == "https://other-hub.invalid"

    @pytest.mark.parametrize("url", ["http://aroh.example.com", "https://x.invalid/hub",
                                     "not a url"])
    def test_a_bad_url_is_refused(self, client, hub, url):
        assert _settings(client, base_url=url).status_code == 400

    def test_connect_needs_the_typed_name_and_a_token(self, client, hub):
        assert _connect(client, confirm="yes").status_code == 400
        r = _connect(client)
        assert r.status_code == 409 and "operator token" in r.text
        assert hub.requests == []

    def test_connect_mints_the_box_key_and_pushes_it(self, client, db, hub):
        _settings(client, base_url=HUB_URL, operator_token=HUB_TOKEN)
        r = _connect(client)
        assert r.status_code == 200, r.text
        body = r.json()
        assert body["ok"] is True and body["outcome"] == "connected"
        assert body["kind"] == "deployment"

        (put,) = hub.calls("PUT")
        assert str(put.url) == f"{HUB_URL}/hub/api/router-key"
        assert put.headers["authorization"] == f"Bearer {HUB_TOKEN}"
        pushed = json.loads(put.content)["key"]
        assert pushed.startswith(body["key_prefix"] + "_")
        # 🔴 The secret went to the hub and nowhere else.
        assert pushed not in r.text and pushed not in json.dumps(_view(client))

        view = _view(client)
        assert view["connected"] is True and view["key_prefix"] == body["key_prefix"]
        assert view["key_capabilities"] == ["resolve", "seat_admin", "serve"]

        # The pushed key IS the Aroh box key: resolve answers as the app box.
        org = _org(client)
        _set_app(client, org)
        assert _resolve(client, pushed, org["owner"]).json()["app"] == "aroh"

    def test_rotate_pushes_a_new_key_and_revokes_the_old_one(self, client, db, hub):
        _settings(client, base_url=HUB_URL, operator_token=HUB_TOKEN)
        first = _connect(client).json()["key_prefix"]
        r = _connect(client)
        assert r.status_code == 200 and r.json()["outcome"] == "rotated"
        second = r.json()["key_prefix"]
        assert second != first
        assert _key_live(db, second) is True
        assert _key_live(db, first) is False, "the old key must stop working"
        assert hub.held.startswith(second + "_")

    def test_a_refusing_hub_leaves_the_old_key_and_revokes_the_new_one(
            self, client, db, hub):
        _settings(client, base_url=HUB_URL, operator_token=HUB_TOKEN)
        first = _connect(client).json()["key_prefix"]

        hub.put_status, hub.put_body = 500, {"ok": False, "error": "boom"}
        r = _connect(client)
        assert r.status_code == 502
        detail = r.json()["detail"]
        assert detail["reason"] == "hub_refused" and detail["hub_status"] == 500
        assert _key_live(db, detail["key_prefix"]) is False
        assert _key_live(db, first) is True
        assert _view(client)["key_prefix"] == first

    def test_an_unreachable_hub_is_a_502_and_mints_nothing_live(self, client, db, hub):
        _settings(client, base_url=HUB_URL, operator_token=HUB_TOKEN)
        hub.unreachable = True
        r = _connect(client)
        assert r.status_code == 502 and "unreachable" in r.text
        assert _key_live(db, r.json()["detail"]["key_prefix"]) is False
        assert _view(client)["connected"] is False

    def test_disconnect_revokes_first_and_tells_the_hub(self, client, db, hub):
        _settings(client, base_url=HUB_URL, operator_token=HUB_TOKEN)
        prefix = _connect(client).json()["key_prefix"]
        r = _disconnect(client)
        assert r.status_code == 200, r.text
        assert r.json()["revoked"] is True and r.json()["revoked_prefix"] == prefix
        assert r.json()["hub"]["ok"] is True
        assert len(hub.calls("DELETE")) == 1 and hub.held is None
        assert _key_live(db, prefix) is False
        view = _view(client)
        assert view["connected"] is False and view["key_prefix"] is None

    def test_disconnect_revokes_even_when_the_hub_is_down(self, client, db, hub):
        _settings(client, base_url=HUB_URL, operator_token=HUB_TOKEN)
        prefix = _connect(client).json()["key_prefix"]
        hub.unreachable = True
        r = _disconnect(client)
        assert r.status_code == 200 and "unreachable" in r.json()["hub"]["error"]
        assert _key_live(db, prefix) is False

    def test_the_probe_reads_the_hub_status_only(self, client, hub):
        _settings(client, base_url=HUB_URL, operator_token=HUB_TOKEN)
        _connect(client)
        view = _view(client, probe=True)
        assert view["hub"]["hub_status"] == 200 and view["hub"]["configured"] is True
        assert len(hub.calls("GET")) == 1

    def test_no_secret_reaches_a_log_line(self, client, hub, caplog):
        caplog.set_level("DEBUG")
        _settings(client, base_url=HUB_URL, operator_token=HUB_TOKEN)
        _connect(client)
        pushed = json.loads(hub.calls("PUT")[0].content)["key"]
        logged = "\n".join(
            f"{rec.getMessage()} {rec.__dict__}" for rec in caplog.records)
        assert HUB_TOKEN not in logged and pushed not in logged


# ── Where this suite is named ────────────────────────────────────────────────

class TestTheSuiteIsWired:
    def test_this_suite_is_named_in_the_ci_skip_guard(self):
        workflow = (_ROOT / ".github/workflows/pr-check.yml").read_text(encoding="utf-8")
        assert "tests/unit/test_customer_console_aroh_app.py" in workflow

    def test_this_suite_is_named_in_the_owning_spec(self):
        spec = (_ROOT / "project-docs/specs/customer_console.md").read_text(
            encoding="utf-8")
        assert "tests/unit/test_customer_console_aroh_app.py" in spec
