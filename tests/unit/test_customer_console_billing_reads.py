"""H-152, the billing half — the customer billing READS under the deployment key.

Spec: ``project-docs/HANDOFF.md`` H-152 · ``customer_console.md`` §6 CP-2h
(D-SEAT-4, the pattern) · ``user_management_contract.md`` R11 · D66.

The gap this suite keeps closed, stated once:

    The customer billing pages read ``/me/billing``, ``/me/seats``,
    ``/me/members``, ``/billing/catalog`` and ``/my/usage/*`` with the
    workbench's ``CUSTOMER_CONSOLE_ORG_KEY``. A ``cc_live_`` key IS one
    organization, so a SHARED box either showed nothing or showed one tenant's
    figures to every tenant. The ``billing_read`` doors serve the same reads
    under the per-box deployment key, and derive the organization from
    placement ∩ the acting member's membership.

What the fences pin, and why each needs a real database:

1. **One answer per fact.** Each door's body equals its organization-key
   twin's for the same organization. A second query would drift, and only a
   real ledger, grid and roster can show the two agree.
2. **Org A never reads org B.** Two orgs on ONE box, one key. The derivation
   is a four-table join, and a fake join agrees with whatever it is handed.
3. **Non-admin scoping.** ``scope=self`` returns the actor's own spend and
   nobody else's, over real ``usage_event`` rows with a CITEXT email.
4. **The capability gate, R11 and the operator refusal.**
5. **The lifecycle.** A suspended org still reads its bill (``can_pay``) and
   is refused its spend reads (``can_use_ai``), as the twins are.

⚠️ **R8.** This suite skips loudly without a server, and ``pr-check.yml``
names it — ``test_this_suite_is_named_in_the_ci_skip_guard`` keeps that true.

Run::

    eval "$(bash scripts/dev_db.sh --export)"
    uv run pytest tests/unit/test_customer_console_billing_reads.py
"""
from __future__ import annotations

import os
import uuid
from decimal import Decimal
from pathlib import Path

import pytest

pytest.importorskip("fastapi")

from customer_console import store
from customer_console.auth import (
    BILLING_READ_CAPABILITY,
    MEMBER_ADMIN_CAPABILITY,
    RESOLVE_CAPABILITY,
    SEAT_ADMIN_CAPABILITY,
)
from customer_console.main import app
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
OP = {"Authorization": f"Bearer {TOKEN}"}

BOX_LABEL = "billing-reads-suite-box"

#: Each deployment-key door, and its organization-key twin.
TWINS = {
    "/registry/billing/summary": "/me/billing",
    "/registry/billing/seats": "/me/seats",
    "/registry/billing/members": "/me/members",
    "/registry/billing/catalog": "/billing/catalog",
    "/registry/usage/members": "/my/usage/members",
}
SCOPED = ("/registry/usage/activity", "/registry/usage/apps")
ALL_DOORS = (*TWINS, *SCOPED)


# ── Fixtures ─────────────────────────────────────────────────────────────────

@pytest.fixture(scope="module", autouse=True)
def _schema():
    eng = create_engine(_URL, future=True)
    with eng.begin() as conn:
        apply_ladder(conn)
    eng.dispose()


@pytest.fixture
def db():
    return create_engine(_URL, future=True)


@pytest.fixture
def client(monkeypatch):
    monkeypatch.setenv("CUSTOMER_CONSOLE_OPERATOR_TOKEN", TOKEN)
    monkeypatch.setenv("CUSTOMER_CONSOLE_INTERNAL_TOKEN", INTERNAL)
    return TestClient(app)


@pytest.fixture(autouse=True)
def _box(db):
    with db.begin() as c:
        ensure_deployment(c, label=BOX_LABEL)


def _new_org(client, *, core_seats: int = 3, label: str = BOX_LABEL) -> dict:
    slug = f"h152-{uuid.uuid4().hex[:8]}"
    owner = f"owner@{slug}.example"
    r = client.post("/orgs/provision", headers=OP, json={
        "slug": slug, "name": "N", "owner_email": owner,
        "core_seats": core_seats, "deployment_label": label,
    })
    assert r.status_code == 200, r.text
    return {"slug": slug, "owner": owner, "id": r.json()["organization_id"]}


def _deployment_id(db, label: str = BOX_LABEL) -> str:
    with db.begin() as c:
        return str(c.execute(
            text("SELECT id FROM deployment WHERE label = :l"), {"l": label},
        ).scalar_one())


def _key(db, *, capabilities: list[str] | None = None,
         label: str = BOX_LABEL) -> str:
    with db.begin() as c:
        return mint_deployment_key(
            c, deployment_id=_deployment_id(db, label), capabilities=capabilities,
        )


@pytest.fixture
def billing_key(db):
    """The box's key with ``billing_read``, plus the two doors that build an
    ACTIVE plain member through the product (invite, then first resolve)."""
    return _key(db, capabilities=[
        RESOLVE_CAPABILITY, MEMBER_ADMIN_CAPABILITY, BILLING_READ_CAPABILITY,
    ])


def _post(client, key: str, path: str, **body):
    return client.post(path, headers={"Authorization": f"Bearer {key}"}, json=body)


def _org_key(client, slug: str) -> str:
    r = client.post("/keys", headers=OP, json={"org_slug": slug})
    assert r.status_code == 200, r.text
    return r.json()["token"]


def _active_member(client, key: str, org: dict, local: str = "plain") -> str:
    """A registry ``member`` who has signed in once, so their status is active."""
    email = f"{local}@{org['slug']}.example"
    r = _post(client, key, "/registry/members",
              member_email=email, actor_email=org["owner"])
    assert r.status_code == 200, r.text
    r = _post(client, key, "/registry/resolve", email=email)
    assert r.status_code == 200, r.text
    return email


def _usage(db, org_id: str, *, email: str, agent: str, credits: str = "2") -> None:
    with db.begin() as c:
        assert store.record_usage(
            c, org_id=org_id, request_id=f"req-{uuid.uuid4().hex}",
            billed_credits=Decimal(credits), user_email=email, agent=agent,
        ) is True


# ── The capability gate ──────────────────────────────────────────────────────

class TestTheCapabilityGate:
    @pytest.mark.parametrize("door", ALL_DOORS)
    def test_a_key_without_billing_read_is_403_and_names_it(
        self, client, db, door
    ):
        """The shape every real key has today: the doors ship dark until the
        owner widens the box's key by hand. ``seat_admin`` and
        ``member_admin`` must not open them — a balance is not a seat."""
        org = _new_org(client)
        narrow = _key(db, capabilities=[
            RESOLVE_CAPABILITY, SEAT_ADMIN_CAPABILITY, MEMBER_ADMIN_CAPABILITY,
        ])
        r = _post(client, narrow, door, actor_email=org["owner"])
        assert r.status_code == 403, r.text
        assert r.json()["detail"].startswith("deployment key lacks the ")
        assert BILLING_READ_CAPABILITY in r.json()["detail"]

    def test_the_refusal_prefix_is_the_one_the_gateway_reads(self, client, db):
        """The gateway turns this 403 into "not configured" by its PREFIX
        (``console_resolve.CAPABILITY_REFUSAL_PREFIX``). If the Console reworded
        it, a member would be told they may not look at their own bill."""
        from acb_auth.console_resolve import CAPABILITY_REFUSAL_PREFIX

        org = _new_org(client)
        r = _post(client, _key(db), "/registry/billing/summary",
                  actor_email=org["owner"])
        assert r.json()["detail"].startswith(CAPABILITY_REFUSAL_PREFIX)

    def test_an_organization_key_cannot_open_the_per_box_door(
        self, client, billing_key
    ):
        org = _new_org(client)
        live = _org_key(client, org["slug"])
        r = _post(client, live, "/registry/billing/summary",
                  actor_email=org["owner"])
        assert r.status_code == 401, r.text

    def test_the_operator_token_is_refused(self, client):
        """These doors serve a member. The operator reads a customer's billing
        through its own cross-org doors, so a second one here is refused."""
        org = _new_org(client)
        r = client.post("/registry/billing/summary", headers=OP,
                        json={"org_slug": org["slug"]})
        assert r.status_code == 403, r.text
        assert "operator" in r.json()["detail"]

    def test_no_credential_is_401(self, client):
        r = client.post("/registry/billing/summary",
                        json={"actor_email": "a@x.example"})
        assert r.status_code == 401


# ── R11 ──────────────────────────────────────────────────────────────────────

class TestR11:
    @pytest.mark.parametrize("door", ALL_DOORS)
    def test_naming_an_org_slug_is_400(self, client, billing_key, door):
        org = _new_org(client)
        r = _post(client, billing_key, door,
                  actor_email=org["owner"], org_slug=org["slug"])
        assert r.status_code == 400, r.text
        assert "may not name an organization" in r.json()["detail"]

    def test_an_actorless_body_is_400(self, client, billing_key):
        r = _post(client, billing_key, "/registry/billing/summary")
        assert r.status_code == 400, r.text

    @pytest.mark.parametrize("field", ["member", "email", "organization_id"])
    def test_an_unknown_field_is_refused_never_ignored(
        self, client, billing_key, field
    ):
        """``extra: forbid``. An ignored ``member`` would be a caller who
        believes it scoped the read to a colleague."""
        org = _new_org(client)
        r = _post(client, billing_key, "/registry/usage/activity",
                  actor_email=org["owner"], **{field: "x@y.example"})
        assert r.status_code == 422, r.text

    def test_scope_is_refused_on_a_door_that_has_one_reading(
        self, client, billing_key
    ):
        org = _new_org(client)
        r = _post(client, billing_key, "/registry/usage/members",
                  actor_email=org["owner"], scope="self")
        assert r.status_code == 422, r.text

    def test_an_unknown_actor_is_the_byte_identical_403(self, client, billing_key):
        r = _post(client, billing_key, "/registry/billing/summary",
                  actor_email="ghost@nowhere.example")
        assert r.status_code == 403, r.text
        assert r.json()["detail"] == (
            "the acting member is not an admin on this deployment"
        )


# ── One answer per fact — each door IS its org-key twin ──────────────────────

class TestTheTwins:
    @pytest.mark.parametrize("door,twin", sorted(TWINS.items()))
    def test_the_door_answers_exactly_what_its_twin_answers(
        self, client, db, billing_key, door, twin
    ):
        """Mutation: a door that re-derived a figure with its own query shows a
        different balance, grid or roster here as soon as the org holds data."""
        org = _new_org(client, core_seats=3)
        _usage(db, org["id"], email=org["owner"], agent="email-assistant")
        live = _org_key(client, org["slug"])

        mine = client.get(twin, headers={"Authorization": f"Bearer {live}"})
        assert mine.status_code == 200, mine.text
        over = _post(client, billing_key, door, actor_email=org["owner"])
        assert over.status_code == 200, over.text
        assert over.json() == mine.json()

    @pytest.mark.parametrize("door", SCOPED)
    def test_the_org_scope_answers_what_the_unscoped_twin_answers(
        self, client, db, billing_key, door
    ):
        org = _new_org(client)
        _usage(db, org["id"], email=org["owner"], agent="a")
        _usage(db, org["id"], email=f"other@{org['slug']}.example", agent="b")
        live = _org_key(client, org["slug"])
        twin = door.replace("/registry/usage/", "/my/usage/")

        mine = client.get(twin, headers={"Authorization": f"Bearer {live}"})
        over = _post(client, billing_key, door,
                     actor_email=org["owner"], scope="org")
        assert over.status_code == 200, over.text
        assert over.json() == mine.json()

    def test_the_summary_is_real_and_not_two_empty_answers(
        self, client, db, billing_key
    ):
        org = _new_org(client)
        _usage(db, org["id"], email=org["owner"], agent="a", credits="5")
        body = _post(client, billing_key, "/registry/billing/summary",
                     actor_email=org["owner"]).json()
        assert body["credits"]["burnThisCycle"] == 5.0


# ── Non-admin scoping — the member sees their own spend only ─────────────────

class TestTheSelfScope:
    @pytest.mark.parametrize("door", SCOPED)
    def test_self_reads_the_actor_and_nobody_else(
        self, client, db, billing_key, door
    ):
        """Mutation: dropping the scope (or reading ``org`` by default) hands a
        plain member every colleague's spend."""
        org = _new_org(client)
        me = _active_member(client, billing_key, org)
        _usage(db, org["id"], email=me.upper(), agent="mine")  # CITEXT
        _usage(db, org["id"], email=org["owner"], agent="theirs")

        r = _post(client, billing_key, door, actor_email=me, scope="self")
        assert r.status_code == 200, r.text
        body = r.json()
        assert body["member"] == me
        names = [row.get("activity") or row.get("app") for row in body["rows"]]
        assert "theirs" not in str(body)
        assert names, "the actor's own row is missing"

    @pytest.mark.parametrize("door", SCOPED)
    def test_a_missing_scope_is_the_narrow_reading(
        self, client, db, billing_key, door
    ):
        """The default fails CLOSED: a gateway that forgot the field shows the
        member themselves, never the organization."""
        org = _new_org(client)
        me = _active_member(client, billing_key, org)
        _usage(db, org["id"], email=org["owner"], agent="theirs")
        body = _post(client, billing_key, door, actor_email=me).json()
        assert body["member"] == me
        assert "theirs" not in str(body)

    def test_a_plain_member_may_read_the_summary(self, client, billing_key):
        """Any ACTIVE member reads the bill, as with the org-key twin. The
        tenant plane, at the gateway, decides the admin-only figures."""
        org = _new_org(client)
        me = _active_member(client, billing_key, org)
        r = _post(client, billing_key, "/registry/billing/summary", actor_email=me)
        assert r.status_code == 200, r.text

    def test_an_invited_member_who_never_signed_in_is_refused(
        self, client, billing_key
    ):
        org = _new_org(client)
        pending = f"pending@{org['slug']}.example"
        assert _post(client, billing_key, "/registry/members",
                     member_email=pending, actor_email=org["owner"]).status_code == 200
        r = _post(client, billing_key, "/registry/billing/summary",
                  actor_email=pending)
        assert r.status_code == 403, r.text


# ── Org A never reads org B ──────────────────────────────────────────────────

class TestCrossOrgIsolation:
    def test_two_orgs_one_key_each_owner_reads_their_own(
        self, client, db, billing_key
    ):
        a = _new_org(client, core_seats=3)
        b = _new_org(client, core_seats=7)
        _usage(db, a["id"], email=a["owner"], agent="a-agent", credits="3")
        _usage(db, b["id"], email=b["owner"], agent="b-agent", credits="11")

        roster = _post(client, billing_key, "/registry/billing/members",
                       actor_email=a["owner"]).json()
        assert {m["email"] for m in roster["members"]} == {a["owner"]}

        seats = _post(client, billing_key, "/registry/billing/seats",
                      actor_email=a["owner"]).json()
        core = next(p for p in seats["plans"] if p["plan_slug"] == "core")
        assert core["purchased"] == 3

        spend = _post(client, billing_key, "/registry/usage/activity",
                      actor_email=a["owner"], scope="org").json()
        assert "b-agent" not in str(spend)
        people = _post(client, billing_key, "/registry/usage/members",
                       actor_email=a["owner"]).json()
        assert b["owner"] not in str(people)

        summary = _post(client, billing_key, "/registry/billing/summary",
                        actor_email=b["owner"]).json()
        assert summary["credits"]["burnThisCycle"] == 11.0

    def test_a_key_on_another_box_resolves_nobody_here(self, client, db):
        org = _new_org(client)
        with db.begin() as c:
            ensure_deployment(c, label="h152-elsewhere")
        elsewhere = _key(db, capabilities=[BILLING_READ_CAPABILITY],
                         label="h152-elsewhere")
        r = _post(client, elsewhere, "/registry/billing/summary",
                  actor_email=org["owner"])
        assert r.status_code == 403, r.text

    def test_a_member_of_another_org_on_this_box_reads_only_that_org(
        self, client, billing_key
    ):
        """A non-member of A, who IS a member of B, gets B — never A. The
        answer is bounded by the actor's membership, not by the box."""
        a = _new_org(client, core_seats=3)
        b = _new_org(client, core_seats=7)
        r = _post(client, billing_key, "/registry/billing/seats",
                  actor_email=b["owner"])
        core = next(p for p in r.json()["plans"] if p["plan_slug"] == "core")
        assert core["purchased"] == 7
        assert a["owner"] not in r.text


# ── The lifecycle ────────────────────────────────────────────────────────────

class TestTheLifecycle:
    def _move(self, client, org, *targets):
        for target in targets:
            r = client.post("/orgs/lifecycle", headers=OP, json={
                "org_slug": org["slug"], "target": target,
            })
            assert r.status_code == 200, r.text

    def test_a_suspended_org_still_reads_its_bill(self, client, billing_key):
        org = _new_org(client)
        self._move(client, org, "suspended")
        for door in ("/registry/billing/summary", "/registry/billing/seats",
                     "/registry/billing/members", "/registry/billing/catalog"):
            r = _post(client, billing_key, door, actor_email=org["owner"])
            assert r.status_code == 200, (door, r.text)

    def test_a_suspended_org_is_refused_its_spend_reads_as_the_twin_is(
        self, client, billing_key
    ):
        org = _new_org(client)
        live = _org_key(client, org["slug"])
        self._move(client, org, "suspended")
        twin = client.get("/my/usage/activity",
                          headers={"Authorization": f"Bearer {live}"})
        r = _post(client, billing_key, "/registry/usage/activity",
                  actor_email=org["owner"], scope="org")
        assert r.status_code == twin.status_code == 403, (r.text, twin.text)

    def test_a_deleted_org_is_refused(self, client, billing_key):
        org = _new_org(client)
        self._move(client, org, "cancelled", "deleted")
        r = _post(client, billing_key, "/registry/billing/summary",
                  actor_email=org["owner"])
        assert r.status_code == 403, r.text


# ── The hand-lists defend themselves ─────────────────────────────────────────

class TestThisSuiteIsRegistered:
    def test_this_suite_is_named_in_the_ci_skip_guard(self):
        workflow = (_ROOT / ".github/workflows/pr-check.yml").read_text(
            encoding="utf-8")
        assert "tests/unit/test_customer_console_billing_reads.py" in workflow

    def test_this_suite_is_named_in_the_owning_spec_verify_block(self):
        spec = (_ROOT / "project-docs/specs/customer_console.md").read_text(
            encoding="utf-8")
        assert "tests/unit/test_customer_console_billing_reads.py" in spec
