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

import logging
import os
import uuid

import pytest

pytest.importorskip("fastapi")
from fastapi.testclient import TestClient
from customer_console import auth, lifecycle
from customer_console.keys import split_key
from sqlalchemy import create_engine, text

from tests.unit._customer_console_ladder import (  # noqa: E402
    DEFAULT_DEPLOYMENT_LABEL,
    apply_ladder,
    ensure_deployment,
    mint_deployment_key,
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
    ``test_nothing_infers_the_deployment_from_there_being_exactly_one``
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


def _orgs(db, slug: str) -> int:
    with db.begin() as c:
        return c.execute(
            text("SELECT count(*) FROM organization WHERE slug = :s"),
            {"s": slug},
        ).scalar_one()


def _bearer(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


def _provision_as(client, token: str, *, slug: str, **extra):
    """POST ``/orgs/provision`` under an ARBITRARY credential, unasserted.

    The sibling of :func:`_provision`, which is the operator's, and
    deliberately not merged with it (WS-31 CP-2c slice 1). The operator body
    always carries a ``deployment_label`` and the deployment-key body must
    never carry one — a single helper with an optional label would turn the
    one difference between the two arms into a keyword argument nobody reads,
    and the arm a call exercises would stop being legible at the call site.
    """
    body = {"slug": slug, "name": "N", "owner_email": f"o@{slug}.com"}
    body.update(extra)
    return client.post("/orgs/provision", headers=_bearer(token), json=body)


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

        ⚠️ **Clause (a) AMENDED 2026-08-19 by WS-31 CP-2c slice 1 (D46.6 item
        1, ruled in `customer_console.md` in writing — not by an implementer
        in silence).** It pinned **422**, i.e. *pydantic refuses before the
        handler runs, so there is no code path in which a count could be
        consulted*. A two-arm route cannot keep that: the deployment-key arm
        has to **refuse** a presented label, which a required field makes
        unexpressible, so ``deployment_label`` became ``str | None`` on the
        ``ResolveRequest.org_slug`` precedent and the refusal moved into the
        handler as a **400**.

        That is a strictly weaker starting point and the clause is written to
        say so: the handler now *does* run with no label, so what is asserted
        is that it refuses **there** rather than reaching for the sole
        deployment. The mutation is unchanged and still the proof — plant a
        ``count(*) = 1`` fallback and this test must go red.

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

        # (a) The field is missing, and since slice 1 of CP-2c the HANDLER is
        # what refuses it — 400, naming the field. The count is reachable from
        # here in a way it was not when pydantic refused first, which is
        # exactly why this clause is the one the mutation is aimed at.
        missing = f"nolabel-{uuid.uuid4().hex[:8]}"
        r = client.post("/orgs/provision", headers=OP, json={
            "slug": missing, "name": "N", "owner_email": f"o@{missing}.com"})
        assert r.status_code == 400, r.text
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
class TestTheDeploymentKeyProvisionArm:
    """WS-31 CP-2c slice 1 — the SECOND arm of ``POST /orgs/provision``.

    Spec: ``customer_console.md`` §6 CP-2c done-when 6 (*"a `{resolve}`-only
    deployment key calling provision is refused and the refusal is logged; a
    `{resolve, provision}` key provisions"*) and done-when 8's Console half ·
    D46.6 item 1 as amended · ``user_management_contract.md`` R11.

    **The shape, stated once.** One endpoint, two schemes, and the *credential*
    decides which — the same dispatcher ``POST /registry/resolve`` already
    takes, gated on a different capability. The operator names the box out loud
    because that credential is cross-org and carries no deployment identity of
    its own; a deployment key **is** the deployment identity, so this arm
    carries no ``deployment_label`` at all and presenting one is **400, never
    ignored** — the identical rule the resolve arm applies to ``org_slug``, for
    the identical reason: a caller that *has* an identity must not be allowed
    to assert a different one, and an ignored field is a caller who believes it
    worked.

    **Every placement-write semantic below is INHERITED from WS-29 MT-1j slice
    4, not re-decided here** — ``ON CONFLICT (organization_id) DO NOTHING``,
    provisioning never moves, 409 on a different target, ``database_target``
    NULL. What this class adds is the proof that they hold when the deployment
    is derived from the credential rather than read from the body, which is a
    different code path to the same writes.

    ⚠️ **No migration, and none may be minted** (done-when 6, verbatim):
    ``deployment_key.capabilities`` is ``TEXT[]`` with no CHECK
    (``006_deployment_key.sql:56``), so the wide set is insertable today and
    the enforcement is entirely ``deployment_or_operator(capability)``, already
    generic. Minting a wide key **into a scratch database from a fixture** is
    agent-safe; issuing or widening a REAL one is OWNER-GATE (§8 gate 7) and
    there is deliberately no HTTP route that does it.

    R8 throughout: capability sets are a Postgres ``TEXT[]``, the placement
    write is an ``ON CONFLICT`` upsert and the resolve loop is a four-table
    join — the class of thing a hermetic fake agrees with whatever it is
    handed.
    """

    @pytest.fixture
    def keyed_box(self, db):
        """One deployment, and BOTH keys for it — narrow and wide.

        Both minted on the *same* deployment on purpose: it makes the only
        difference between the two credentials the capability set, so a test
        that passes with one and fails with the other cannot be explained by
        placement.
        """
        label = f"cp2c-{uuid.uuid4().hex[:8]}"
        with db.begin() as c:
            deployment = ensure_deployment(c, label=label)
            narrow = mint_deployment_key(
                c, deployment_id=deployment,
                capabilities=[auth.RESOLVE_CAPABILITY],
            )
            wide = mint_deployment_key(
                c, deployment_id=deployment,
                capabilities=[auth.RESOLVE_CAPABILITY,
                              auth.PROVISION_CAPABILITY],
            )
        return {"label": label, "deployment_id": deployment,
                "narrow": narrow, "wide": wide}

    def test_a_resolve_only_key_may_not_provision_and_the_refusal_is_logged(
        self, client, db, keyed_box, caplog
    ):
        """Done-when 6's first half — **both** halves of it.

        403 rather than 401: the credential is valid and the caller is told
        what it lacks rather than sent to re-authenticate in a loop (the shape
        ``deployment_or_operator`` already applies to ``resolve``).

        **And the refusal is LOGGED**, which the clause names separately and a
        silent 403 does not satisfy. This credential is the one an operator
        would widen by hand into a live box (§8 gate 7), so *"a key tried to do
        something it was not issued for"* is the signal that a widening was
        forgotten or that a leaked key is being probed — and it was
        unobservable before slice 1: the raise carried no log line at all.
        What is logged is the **capability and the key prefix, never the
        secret**, which the last assertion here pins rather than trusts.
        """
        slug = f"narrow-{uuid.uuid4().hex[:8]}"

        with caplog.at_level(logging.WARNING, logger="platform.auth"):
            r = _provision_as(client, keyed_box["narrow"], slug=slug)

        assert r.status_code == 403, r.text
        assert auth.PROVISION_CAPABILITY in r.json()["detail"]

        refusals = [
            rec for rec in caplog.records
            if rec.getMessage() == "deployment_key.capability_refused"
        ]
        assert len(refusals) == 1, [rec.getMessage() for rec in caplog.records]
        assert refusals[0].capability == auth.PROVISION_CAPABILITY
        parsed = split_key(keyed_box["narrow"])
        assert parsed is not None
        assert refusals[0].key_prefix == parsed[0]
        # The SECRET half never reaches a log line. Asserted on the whole
        # captured text, not on the one record, because a second log site
        # added later would be caught here too.
        assert parsed[1] not in caplog.text

        # Refused means refused: nothing was written on the way out.
        assert _orgs(db, slug) == 0

    def test_a_wide_key_provisions_onto_the_deployment_the_key_names(
        self, client, db, keyed_box
    ):
        """Done-when 6's second half, and the loop it exists to close.

        The org is created, placed on the KEY's own deployment, and then
        **resolved by that same key** — which is the whole point of the arm:
        a box that provisions a customer must be able to sign them in
        afterwards, and the inner join in ``store.deployment_visible_orgs``
        makes that true only if the placement landed on the right deployment.
        Asserting the placement row alone would pass on a write that put the
        org on some other box.
        """
        slug = f"wide-{uuid.uuid4().hex[:8]}"

        r = _provision_as(client, keyed_box["wide"], slug=slug)

        assert r.status_code == 200, r.text
        assert r.json()["slug"] == slug
        row = _placement(db, slug)
        assert row is not None, "the deployment arm wrote no org_placement row"
        assert row["label"] == keyed_box["label"]
        # `database_target` is left NULL — inherited from slice 4 unchanged.
        assert row["database_target"] is None
        assert _placement_rows(db, slug) == 1

        answer = client.post(
            "/registry/resolve", headers=_bearer(keyed_box["wide"]),
            json={"email": f"o@{slug}.com"},
        )
        assert answer.status_code == 200, answer.text
        assert [o["slug"] for o in answer.json()["organizations"]] == [slug]

    def test_a_deployment_key_may_not_name_a_deployment_label(
        self, client, db, keyed_box
    ):
        """400, never ignored — R11, the same rule as the resolve arm's ``org_slug``.

        Refused on **shape, before the value is looked at**, which is why
        naming its OWN box and naming a box that does not exist are the same
        refusal byte for byte. Were the value consulted first, the status code
        would answer *"does this deployment exist"* for every label an
        attacker cares to try — the existence oracle clause 5 removes from the
        resolve arm, re-entering by a different door.
        """
        mine = f"named-{uuid.uuid4().hex[:8]}"
        r = _provision_as(client, keyed_box["wide"], slug=mine,
                          deployment_label=keyed_box["label"])

        assert r.status_code == 400, r.text
        assert _orgs(db, mine) == 0

        ghost = f"ghost-{uuid.uuid4().hex[:8]}"
        other = _provision_as(client, keyed_box["wide"], slug=ghost,
                              deployment_label="no-such-box-anywhere")

        assert (other.status_code, other.json()) == (400, r.json())
        assert _orgs(db, ghost) == 0

    def test_the_deployment_arm_never_moves_a_placement(
        self, client, db, keyed_box, box
    ):
        """409, inherited from slice 4 — and inherited through a NEW code path.

        The org is born on the operator's box and a wide key for a *different*
        deployment tries to provision the same slug. With ``DO NOTHING`` alone
        that call would answer 200 while the organization stayed exactly where
        it was, and the caller — here a box, not a human who might notice —
        would believe it had taken over a customer.
        """
        slug = f"stay-{uuid.uuid4().hex[:8]}"
        assert _provision(client, slug, box).status_code == 200
        before = _placement(db, slug)

        r = _provision_as(client, keyed_box["wide"], slug=slug)

        assert r.status_code == 409, r.text
        assert _placement(db, slug) == before
        assert _placement_rows(db, slug) == 1

    def test_re_provisioning_on_the_same_key_leaves_the_row_untouched(
        self, client, db, keyed_box
    ):
        """Idempotent on the SLUG under this arm too, ``moved_at`` the evidence.

        A retrying signup form resends the natural key, and a re-run that
        refreshed ``moved_at`` would make *"when was this customer moved"*
        answer *"whenever the form was last retried"*.
        """
        slug = f"again-{uuid.uuid4().hex[:8]}"
        assert _provision_as(client, keyed_box["wide"],
                             slug=slug).status_code == 200
        before = _placement(db, slug)

        assert _provision_as(client, keyed_box["wide"],
                             slug=slug).status_code == 200

        assert _placement(db, slug) == before
        assert _placement_rows(db, slug) == 1
        assert _orgs(db, slug) == 1

    def test_the_deployment_arms_write_is_audited_as_a_deployment_act(
        self, client, db, keyed_box, box
    ):
        """``control_audit.actor`` tells the two arms apart.

        ``_audit``'s own docstring is the authority: ``actor`` exists because
        *"an audit trail that called those acts 'operator' would misattribute
        the one class of write we most need to tell apart later"*. A box
        provisioning a self-serve customer is exactly such a class — it is the
        one provisioning act with no human in it — so it must not be recorded
        as staff having done it.

        Agent-proposed default (D16/D17 class), recorded in the build box.
        """
        by_key = f"audit-key-{uuid.uuid4().hex[:8]}"
        by_operator = f"audit-op-{uuid.uuid4().hex[:8]}"
        assert _provision_as(client, keyed_box["wide"],
                             slug=by_key).status_code == 200
        assert _provision(client, by_operator, box).status_code == 200

        with db.begin() as c:
            actors = dict(c.execute(
                text(
                    "SELECT o.slug, a.actor FROM control_audit a "
                    "  JOIN organization o ON o.id = a.organization_id "
                    " WHERE a.action = 'org.provision' AND o.slug IN (:k, :o)"
                ),
                {"k": by_key, "o": by_operator},
            ).all())

        assert actors == {by_key: "deployment", by_operator: "operator"}

    def test_the_wide_key_is_still_not_an_operator_token(
        self, client, keyed_box, org
    ):
        """A second capability widens the KEY, never the SCHEME.

        The credential that may now create an organization must still be
        unable to read a balance, grant a credit or open ``/me`` — capabilities
        are enumerated per door, and the failure mode this guards is a
        dispatcher that started treating "holds two capabilities" as "is
        trusted".
        """
        wide = _bearer(keyed_box["wide"])

        assert client.get(f"/billing/summary?org_slug={org}",
                          headers=wide).status_code == 401
        assert client.get("/me", headers=wide).status_code == 401
        assert client.post("/credits/grant", headers=wide, json={
            "org_slug": org, "credits": "1"}).status_code == 401


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
