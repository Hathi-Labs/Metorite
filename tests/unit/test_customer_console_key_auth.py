"""CP-3 — the key resolves the tenant, and it may not move the ledger.

Spec: ``project-docs/specs/customer_console.md`` §4.3 / CP-3 ·
``user_management_contract.md`` R11 · D32.3.

Acceptance, verbatim: *"a request bearing org A's key and a forged
``X-CC-Member``/``X-CC-Org`` header for org B writes its ``usage_event`` under
**org A**; a revoked key 401s; the key secret is never logged (assert over the
log record, not by reading the code)."*

**This file was rewritten after independent verification returned FAIL.** The
first version put ``/usage/record`` under organization-key auth, which produced
a customer-reachable credit-minting endpoint (a negative ``billed_credits``
became a positive ledger delta) and made the metered party the reporter of its
own usage. The classes below pin three separate properties:

  * the key **resolves** the organization and no input can override it;
  * the key is **read-only** — it reaches ``/me`` and nothing that writes;
  * the meter is written only by the Router's internal token, with every
    quantity floored at zero and idempotency scoped per organization.
"""
from __future__ import annotations

import logging
import os
import uuid
from decimal import Decimal

import pytest

pytest.importorskip("fastapi")
from fastapi.testclient import TestClient
from customer_console.keys import split_key
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
INTERNAL = "test-internal-token"
OP = {"Authorization": f"Bearer {TOKEN}"}
INT = {"Authorization": f"Bearer {INTERNAL}"}



@pytest.fixture(scope="module", autouse=True)
def _schema():
    eng = create_engine(_URL, future=True)
    with eng.begin() as conn:
        apply_ladder(conn)


@pytest.fixture
def client(monkeypatch):
    monkeypatch.setenv("CUSTOMER_CONSOLE_OPERATOR_TOKEN", TOKEN)
    monkeypatch.setenv("CUSTOMER_CONSOLE_INTERNAL_TOKEN", INTERNAL)
    from customer_console.main import app
    return TestClient(app)


@pytest.fixture
def db():
    return create_engine(_URL, future=True)


@pytest.fixture(autouse=True)
def _box():
    """The deployment every org here is provisioned onto (MT-1j slice 4).

    Autouse and per-test: the helpers below are plain functions taking only a
    ``client``, and threading a fixture through every one of them would be more
    edit than the fact deserves — this suite's subject is credentials, not
    placement.
    """
    eng = create_engine(_URL, future=True)
    with eng.begin() as conn:
        ensure_deployment(conn)
    eng.dispose()


def _new_org(client, prefix: str = "org") -> str:
    slug = f"{prefix}-{uuid.uuid4().hex[:8]}"
    r = client.post("/orgs/provision", headers=OP, json={
        "slug": slug, "name": "N", "owner_email": f"o@{slug}.com",
        "core_seats": 3, "deployment_label": DEFAULT_DEPLOYMENT_LABEL,
    })
    assert r.status_code == 200, r.text
    return slug


def _prefix_of(token: str) -> str:
    """The stored prefix of a key.

    Via ``split_key`` rather than a hand-rolled ``rsplit``: the secret contains
    underscores, so an rsplit lands inside it. This helper was written with
    ``rsplit`` first and failed immediately — the same trap ``split_key``'s
    docstring warns about, hit a second time while testing the fix for it. It
    also made ``test_one_org_cannot_revoke_anothers_key`` pass for the wrong
    reason: revocation "failed" because the prefix was garbage, not because org
    scoping worked.
    """
    parsed = split_key(token)
    assert parsed is not None, token
    return parsed[0]


def _key_for(client, slug: str) -> str:
    r = client.post("/keys", headers=OP, json={"org_slug": slug})
    assert r.status_code == 200, r.text
    return r.json()["token"]


def _org_id(client, slug: str) -> str:
    """The org's id. Provisioning is idempotent on slug, so this is a lookup."""
    return client.post("/orgs/provision", headers=OP, json={
        "slug": slug, "name": "N", "owner_email": f"o@{slug}.com",
        "deployment_label": DEFAULT_DEPLOYMENT_LABEL,
    }).json()["organization_id"]


# ── The key resolves the tenant ─────────────────────────────────────────────

class TestTheKeyResolvesTheTenant:
    def test_no_input_can_move_the_key_to_another_org(self, client):
        """THE acceptance criterion, over every lever a caller could pull."""
        victim = _new_org(client, "victim")
        attacker = _new_org(client, "attacker")
        victim_id = _org_id(client, victim)
        key = _key_for(client, attacker)

        r = client.get("/me", headers={
            "Authorization": f"Bearer {key}",
            "X-CC-Org": victim,
            "X-CC-Organization": victim_id,
            "X-Org-Id": victim_id,
            "X-CC-Tenant": victim,
            "X-CC-Member": f"ceo@{victim}.com",
        }, params={"org_slug": victim, "organization_id": victim_id})

        assert r.status_code == 200, r.text
        assert r.json()["slug"] == attacker

    def test_attribution_headers_still_refine_INSIDE_the_org(self, client):
        # Refining within the org is what the headers are for. Only crossing
        # organizations must be impossible.
        slug = _new_org(client)
        key = _key_for(client, slug)

        r = client.get("/me", headers={"Authorization": f"Bearer {key}",
                                       "X-CC-Member": "alice@corp.com"})
        assert r.json()["slug"] == slug

    def test_the_router_bills_the_org_it_names_and_only_that_one(self, client, db):
        victim = _new_org(client, "victim")
        attacker = _new_org(client, "attacker")
        attacker_id = _org_id(client, attacker)
        client.post("/credits/grant", headers=OP,
                    json={"org_slug": victim, "credits": "1000"})
        client.post("/credits/grant", headers=OP,
                    json={"org_slug": attacker, "credits": "1000"})

        rid = f"req-{uuid.uuid4().hex}"
        assert client.post("/usage/record", headers=INT, json={
            "organization_id": attacker_id, "request_id": rid,
            "billed_credits": "50", "model": "m"}).status_code == 200

        with db.begin() as c:
            billed = c.execute(text(
                "SELECT o.slug FROM usage_event u JOIN organization o "
                "ON o.id = u.organization_id WHERE u.request_id = :r"
            ), {"r": rid}).scalar_one()
        assert billed == attacker

        victim_balance = client.get(
            f"/credits/balance?org_slug={victim}", headers=OP).json()["balance"]
        assert Decimal(victim_balance) == Decimal("1000")


# ── The key is read-only ────────────────────────────────────────────────────

class TestTheKeyCannotMoveMoney:
    def test_a_key_cannot_write_the_meter(self, client):
        # The finding: /usage/record under key auth made the metered party the
        # reporter of its own usage — "not a meter, a suggestion" (§4.1).
        slug = _new_org(client)
        key = _key_for(client, slug)
        org_id = _org_id(client, slug)

        r = client.post("/usage/record",
                        headers={"Authorization": f"Bearer {key}"},
                        json={"organization_id": org_id,
                              "request_id": f"r-{uuid.uuid4().hex}",
                              "billed_credits": "1"})
        assert r.status_code == 401

    def test_a_key_cannot_grant_itself_credits(self, client):
        slug = _new_org(client)
        key = _key_for(client, slug)

        r = client.post("/credits/grant",
                        headers={"Authorization": f"Bearer {key}"},
                        json={"org_slug": slug, "credits": "999999"})
        assert r.status_code == 401

    def test_negative_credits_are_refused_by_the_api(self, client):
        # Measured on a live database before the fix: billed_credits=-100000
        # moved a balance from 989 to 100989, because record_usage negates it.
        org_id = _org_id(client, _new_org(client))

        r = client.post("/usage/record", headers=INT, json={
            "organization_id": org_id, "request_id": f"r-{uuid.uuid4().hex}",
            "billed_credits": "-100000"})
        assert r.status_code == 422

    def test_negative_credits_are_ALSO_refused_by_the_schema(self, client, db):
        # R7 prefers a fence that holds over validation the next call site can
        # bypass. The CHECK constraint is that fence (migration 003).
        org_id = _org_id(client, _new_org(client))

        with pytest.raises(Exception) as exc:
            with db.begin() as c:
                c.execute(text(
                    "INSERT INTO usage_event (organization_id, request_id, "
                    "billed_credits) VALUES (:o, :r, -500)"),
                    {"o": org_id, "r": f"r-{uuid.uuid4().hex}"})
        assert "billed_credits_nonneg" in str(exc.value)

    def test_negative_token_counts_are_refused(self, client):
        org_id = _org_id(client, _new_org(client))
        r = client.post("/usage/record", headers=INT, json={
            "organization_id": org_id, "request_id": f"r-{uuid.uuid4().hex}",
            "prompt_tokens": -5})
        assert r.status_code == 422


# ── Idempotency is per organization ─────────────────────────────────────────

class TestIdempotencyIsScopedToOneOrg:
    def test_one_orgs_request_id_cannot_suppress_anothers_charge(self, client, db):
        # Measured before the fix: org B posting an id org A had used got
        # recorded:false, its 500-credit charge silently dropped, and the
        # boolean itself leaked whether another tenant had used that id.
        a = _org_id(client, _new_org(client, "a"))
        b_slug = _new_org(client, "b")
        b = _org_id(client, b_slug)
        client.post("/credits/grant", headers=OP,
                    json={"org_slug": b_slug, "credits": "1000"})

        shared = f"req-shared-{uuid.uuid4().hex}"
        client.post("/usage/record", headers=INT, json={
            "organization_id": a, "request_id": shared, "billed_credits": "0"})

        second = client.post("/usage/record", headers=INT, json={
            "organization_id": b, "request_id": shared,
            "billed_credits": "500"}).json()

        assert second["recorded"] is True
        assert Decimal(second["balance"]) == Decimal("500")

        with db.begin() as c:
            rows = c.execute(text(
                "SELECT count(*) FROM usage_event WHERE request_id = :r"),
                {"r": shared}).scalar_one()
        assert rows == 2

    def test_a_replay_within_one_org_still_bills_once(self, client):
        slug = _new_org(client)
        org_id = _org_id(client, slug)
        client.post("/credits/grant", headers=OP,
                    json={"org_slug": slug, "credits": "100"})
        rid = f"r-{uuid.uuid4().hex}"
        body = {"organization_id": org_id, "request_id": rid,
                "billed_credits": "12.5"}

        first = client.post("/usage/record", headers=INT, json=body).json()
        replay = client.post("/usage/record", headers=INT, json=body).json()

        assert (first["recorded"], replay["recorded"]) == (True, False)
        assert Decimal(replay["balance"]) == Decimal("87.5")


# ── Lifecycle ───────────────────────────────────────────────────────────────

class TestOrganizationLifecycle:
    @pytest.mark.parametrize("status", ["suspended", "cancelled", "deleted"])
    def test_a_dead_organizations_key_is_refused(self, client, db, status):
        slug = _new_org(client)
        key = _key_for(client, slug)
        with db.begin() as c:
            c.execute(text("UPDATE organization SET status = :s WHERE slug = :g"),
                      {"s": status, "g": slug})

        r = client.get("/me", headers={"Authorization": f"Bearer {key}"})
        # 403 not 401: the credential is fine, the account is not — sending them
        # to re-authenticate would be a loop.
        assert r.status_code == 403
        assert status in r.json()["detail"]

    @pytest.mark.parametrize("status", ["trial", "active", "past_due"])
    def test_a_live_or_late_organization_still_works(self, client, db, status):
        # past_due deliberately still transacts: §4.1d keeps a late payer
        # working through grace, because a customer cut off at the first missed
        # payment does not pay, they churn.
        slug = _new_org(client)
        key = _key_for(client, slug)
        with db.begin() as c:
            c.execute(text("UPDATE organization SET status = :s WHERE slug = :g"),
                      {"s": status, "g": slug})

        assert client.get("/me", headers={"Authorization": f"Bearer {key}"}
                          ).status_code == 200


# ── Rejection and scheme separation ─────────────────────────────────────────

class TestRejection:
    def test_a_revoked_key_401s(self, client):
        slug = _new_org(client)
        token = _key_for(client, slug)

        assert client.post("/keys/revoke", headers=OP, json={
            "org_slug": slug, "prefix": _prefix_of(token)}).json()["revoked"] is True

        assert client.get("/me", headers={"Authorization": f"Bearer {token}"}
                          ).status_code == 401

    def test_one_org_cannot_revoke_anothers_key(self, client, db):
        owner = _new_org(client, "owner")
        other = _new_org(client, "other")
        token = _key_for(client, owner)
        prefix = _prefix_of(token)

        assert client.post("/keys/revoke", headers=OP, json={
            "org_slug": other, "prefix": prefix}).json()["revoked"] is False

        # Checked at the SQL level, not just the response.
        with db.begin() as c:
            revoked_at = c.execute(text(
                "SELECT revoked_at FROM llm_api_key WHERE prefix = :p"),
                {"p": prefix}).scalar_one()
        assert revoked_at is None
        assert client.get("/me", headers={"Authorization": f"Bearer {token}"}
                          ).status_code == 200

    @pytest.mark.parametrize("bad", [
        None, "", "Bearer ", "Bearer nonsense", "Bearer cc_live_nope_secret",
        "cc_live_a_b",
    ])
    def test_malformed_and_unknown_keys_401(self, client, bad):
        headers = {"Authorization": bad} if bad is not None else {}
        assert client.get("/me", headers=headers).status_code == 401

    def test_unknown_and_wrong_secret_are_indistinguishable(self, client):
        # Distinguishing them tells an attacker which half of a guess was right.
        prefix = _prefix_of(_key_for(client, _new_org(client)))

        wrong = client.get("/me", headers={"Authorization": f"Bearer {prefix}_bad"})
        unknown = client.get(
            "/me", headers={"Authorization": "Bearer cc_live_ffffffffffff_bad"})

        assert wrong.status_code == unknown.status_code == 401
        assert wrong.json() == unknown.json()

    def test_the_three_schemes_do_not_open_each_other(self, client):
        slug = _new_org(client)
        key = _key_for(client, slug)
        org_id = _org_id(client, slug)
        usage = {"organization_id": org_id, "request_id": f"r-{uuid.uuid4().hex}"}

        # Operator token is not an internal token: a leaked console credential
        # must not be able to rewrite ledgers.
        assert client.post("/usage/record", headers=OP, json=usage).status_code == 401
        # Internal token is not an operator token...
        assert client.post("/credits/grant", headers=INT, json={
            "org_slug": slug, "credits": "1"}).status_code == 401
        # ...and is not an organization key.
        assert client.get("/me", headers=INT).status_code == 401
        # An organization key opens neither of the others.
        assert client.post("/usage/record",
                           headers={"Authorization": f"Bearer {key}"},
                           json=usage).status_code == 401
        # The body is COMPLETE and valid — including slice 4's required
        # `deployment_label` — so 401 can only be about the credential. A body
        # the model would reject anyway would let a 422 masquerade as this
        # refusal the day the door stopped shutting.
        assert client.post("/orgs/provision",
                           headers={"Authorization": f"Bearer {key}"},
                           json={"slug": "x", "name": "x",
                                 "owner_email": "x@y.com",
                                 "deployment_label": DEFAULT_DEPLOYMENT_LABEL,
                                 }).status_code == 401

    def test_a_key_shaped_operator_token_is_still_not_a_key(self, client, monkeypatch):
        # Guards the parser rather than the comparison: a staff token that
        # happens to look like a key must not be parsed as one.
        monkeypatch.setenv("CUSTOMER_CONSOLE_OPERATOR_TOKEN",
                           "cc_live_deadbeefcafe_supersecret")
        from customer_console.main import app
        c = TestClient(app)
        assert c.get("/me", headers={
            "Authorization": "Bearer cc_live_deadbeefcafe_supersecret"}
        ).status_code == 401

    def test_an_unconfigured_internal_token_fails_closed(self, client, monkeypatch):
        monkeypatch.delenv("CUSTOMER_CONSOLE_INTERNAL_TOKEN", raising=False)
        from customer_console.main import app
        r = TestClient(app).post("/usage/record", headers=INT, json={
            "organization_id": "00000000-0000-0000-0000-000000000000",
            "request_id": "y"})
        assert r.status_code == 503


# ── The secret never reaches a log ──────────────────────────────────────────

class TestTheSecretNeverReachesALog:
    def test_no_log_record_carries_the_secret_in_ANY_field(self, client, caplog):
        """Asserted over the whole record, not just its formatted message.

        The first version read ``record.getMessage()`` only, and verification
        showed that missed both idioms that actually matter:
        ``logger.warning(msg, extra={"token": token})`` — the normal shape for a
        structured/JSON log pipeline — and a token inside ``exc_info``, the
        shape an error handler produces. Both stayed green while leaking.
        """
        slug = _new_org(client)

        with caplog.at_level(logging.DEBUG):
            token = _key_for(client, slug)
            secret = split_key(token)[1]
            client.get("/me", headers={"Authorization": f"Bearer {token}"})
            # The likelier leak path: a rejected credential, where error
            # handlers love to echo the offending input.
            client.get("/me", headers={"Authorization": f"Bearer {token}_tampered"})

        haystack: list[str] = []
        for rec in caplog.records:
            haystack.append(rec.getMessage())
            haystack.extend(str(v) for v in rec.__dict__.values())
            if rec.exc_info:
                haystack.append(str(rec.exc_info))
        blob = "\n".join(haystack)

        assert secret not in blob
        assert token not in blob

    def test_the_audit_row_records_the_prefix_not_the_token(self, client, db):
        slug = _new_org(client)
        token = _key_for(client, slug)
        secret = split_key(token)[1]

        with db.begin() as c:
            details = "\n".join(
                str(r[0]) for r in c.execute(text(
                    "SELECT detail FROM control_audit WHERE action = 'key.issue'"))
            )
        assert secret not in details
        assert token not in details

    def test_listing_keys_returns_no_hash_and_no_token(self, client):
        slug = _new_org(client)
        token = _key_for(client, slug)

        listed = client.get(f"/keys?org_slug={slug}", headers=OP).json()["keys"]
        assert set(listed[0]) == {"prefix", "label", "created_at", "revoked"}
        assert token not in str(listed)
