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

from tests.unit._customer_console_ladder import (  # noqa: E402
    DEFAULT_DEPLOYMENT_LABEL,
    apply_ladder,
    ensure_deployment,
)

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
def box(db):
    """The deployment this suite provisions onto, ensured per test.

    Per test rather than per module because
    ``test_a_missing_deployment_label_is_422_even_with_exactly_one_deployment``
    empties the table to build the world a sole-deployment heuristic would have
    guessed right in.
    """
    with db.begin() as c:
        ensure_deployment(c)
    return DEFAULT_DEPLOYMENT_LABEL


@pytest.fixture
def org(client, box):
    slug = f"lc-{uuid.uuid4().hex[:8]}"
    r = client.post("/orgs/provision", headers=OP, json={
        "slug": slug, "name": "N", "owner_email": f"o@{slug}.com",
        "core_seats": 5, "deployment_label": box})
    assert r.status_code == 200, r.text
    return slug


def _move(client, slug, target, **kw):
    return client.post("/orgs/lifecycle", headers=OP,
                       json={"org_slug": slug, "target": target, **kw})


def _provision(client, slug: str, label: str, **extra):
    """POST ``/orgs/provision`` for *slug* onto *label*, unasserted.

    Unasserted on purpose: half this section's subjects are refusals, and a
    helper that asserted 200 could only be used by the happy path.
    """
    body = {"slug": slug, "name": "N", "owner_email": f"o@{slug}.com",
            "deployment_label": label}
    body.update(extra)
    return client.post("/orgs/provision", headers=OP, json=body)


def _placement(db, slug: str) -> dict | None:
    """The placement row for *slug*, joined back through the organization."""
    with db.begin() as c:
        row = c.execute(
            text(
                "SELECT d.label AS label, p.database_target AS database_target,"
                "       p.moved_at AS moved_at "
                "  FROM organization o "
                "  JOIN org_placement p ON p.organization_id = o.id "
                "  JOIN deployment d ON d.id = p.deployment_id "
                " WHERE o.slug = :s"
            ),
            {"s": slug},
        ).mappings().first()
    return dict(row) if row else None


def _placement_rows(db, slug: str) -> int:
    with db.begin() as c:
        return c.execute(
            text(
                "SELECT count(*) FROM org_placement p "
                "  JOIN organization o ON o.id = p.organization_id "
                " WHERE o.slug = :s"
            ),
            {"s": slug},
        ).scalar_one()


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
        # Slice 4's step is recorded for symmetry with the existing array.
        # NOTHING reads steps_done today (review-measured: one INSERT, two test
        # assertions, no production reader) — the resume path is CP-2a's owed
        # step-kill clause, and this assertion only pins the array's shape.
        assert "placement" in row[1]

    def test_re_provisioning_does_not_create_a_second_run_or_org(
        self, client, org, db, box
    ):
        assert _provision(client, org, box).status_code == 200
        with db.begin() as c:
            runs = c.execute(text(
                "SELECT count(*) FROM provisioning_run WHERE idempotency_key = :k"),
                {"k": f"provision:{org}"}).scalar_one()
            orgs = c.execute(text(
                "SELECT count(*) FROM organization WHERE slug = :g"),
                {"g": org}).scalar_one()
        assert (runs, orgs) == (1, 1)


@pytestmark_db
class TestProvisioningPlacesTheOrganization:
    """WS-29 MT-1j slice 4a — the Console-side half that was missing.

    Spec: ``saas_multitenancy.md`` §11 MT-1j slice 4 (done-when 4a) · D46.6.

    Until 2026-08-19 nothing in the tree wrote ``org_placement`` and
    ``store.deployment_visible_orgs`` inner-joins it, so an organization
    created through this route could **never** be resolved by any deployment
    key — CP-2b's resolve arm returned nothing for it forever, failing closed
    in a way that reads as "the Console is down". The end of that chain is
    fenced one suite over
    (``test_customer_console_resolve.py::TestProvisioningIsWhatPlacesAnOrg``);
    what is fenced here is the write itself and its two refusals.

    R8 throughout: every subject below is a conflict target, an inner join, a
    NULLable column or a unique index — the class of thing a hermetic fake
    agrees with whatever it is handed.
    """

    def test_provisioning_places_the_org_on_the_named_deployment(
        self, client, org, db, box
    ):
        """Exactly one row, on the deployment the operator named, target NULL."""
        assert _placement_rows(db, org) == 1

        row = _placement(db, org)
        assert row is not None, "provisioning wrote no org_placement row"
        assert row["label"] == box
        # `database_target` is left NULL by slice 4 — stated, not forgotten. Day
        # one every row resolves to the same target and the INDIRECTION is the
        # point; a value arrives when a real need names one.
        assert row["database_target"] is None

    def test_re_provisioning_the_same_label_leaves_the_row_untouched(
        self, client, org, db, box
    ):
        """Idempotent on the SLUG, and ``moved_at`` is the evidence.

        ``ON CONFLICT (organization_id) DO NOTHING`` — not ``DO UPDATE``. A
        re-run that refreshed ``moved_at`` would make "when was this customer
        moved" answer "whenever the signup form was last retried", which is the
        one question that column exists to answer.
        """
        before = _placement(db, org)

        assert _provision(client, org, box).status_code == 200

        after = _placement(db, org)
        assert _placement_rows(db, org) == 1
        assert after == before

    def test_re_provisioning_a_different_label_is_refused_and_moves_nothing(
        self, client, org, db
    ):
        """409 — provisioning never MOVES a placement (D46.6 item 4).

        A move is a separate operator act with its own semantics, owned by a
        future placement ticket. The refusal is what makes that true: with
        ``DO NOTHING`` alone the second call would answer 200 while the
        organization stayed exactly where it was, and the operator would
        believe they had moved a customer.
        """
        before = _placement(db, org)
        with db.begin() as c:
            ensure_deployment(c, label="second-box")

        r = _provision(client, org, "second-box")

        assert r.status_code == 409, r.text
        assert _placement(db, org) == before
        assert _placement_rows(db, org) == 1

    def test_an_unknown_deployment_label_is_404_naming_the_label(
        self, client, db
    ):
        """The operator idiom ``_org_id`` already ships (``main.py:449-455``).

        This credential is cross-org by design, so naming what it asked about
        is not an existence oracle — ``customer_console.md`` CP-9 clause 7 is
        the authority and calls this "the contrast, not the precedent". Ruled
        at the 2026-08-19 re-audit, which found the earlier "collapsed refusal"
        wording untestable and contradicting that clause.

        And the refusal is TOTAL: the organization is not half-created on the
        way to it.
        """
        slug = f"ghost-{uuid.uuid4().hex[:8]}"

        r = _provision(client, slug, "no-such-box")

        assert r.status_code == 404, r.text
        assert "no-such-box" in r.json()["detail"]
        with db.begin() as c:
            assert c.execute(
                text("SELECT count(*) FROM organization WHERE slug = :s"),
                {"s": slug},
            ).scalar_one() == 0

    def test_nothing_infers_the_deployment_from_there_being_exactly_one(
        self, client, db
    ):
        """Adjudication item 3 — the world a heuristic would have GOT RIGHT.

        ``count(deployment) = 1`` is forbidden **by name**: it would be a
        fourth copy of the sole-organization guess this same ticket retires
        (``key_store.py:114-139`` · ``model_config.py:40-48`` ·
        ``acb_common/placement.py:46-50``), and it silently mis-places every
        organization the day a second box exists. Asserting either refusal
        against a database holding several deployments would prove nothing —
        the heuristic refuses there too. So the table is emptied and exactly
        one row is left, which is the only state in which a guess is
        indistinguishable from a rule.

        **Two clauses, because "no arm may infer" has two arms**, and the
        second was found missing by mutation: substituting a
        ``count(*) = 1`` fallback for the 404 left every other fence in this
        file green, because they all run against a database with many
        deployments.

        ⚠️ The wipe is safe because every suite that provisions ensures its
        deployment row **per test** (``_box`` here and in the four sibling
        suites), and because these files run serially. It is the reason those
        fixtures are function-scoped.
        """
        with db.begin() as c:
            c.execute(text("DELETE FROM deployment_key"))
            c.execute(text("DELETE FROM org_placement"))
            c.execute(text("DELETE FROM deployment"))
            ensure_deployment(c, label="sole-box")
            assert c.execute(
                text("SELECT count(*) FROM deployment")
            ).scalar_one() == 1

        # (a) The field is missing. Pydantic refuses before the handler runs —
        # which is exactly the property: there is no code path in which the
        # count could be consulted.
        missing = f"nolabel-{uuid.uuid4().hex[:8]}"
        r = client.post("/orgs/provision", headers=OP, json={
            "slug": missing, "name": "N", "owner_email": f"o@{missing}.com"})
        assert r.status_code == 422, r.text
        assert "deployment_label" in r.text

        # (b) The field names a box that does not exist. Still 404 — the sole
        # deployment is NOT a fallback, and the organization is not placed on
        # it behind the operator's back.
        unknown = f"unknown-{uuid.uuid4().hex[:8]}"
        r = _provision(client, unknown, "not-the-sole-box")
        assert r.status_code == 404, r.text

        with db.begin() as c:
            assert c.execute(
                text("SELECT count(*) FROM organization WHERE slug IN "
                     "(:a, :b)"),
                {"a": missing, "b": unknown},
            ).scalar_one() == 0
            assert c.execute(
                text("SELECT count(*) FROM org_placement")
            ).scalar_one() == 0


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
