"""The organization key arrives WITH the organization, on the operator arm.

Spec: ``project-docs/specs/customer_console.md`` §4.3 / CP-3 (what the key is)
· ``saas_multitenancy.md`` §11 MT-1j slice 4 (what provisioning writes).

🔴 **`hathi-labs-llp` was provisioned and ran for weeks with ZERO keys.**
A customer's deployment presents its organization key to the Router on every AI
call, so an organization without one can never be served and never be billed.
Nothing failed loudly. The organization simply existed and could not buy
anything, and the owner found it by hand. Measured 2026-09-21.

A key an operator must remember to mint is a step that gets skipped, and its
absence is silent. So the key is now a property of an organization existing,
exactly like its placement, its seats and its trial subscription — every one of
which ``POST /orgs/provision`` already writes for the same reason.

⚠️ **The DEPLOYMENT-KEY arm mints nothing, and that is the subject of half
this file.** That arm is driven by an unauthenticated signup form. Handing a
credential back through it would make account creation a credential-issuing
endpoint for anybody who can post to it. Minting one and withholding it would
be worse than useless: the gateway reads ONE ``CUSTOMER_CONSOLE_ORG_KEY`` from
its environment, so a shared box has one slot and N tenants, and a key for
tenant N+1 has nowhere to live. The real fix for self-serve is the
deployment-key arm on the Router doors, which ``GET /seats/overview`` already
proved out for seats (D-SEAT-4).

R8: a real Postgres, because the guard is an ``EXISTS`` over a partial
condition and the idempotence is a second HTTP call against committed state.
A fake agrees with whatever SQL it is handed.

Run::

    eval "$(bash scripts/dev_db.sh --export)"
    uv run pytest tests/unit/test_provision_mints_the_key.py -v -rs
"""
from __future__ import annotations

import os
import uuid

import pytest

pytest.importorskip("fastapi")
from customer_console.keys import split_key
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
def _box():
    eng = create_engine(_URL, future=True)
    with eng.begin() as conn:
        ensure_deployment(conn)
    eng.dispose()


def _provision(client, slug: str, **over):
    body = {
        "slug": slug,
        "name": "N",
        "owner_email": f"owner@{slug}.example",
        "core_seats": 3,
        "deployment_label": DEFAULT_DEPLOYMENT_LABEL,
    }
    body.update(over)
    return client.post("/orgs/provision", headers=OP, json=body)


def _slug() -> str:
    return f"mint-{uuid.uuid4().hex[:8]}"


def _live_keys(db, slug: str) -> list[str]:
    with db.begin() as conn:
        return [
            r[0]
            for r in conn.execute(
                text(
                    "SELECT k.prefix FROM llm_api_key k "
                    "JOIN organization o ON o.id = k.organization_id "
                    "WHERE o.slug = :s AND k.revoked_at IS NULL"
                ),
                {"s": slug},
            )
        ]


# ── The operator arm ────────────────────────────────────────────────────────


class TestProvisioningMintsTheKey:
    def test_a_new_organization_holds_a_LIVE_key(self, client, db):
        """🔴 The whole defect. This was zero for `hathi-labs-llp`."""
        slug = _slug()
        assert _provision(client, slug).status_code == 200
        assert len(_live_keys(db, slug)) == 1, (
            "an organization with no key can never be served by the Router "
            "and never billed, and nothing anywhere says so")

    def test_the_token_comes_back_ONCE_and_it_works(self, client):
        """A minted secret nobody receives is theatre.

        Only the hash is stored, so the response is the only moment the secret
        exists anywhere. The proof it is the real credential is that it opens
        the organization key's own door.
        """
        slug = _slug()
        body = _provision(client, slug).json()
        token = body["key"]["token"]

        me = client.get("/me", headers={"Authorization": f"Bearer {token}"})
        assert me.status_code == 200, me.text
        assert me.json()["slug"] == slug

    def test_the_response_carries_the_prefix_beside_the_token(self, client):
        """The prefix is what every later surface names the key by — the
        operator list, the revoke call, the audit trail. Returning the token
        alone would make the operator split it by hand."""
        body = _provision(client, _slug()).json()
        assert body["key"]["prefix"] == split_key(body["key"]["token"])[0]

    def test_a_RE_provision_mints_no_SECOND_key(self, client, db):
        """⚠️ Provisioning is re-run BY DESIGN — a retrying form, an operator
        correcting a name. An unguarded mint would leave a pile of live
        credentials nobody asked for and nobody can tell apart."""
        slug = _slug()
        first = _provision(client, slug).json()["key"]["prefix"]

        again = _provision(client, slug)
        assert again.status_code == 200, again.text
        assert "key" not in again.json(), (
            "a re-provision answered with a credential, so every retry of a "
            "signup form would issue another one")
        assert _live_keys(db, slug) == [first]

    def test_a_REVOKED_key_is_not_a_key_so_a_re_provision_re_arms(self, client, db):
        """⚠️ The guard is on LIVE keys. Treating a revoked key as "already
        has one" would leave an organization that rotated its credential
        permanently unable to be re-armed."""
        slug = _slug()
        first = _provision(client, slug).json()["key"]["prefix"]
        assert client.post(
            "/keys/revoke", headers=OP,
            json={"org_slug": slug, "prefix": first},
        ).json()["revoked"] is True

        second = _provision(client, slug)
        assert "key" in second.json(), (
            "an organization whose only key was revoked stayed dark through a "
            "re-provision")
        assert _live_keys(db, slug) == [second.json()["key"]["prefix"]]

    def test_the_mint_is_AUDITED_by_prefix_and_never_by_token(self, client, db):
        """The same act name `POST /keys` writes, so the key trail is one
        story rather than two — and the row carries the prefix alone."""
        slug = _slug()
        token = _provision(client, slug).json()["key"]["token"]
        prefix = split_key(token)[0]

        with db.begin() as conn:
            rows = [
                (r[0], r[1])
                for r in conn.execute(
                    text(
                        "SELECT a.action, a.detail::text FROM control_audit a "
                        "JOIN organization o ON o.id = a.organization_id "
                        "WHERE o.slug = :s"
                    ),
                    {"s": slug},
                )
            ]
        issued = [d for act, d in rows if act == "key.issue"]
        assert len(issued) == 1, f"expected one key.issue row, got {rows}"
        assert prefix in issued[0]
        secret = token.split("_")[-1]
        assert secret not in issued[0], "the audit trail recorded the SECRET"


# ── The deployment-key arm mints nothing ────────────────────────────────────


class TestTheSelfServeArmIssuesNoCredential:
    def test_the_deployment_key_arm_gets_no_key_back(self, client, db):
        """⚠️ That arm is driven by an UNAUTHENTICATED signup form.

        Returning a credential through it would make account creation a
        credential-issuing endpoint for anybody who can post to it. A
        self-serve organization is armed by the deployment-key arm on the
        Router doors instead — recorded as a handoff, not faked here.
        """
        with db.begin() as conn:
            dep = conn.execute(
                text("SELECT id FROM deployment WHERE label = :l"),
                {"l": DEFAULT_DEPLOYMENT_LABEL},
            ).scalar_one()
        from customer_console import store
        from customer_console.keys import mint_key

        minted = mint_key(env="depl")
        with db.begin() as conn:
            store.issue_deployment_key(
                conn,
                deployment_id=str(dep),
                prefix=minted.prefix,
                key_hash=minted.key_hash,
                capabilities=["provision"],
            )

        slug = _slug()
        r = client.post(
            "/orgs/provision",
            headers={"Authorization": f"Bearer {minted.token}"},
            json={
                "slug": slug,
                "name": "N",
                "owner_email": f"owner@{slug}.example",
                "core_seats": 3,
            },
        )
        assert r.status_code == 200, r.text
        assert "key" not in r.json()
        assert _live_keys(db, slug) == [], (
            "the self-serve arm minted a credential that has nowhere to live: "
            "the gateway reads ONE CUSTOMER_CONSOLE_ORG_KEY per box")
