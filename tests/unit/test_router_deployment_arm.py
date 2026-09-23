"""A per-box deployment key can serve AI. H-152.

Spec: ``customer_console.md`` §6 CP-2b (the dual-arm door) · D-SEAT-4 (the same
move, one plane over) · ``user_management_contract.md`` R11 (the caller makes
no tenant claim).

🔴 **A self-serve customer could never be served AI, and nothing said so.**
The gateway presents one ``CUSTOMER_CONSOLE_ORG_KEY`` from its environment, and
that names ONE tenant. A shared box therefore has one slot and N tenants: it
serves tenant one and is dark for every other, without failing.

Minting more organization keys does not help. There is one environment slot, so
a key for tenant N+1 has nowhere to live. ``gateway/routes/seats.py`` already
records this shape in its own words — *"On a shared multi-tenant deployment no
single org key is correct ... a STRUCTURAL dark, not a missing flag flip."*

The **deployment key is per-BOX**, so it works for every tenant placed on it,
and the Console derives the organization from placement ∩ membership. That is
exactly what ``GET /seats/overview`` does (D-SEAT-4).

⚠️ **R8, and a fake would be worse than nothing here.** Every subject below is
a JOIN across `org_membership`, `org_placement` and `organization`, or a
lifecycle gate keyed on a column. A fake agrees with whatever SQL it is handed.

Run::

    eval "$(bash scripts/dev_db.sh --export)"
    uv run pytest tests/unit/test_router_deployment_arm.py -v -rs
"""
from __future__ import annotations

import os
import uuid

import pytest

pytest.importorskip("fastapi")
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, text

from tests.unit._customer_console_ladder import (  # noqa: E402
    DEFAULT_DEPLOYMENT_LABEL,
    apply_ladder,
    ensure_deployment,
)

_URL = os.environ.get("CUSTOMER_CONSOLE_DATABASE_URL", "").strip()

pytestmark = pytest.mark.skipif(
    not _URL,
    reason=(
        "CUSTOMER_CONSOLE_DATABASE_URL unset — R8 requires a REAL Postgres. "
        "A skip here is not a pass; CI must set it."
    ),
)

TOKEN = "test-operator-token"
OP = {"Authorization": f"Bearer {TOKEN}"}


@pytest.fixture(scope="module", autouse=True)
def _schema():
    eng = create_engine(_URL, future=True)
    with eng.begin() as conn:
        apply_ladder(conn)
    eng.dispose()


@pytest.fixture
def client(monkeypatch):
    monkeypatch.setenv("CUSTOMER_CONSOLE_OPERATOR_TOKEN", TOKEN)
    from customer_console.main import app

    return TestClient(app)


@pytest.fixture
def db():
    eng = create_engine(_URL, future=True)
    yield eng
    eng.dispose()


@pytest.fixture(autouse=True)
def _box(db):
    with db.begin() as conn:
        ensure_deployment(conn)


def _deployment_id(db) -> str:
    with db.begin() as conn:
        return str(
            conn.execute(
                text("SELECT id FROM deployment WHERE label = :l"),
                {"l": DEFAULT_DEPLOYMENT_LABEL},
            ).scalar_one()
        )


def _mint_deployment_key(db, *, capabilities: list[str]) -> str:
    """A real deployment key on the shared box, with a chosen capability set."""
    from customer_console import store
    from customer_console.keys import mint_key

    minted = mint_key(env="depl")
    with db.begin() as conn:
        store.issue_deployment_key(
            conn,
            deployment_id=_deployment_id(db),
            prefix=minted.prefix,
            key_hash=minted.key_hash,
            capabilities=capabilities,
        )
    return minted.token


def _provision(client, *, owner: str) -> str:
    slug = f"depl-{uuid.uuid4().hex[:8]}"
    r = client.post(
        "/orgs/provision",
        headers=OP,
        json={
            "slug": slug,
            "name": "N",
            "owner_email": owner,
            "core_seats": 2,
            "deployment_label": DEFAULT_DEPLOYMENT_LABEL,
        },
    )
    assert r.status_code == 200, r.text
    return slug


def _org_id(db, slug: str) -> str:
    with db.begin() as conn:
        return str(
            conn.execute(
                text("SELECT id FROM organization WHERE slug = :s"), {"s": slug}
            ).scalar_one()
        )


def _chat(client, token: str, *, member: str | None):
    headers = {"Authorization": f"Bearer {token}"}
    if member is not None:
        headers["X-CC-Member"] = member
    return client.post(
        "/v1/chat/completions",
        headers=headers,
        json={"tier": "tier-fast", "messages": [{"role": "user", "content": "hi"}]},
    )


class TestTheDeploymentArmResolvesTheTenant:
    def test_a_deployment_key_and_a_member_REACH_the_router(self, client, db):
        """🔴 The whole point. Before this, only the one org named by
        `CUSTOMER_CONSOLE_ORG_KEY` could ever be served.

        ⚠️ This asserts the door OPENS, not that a completion happens — there
        is no vendor credential here, so the call fails downstream. What
        matters is that it fails PAST authentication: anything but 401/403/400
        proves the tenant resolved.
        """
        owner = f"owner-{uuid.uuid4().hex[:6]}@example.com"
        _provision(client, owner=owner)
        token = _mint_deployment_key(db, capabilities=["resolve", "serve"])

        r = _chat(client, token, member=owner)
        assert r.status_code not in (400, 401, 403, 409), (
            f"the deployment arm never resolved the tenant: {r.status_code} {r.text}")

    def test_it_resolves_the_MEMBER_S_org_and_not_some_other_one(self, client, db):
        """⚠️ Two tenants on one box is the case the whole entry is about.

        The usage has to land on the member's own organization. Picking the
        wrong one bills a stranger.
        """
        from customer_console import auth

        a_owner = f"a-{uuid.uuid4().hex[:6]}@example.com"
        b_owner = f"b-{uuid.uuid4().hex[:6]}@example.com"
        a_slug = _provision(client, owner=a_owner)
        b_slug = _provision(client, owner=b_owner)
        token = _mint_deployment_key(db, capabilities=["serve"])

        for owner, slug in ((a_owner, a_slug), (b_owner, b_slug)):
            caller = auth.organization_from_key_or_deployment(
                request=None,  # type: ignore[arg-type]
                authorization=f"Bearer {token}",
                x_cc_member=owner,
            )
            assert caller.organization_id == _org_id(db, slug), (
                f"{owner} resolved to the wrong tenant")
            # The DEPLOYMENT key's prefix, because that is what acted.
            assert caller.key_prefix.startswith("cc_depl_")
            assert caller.member == owner


class TestWhatItRefuses:
    def test_no_member_header_is_a_400_and_never_a_GUESS(self, client, db):
        """⚠️ D46.6 item 3 forbids a `count(*) = 1` inference by name. With one
        org on the box the wrong answer would look right, which is why the
        refusal is on the SHAPE and never on the count."""
        _provision(client, owner=f"solo-{uuid.uuid4().hex[:6]}@example.com")
        token = _mint_deployment_key(db, capabilities=["serve"])

        r = _chat(client, token, member=None)
        assert r.status_code == 400
        assert "X-CC-Member" in r.json()["detail"]

    def test_a_key_without_SERVE_is_403_and_names_the_capability(self, client, db):
        """The column default is `{resolve}`. A key that may resolve a sign-in
        must not thereby be able to spend a tenant's credits."""
        _provision(client, owner=f"c-{uuid.uuid4().hex[:6]}@example.com")
        token = _mint_deployment_key(db, capabilities=["resolve"])

        r = _chat(client, token, member="c@example.com")
        assert r.status_code == 403
        assert "serve" in r.json()["detail"]

    def test_an_unknown_member_is_403_that_names_NOTHING(self, client, db):
        """⚠️ An existence oracle over every tenant on the box.

        "No such member" told apart from "not on this deployment" reads out
        the membership of every organization placed here, one probe at a time.
        """
        token = _mint_deployment_key(db, capabilities=["serve"])
        r = _chat(client, token, member=f"ghost-{uuid.uuid4().hex}@example.com")
        assert r.status_code == 403
        body = r.json()["detail"]
        assert body == "no organization for this member"

    def test_a_member_in_TWO_orgs_is_409_and_the_Console_picks_neither(
        self, client, db
    ):
        """⚠️ The same answer `/registry/seats/overview` gives. Choosing would
        bill one of two real tenants, and a coin flip is not a billing
        decision."""
        shared = f"both-{uuid.uuid4().hex[:6]}@example.com"
        a_slug = _provision(client, owner=shared)
        b_slug = _provision(client, owner=f"other-{uuid.uuid4().hex[:6]}@ex.com")
        # Put the same identity in the second org too.
        with db.begin() as conn:
            conn.execute(
                text(
                    "INSERT INTO org_membership "
                    "  (organization_id, user_identity_id, role, status, joined_at) "
                    "SELECT :b, ui.id, 'member', 'active', now() "
                    "FROM user_identity ui WHERE lower(ui.email) = lower(:e) "
                    "ON CONFLICT DO NOTHING"
                ),
                {"b": _org_id(db, b_slug), "e": shared},
            )
        assert a_slug != b_slug

        token = _mint_deployment_key(db, capabilities=["serve"])
        r = _chat(client, token, member=shared)
        assert r.status_code == 409, r.text
        assert "more than one organization" in r.json()["detail"]

    def test_a_SUSPENDED_org_is_refused_through_this_door_too(self, client, db):
        """🔴 F4's defect, reachable through a second door.

        The organization-key arm 403s a suspended tenant. A deployment arm
        that did not would be a way around the lifecycle gate, and it would
        be the cheapest one in the system.
        """
        owner = f"susp-{uuid.uuid4().hex[:6]}@example.com"
        slug = _provision(client, owner=owner)
        token = _mint_deployment_key(db, capabilities=["serve"])
        # ⚠️ **Restored in a `finally`, because this database is SHARED.**
        # A suspended organization left behind is a row every later suite in
        # the run has to be indifferent to, and the one that is not fails
        # somewhere else entirely. That is the shape H-160 cost a deploy.
        try:
            with db.begin() as conn:
                conn.execute(
                    text(
                        "UPDATE organization SET status = 'suspended' "
                        "WHERE slug = :s"
                    ),
                    {"s": slug},
                )
            r = _chat(client, token, member=owner)
            assert r.status_code == 403
            assert "suspended" in r.json()["detail"]
        finally:
            with db.begin() as conn:
                conn.execute(
                    text("UPDATE organization SET status = 'active' WHERE slug = :s"),
                    {"s": slug},
                )


class TestTheORGANIZATION_ARM_IS_UNCHANGED:
    def test_a_bad_token_still_gets_the_organization_arm_s_401(self, client):
        """⚠️ Shape dispatch, never a fallback ladder. A malformed token is
        refused as an organization key and is not retried as a deployment
        one — the property `test_a_key_shaped_operator_token_is_still_not_a_
        key` already pins for the sibling doors."""
        r = _chat(client, "not-a-key-at-all", member="x@example.com")
        assert r.status_code == 401
        assert r.json()["detail"] == "Invalid API key"

    def test_an_ORG_key_still_opens_the_door_with_no_member_header(
        self, client, db
    ):
        """The organization arm never needed `X-CC-Member` — the org is a
        property of that credential — and the new arm must not impose its own
        requirement on it."""
        slug = _provision(client, owner=f"ok-{uuid.uuid4().hex[:6]}@example.com")
        r = client.post("/keys", headers=OP, json={"org_slug": slug})
        assert r.status_code == 200, r.text
        org_token = r.json()["token"]

        out = _chat(client, org_token, member=None)
        assert out.status_code not in (400, 401, 403, 409), (
            f"the organization arm changed behaviour: {out.status_code} {out.text}")
