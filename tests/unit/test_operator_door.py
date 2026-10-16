"""The gateway operator door (CP-2g) — the two-token ladder, on the REAL app.

Review round 1's P0: the first draft of this suite built a bare
``FastAPI(); include_router(router)`` and was green while the real gateway —
whose APP-LEVEL ``require_authenticated`` dependency consumes the
``Authorization`` header — refused every call before the door's own gate ran.
A fence that tests a different app is not a fence. Every test here drives
``gateway.main.app``, so the app-level gate, the router mounting (past
``main.py``'s ``except Exception: pass`` include idiom) and the door's own
ladder are all in the measured path.

The design under test: BOTH tokens, always —
``Authorization: Bearer <GATEWAY_INTERNAL_TOKEN>`` clears the app-level gate,
``X-Operator-Token: <GATEWAY_OPERATOR_TOKEN>`` clears the door. The purge's
own semantics are R8-fenced in ``test_org_purge_tenant.py``; here the purge is
monkeypatched and the ladder is the subject: missing app auth → 401 (app
gate), operator token unset → 503 (ship-dark), wrong operator token → 401,
mismatched confirm → 400, purge failure → 502 carrying the cause — and every
refusal happens BEFORE the purge runs.
"""
from __future__ import annotations

import pytest

pytest.importorskip("fastapi")

import acb_auth.offboard as offboard
from fastapi.testclient import TestClient

INTERNAL = "test-internal-token"
OPERATOR = "test-gateway-operator-token"


@pytest.fixture
def calls(monkeypatch):
    """Record purge invocations; answer a canned receipt."""
    made: list[str] = []

    async def fake_purge(*, slug: str, before_delete=None):
        made.append(slug)
        return {
            "slug": slug,
            "already_absent": False,
            "deleted": {"organization": 1},
        }

    monkeypatch.setattr(offboard, "purge_tenant_organization", fake_purge)
    return made


@pytest.fixture
def client(monkeypatch, calls):
    monkeypatch.setenv("GATEWAY_INTERNAL_TOKEN", INTERNAL)
    monkeypatch.setenv("GATEWAY_OPERATOR_TOKEN", OPERATOR)
    from gateway.main import app

    return TestClient(app)


def _delete(client, slug="acme", *, confirm=None, bearer=INTERNAL,
            operator=OPERATOR):
    headers = {}
    if bearer is not None:
        headers["Authorization"] = f"Bearer {bearer}"
    if operator is not None:
        headers["X-Operator-Token"] = operator
    return client.delete(
        f"/internal/operator/organizations/{slug}",
        params={"confirm": slug if confirm is None else confirm},
        headers=headers,
    )


class TestTheTwoTokenLadder:
    def test_the_door_is_mounted_and_reachable_on_the_real_app(self, client):
        """The P0 fence: the route resolves on gateway.main.app (not a 404,
        which is what a failed import under the include's bare except would
        produce) and the door's OWN voice answers once both tokens present."""
        r = _delete(client, "hathilabs")
        assert r.status_code == 200, r.text

    def test_without_the_app_level_bearer_the_gateway_refuses_first(
        self, client, calls
    ):
        """The internal token is REQUIRED: the operator token alone must not
        bypass the gateway's ordinary machine auth."""
        r = _delete(client, bearer=None)
        assert r.status_code == 401, r.text
        # The refusal is the APP gate's, not the door's — the door never ran.
        assert "operator token" not in r.json()["detail"]
        assert calls == []

    def test_the_internal_token_alone_is_not_an_org_destroy_credential(
        self, client, calls
    ):
        """The other half of the two-token argument: a caller holding only
        the gateway's ordinary bearer (whose unprovisioned-box fallback is
        LITELLM_MASTER_KEY — a credential agents hold) is refused by the
        door's own gate."""
        assert _delete(client, operator=None).status_code == 401
        assert _delete(client, operator="not-it").status_code == 401
        assert calls == []

    def test_ship_dark_an_unset_operator_token_answers_503(
        self, client, monkeypatch, calls
    ):
        monkeypatch.delenv("GATEWAY_OPERATOR_TOKEN", raising=False)
        r = _delete(client)
        assert r.status_code == 503, r.text
        assert calls == []

    def test_a_mismatched_confirm_is_400_and_purges_nothing(self, client, calls):
        r = _delete(client, confirm="other")
        assert r.status_code == 400, r.text
        assert calls == []

    def test_a_failing_purge_is_a_502_that_carries_the_cause(
        self, client, monkeypatch
    ):
        async def boom(*, slug: str, before_delete=None):
            raise RuntimeError("the database went away")

        monkeypatch.setattr(offboard, "purge_tenant_organization", boom)
        r = _delete(client)
        assert r.status_code == 502, r.text
        assert "the database went away" in r.json()["detail"]


class TestTheHappyPath:
    def test_the_receipt_is_relayed_and_the_slug_reaches_the_purge(
        self, client, calls
    ):
        r = _delete(client, "hathilabs")
        assert r.status_code == 200, r.text
        assert r.json()["deleted"] == {"organization": 1}
        assert calls == ["hathilabs"]


# ════════════════════════════════════════════════════════════════════════════
# The member door (owner request, 2026-10-15)
#
# `DELETE /internal/operator/organizations/{slug}/members/{email}` removes or
# purges ONE member. It runs on the REAL app for the reason the module
# docstring gives. The tenant session is the shared admin fake, so the route
# runs the SAME `remove_member_rows` / `purge_member_rows` bodies the admin
# routes run. Their SQL is fenced in the admin suites.
# ════════════════════════════════════════════════════════════════════════════

from contextlib import asynccontextmanager  # noqa: E402

from tests.unit._admin_fakes import ORG, _FakeDB, bind_admin_db  # noqa: E402

PRIYA = "priya@fracktal.in"


@pytest.fixture
def member_world(monkeypatch):
    """One org (slug ``acme``) with an owner and a colleague, on the fake."""
    from gateway.routes import operator as op
    from gateway.routes.admin import members

    fake = _FakeDB()
    bind_admin_db(monkeypatch, fake, (members, op))
    fake.seed_user("u-owner", "owner@fracktal.in", joined_at="then")
    fake.user_roles["u-owner"] = ["owner"]
    fake.seed_user("u-priya", PRIYA, joined_at="then")
    fake.user_roles["u-priya"] = ["member"]

    world = {"db": fake, "slugs": [], "bound": [], "mirrors": [],
             "shadow_purges": [], "console": []}

    async def _org_for(slug):
        world["slugs"].append(slug)
        return ORG if slug == "acme" else None

    monkeypatch.setattr(offboard, "organization_id_for_slug", _org_for)

    @asynccontextmanager
    async def _bound(organization_id=None):
        world["bound"].append(organization_id)
        yield fake
        await fake.commit()

    monkeypatch.setattr(op, "_tenant_session", _bound)

    async def _mirror(**kw):
        world["mirrors"].append(kw)

    async def _shadow(**kw):
        world["shadow_purges"].append(kw)

    monkeypatch.setattr(op, "mirror_membership_status", _mirror)
    monkeypatch.setattr(op, "purge_identity_shadow", _shadow)

    # The door must not call the Customer Console. Record any call so a test
    # can prove that none happened.
    async def _console(**kw):
        world["console"].append(kw)
        return 200, {}

    monkeypatch.setattr(members, "remove_member_on_console", _console)
    return world


def _member_delete(client, slug="acme", email=PRIYA, *, confirm=None,
                   mode=None, bearer=INTERNAL, operator=OPERATOR):
    headers = {}
    if bearer is not None:
        headers["Authorization"] = f"Bearer {bearer}"
    if operator is not None:
        headers["X-Operator-Token"] = operator
    params = {"confirm": email if confirm is None else confirm}
    if mode is not None:
        params["mode"] = mode
    return client.delete(
        f"/internal/operator/organizations/{slug}/members/{email}",
        params=params, headers=headers,
    )


class TestTheMemberDoorLadder:
    """The same two tokens as the org purge, then confirm, then mode."""

    def test_without_the_app_level_bearer_the_gateway_refuses_first(
        self, client, member_world
    ):
        r = _member_delete(client, bearer=None)
        assert r.status_code == 401, r.text
        assert "operator token" not in r.json()["detail"]
        assert member_world["slugs"] == []

    def test_the_internal_token_alone_is_refused_by_the_door(
        self, client, member_world
    ):
        assert _member_delete(client, operator=None).status_code == 401
        assert _member_delete(client, operator="not-it").status_code == 401
        assert member_world["slugs"] == []

    def test_ship_dark_an_unset_operator_token_answers_503(
        self, client, member_world, monkeypatch
    ):
        monkeypatch.delenv("GATEWAY_OPERATOR_TOKEN", raising=False)
        r = _member_delete(client)
        assert r.status_code == 503, r.text
        assert member_world["slugs"] == []

    @pytest.mark.parametrize("confirm", [
        "someone@else.in", "PRIYA@fracktal.in", "priya@fracktal.in ",
    ])
    def test_confirm_must_equal_the_email_verbatim(
        self, client, member_world, confirm
    ):
        r = _member_delete(client, confirm=confirm)
        assert r.status_code == 400, r.text
        assert "confirm must equal the member email" in r.json()["detail"]
        assert member_world["db"].users["u-priya"]["status"] == "active"
        assert member_world["slugs"] == []

    def test_an_unknown_mode_is_400_and_touches_nothing(
        self, client, member_world
    ):
        r = _member_delete(client, mode="erase")
        assert r.status_code == 400, r.text
        assert member_world["slugs"] == []
        assert member_world["db"].committed == 0

    def test_an_unknown_org_is_404_with_a_detail(self, client, member_world):
        r = _member_delete(client, slug="nowhere")
        assert r.status_code == 404, r.text
        assert "nowhere" in r.json()["detail"]
        assert member_world["bound"] == []


class TestTheMemberDoorActs:
    def test_remove_is_the_default_and_runs_the_shared_body(
        self, client, member_world
    ):
        r = _member_delete(client)
        assert r.status_code == 200, r.text
        db = member_world["db"]
        # The count is what the seal UPDATE reported (the fake answers 1).
        # The seal itself is the shared body's, and it reached Priya's tree.
        assert r.json() == {"status": "removed", "email": PRIYA,
                            "tree_projects": 1}
        assert [s["action"] for s in db.sealed] == ["seal"]
        assert db.users["u-priya"]["status"] == "removed"
        assert db.user_roles["u-priya"] == []
        assert db.committed == 1
        # The session was bound to the org the SLUG named, never a header.
        assert member_world["bound"] == [ORG]
        assert member_world["mirrors"] == [
            {"email": PRIYA, "org_id": ORG, "status": "removed"}]
        assert PRIYA in db.invalidated
        assert db.audit == [("org.member_removed", f"user:{PRIYA}")]
        # The BFF tells the Console. The gateway door does not.
        assert member_world["console"] == []

    def test_purge_deletes_the_member_and_answers_the_purge_shape(
        self, client, member_world
    ):
        r = _member_delete(client, mode="purge")
        assert r.status_code == 200, r.text
        body = r.json()
        assert body["status"] == "purged"
        assert body["email"] == PRIYA
        assert body["deleted"]["member_record"] == 1
        assert "kept" in body
        db = member_world["db"]
        assert "u-priya" not in db.users
        assert db.committed == 1
        # The audit entry is written BEFORE the commit, by the shared body.
        assert db.audit_at_commit == [1]
        assert db.audit == [("org.member_purged", f"user:{PRIYA}")]
        assert member_world["shadow_purges"] == [
            {"email": PRIYA, "org_id": ORG}]
        assert PRIYA in db.invalidated
        assert member_world["console"] == []

    @pytest.mark.parametrize("mode", ["remove", "purge"])
    def test_an_absent_member_is_200_already_absent(
        self, client, member_world, mode
    ):
        email = "ghost@fracktal.in"
        r = _member_delete(client, email=email, mode=mode)
        assert r.status_code == 200, r.text
        assert r.json() == {"already_absent": True, "email": email}
        assert member_world["mirrors"] == []
        assert member_world["shadow_purges"] == []
        assert member_world["db"].audit == []

    @pytest.mark.parametrize("mode", ["remove", "purge"])
    def test_the_last_owner_survives_with_a_409(
        self, client, member_world, mode
    ):
        """Invariant 1, from the shared body. The operator is not a member,
        so no self-lockout guard can fire here: only this one."""
        r = _member_delete(client, email="owner@fracktal.in", mode=mode)
        assert r.status_code == 409, r.text
        assert "no owner" in r.json()["detail"]
        db = member_world["db"]
        assert db.users["u-owner"]["status"] == "active"
        assert db.user_roles["u-owner"] == ["owner"]
        assert db.committed == 0
        assert db.audit == []
        assert member_world["mirrors"] == []

    def test_a_second_owner_may_be_removed_by_the_operator(
        self, client, member_world
    ):
        db = member_world["db"]
        db.seed_user("u-owner2", "second@fracktal.in", joined_at="then")
        db.user_roles["u-owner2"] = ["owner"]
        r = _member_delete(client, email="second@fracktal.in")
        assert r.status_code == 200, r.text
        assert db.users["u-owner2"]["status"] == "removed"
