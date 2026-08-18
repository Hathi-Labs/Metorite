"""CP-2a — the organization lifecycle, and what each state permits.

Spec: ``customer_console.md`` §4.1d · ``saas_operations_doctrine.md`` §2.1.

Two properties carry most of the weight:

  * **`suspended` locks features but keeps login working.** A suspended customer
    who cannot log in cannot pay you — cannot see the invoice, cannot update a
    card, cannot export. Locking them out converts a recoverable billing problem
    into a churned account.
  * **Nothing reaches `deleted` except through `cancelled`.** The export window
    cannot be skipped by any sequence of operator actions, because the graph has
    no edge that skips it.
"""
from __future__ import annotations

import os
import uuid

import pytest

pytest.importorskip("fastapi")
from fastapi.testclient import TestClient
from customer_console import lifecycle
from sqlalchemy import create_engine, text

from tests.unit._customer_console_ladder import apply_ladder  # noqa: E402

_URL = os.environ.get("CUSTOMER_CONSOLE_DATABASE_URL", "").strip()
TOKEN = "test-operator-token"
OP = {"Authorization": f"Bearer {TOKEN}"}



# ── The state machine itself (pure — runs without a database) ───────────────

class TestTheStateMachine:
    def test_suspended_keeps_login_open_and_locks_everything_else(self):
        caps = lifecycle.capabilities_of("suspended")
        assert caps.can_sign_in is True      # they must be able to come and pay
        assert caps.can_use_ai is False
        assert caps.can_write_seats is False
        assert caps.data_retained is True

    def test_cancelled_still_signs_in_because_that_is_the_export_window(self):
        caps = lifecycle.capabilities_of("cancelled")
        assert caps.can_sign_in is True
        assert caps.data_retained is True

    def test_past_due_still_works_because_a_cut_off_customer_does_not_pay(self):
        caps = lifecycle.capabilities_of("past_due")
        assert (caps.can_sign_in, caps.can_use_ai, caps.can_write_seats) == \
            (True, True, True)

    def test_deleted_permits_nothing(self):
        caps = lifecycle.capabilities_of("deleted")
        assert not any((caps.can_sign_in, caps.can_use_ai,
                        caps.can_write_seats, caps.data_retained))

    def test_an_unknown_state_fails_CLOSED(self):
        # A hand-edited column, or a state added to the schema and not here,
        # must not read as "allowed".
        caps = lifecycle.capabilities_of("banana")
        assert not any((caps.can_sign_in, caps.can_use_ai, caps.data_retained))

    def test_deletion_is_reachable_ONLY_from_cancelled(self):
        """The export window cannot be skipped, by construction."""
        for state in lifecycle.STATES:
            if state == "cancelled":
                assert lifecycle.can_transition(state, "deleted") is True
            else:
                assert lifecycle.can_transition(state, "deleted") is False, state

    def test_suspension_is_recoverable(self):
        assert lifecycle.can_transition("suspended", "active") is True

    def test_a_cancelled_org_can_change_its_mind_inside_the_window(self):
        assert lifecycle.can_transition("cancelled", "active") is True

    def test_deleted_is_terminal(self):
        for target in lifecycle.STATES:
            assert lifecycle.can_transition("deleted", target) is False

    def test_an_off_graph_move_raises(self):
        with pytest.raises(lifecycle.TransitionRefused):
            lifecycle.assert_transition("past_due", "deleted")


# ── Over HTTP, against a real database ──────────────────────────────────────

pytestmark_db = pytest.mark.skipif(
    not _URL,
    reason=("CUSTOMER_CONSOLE_DATABASE_URL unset — R8 requires a REAL Postgres. "
            "A skip here is not a pass; CI must set it."),
)


@pytest.fixture(scope="module", autouse=True)
def _schema():
    if not _URL:
        return
    eng = create_engine(_URL, future=True)
    with eng.begin() as conn:
        apply_ladder(conn)


@pytest.fixture
def client(monkeypatch):
    monkeypatch.setenv("CUSTOMER_CONSOLE_OPERATOR_TOKEN", TOKEN)
    monkeypatch.setenv("CUSTOMER_CONSOLE_INTERNAL_TOKEN", "internal")
    from customer_console.main import app
    return TestClient(app)


@pytest.fixture
def db():
    return create_engine(_URL, future=True)


@pytest.fixture
def org(client):
    slug = f"lc-{uuid.uuid4().hex[:8]}"
    r = client.post("/orgs/provision", headers=OP, json={
        "slug": slug, "name": "N", "owner_email": f"o@{slug}.com",
        "core_seats": 5})
    assert r.status_code == 200, r.text
    return slug


def _move(client, slug, target, **kw):
    return client.post("/orgs/lifecycle", headers=OP,
                       json={"org_slug": slug, "target": target, **kw})


@pytestmark_db
class TestProvisioningLeavesACompleteAccount:
    def test_a_new_org_starts_in_trial_with_a_subscription(self, client, org, db):
        # Without a subscription row every billing surface has to invent a
        # default, which is how two surfaces come to disagree about trial state.
        with db.begin() as c:
            row = c.execute(text(
                "SELECT o.status, s.status, s.trial_ends_at IS NOT NULL "
                "FROM organization o JOIN org_subscription s "
                "ON s.organization_id = o.id WHERE o.slug = :g"), {"g": org}).first()
        assert row == ("trial", "trial", True)

    def test_provisioning_records_a_resumable_run(self, client, org, db):
        with db.begin() as c:
            row = c.execute(text(
                "SELECT status, steps_done FROM provisioning_run "
                "WHERE idempotency_key = :k"), {"k": f"provision:{org}"}).first()
        assert row[0] == "complete"
        assert "subscription" in row[1]

    def test_re_provisioning_does_not_create_a_second_run_or_org(self, client, org, db):
        client.post("/orgs/provision", headers=OP, json={
            "slug": org, "name": "N", "owner_email": f"o@{org}.com"})
        with db.begin() as c:
            runs = c.execute(text(
                "SELECT count(*) FROM provisioning_run WHERE idempotency_key = :k"),
                {"k": f"provision:{org}"}).scalar_one()
            orgs = c.execute(text(
                "SELECT count(*) FROM organization WHERE slug = :g"),
                {"g": org}).scalar_one()
        assert (runs, orgs) == (1, 1)


@pytestmark_db
class TestLifecycleOverHttp:
    def test_a_suspended_org_can_still_sign_in(self, client, org):
        _move(client, org, "suspended")

        r = client.post("/registry/resolve", headers=OP,
                        json={"org_slug": org, "email": "staff@x.com"})
        assert r.status_code == 200

    def test_a_suspended_org_cannot_be_given_seats(self, client, org):
        _move(client, org, "suspended")

        r = client.post("/billing/seats", headers=OP, json={
            "org_slug": org, "email": "staff@x.com", "plan_slug": "sales"})
        assert r.status_code == 403
        assert "suspended" in r.json()["detail"]

    def test_a_deleted_org_cannot_sign_in(self, client, org):
        _move(client, org, "cancelled")
        _move(client, org, "deleted")

        r = client.post("/registry/resolve", headers=OP,
                        json={"org_slug": org, "email": "staff@x.com"})
        assert r.status_code == 403

    def test_cancelling_opens_a_dated_export_window(self, client, org, db):
        _move(client, org, "cancelled", export_window_days=45)

        with db.begin() as c:
            until = c.execute(text(
                "SELECT export_until FROM organization WHERE slug = :g"),
                {"g": org}).scalar_one()
        assert until is not None

    def test_an_off_graph_transition_is_409_not_a_silent_write(self, client, org, db):
        # THE property: no operator action can destroy data without the window.
        r = _move(client, org, "deleted")

        assert r.status_code == 409
        assert "export window" in r.json()["detail"]
        with db.begin() as c:
            still = c.execute(text(
                "SELECT status FROM organization WHERE slug = :g"),
                {"g": org}).scalar_one()
        assert still == "trial"

    def test_a_transition_reports_the_new_capabilities(self, client, org):
        body = _move(client, org, "suspended").json()
        assert body["can_sign_in"] is True
        assert body["can_use_ai"] is False
        assert body["data_retained"] is True

    def test_every_transition_lands_an_audit_row(self, client, org, db):
        _move(client, org, "active", reason="paid")

        with db.begin() as c:
            n = c.execute(text(
                "SELECT count(*) FROM control_audit a JOIN organization o "
                "ON o.id = a.organization_id "
                "WHERE o.slug = :g AND a.action = 'org.lifecycle'"),
                {"g": org}).scalar_one()
        assert n == 1

    def test_the_subscription_follows_the_organization(self, client, org, db):
        _move(client, org, "active")

        with db.begin() as c:
            sub = c.execute(text(
                "SELECT s.status FROM org_subscription s JOIN organization o "
                "ON o.id = s.organization_id WHERE o.slug = :g"),
                {"g": org}).scalar_one()
        assert sub == "active"
