"""CP-2h slice 1 — ``POST /registry/seats/overview``, the D-SEAT-4 seat READ.

Spec: ``project-docs/specs/customer_console.md`` §6 **CP-2h** (D-SEAT-4) ·
§6 items (g)/(h)/(i) · ``user_management_contract.md`` R11 · D12 · D32.5.

The gap this suite exists to keep closed, stated once:

    The customer Seats tab (Settings → Organization → Seat assignments) read its
    counts and its roster from the Console's ORGANIZATION-key doors,
    ``GET /me/seats`` + ``GET /me/members``, through a per-org
    ``CUSTOMER_CONSOLE_ORG_KEY`` held by the workbench. **A ``cc_live_`` key IS an
    organization** (CP-3), so a SHARED multi-tenant deployment has no single
    correct one: the variable is unset, every seat read 503s, and the tab reads
    "not configured for this deployment" permanently — the state the owner
    photographed on 2026-08-24. The `seat_admin` capability opened two WRITES and
    no read, so there was no per-BOX door to move to. This is that door.

What the fences below pin, and why each is the kind of claim a fake would agree
with rather than test:

1. **One seat vocabulary.** The overview's ``plans`` must be byte-identical to
   ``GET /me/seats``'s for the same org, and its ``members`` byte-identical to
   ``GET /me/members``'s. Two doors reporting two answers about one org's seats
   is precisely the D32.5 failure, and only a real grid over real
   ``seat_assignment``/``seat_grant`` rows can show they agree.
2. **Org A can never read org B.** The org is DERIVED from
   ``deployment_visible_orgs(deployment_id, actor_email)`` — a four-table
   placement ∩ membership join — never named. A fake join agrees with whatever
   it is handed; a real one is what proves B's members are absent.
3. **The role gate is the writes' gate.** An active ``member`` is 403, from the
   one shared ``_admin_scheme_context``.
4. **A ``deleted`` organization is refused**, through ``capabilities_of``'s
   ``can_sign_in``, on the real lifecycle column.
5. **R11 and the capability gate**: a deployment key naming an ``org_slug`` is
   400; a key without ``seat_admin`` is 403.

⚠️ **R8, and it is not decoration.** Every clause reads real SQL: the seat grid
(``seat_rows`` over the live/released predicate), the CITEXT roster join, and
the placement ∩ membership derivation. So this suite skips **loudly** without a
server, and ``pr-check.yml``'s hand-list names it —
``test_this_suite_is_named_in_the_ci_skip_guard`` keeps that true from inside.

Run::

    export CUSTOMER_CONSOLE_DATABASE_URL=postgresql+psycopg://cc:cc@127.0.0.1:5442/cc_platform
    uv run pytest tests/unit/test_customer_console_seat_overview.py
"""
from __future__ import annotations

import os
import uuid
from pathlib import Path

import pytest

pytest.importorskip("fastapi")

from customer_console.auth import (
    MEMBER_ADMIN_CAPABILITY,
    RESOLVE_CAPABILITY,
    SEAT_ADMIN_CAPABILITY,
)
from customer_console.main import app
from fastapi.testclient import TestClient
from sqlalchemy import create_engine

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
#: every call site — `test_customer_console_member_write.py`'s argument, applied
#: to a file whose subject is a read.
BOX_LABEL = "seat-overview-suite-box"

#: The door under test. Written once so a rename is one edit, not fifteen.
OVERVIEW = "/registry/seats/overview"


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
    """Provision an org through the product and return ``{slug, owner, id}``."""
    slug = f"cp2h-{uuid.uuid4().hex[:8]}"
    owner = f"owner@{slug}.example"
    r = client.post("/orgs/provision", headers=OP, json={
        "slug": slug, "name": "N", "owner_email": owner,
        "core_seats": core_seats, "deployment_label": label,
    })
    assert r.status_code == 200, r.text
    return {"slug": slug, "owner": owner, "id": r.json()["organization_id"]}


def _deployment_id(db, label: str = BOX_LABEL) -> str:
    from sqlalchemy import text

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
def seat_key(db):
    """A deployment key on THIS box carrying the full customer-admin set.

    ``resolve`` rides along because the promotion path (D50.3, ``invited →
    active``) is how this suite builds an ACTIVE non-admin member through the
    product rather than by hand; ``member_admin`` because that is the door that
    creates one. The capability under test is ``seat_admin``, and
    :class:`TestTheCapabilityGate` mints a key WITHOUT it.
    """
    return _key(db, capabilities=[
        RESOLVE_CAPABILITY, MEMBER_ADMIN_CAPABILITY, SEAT_ADMIN_CAPABILITY,
    ])


def _overview(client, key: str, **body):
    return client.post(
        OVERVIEW, headers={"Authorization": f"Bearer {key}"}, json=body,
    )


def _org_key(client, slug: str) -> str:
    """A real ``cc_live_`` organization key — the credential the OLD path used."""
    r = client.post("/keys", headers=OP, json={"org_slug": slug})
    assert r.status_code == 200, r.text
    token = r.json()["token"]
    assert token.startswith("cc_live_")
    return token


def _add_member(client, key: str, *, actor: str, email: str):
    return client.post(
        "/registry/members",
        headers={"Authorization": f"Bearer {key}"},
        json={"member_email": email, "actor_email": actor},
    )


def _resolve(client, key: str, *, email: str):
    return client.post(
        "/registry/resolve",
        headers={"Authorization": f"Bearer {key}"},
        json={"email": email},
    )


# ── The capability gate ──────────────────────────────────────────────────────

class TestTheCapabilityGate:
    """The read sits on ``seat_admin``, the SAME capability as the two writes."""

    def test_a_resolve_only_key_is_refused_and_told_what_it_lacks(
        self, client, db
    ):
        # `{resolve}` is the COLUMN DEFAULT and the only set anything mints by
        # accident — so this is the shape a real deployment key has today, and
        # it is why this door ships dark by construction.
        org = _new_org(client)
        narrow = _key(db)
        r = _overview(client, narrow, actor_email=org["owner"])
        # 403, not 401: the credential is valid, it simply may not do this.
        assert r.status_code == 403, r.text
        assert SEAT_ADMIN_CAPABILITY in r.json()["detail"]

    def test_the_member_admin_capability_does_not_open_this_door(
        self, client, db
    ):
        # "may invite members" must not silently mean "may read the seat grid".
        org = _new_org(client)
        memberish = _key(db, capabilities=[MEMBER_ADMIN_CAPABILITY])
        r = _overview(client, memberish, actor_email=org["owner"])
        assert r.status_code == 403, r.text
        assert SEAT_ADMIN_CAPABILITY in r.json()["detail"]

    def test_an_organization_key_cannot_reach_it(self, client, db, seat_key):
        # The whole point of the slice: a `cc_live_` key is what the OLD path
        # held, and it may not open the per-BOX door. If this ever passes, the
        # two credentials have been conflated and a shared deployment could
        # serve one tenant's key another tenant's answer.
        org = _new_org(client)
        live = _org_key(client, org["slug"])
        r = _overview(client, live, actor_email=org["owner"])
        assert r.status_code == 401, r.text

    def test_no_credential_at_all_is_refused(self, client):
        assert client.post(OVERVIEW, json={"actor_email": "a@x.example"}) \
            .status_code == 401


# ── R11 — the body may not name a tenant ─────────────────────────────────────

class TestR11:
    def test_a_deployment_key_naming_an_org_slug_is_400_never_ignored(
        self, client, seat_key
    ):
        """The org is the ANSWER, derived from placement ∩ membership.

        400 rather than ignored, because an ignored field is a caller who
        believes it named its tenant. Mutation: dropping the ``org_slug`` check
        in ``_admin_scheme_for_deployment`` turns this into a 200 over whichever
        org the derivation happened to pick.
        """
        org = _new_org(client)
        r = _overview(
            client, seat_key, actor_email=org["owner"], org_slug=org["slug"],
        )
        assert r.status_code == 400, r.text
        assert "may not name an organization" in r.json()["detail"]

    def test_an_actorless_body_is_400(self, client, seat_key):
        # The deployment key is the BOX, not a person: with no actor there is no
        # membership to join on and therefore no org. Refused, never guessed.
        r = _overview(client, seat_key)
        assert r.status_code == 400, r.text
        assert "actor_email" in r.json()["detail"]

    def test_an_unknown_actor_is_a_byte_identical_403(self, client, seat_key):
        # No cross-org existence oracle: "not a member here" and "nobody at all"
        # answer the same sentence (CP-2b clause 5).
        unknown = _overview(client, seat_key, actor_email="ghost@nowhere.example")
        assert unknown.status_code == 403, unknown.text
        assert unknown.json()["detail"] == (
            "the acting member is not an admin on this deployment"
        )


# ── The ONE seat vocabulary — the overview IS the two org-key reads ──────────

class TestOneSeatVocabulary:
    """D32.5: two doors onto one org's seats may never report two answers."""

    def test_the_plans_block_is_byte_identical_to_GET_me_seats(
        self, client, seat_key
    ):
        """The counts an admin sees through the deployment door and through the
        organization key are the SAME numbers, because they are the same
        ``_seat_grid`` call.

        Mutation: recomputing the grid here (or filtering it differently) fails
        this equality the moment an org holds a grant, which is why the org is
        provisioned with seats and its owner already holds one.
        """
        org = _new_org(client, core_seats=3)
        live = _org_key(client, org["slug"])

        mine = client.get(
            "/me/seats", headers={"Authorization": f"Bearer {live}"},
        )
        assert mine.status_code == 200, mine.text

        over = _overview(client, seat_key, actor_email=org["owner"])
        assert over.status_code == 200, over.text

        assert over.json()["plans"] == mine.json()["plans"]
        # And it is a real grid, not two empty lists agreeing with each other.
        core = next(p for p in over.json()["plans"] if p["plan_slug"] == "core")
        assert (core["purchased"], core["assigned"]) == (3, 1)
        assert core["available"] == 2
        assert core["oversubscribed"] is False

    def test_the_members_block_is_byte_identical_to_GET_me_members(
        self, client, seat_key
    ):
        """The roster is ``MemberView``'s rows — the same three membership fields
        plus the LS-7 seat slugs — not a second shape composed here."""
        org = _new_org(client)
        live = _org_key(client, org["slug"])

        mine = client.get(
            "/me/members", headers={"Authorization": f"Bearer {live}"},
        )
        assert mine.status_code == 200, mine.text

        over = _overview(client, seat_key, actor_email=org["owner"])
        assert over.status_code == 200, over.text
        assert over.json()["members"] == mine.json()["members"]

        rows = over.json()["members"]
        assert [r["email"] for r in rows] == [org["owner"]]
        assert rows[0]["seats"] == ["core"], "the owner holds their Core seat"
        # Exactly the membership triple + seats, nothing invented on this wire.
        assert set(rows[0]) == {"email", "role", "status", "seats"}

    def test_the_envelope_carries_those_two_keys_and_nothing_else(
        self, client, seat_key
    ):
        # A field nothing reads is a field somebody eventually reads —
        # `SeatPlanView`'s rule, applied to the envelope. In particular NO
        # organization id: echoing it back would name the one thing a customer
        # must never be able to name (R11).
        org = _new_org(client)
        body = _overview(client, seat_key, actor_email=org["owner"]).json()
        assert set(body) == {"plans", "members"}

    def test_an_unseated_member_is_present_with_an_empty_seat_list(
        self, client, seat_key
    ):
        """*Unassigned is a state, not an absence* (LS-7). The surface cannot
        offer a seat to somebody who is not on the list."""
        org = _new_org(client)
        colleague = f"colleague@{org['slug']}.example"
        assert _add_member(
            client, seat_key, actor=org["owner"], email=colleague,
        ).status_code == 200

        rows = _overview(
            client, seat_key, actor_email=org["owner"],
        ).json()["members"]
        row = next(r for r in rows if r["email"] == colleague)
        assert row["seats"] == []
        assert row["status"] == "invited"

    def test_the_read_writes_nothing_and_burns_no_seat(self, client, seat_key):
        """Reading the surface must never allocate: the cap is farmable the
        moment a read has a side effect. Ten reads, same counts, same roster."""
        org = _new_org(client, core_seats=3)
        first = _overview(client, seat_key, actor_email=org["owner"]).json()
        for _ in range(10):
            again = _overview(client, seat_key, actor_email=org["owner"]).json()
            assert again == first


# ── Org A can never read org B ───────────────────────────────────────────────

class TestCrossOrgIsolation:
    def test_an_actor_sees_their_own_org_and_never_the_other(
        self, client, seat_key
    ):
        """Two orgs on the SAME deployment, therefore reachable by the SAME key —
        which is exactly the shared-box shape this door exists for. The answer is
        still bounded by the ACTOR's membership.

        Mutation: deriving the org from anything but ``deployment_visible_orgs``
        (a deployment-wide list, say) shows B's roster to A's owner here.
        """
        a = _new_org(client, core_seats=3)
        b = _new_org(client, core_seats=7)

        seen = _overview(client, seat_key, actor_email=a["owner"]).json()
        emails = {r["email"] for r in seen["members"]}
        assert emails == {a["owner"]}
        assert b["owner"] not in emails

        # And the COUNTS are A's, not B's — a grid keyed on the wrong org would
        # still look plausible without this.
        core = next(p for p in seen["plans"] if p["plan_slug"] == "core")
        assert core["purchased"] == 3

        other = _overview(client, seat_key, actor_email=b["owner"]).json()
        assert {r["email"] for r in other["members"]} == {b["owner"]}
        assert next(
            p for p in other["plans"] if p["plan_slug"] == "core"
        )["purchased"] == 7

    def test_a_key_placed_ON_ANOTHER_BOX_resolves_nobody_here(
        self, client, db, seat_key
    ):
        """The placement half of placement ∩ membership. A key minted for a
        DIFFERENT deployment must not read this org, even though the actor is a
        genuine active owner — otherwise one customer's box could read every
        other customer's seats."""
        org = _new_org(client)
        with db.begin() as c:
            ensure_deployment(c, label="cp2h-elsewhere")
        elsewhere = _key(
            db, capabilities=[SEAT_ADMIN_CAPABILITY], label="cp2h-elsewhere",
        )

        r = _overview(client, elsewhere, actor_email=org["owner"])
        assert r.status_code == 403, r.text
        assert r.json()["detail"] == (
            "the acting member is not an admin on this deployment"
        )


# ── The role gate is the WRITES' gate ────────────────────────────────────────

class TestTheRoleGate:
    def test_an_active_plain_member_is_403(self, client, seat_key):
        """The Seats tab is an admin surface, so "may read it" is the same
        question as "may move a seat" — one gate, ``_SEAT_ADMIN_ROLES``.

        The colleague is built through the PRODUCT: invited through the
        ``member_admin`` door (role defaults to ``member``, D12) and promoted to
        ``active`` by their first deployment resolve (D50.3), so the refusal is
        about the ROLE and not about the status.

        Mutation: widening the role set here (to ``_MEMBER_ADMIN_ROLES``, say)
        turns this into a 200 and hands the org's whole roster to any member.
        """
        org = _new_org(client)
        colleague = f"plain@{org['slug']}.example"
        assert _add_member(
            client, seat_key, actor=org["owner"], email=colleague,
        ).status_code == 200
        assert _resolve(client, seat_key, email=colleague).status_code == 200

        r = _overview(client, seat_key, actor_email=colleague)
        assert r.status_code == 403, r.text
        assert r.json()["detail"] == (
            "the acting member is not an active admin of this organization"
        )

    def test_an_invited_admin_who_never_signed_in_is_403(self, client, seat_key):
        """The ``status == 'active'`` half, written once for every door: an actor
        who is themselves still ``invited`` acts for nobody."""
        org = _new_org(client)
        colleague = f"pending@{org['slug']}.example"
        assert _add_member(
            client, seat_key, actor=org["owner"], email=colleague,
        ).status_code == 200

        r = _overview(client, seat_key, actor_email=colleague)
        assert r.status_code == 403, r.text

    def test_the_owner_is_admitted(self, client, seat_key):
        # The positive half of the gate — without it the two 403s above are
        # satisfied by a door that refuses everybody.
        org = _new_org(client)
        assert _overview(
            client, seat_key, actor_email=org["owner"],
        ).status_code == 200


# ── The lifecycle: deleted is refused, suspended can still SEE ───────────────

class TestTheLifecycle:
    def test_a_deleted_organization_is_refused(self, client, seat_key):
        """``capabilities_of(...).can_sign_in`` is false for ``deleted``, so the
        org is inadmissible and the byte-identical 403 answers.

        Reached the only way the graph allows — through ``cancelled``, i.e. after
        the export window — because nothing may jump straight to deletion.
        """
        org = _new_org(client)
        for target in ("cancelled", "deleted"):
            r = client.post("/orgs/lifecycle", headers=OP, json={
                "org_slug": org["slug"], "target": target,
            })
            assert r.status_code == 200, r.text

        r = _overview(client, seat_key, actor_email=org["owner"])
        assert r.status_code == 403, r.text
        assert r.json()["detail"] == (
            "the acting member is not an admin on this deployment"
        )

    def test_a_SUSPENDED_organization_can_still_read_its_seats(
        self, client, seat_key
    ):
        """The §9.3(5) reasoning, one door along: the suspended customer is the
        one deciding whether to pay, so they must be able to SEE what they hold.

        This is why the read carries no ``can_write_seats`` gate — that gate is
        the WRITE's, and copying it here would blind exactly the customer who
        most needs the number.
        """
        org = _new_org(client, core_seats=3)
        assert client.post("/orgs/lifecycle", headers=OP, json={
            "org_slug": org["slug"], "target": "suspended",
        }).status_code == 200

        r = _overview(client, seat_key, actor_email=org["owner"])
        assert r.status_code == 200, r.text
        assert next(
            p for p in r.json()["plans"] if p["plan_slug"] == "core"
        )["purchased"] == 3


# ── The hand-list defends itself ─────────────────────────────────────────────

class TestThisSuiteIsRegistered:
    """An R8 suite that silently skipped would prove nothing while reporting
    green, so this file's name is asserted in both places that keep it armed —
    the closest a hand-list gets to defending itself."""

    def test_this_suite_is_named_in_the_ci_skip_guard(self):
        workflow = (_ROOT / ".github/workflows/pr-check.yml").read_text(
            encoding="utf-8")
        assert "tests/unit/test_customer_console_seat_overview.py" in workflow

    def test_this_suite_is_named_in_the_owning_spec_verify_block(self):
        spec = (_ROOT / "project-docs/specs/customer_console.md").read_text(
            encoding="utf-8")
        assert "tests/unit/test_customer_console_seat_overview.py" in spec
