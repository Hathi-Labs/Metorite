"""CP-2f — the Console member-write door, and D50.3's first-resolve promotion.

Spec: ``project-docs/specs/customer_console.md`` CP-2f (done-whens 1-7, 9) ·
``subscription_console.md`` SC-2c · ``work_plan.md`` §3 **D49** ·
``user_management_contract.md`` R11 · D12 · D19.3 / D32.5.

The gap this suite exists to keep closed, stated once:

    Until 2026-08-24 ``POST /orgs/provision``'s **founder** INSERT was the ONLY
    membership writer in this service. So a colleague invited through the tenant
    plane never reached the registry, and three things followed: they were
    invisible to ``GET /me/members``, ``_seat_admin_target`` 404'd them so no
    seat could be assigned, and — the load-bearing one —
    ``store.deployment_visible_orgs`` returned nothing for them, so their
    sign-in resolved to **zero organizations** and the self-serve funnel offered
    to create them an organization OF THEIR OWN. **An invite, correctly
    performed, produced a second tenant.**

⚠️ **R8, and it is not decoration.** Every clause here reads or writes real SQL
whose behaviour a fake would simply agree with: an ``ON CONFLICT DO NOTHING``
whose ``RETURNING`` is empty on the conflict path (the whole create-only claim),
a guarded ``UPDATE … AND status = 'invited'`` (the whole un-suspension claim), a
CITEXT identity upsert, and a four-table placement∩membership join. So this suite
skips **loudly** without a server, and ``pr-check.yml``'s hand-list names it —
``test_this_suite_is_named_in_the_ci_skip_guard`` keeps that true from inside.

Run::

    export CUSTOMER_CONSOLE_DATABASE_URL=postgresql+psycopg://cc:cc@127.0.0.1:5442/cc_platform
    uv run pytest tests/unit/test_customer_console_member_write.py
"""
from __future__ import annotations

import os
import uuid
from pathlib import Path

import pytest

pytest.importorskip("fastapi")

from customer_console import store
from customer_console.auth import MEMBER_ADMIN_CAPABILITY, SEAT_ADMIN_CAPABILITY
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

#: Where this suite's organizations are BORN. Named once rather than invented at
#: fifteen call sites — the resolve suite's `PARKING_LABEL` argument, applied to
#: a file whose subject is memberships rather than placement.
BOX_LABEL = "member-write-suite-box"


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
    """This suite's deployment row — per TEST, because another suite empties it."""
    with db.begin() as c:
        ensure_deployment(c, label=BOX_LABEL)


# ── Helpers ──────────────────────────────────────────────────────────────────

def _new_org(client, *, core_seats: int = 3, label: str = BOX_LABEL) -> dict:
    """Provision an org through the product and return ``{slug, owner}``."""
    slug = f"cp2f-{uuid.uuid4().hex[:8]}"
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


def _key(db, *, deployment_id: str, capabilities: list[str] | None = None) -> str:
    with db.begin() as c:
        return mint_deployment_key(
            c, deployment_id=deployment_id, capabilities=capabilities,
        )


@pytest.fixture
def member_key(db):
    """A deployment key on THIS box carrying only ``member_admin``."""
    return _key(
        db,
        deployment_id=_deployment_id(db),
        capabilities=[MEMBER_ADMIN_CAPABILITY],
    )


def _add(client, key: str, **body) -> object:
    return client.post(
        "/registry/members",
        headers={"Authorization": f"Bearer {key}"},
        json=body,
    )


def _status(db, *, org_id: str, email: str) -> str | None:
    with db.begin() as c:
        return c.execute(
            text(
                """
                SELECT m.status FROM org_membership m
                JOIN user_identity ui ON ui.id = m.user_identity_id
                WHERE m.organization_id = :o AND ui.email = :e
                """
            ),
            {"o": org_id, "e": email},
        ).scalar_one_or_none()


def _set_status(db, *, org_id: str, email: str, status: str) -> None:
    """Force a membership status directly — the PRECONDITION, never the subject.

    The Console ships no status-write door (CP-2f's named residual), so a
    ``suspended`` or ``removed`` member cannot be constructed through the
    product. This helper builds the world; every assertion below is still about
    what the PRODUCT does to it.
    """
    with db.begin() as c:
        c.execute(
            text(
                """
                UPDATE org_membership m SET status = :s
                  FROM user_identity ui
                 WHERE ui.id = m.user_identity_id
                   AND m.organization_id = :o AND ui.email = :e
                """
            ),
            {"s": status, "o": org_id, "e": email},
        )


def _joined_at(db, *, org_id: str, email: str):
    with db.begin() as c:
        return c.execute(
            text(
                """
                SELECT m.joined_at FROM org_membership m
                JOIN user_identity ui ON ui.id = m.user_identity_id
                WHERE m.organization_id = :o AND ui.email = :e
                """
            ),
            {"o": org_id, "e": email},
        ).scalar_one()


def _resolve(client, key: str, *, email: str):
    return client.post(
        "/registry/resolve",
        headers={"Authorization": f"Bearer {key}"},
        json={"email": email},
    )


# ── Done-when 1 — the capability gate ────────────────────────────────────────

class TestTheCapabilityGate:
    """The door is on the deployment-key scheme, behind ``member_admin``."""

    def test_a_resolve_only_key_is_refused_and_told_what_it_lacks(
        self, client, db
    ):
        # `{resolve}` is the COLUMN DEFAULT and the only set anything mints by
        # accident — so this is the shape a real deployment key has today, and
        # it is why the door ships dark by construction.
        org = _new_org(client)
        narrow = _key(db, deployment_id=_deployment_id(db))
        r = _add(client, narrow, member_email="new@x.example",
                 actor_email=org["owner"])
        # 403, not 401: the credential is valid, it simply may not do this.
        assert r.status_code == 403, r.text
        assert MEMBER_ADMIN_CAPABILITY in r.json()["detail"]

    def test_the_seat_admin_capability_does_not_open_this_door(self, client, db):
        # The whole argument for a FOURTH capability rather than reusing the
        # third: "may move seats" must not silently mean "may create members".
        # If this ever passes, the two doors have been conflated.
        org = _new_org(client)
        seatish = _key(
            db, deployment_id=_deployment_id(db),
            capabilities=[SEAT_ADMIN_CAPABILITY],
        )
        r = _add(client, seatish, member_email="new@x.example",
                 actor_email=org["owner"])
        assert r.status_code == 403, r.text
        assert MEMBER_ADMIN_CAPABILITY in r.json()["detail"]

    def test_a_member_admin_key_gets_in(self, client, member_key):
        org = _new_org(client)
        r = _add(client, member_key, member_email="new@x.example",
                 actor_email=org["owner"])
        assert r.status_code == 200, r.text


# ── Done-when 2 — the org is DERIVED, never named (R11) ──────────────────────

class TestTheOrgIsDerived:
    """The caller makes no tenant claim; the org is the ANSWER."""

    def test_naming_an_org_is_400_never_ignored(self, client, member_key):
        org = _new_org(client)
        r = _add(client, member_key, member_email="new@x.example",
                 actor_email=org["owner"], org_slug=org["slug"])
        # 400 rather than a 200 that silently ignored the field: an ignored
        # field is a caller who believes it named its tenant.
        assert r.status_code == 400, r.text
        assert "may not name an organization" in r.json()["detail"]

    def test_a_missing_actor_is_400(self, client, member_key):
        r = _add(client, member_key, member_email="new@x.example")
        assert r.status_code == 400, r.text
        assert "actor_email is required" in r.json()["detail"]

    def test_the_three_invisible_cases_are_ONE_byte_identical_403(
        self, client, db, member_key
    ):
        """A distinguishable negative IS a cross-org existence oracle."""
        # (a) an address the Console has never heard of.
        unknown = _add(client, member_key, member_email="x@y.example",
                       actor_email=f"nobody-{uuid.uuid4().hex[:6]}@z.example")
        # (b) a real owner of a real org placed on ANOTHER deployment.
        with db.begin() as c:
            ensure_deployment(c, label="cp2f-elsewhere")
        elsewhere = _new_org(client, label="cp2f-elsewhere")
        other_box = _add(client, member_key, member_email="x@y.example",
                         actor_email=elsewhere["owner"])
        # (c) an owner of an org on THIS box that has been deleted.
        dead = _new_org(client)
        for target in ("cancelled", "deleted"):
            assert client.post("/orgs/lifecycle", headers=OP, json={
                "org_slug": dead["slug"], "target": target,
            }).status_code == 200
        deleted = _add(client, member_key, member_email="x@y.example",
                       actor_email=dead["owner"])

        for r in (unknown, other_box, deleted):
            assert r.status_code == 403, r.text
        assert unknown.json() == other_box.json() == deleted.json(), (
            "the three invisible cases must be BYTE-IDENTICAL — telling them "
            "apart tells a caller whether an org it does not serve exists"
        )

    def test_an_actor_in_two_orgs_on_this_box_is_409_not_a_guess(
        self, client, db, member_key
    ):
        a = _new_org(client)
        b = _new_org(client)
        shared = f"both-{uuid.uuid4().hex[:6]}@x.example"
        with db.begin() as c:
            identity = store.ensure_identity(c, email=shared)
            for org in (a, b):
                c.execute(
                    text(
                        "INSERT INTO org_membership (organization_id, "
                        "user_identity_id, role, status, joined_at) "
                        "VALUES (:o, :i, 'admin', 'active', now())"
                    ),
                    {"o": org["id"], "i": identity},
                )
        r = _add(client, member_key, member_email="new@x.example",
                 actor_email=shared)
        # Choosing among them is the chooser, a named non-goal — as at resolve.
        assert r.status_code == 409, r.text


# ── Done-when 3 — the member is visible AND seatable ─────────────────────────

class TestTheInvitedMemberIsVisibleAndSeatable:
    """The end-to-end proof that the three consequences are closed."""

    def test_the_row_lands_invited_at_the_default_role(
        self, client, db, member_key
    ):
        org = _new_org(client)
        who = f"colleague-{uuid.uuid4().hex[:6]}@x.example"
        r = _add(client, member_key, member_email=who,
                 actor_email=org["owner"], display_name="Ada")
        assert r.status_code == 200, r.text
        assert r.json() == {"created": True, "status": "invited"}

        with db.begin() as c:
            rows = store.org_members(c, org_id=org["id"])
        entry = next(m for m in rows if m["email"].lower() == who)
        assert entry["status"] == "invited"
        # D12: the registry role is billing vocabulary and this door maps
        # nothing onto it. A `role` that is anything but the column default
        # means a cross-plane mapping was minted.
        assert entry["role"] == "member"

    def test_the_seat_door_no_longer_404s_them(self, client, db, member_key):
        """``_seat_admin_target`` used to refuse every invited colleague."""
        org = _new_org(client)
        who = f"seatme-{uuid.uuid4().hex[:6]}@x.example"
        seat_key = _key(
            db, deployment_id=_deployment_id(db),
            capabilities=[SEAT_ADMIN_CAPABILITY],
        )

        # BEFORE: the exact 404 that made an invited colleague unseatable.
        before = client.post("/registry/seats", headers={
            "Authorization": f"Bearer {seat_key}",
        }, json={"member_email": who, "plan_slug": "core",
                 "actor_email": org["owner"]})
        assert before.status_code in (400, 404), before.text

        assert _add(client, member_key, member_email=who,
                    actor_email=org["owner"]).status_code == 200

        # AFTER: the target resolves. `core` is refused for its own reason
        # (membership IS the Core seat), which is a DIFFERENT refusal — so the
        # discriminator is the seat door reaching its own rule at all.
        # `sales` is a SEEDED Center plan (002_seed_catalog.sql), so the FK on
        # `seat_grant.plan_slug` is satisfied by the product's own catalog
        # rather than by a row this test invented.
        with db.begin() as c:
            store.grant_seats(c, org_id=org["id"], plan_slug="sales",
                              quantity=2, reason="test")
        after = client.post("/registry/seats", headers={
            "Authorization": f"Bearer {seat_key}",
        }, json={"member_email": who, "plan_slug": "sales",
                 "actor_email": org["owner"]})
        assert after.status_code == 200, after.text
        assert after.json()["assigned"] is True

    def test_they_resolve_to_their_employer_not_to_console_empty(
        self, client, db, member_key
    ):
        """The load-bearing one: no `{"organizations": []}`, no signup funnel."""
        org = _new_org(client)
        who = f"joiner-{uuid.uuid4().hex[:6]}@x.example"
        resolve_key = _key(db, deployment_id=_deployment_id(db))

        before = _resolve(client, resolve_key, email=who)
        assert before.status_code == 200
        # This is the bug, reproduced: zero orgs is what the gateway turns into
        # `signup_eligible=True` and the browser into "create your own company".
        assert before.json() == {"organizations": []}

        assert _add(client, member_key, member_email=who,
                    actor_email=org["owner"]).status_code == 200

        after = _resolve(client, resolve_key, email=who)
        assert after.status_code == 200, after.text
        slugs = [o["slug"] for o in after.json()["organizations"]]
        assert slugs == [org["slug"]]


# ── Done-when 4 — no seat is burned ──────────────────────────────────────────

class TestTheInviteBurnsNoSeat:
    """A membership row is not a seat; D19.3's burn point is unmoved."""

    def test_assigned_is_unchanged_across_the_write(
        self, client, db, member_key
    ):
        org = _new_org(client, core_seats=3)

        def assigned() -> int:
            r = client.get(f"/billing/summary?org_slug={org['slug']}",
                           headers=OP)
            assert r.status_code == 200, r.text
            core = next(p for p in r.json()["seats"] if p["plan_slug"] == "core")
            return core["assigned"]

        before = assigned()
        for n in range(3):
            assert _add(
                client, member_key,
                member_email=f"n{n}-{uuid.uuid4().hex[:6]}@x.example",
                actor_email=org["owner"],
            ).status_code == 200
        # Three invites into a THREE-seat org. If a membership burned a seat the
        # org would now be full and the founder's own re-resolve would 409.
        assert assigned() == before

    def test_the_seat_arrives_only_at_first_resolve(
        self, client, db, member_key
    ):
        org = _new_org(client, core_seats=3)
        who = f"late-{uuid.uuid4().hex[:6]}@x.example"
        resolve_key = _key(db, deployment_id=_deployment_id(db))
        assert _add(client, member_key, member_email=who,
                    actor_email=org["owner"]).status_code == 200

        with db.begin() as c:
            identity = store.ensure_identity(c, email=who)
            assert not store.has_live_seat(
                c, org_id=org["id"], plan_slug="core", identity_id=identity,
            )

        r = _resolve(client, resolve_key, email=who)
        assert r.status_code == 200, r.text
        assert r.json()["organizations"][0]["seat"] == "allocated"


# ── Done-when 5 — the write is create-only ───────────────────────────────────

class TestTheWriteIsCreateOnly:
    """A conflict changes NOTHING, and the caller is told so."""

    def test_re_inviting_an_active_member_does_not_demote_them(
        self, client, db, member_key
    ):
        org = _new_org(client)
        # The founder is `owner`/`active` from provisioning — the strongest
        # possible thing to accidentally overwrite.
        r = _add(client, member_key, member_email=org["owner"],
                 actor_email=org["owner"])
        assert r.status_code == 200, r.text
        assert r.json() == {"created": False, "status": "active"}
        assert _status(db, org_id=org["id"], email=org["owner"]) == "active"
        with db.begin() as c:
            roster = store.org_members(c, org_id=org["id"])
        owner_row = next(
            m for m in roster if m["email"].lower() == org["owner"]
        )
        assert owner_row["role"] == "owner", (
            "a re-invite must not touch the registry role either"
        )

    def test_re_inviting_a_removed_member_does_not_resurrect_them(
        self, client, db, member_key
    ):
        org = _new_org(client)
        who = f"gone-{uuid.uuid4().hex[:6]}@x.example"
        assert _add(client, member_key, member_email=who,
                    actor_email=org["owner"]).status_code == 200
        _set_status(db, org_id=org["id"], email=who, status="removed")

        r = _add(client, member_key, member_email=who,
                 actor_email=org["owner"])
        assert r.status_code == 200, r.text
        # Reinstating somebody is an `admin:members:manage` decision on the
        # tenant plane; it must not ride in on the weaker invite door.
        assert r.json() == {"created": False, "status": "removed"}
        assert _status(db, org_id=org["id"], email=who) == "removed"

    def test_the_identity_is_case_insensitive_and_not_duplicated(
        self, client, db, member_key
    ):
        org = _new_org(client)
        who = f"Case-{uuid.uuid4().hex[:6]}@X.Example"
        assert _add(client, member_key, member_email=who,
                    actor_email=org["owner"]).status_code == 200
        again = _add(client, member_key, member_email=who.lower(),
                     actor_email=org["owner"])
        # `user_identity.email` is CITEXT (001:110) — one human, not two.
        assert again.json()["created"] is False
        with db.begin() as c:
            rows = [
                m for m in store.org_members(c, org_id=org["id"])
                if m["email"].lower() == who.lower()
            ]
        assert len(rows) == 1


# ── Done-when 6 — the actor gate ─────────────────────────────────────────────

class TestTheActorMustBeAnActiveMember:
    """``status='active'``, ANY role — and the role width is deliberate."""

    @pytest.mark.parametrize("status", ["invited", "suspended", "removed"])
    def test_a_non_active_actor_is_refused(
        self, client, db, member_key, status
    ):
        org = _new_org(client)
        _set_status(db, org_id=org["id"], email=org["owner"], status=status)
        r = _add(client, member_key, member_email="new@x.example",
                 actor_email=org["owner"])
        assert r.status_code == 403, r.text
        assert _status(db, org_id=org["id"], email="new@x.example") is None

    def test_a_plain_active_member_MAY_invite_and_the_reason_matters(
        self, client, db, member_key
    ):
        """A Console `member` is what the tenant's SECOND admin actually is.

        An `owner|admin` gate here would 403 their invites — best-effort, so
        nothing surfaces — and every colleague they invited would resolve
        `console-empty` into an org of their own. That is the exact funnel this
        ticket closes, so the role set is wider than the seat door's ON PURPOSE.
        """
        org = _new_org(client)
        plain = f"plain-{uuid.uuid4().hex[:6]}@x.example"
        with db.begin() as c:
            identity = store.ensure_identity(c, email=plain)
            c.execute(
                text(
                    "INSERT INTO org_membership (organization_id, "
                    "user_identity_id, role, status, joined_at) "
                    "VALUES (:o, :i, 'member', 'active', now())"
                ),
                {"o": org["id"], "i": identity},
            )
        r = _add(client, member_key,
                 member_email=f"theirs-{uuid.uuid4().hex[:6]}@x.example",
                 actor_email=plain)
        assert r.status_code == 200, r.text

    def test_a_suspended_organization_cannot_grow(self, client, member_key):
        org = _new_org(client)
        assert client.post("/orgs/lifecycle", headers=OP, json={
            "org_slug": org["slug"], "target": "suspended",
        }).status_code == 200
        r = _add(client, member_key, member_email="new@x.example",
                 actor_email=org["owner"])
        assert r.status_code == 403, r.text
        assert "membership is locked" in r.json()["detail"]


# ── Done-when 7 — D50.3, the guarded promotion ───────────────────────────────

class TestFirstResolveActivatesTheInvited:
    def test_an_invited_member_becomes_active_and_joined_at_is_stamped(
        self, client, db, member_key
    ):
        org = _new_org(client)
        who = f"first-{uuid.uuid4().hex[:6]}@x.example"
        resolve_key = _key(db, deployment_id=_deployment_id(db))
        assert _add(client, member_key, member_email=who,
                    actor_email=org["owner"]).status_code == 200
        assert _status(db, org_id=org["id"], email=who) == "invited"
        assert _joined_at(db, org_id=org["id"], email=who) is None

        assert _resolve(client, resolve_key, email=who).status_code == 200
        assert _status(db, org_id=org["id"], email=who) == "active"
        assert _joined_at(db, org_id=org["id"], email=who) is not None

    def test_a_second_resolve_does_not_re_stamp_joined_at(
        self, client, db, member_key
    ):
        org = _new_org(client)
        who = f"again-{uuid.uuid4().hex[:6]}@x.example"
        resolve_key = _key(db, deployment_id=_deployment_id(db))
        assert _add(client, member_key, member_email=who,
                    actor_email=org["owner"]).status_code == 200
        assert _resolve(client, resolve_key, email=who).status_code == 200
        first = _joined_at(db, org_id=org["id"], email=who)
        assert _resolve(client, resolve_key, email=who).status_code == 200
        # `COALESCE(joined_at, now())` — a returning member keeps their first
        # join date, and the guard means the UPDATE does not even match.
        assert _joined_at(db, org_id=org["id"], email=who) == first


class TestThePromotionIsGuardedToInvited:
    """``AND status = 'invited'`` lives in the UPDATE's own ``WHERE``."""

    @pytest.mark.parametrize("status", ["suspended", "removed"])
    def test_resolve_never_promotes_anything_but_invited(
        self, client, db, member_key, status
    ):
        org = _new_org(client)
        who = f"guard-{uuid.uuid4().hex[:6]}@x.example"
        resolve_key = _key(db, deployment_id=_deployment_id(db))
        assert _add(client, member_key, member_email=who,
                    actor_email=org["owner"]).status_code == 200
        _set_status(db, org_id=org["id"], email=who, status=status)

        assert _resolve(client, resolve_key, email=who).status_code == 200
        # The mutation this is shown red with: dropping the `AND status =
        # 'invited'` predicate reactivates a removed member on their next
        # sign-in — the failure `colleague_onboarding.md` §6 predicted before
        # this existed.
        assert _status(db, org_id=org["id"], email=who) == status

    def test_a_suspended_ORGANIZATION_still_refuses_and_promotes_nobody(
        self, client, db, member_key
    ):
        """Org lifecycle and membership status are different axes."""
        org = _new_org(client)
        who = f"orgsusp-{uuid.uuid4().hex[:6]}@x.example"
        resolve_key = _key(db, deployment_id=_deployment_id(db))
        assert _add(client, member_key, member_email=who,
                    actor_email=org["owner"]).status_code == 200
        assert client.post("/orgs/lifecycle", headers=OP, json={
            "org_slug": org["slug"], "target": "cancelled",
        }).status_code == 200
        assert client.post("/orgs/lifecycle", headers=OP, json={
            "org_slug": org["slug"], "target": "deleted",
        }).status_code == 200

        r = _resolve(client, resolve_key, email=who)
        # `deleted` is the one state with can_sign_in=False, so the partition
        # above the promotion refuses first and nothing is written.
        assert r.status_code == 403, r.text
        assert _status(db, org_id=org["id"], email=who) == "invited"

    def test_the_operator_arm_activates_nobody(self, client, db, member_key):
        """A staff query about a named customer must not activate anybody."""
        org = _new_org(client)
        who = f"staff-{uuid.uuid4().hex[:6]}@x.example"
        assert _add(client, member_key, member_email=who,
                    actor_email=org["owner"]).status_code == 200
        r = client.post("/registry/resolve", headers=OP,
                        json={"org_slug": org["slug"], "email": who})
        assert r.status_code == 200, r.text
        assert _status(db, org_id=org["id"], email=who) == "invited"


# ── Done-when 9 — the R8 gate cannot silently disarm ─────────────────────────

def test_this_suite_is_named_in_the_ci_skip_guard() -> None:
    """A hand-list that nothing checks is a hand-list that goes stale.

    ``pr-check.yml``'s skip-guard names the R8 suites that must actually RUN;
    a suite missing from it still runs in the directory step and still SKIPS
    silently there when the database variable is absent, which is exactly the
    CP-3 disarmed-gate failure. This is the closest a hand-list gets to
    defending itself — the precedent is CP-9 clause 11's.
    """
    workflow = (_ROOT / ".github" / "workflows" / "pr-check.yml").read_text(
        encoding="utf-8"
    )
    assert "tests/unit/test_customer_console_member_write.py" in workflow, (
        "this suite is not in pr-check.yml's R8 skip-guard list — without the "
        "entry it can skip in CI while the job reports green"
    )


def test_this_suite_is_named_in_the_spec_verification_block() -> None:
    """The spec's §7 is what a human runs; a suite absent from it is invisible."""
    spec = (
        _ROOT / "project-docs" / "specs" / "customer_console.md"
    ).read_text(encoding="utf-8")
    assert "tests/unit/test_customer_console_member_write.py" in spec, (
        "customer_console.md §7 does not name this suite — CP-2f's acceptance "
        "would then be verified by nobody following the spec"
    )


# ── Review round 1, finding 3 — the cap-vs-promotion interaction ─────────────

class TestTheCapRollsThePromotionBack:
    def test_a_cap_409_rolls_the_promotion_back(self, client, db, member_key):
        """A colleague refused a seat stays ``invited`` and no seat is burned.

        Measured in review round 1 (finding 3): ``activate_invited_member`` and
        ``_allocate_core_seat`` share one transaction, so the seat-cap
        ``HTTPException`` unwinds the promotion with it. The comment above the
        promotion used to claim the OPPOSITE order of events; this pins the
        real one so neither the comment nor the behaviour can drift silently.
        """
        org = _new_org(client, core_seats=1)
        resolve_key = _key(db, deployment_id=_deployment_id(db))
        # The founder takes the only seat.
        assert _resolve(client, resolve_key, email=org["owner"]).status_code == 200

        who = f"capped-{uuid.uuid4().hex[:6]}@x.example"
        assert _add(client, member_key, member_email=who,
                    actor_email=org["owner"]).status_code == 200
        assert _status(db, org_id=org["id"], email=who) == "invited"

        r = _resolve(client, resolve_key, email=who)
        assert r.status_code == 409, r.text
        # The rollback is the assertion: still invited, joined_at never stamped.
        assert _status(db, org_id=org["id"], email=who) == "invited"
        assert _joined_at(db, org_id=org["id"], email=who) is None


# ── Review round 1, finding 8 — the REAL client against the REAL route ───────

class TestTheGatewayClientSpeaksThisDoorsWire:
    def test_invite_member_on_console_lands_a_row_end_to_end(
        self, client, db, member_key, monkeypatch
    ):
        """`acb_auth.console_resolve.invite_member_on_console` → the real app.

        Both halves shipped with their own suites — the client against a
        ``MockTransport``, the door via ``TestClient`` — so a field-name drift
        between them would have stayed green on both sides and shown up only in
        production as a silently-logged refusal (finding 1's blindness). This
        drives the REAL client through ``httpx.ASGITransport`` into the REAL
        Console app, the ``test_org_provisioning.py`` precedent applied to the
        member door.
        """
        import asyncio

        import httpx

        from acb_auth import console_resolve as cr

        org = _new_org(client)
        who = f"wire-{uuid.uuid4().hex[:6]}@x.example"

        monkeypatch.setenv("CUSTOMER_CONSOLE_URL", "http://console.test")
        monkeypatch.setenv("CUSTOMER_CONSOLE_DEPLOYMENT_KEY", member_key)

        from customer_console.main import app as console_app

        def asgi_client():
            return httpx.AsyncClient(
                transport=httpx.ASGITransport(app=console_app),
                base_url="http://console.test",
                timeout=5.0,
            )

        monkeypatch.setattr(cr, "_new_http_client", asgi_client)

        status, body = asyncio.run(
            cr.invite_member_on_console(
                actor_email=org["owner"], member_email=who,
            )
        )
        assert status == 200, body
        # The wire matched: the row is really there, with the door's semantics.
        assert _status(db, org_id=org["id"], email=who) == "invited"
