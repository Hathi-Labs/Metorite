"""The admin plane · the TENANT boundary — what one organization's admin
cannot reach in another (WS-29e).

Spec: ``ai-company-brain/specs/multi_tenancy_leak_audit.md`` S1-1 and
``multi_tenancy.md`` §3 (D-MT-1 (a)).

``test_projects_tenancy.py`` fences the READ path: one company's portfolio
against another's. This file fences the **write** path into access control
itself, and the audit ranks it above every read leak in the system for one
reason — *it grants further access*. A tenant-B admin who can invite into
tenant A, or grant a role there, does not merely see A's data; they mint a
principal that can.

**The defect this file exists to prevent, stated exactly.**
``_common.get_org_id`` resolved the tenant with
``SELECT id FROM organization WHERE slug = 'default'`` and **never consulted
the caller**. Twenty-six call sites inherited it — the roster, invites, member
status, purge, role assignment, permission overrides, group membership, the
sign-in queue and ``GET /auth/me``. Every one of them was operating on the
`default` organization no matter who asked.

⚠️ **Every test here seeds TWO organizations, and the acting admin of each is a
real directory row.** A one-organization suite cannot tell a caller-derived
tenant from a hard-coded one: both answer `ORG`. That is precisely why the
existing admin suites — which are thorough about invariants 1 through 4 — were
all green while this was live.

Three shapes of defect are covered, because closing only the first leaves the
bug intact:

1. **The tenant id.** ``get_org_id`` must read the caller (R3), and must fail
   closed when they have none.
2. **The row reached by address.** Every member-targeted route finds its
   subject through ``find_member``/``get_member``, which took no organization
   at all. A caller-derived id in front of an unscoped lookup is the same
   cross-tenant write with an extra query before it.
3. **The upsert.** ``app_user.email`` is globally UNIQUE (D-MT-1 (a)), so
   inviting an address that belongs to another tenant CONFLICTS with their row
   — and the ``DO UPDATE`` arm used to move that person into the inviter's
   organization.

Hermetic: no Postgres, no network, no TestClient — the route functions are
called directly with the DB seam monkeypatched onto each SUT submodule, the
house convention of ``test_admin_groups.py`` and ``test_signin_requests.py``.
The database's own half (the unique index that makes the conflict happen at
all) is proved against a real Postgres, not here.
"""
from __future__ import annotations

import re
from pathlib import Path

import pytest
from acb_auth import UserContext, UserRole, build_access
from fastapi import HTTPException
from gateway.routes.admin import _common, access_requests, groups, me, members

from tests.unit._admin_fakes import ORG, ORG_B, _FakeDB, bind_admin_db

#: The write modules — these have the cache and audit seams to bind.
MODULES = (members, groups, access_requests)

ADMIN_PERMISSIONS = [
    "admin:members:read",
    "admin:members:invite",
    "admin:members:manage",
    "admin:access:manage",
]


def _admin(email: str) -> UserContext:
    """A caller who has already passed every permission gate.

    The routes are invoked directly, so FastAPI's dependencies do not run.
    That is the point: **both admins below are correctly authorised.** Nothing
    in this file is about a missing permission — it is about a caller the
    permission system was right to admit reaching the wrong organization.
    """
    return UserContext(
        email=email, role=UserRole.EXECUTIVE,
        access=build_access(ADMIN_PERMISSIONS, roles=["admin"]),
    )


#: One admin per tenant. D-MT-1 (a): one email, one person, one organization —
#: which is what makes the tenant derivable from `X-User-Email` alone.
ANA = _admin("ana@alpha.example")      # organization A (`ORG`)
BEN = _admin("ben@beta.example")       # organization B (`ORG_B`)

#: ⚠️ A caller the directory has never heard of, holding a full admin set. Not
#: a contradiction: `EXECUTIVE_EMAILS` bootstrap, a service principal
#: (`system:internal` holds `*` and has no `app_user` row at all — `deps.py`
#: branch 1b), or an address provisioned in the IdP and not here.
STRANGER = _admin("nobody@nowhere.example")


@pytest.fixture()
def db(monkeypatch: pytest.MonkeyPatch) -> _FakeDB:
    """Two organizations, one admin and one colleague each."""
    fake = _FakeDB()
    bind_admin_db(monkeypatch, fake, MODULES)
    # `/auth/me` is a read: it has the DB seam and neither of the write seams.

    from contextlib import asynccontextmanager

    @asynccontextmanager
    async def _tenant_session(organization_id=None):
        yield fake
        await fake.commit()

    monkeypatch.setattr(me, "_tenant_session", _tenant_session)

    fake.seed_organization(ORG, "alpha", "Alpha Industries")
    fake.seed_organization(ORG_B, "beta", "Beta Consulting")

    fake.seed_user("u-ana", "ana@alpha.example", organization_id=ORG)
    fake.user_roles["u-ana"] = ["owner"]
    fake.seed_user("u-alpha-1", "priya@alpha.example", organization_id=ORG)
    fake.user_roles["u-alpha-1"] = ["member"]

    fake.seed_user("u-ben", "ben@beta.example", organization_id=ORG_B)
    fake.user_roles["u-ben"] = ["owner"]
    fake.seed_user("u-beta-1", "bob@beta.example", organization_id=ORG_B)
    fake.user_roles["u-beta-1"] = ["member"]
    return fake


def _nothing_was_written(db: _FakeDB) -> None:
    """No commit, no cache invalidation, no audit entry.

    A refusal that lands after something was written, or that records the act
    it declined, is only half a refusal.
    """
    assert db.committed == 0
    assert db.invalidated == []
    assert db.audit == []


# ════════════════════════════════════════════════════════════════════════════
# 1. The resolver itself — R3
# ════════════════════════════════════════════════════════════════════════════

async def test_the_tenant_comes_from_the_caller_not_from_a_slug(
    db: _FakeDB,
) -> None:
    """⚠️ THE line. ``get_org_id`` used to answer `default` for everybody.

    What breaks without it: every assertion further down this file, because
    every route inherits this one answer. Both callers below are full admins
    and the ONLY thing that differs is which directory row their address is on.
    """
    assert await _common.get_org_id(db, ANA) == ORG
    assert await _common.get_org_id(db, BEN) == ORG_B


async def test_the_answer_is_case_insensitive_on_both_sides(db: _FakeDB) -> None:
    """R10. An IdP may return a UPN cased differently between sessions, and a
    tenant that switches off when someone's address arrives in title case is
    not a boundary — it is a coincidence of casing."""
    db.seed_user("u-upper", "Casey@Alpha.Example", organization_id=ORG)
    assert await _common.get_org_id(db, _admin("CASEY@ALPHA.EXAMPLE")) == ORG
    assert await _common.get_org_id(db, _admin("  casey@alpha.example  ")) == ORG


async def test_a_caller_with_no_organization_is_refused_not_defaulted(
    db: _FakeDB,
) -> None:
    """⚠️ The fallback IS the bug.

    A caller the directory does not know is exactly the case a
    ``DEFAULT_ORG_SLUG`` fallback would answer — and answering it hands a
    stranger, or the `*`-holding internal service principal, the `default`
    organization's entire access control. Absence must refuse.

    403 rather than 404 for the reason ``projects.core.require_organization``
    states: this says nothing about what exists, it says the caller's own
    account is not attached. R5's "404, never 403" governs RECORDS, and the
    records — members, groups, roles — do answer 404 below.
    """
    with pytest.raises(HTTPException) as exc:
        await _common.get_org_id(db, STRANGER)
    assert exc.value.status_code == 403
    assert "not attached to an organization" in exc.value.detail


async def test_an_identity_less_caller_has_no_organization(db: _FakeDB) -> None:
    """The empty string is not everybody, and it is not `default` either."""
    with pytest.raises(HTTPException) as exc:
        await _common.get_org_id(db, _admin(""))
    assert exc.value.status_code == 403


async def test_a_suspended_caller_resolves_to_nothing(db: _FakeDB) -> None:
    """The lookup asks for an ACTIVE row, inherited from the Projects seam.

    Consistent with the rest of the model rather than a new rule:
    ``EffectiveAccess.is_active`` is ``status == 'active'`` exactly, so a
    suspended member holds no permissions and never passes
    ``require_admin_user``. Their tenant resolving to nothing means the two
    layers agree instead of one of them having to remember.
    """
    db.users["u-ana"]["status"] = "suspended"
    with pytest.raises(HTTPException) as exc:
        await _common.get_org_id(db, ANA)
    assert exc.value.status_code == 403


async def test_an_unprovisioned_deployment_still_says_so(db: _FakeDB) -> None:
    """The 503 this function was originally written for is kept, and it is a
    DIFFERENT failure from the 403 above: one is an operator whose migration
    never ran, the other is a caller whose account is not set up. Collapsing
    them sends an operator looking at the wrong thing. Both refuse."""
    db.provisioned = False
    with pytest.raises(HTTPException) as exc:
        await _common.get_org_id(db, STRANGER)
    assert exc.value.status_code == 503
    assert "130_org_access_control.sql" in exc.value.detail


# ════════════════════════════════════════════════════════════════════════════
# 2. SEE — the roster and the member record
# ════════════════════════════════════════════════════════════════════════════

async def test_each_admin_sees_only_their_own_roster(db: _FakeDB) -> None:
    """The read every admin opens first. Same route, same permissions, two
    answers — and only the caller's directory row can have chosen between
    them."""
    assert sorted(m.email for m in await members.list_members(admin=ANA)) == [
        "ana@alpha.example", "priya@alpha.example",
    ]
    assert sorted(m.email for m in await members.list_members(admin=BEN)) == [
        "ben@beta.example", "bob@beta.example",
    ]


async def test_reading_another_tenants_member_is_a_404_not_a_403(
    db: _FakeDB,
) -> None:
    """R5. "No such member" and "not in your organization" must be the same
    answer, or the status code is an oracle for who exists in the deployment —
    and a member roster is a customer list."""
    with pytest.raises(HTTPException) as exc:
        await members.get_member_access("priya@alpha.example", BEN)
    assert exc.value.status_code == 404


async def test_auth_me_names_the_callers_own_organization(db: _FakeDB) -> None:
    """``GET /auth/me`` reported the `default` org's slug and display name to
    every signed-in member of every tenant, so the frontend's idea of "which
    organization am I in" (`lib/access.ts` → the Members page header) was
    wrong for all but one."""
    assert (await me.get_me(user=ANA))["organization"]["slug"] == "alpha"
    assert (await me.get_me(user=BEN))["organization"]["slug"] == "beta"


async def test_auth_me_survives_a_schema_without_the_registry_projection(
    db: _FakeDB,
) -> None:
    """CP-2j. An OLD schema must cost the banner, never the identity.

    ``registry_status`` (177) and ``registry_trial_ends_at`` (199) are newer
    than this endpoint, and this fake's organization row carries neither — which
    is exactly a box whose ladder has not reached them yet.

    Folded into the organization SELECT, that raised, the route's broad handler
    set ``organization = {}``, and every member lost their org's slug and
    display name. A banner's OPTIONAL data took out the identity beside it, and
    the only symptom was a header that said nothing.
    """
    answer = await me.get_me(user=ANA)

    # The identity survives — this is the half that must never depend on the
    # projection.
    assert answer["organization"]["slug"] == "alpha"
    assert answer["organization"]["display_name"]
    # And the projection degrades to ABSENT, which is what the banner reads as
    # "say nothing" rather than as a state.
    assert answer["organization"].get("registry_status") is None
    assert answer["organization"].get("trial_ends_at") is None


async def test_auth_me_reports_no_organization_rather_than_a_default(
    db: _FakeDB,
) -> None:
    """A caller with no directory row gets an empty object — the frontend
    already renders that as the neutral "Organization" — never somebody
    else's name."""
    assert (await me.get_me(user=STRANGER))["organization"] == {}


# ════════════════════════════════════════════════════════════════════════════
# 3. INVITE INTO — the upsert, and the tenant steal
# ════════════════════════════════════════════════════════════════════════════

async def test_inviting_into_another_tenant_provisions_nothing(
    db: _FakeDB,
) -> None:
    """Ben invites a NEW address; it lands in Beta, never in Alpha."""
    await members.invite_member(
        members.InviteRequest(email="new@beta.example"), admin=BEN,
    )
    assert db.user_by_email("new@beta.example")["organization_id"] == ORG_B


async def test_inviting_another_tenants_member_cannot_steal_their_row(
    db: _FakeDB,
) -> None:
    """⚠️ The write the caller-derived org id does NOT close on its own.

    `app_user.email` is globally UNIQUE, so Ben inviting Priya conflicts with
    Alpha's row. Before the fence, ``ON CONFLICT (email) DO UPDATE SET
    organization_id = EXCLUDED.organization_id`` **moved Priya into Beta** —
    and ``set_roles``, which replaces assignments wholesale, was next in the
    same transaction. One POST, and another company's member is yours.

    The refusal is a 404 (R5): the invite form must not answer "that address
    exists somewhere in this deployment".
    """
    with pytest.raises(HTTPException) as exc:
        await members.invite_member(
            members.InviteRequest(email="priya@alpha.example", roles=["member"]),
            admin=BEN,
        )
    assert exc.value.status_code == 404

    assert db.users["u-alpha-1"]["organization_id"] == ORG
    assert db.users["u-alpha-1"]["status"] == "active"
    assert db.user_roles["u-alpha-1"] == ["member"]
    _nothing_was_written(db)


async def test_a_legacy_row_with_no_tenant_is_still_adoptable(
    db: _FakeDB,
) -> None:
    """The other direction, and the reason the fence has an ``IS NULL`` arm.

    Migration 130 added ``app_user.organization_id`` to rows that predate it.
    A fence written as "the tenants must match" would refuse to provision any
    of them — which looks identical to a correct refusal and is a lockout, not
    a boundary. Same shape as ``acb_auth.access._BOOTSTRAP_OWNER_SQL``.
    """
    db.seed_user("u-orphan", "old@nowhere.example", status="invited",
                 organization_id=None)

    await members.invite_member(
        members.InviteRequest(email="old@nowhere.example"), admin=BEN,
    )
    assert db.users["u-orphan"]["organization_id"] == ORG_B


async def test_a_differently_cased_address_is_the_same_person(
    db: _FakeDB,
) -> None:
    """⚠️ **Found on live Postgres, invisible to every hermetic test before it.**

    ``app_user_email_key`` is ``UNIQUE (email)`` — **byte-exact**. Every lookup
    in this package matches ``lower(email)`` (R10). So Alpha's row spelled
    ``Casey@Alpha.Example`` does not conflict with the lower-cased address
    ``provision_member`` inserts, the ``ON CONFLICT`` fence never fires because
    there is no conflict, and Postgres writes a SECOND ``app_user`` row —
    the same human, in two organizations.

    That is D-MT-1 (a) broken at the root: ``resolve_organization_id`` returns
    whichever row the planner hands back, so the person's tenant becomes
    non-deterministic and their whole visibility with it.

    The fake now models the index byte-exactly, which is what lets this run
    here at all. It was measured green against the case-insensitive fake and
    red against Postgres — the gap, not the test, is the finding.
    """
    db.seed_user("u-casey", "Casey@Alpha.Example", organization_id=ORG)

    with pytest.raises(HTTPException) as exc:
        await members.invite_member(
            members.InviteRequest(email="casey@alpha.example"), admin=BEN,
        )
    assert exc.value.status_code == 404

    caseys = [u for u in db.users.values()
              if u["email"].lower() == "casey@alpha.example"]
    assert len(caseys) == 1, "a differently-cased twin was written"
    assert caseys[0]["organization_id"] == ORG


async def test_re_inviting_your_own_member_does_not_write_a_cased_twin(
    db: _FakeDB,
) -> None:
    """The same defect inside ONE tenant, which is where it is reachable
    without any adversary at all: re-inviting a colleague whose row the IdP
    stored in title case. Nothing cross-tenant happens, and two rows for one
    person is still the thing that makes their organization ambiguous."""
    db.seed_user("u-casey", "Casey@Alpha.Example", status="invited",
                 organization_id=ORG)

    await members.invite_member(
        members.InviteRequest(email="CASEY@alpha.example"), admin=ANA,
    )

    caseys = [u for u in db.users.values()
              if u["email"].lower() == "casey@alpha.example"]
    assert len(caseys) == 1
    assert caseys[0]["id"] == "u-casey"


async def test_approving_a_sign_in_request_cannot_reach_across_either(
    db: _FakeDB,
) -> None:
    """The second provisioning door.

    ``access_request`` has no tenant column and genuinely cannot have one — an
    address knocking has no organization yet (leak audit §5). So the queue is
    shared, and the fence has to be on what approval WRITES rather than on
    what it reads. Ben approving Alpha's member must not adopt them.
    """
    db.seed_request("priya@alpha.example")

    with pytest.raises(HTTPException) as exc:
        await access_requests.approve_access_request(
            "priya@alpha.example",
            access_requests.ApproveRequest(roles=["member"]),
            admin=BEN,
        )
    assert exc.value.status_code == 404

    assert db.users["u-alpha-1"]["organization_id"] == ORG
    assert db.requests["priya@alpha.example"]["status"] == "pending"
    _nothing_was_written(db)


# ════════════════════════════════════════════════════════════════════════════
# 4. GRANT A ROLE IN — the write the audit ranks worst
# ════════════════════════════════════════════════════════════════════════════

async def test_granting_a_role_in_another_tenant_is_a_404(db: _FakeDB) -> None:
    """⚠️ The worst shape of leak in the system: a cross-tenant write **into
    access control**, by a correctly-authorised caller. It does not end at the
    response — it mints a principal in another company."""
    with pytest.raises(HTTPException) as exc:
        await members.set_member_roles(
            "priya@alpha.example",
            members.RoleAssignment(roles=["owner"]), admin=BEN,
        )
    assert exc.value.status_code == 404
    assert db.user_roles["u-alpha-1"] == ["member"]
    _nothing_was_written(db)


async def test_writing_an_override_in_another_tenant_is_a_404(
    db: _FakeDB,
) -> None:
    """The other half of access control — per-user allow/deny. A deny written
    into another tenant is a denial of service on a colleague nobody in that
    company chose to restrict."""
    with pytest.raises(HTTPException) as exc:
        await members.set_member_overrides(
            "priya@alpha.example",
            members.OverrideRequest(overrides=[
                members.OverrideEntry(permission="feature:email", effect="deny"),
            ]),
            admin=BEN,
        )
    assert exc.value.status_code == 404
    _nothing_was_written(db)


async def test_suspending_and_removing_reach_only_your_own_tenant(
    db: _FakeDB,
) -> None:
    """Both doors to ``is_active = False``, across the boundary. Locking a
    rival's staff out of their own tooling is one PATCH."""
    for call in (
        members.update_member(
            "priya@alpha.example",
            members.MemberPatch(status="suspended"), admin=BEN,
        ),
        members.remove_member("priya@alpha.example", admin=BEN),
        members.purge_member("priya@alpha.example", admin=BEN),
    ):
        with pytest.raises(HTTPException) as exc:
            await call
        assert exc.value.status_code == 404

    assert db.users["u-alpha-1"]["status"] == "active"
    assert "u-alpha-1" in db.users
    _nothing_was_written(db)


async def test_the_same_admin_can_still_manage_their_own_tenant(
    db: _FakeDB,
) -> None:
    """The control every refusal above needs.

    Without it a route that answered 404 for EVERYBODY would pass this file
    completely — the tenancy tests would be green and the admin surface would
    be dead. Same route, same permissions, one address different.
    """
    entry = await members.update_member(
        "bob@beta.example", members.MemberPatch(status="suspended"), admin=BEN,
    )
    assert entry.status == "suspended"
    assert db.users["u-beta-1"]["status"] == "suspended"
    assert db.committed == 1


# ════════════════════════════════════════════════════════════════════════════
# 5. Groups — the third door into another tenant's access
# ════════════════════════════════════════════════════════════════════════════

async def test_adding_another_tenants_member_to_your_group_is_a_404(
    db: _FakeDB,
) -> None:
    """``POST /admin/groups/{slug}/members`` reaches a person by ADDRESS, and
    with ``grant_center_access`` it writes a ``feature:center.<slug>``
    override on their row as well — so the group shortcut is a second path to
    the permission write fenced above."""
    db.seed_group("g-beta-people", "people", organization_id=ORG_B)

    with pytest.raises(HTTPException) as exc:
        await groups.add_group_member(
            "people", groups.GroupMemberAdd(email="priya@alpha.example"),
            admin=BEN,
        )
    assert exc.value.status_code == 404
    assert db.group_members == {}
    assert db.overrides == {}
    _nothing_was_written(db)


async def test_a_group_slug_that_exists_in_both_tenants_resolves_to_yours(
    db: _FakeDB,
) -> None:
    """Group slugs are UNIQUE **per organization** (leak audit S2-5), so
    `people` is a legal slug in every tenant at once. Matching on the bare slug
    is how one company's roster edit lands in another's group."""
    db.seed_group("g-alpha-people", "people", organization_id=ORG)
    db.seed_group("g-beta-people", "people", organization_id=ORG_B)

    await groups.add_group_member(
        "people", groups.GroupMemberAdd(email="bob@beta.example",
                                        grant_center_access=False),
        admin=BEN,
    )
    assert list(db.group_members) == [("g-beta-people", "u-beta-1")]


# ════════════════════════════════════════════════════════════════════════════
# 6. The seam, read as text
#
# Behaviour is the real assertion. These read the source because the shape of
# this particular defect is one a reviewer's eye slides over: a constant that
# looks like configuration, and a helper signature that looks complete.
# ════════════════════════════════════════════════════════════════════════════

ADMIN_DIR = Path(_common.__file__).parent
ADMIN_SOURCES = sorted(p for p in ADMIN_DIR.glob("*.py"))


def test_the_default_org_slug_is_gone_entirely() -> None:
    """⚠️ Not kept as a fallback, because a fallback re-creates the bug.

    The day the caller lookup returns nothing is exactly the day the slug would
    hand them `default` again — which is the failure this ticket closes, with a
    tenant-shaped comment in front of it.
    """
    assert not hasattr(_common, "DEFAULT_ORG_SLUG")
    # Comment lines are exempt — the constant's absence is documented where it
    # used to live, and a scan that could not tell prose from code would force
    # that explanation out of the file it belongs in.
    for path in ADMIN_SOURCES:
        code = [
            line for line in path.read_text(encoding="utf-8").splitlines()
            if not line.lstrip().startswith(("#", "#:"))
        ]
        assert "DEFAULT_ORG_SLUG" not in "\n".join(code), (
            f"{path.name} still uses it in code"
        )


def test_no_admin_route_resolves_an_organization_from_a_literal() -> None:
    """The generalisation of the test above: no statement in this package may
    reach `organization` by slug at all. Provisioning by slug is legitimate and
    lives where it has no caller to derive a tenant from — migration 130 and
    ``acb_auth.access._BOOTSTRAP_OWNER_SQL`` — neither of which is on a path a
    request can reach."""
    pattern = re.compile(r"FROM\s+organization\s+WHERE\s+slug", re.IGNORECASE)
    for path in ADMIN_SOURCES:
        assert not pattern.search(path.read_text(encoding="utf-8")), (
            f"{path.name} resolves an organization from a slug"
        )


def test_every_call_site_passes_a_caller() -> None:
    """The 26 call sites, asserted as a set rather than trusted.

    ``get_org_id(db)`` still parses — the parameter has no default — so a call
    site missed during the sweep would be a TypeError at request time on
    whichever route nobody exercised, not a failure here. This reads them.
    """
    bare = re.compile(r"get_org_id\(\s*db\s*\)")
    passing = re.compile(r"get_org_id\(\s*db\s*,\s*(admin|user)\b")
    sites = 0
    for path in ADMIN_SOURCES:
        body = path.read_text(encoding="utf-8")
        assert not bare.search(body), f"{path.name} calls get_org_id without a caller"
        sites += len(passing.findall(body))
    assert sites >= 20, f"only {sites} call sites found — did the sweep miss a module?"


def test_the_member_lookup_carries_the_tenant() -> None:
    """The half that a caller-derived id does not fix.

    Every member-targeted route finds its subject through here. The predicate
    is asserted structurally as well as behaviourally because the fake is a
    mirror: it reads this clause out of the statement, so the two agree by
    construction and only a test that reads the real string can notice it
    going missing.
    """
    import inspect

    # The statement is assembled from adjacent string literals, so the source
    # is joined the way Python joins it before it is read.
    source = inspect.getsource(_common.find_member)
    sql = " ".join(re.findall(r'"((?:[^"\\]|\\.)*)"', source))
    normalised = " ".join(sql.split())
    assert "FROM app_user WHERE lower(email) = :email" in normalised
    assert "AND organization_id = CAST(:org AS uuid)" in normalised


def test_the_roster_statement_is_scoped_and_cannot_be_widened() -> None:
    """⚠️ Structural because the fake cannot be.

    ``_admin_fakes`` decides which rows a roster statement addresses by reading
    the clause out of the statement — which is a stronger mirror than restating
    the predicate in Python, and still cannot evaluate SQL. Widening the WHERE
    to ``(u.organization_id = :org OR TRUE)`` leaves the clause the fake looks
    for exactly where it was, so the behavioural test above stays green while
    every organization's roster is served. Measured: that mutant survived the
    behavioural suite and is killed here.

    ``OR`` is refused outright rather than pattern-matched. The roster has no
    legitimate disjunction, and "an OR appeared in the one query that lists
    people" is a thing to look at rather than a thing to parse.
    """
    import inspect

    source = inspect.getsource(members.list_members)
    sql = " ".join(re.findall(r'"((?:[^"\\]|\\.)*)"', source))
    normalised = " ".join(sql.split())
    assert "FROM app_user u WHERE u.organization_id = CAST(:org AS uuid)" \
        in normalised
    assert " OR " not in normalised.upper(), (
        "the roster grew a disjunction — anything OR-ed beside the tenant "
        "predicate widens it"
    )


def test_the_provisioning_upsert_fences_its_conflict_arm() -> None:
    """⚠️ The tenant steal, structurally.

    ``ON CONFLICT (email) DO UPDATE`` without a ``WHERE`` reaches the one row
    in the table that a globally-unique email can collide with, which under
    D-MT-1 (a) is by definition another tenant's. The ``SET`` must also keep
    the existing tenant rather than overwrite it — either half alone is not
    enough, so both are read.
    """
    sql = " ".join(_common._PROVISION_MEMBER_SQL.split())
    arm = sql.split("DO UPDATE", 1)[1]
    assert "COALESCE(app_user.organization_id, EXCLUDED.organization_id)" in arm, (
        "the conflict arm overwrites the existing tenant — that is the steal"
    )
    assert re.search(
        r"WHERE\s+app_user\.organization_id\s+IS\s+NULL\s+OR\s+"
        r"app_user\.organization_id\s*=\s*EXCLUDED\.organization_id",
        arm, re.IGNORECASE,
    ), "the conflict arm has no tenant fence"
