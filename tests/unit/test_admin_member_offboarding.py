"""Off-boarding yourself — the guard on both doors to ``is_active = False``.

Spec: ``project-docs/specs/colleague_onboarding.md`` §2 Step 5 (WS-24 / N7).

``DELETE /admin/members/{email}`` refused the caller. ``PATCH
/admin/members/{email} {"status": "suspended"}`` reaches the same outcome by the
same mechanism and had **no self-check at all** — its only invariant was
``assert_owner_survives``, which refuses self-suspension purely by coincidence
while the caller is the org's last owner. Seed a second owner and the hole
opens; the UI, which never learned who the viewer was, rendered the button.

The two facts this file has to keep apart:

* the **self-guard** (invariant 4, ``_common.assert_not_self_lockout``) — you
  may not take your own access away, whoever else holds `owner`;
* **invariant 1** (``assert_owner_survives``) — the org may not be left
  ownerless, whoever is asking.

⚠️ **Both answer 409.** A test that asserts only the status code cannot tell
which one fired, and the one that fires today for self-suspension is the wrong
one. Every refusal below is discriminated by its detail text *and* by what was
and was not written — and the pair
``test_self_suspension_is_refused_even_when_another_owner_survives`` /
``test_that_same_world_still_lets_the_other_owner_be_suspended`` is the whole
ticket: same world, same route, same permissions, only the identity differs.
"""
from __future__ import annotations

import inspect
import re
from pathlib import Path
from typing import Any

import pytest
from acb_auth import UserContext, UserRole, build_access
from fastapi import HTTPException

from tests.unit._admin_fakes import _FakeDB, bind_admin_db

ADMIN_PERMISSIONS = [
    "admin:members:read",
    "admin:members:invite",
    "admin:members:manage",
]


def _caller(email: str, *, roles: list[str] | None = None) -> UserContext:
    """A caller who already passed ``admin:members:manage``.

    The routes are invoked directly, so FastAPI's dependencies do not run; the
    permission gate is a separate, already-tested concern (`test_org_access_*`)
    and every case here is about what happens *after* it has been satisfied.
    """
    return UserContext(
        email=email,
        role=UserRole.EXECUTIVE,
        access=build_access(ADMIN_PERMISSIONS, roles=roles or ["owner"]),
    )


OWNER = _caller("owner@fracktal.in")
ADMIN = _caller("admin@fracktal.in", roles=["admin"])


@pytest.fixture()
def db(monkeypatch: pytest.MonkeyPatch) -> _FakeDB:
    """An org with one owner, one admin, and one colleague."""
    from gateway.routes.admin import members

    fake = _FakeDB()
    bind_admin_db(monkeypatch, fake, (members,))

    fake.seed_user("u-owner", "owner@fracktal.in", joined_at="then")
    fake.user_roles["u-owner"] = ["owner"]
    fake.seed_user("u-admin", "admin@fracktal.in", joined_at="then")
    fake.user_roles["u-admin"] = ["admin"]
    fake.seed_user("u-priya", "priya@fracktal.in", joined_at="then")
    fake.user_roles["u-priya"] = ["member"]
    return fake


def _second_owner(db: _FakeDB) -> None:
    """The state §2 Step 2 exists to produce — and the one that opens the hole.

    With this row present ``assert_owner_survives`` passes for *every* case in
    this file, so any refusal that still happens can only be the self-guard.
    """
    db.seed_user("u-owner2", "second@fracktal.in", joined_at="then")
    db.user_roles["u-owner2"] = ["owner"]


def _nothing_was_written(db: _FakeDB) -> None:
    """No commit, no cache invalidation, no audit entry.

    The N6a lesson: a guard that refuses *after* something landed, or that
    records the act it declined, is only half a refusal.
    """
    assert db.committed == 0
    assert db.invalidated == []
    assert db.audit == []


# ════════════════════════════════════════════════════════════════════════════
# 1. dw4 — the guard is independent of `assert_owner_survives`
# ════════════════════════════════════════════════════════════════════════════

async def test_self_suspension_is_refused_even_when_another_owner_survives(
    db: _FakeDB,
) -> None:
    """dw4, and the reason this ticket exists.

    Two owners, so invariant 1 is satisfied and has nothing to say. The refusal
    that remains is the self-guard, and it is checked by its wording — the two
    409s in this route are otherwise indistinguishable, and today's refusal in
    a one-owner org is the *other* one firing by accident.

    ⚠️ Mutation-checked: deleting the ``assert_not_self_lockout`` call from
    ``update_member`` makes this test fail (the PATCH goes through and the
    owner's row reads `suspended`). A version of this test that seeded one
    owner would stay green under that mutation and prove nothing.
    """
    from gateway.routes.admin.members import MemberPatch, update_member

    _second_owner(db)

    with pytest.raises(HTTPException) as exc:
        await update_member(
            "owner@fracktal.in", MemberPatch(status="suspended"), admin=OWNER,
        )

    assert exc.value.status_code == 409
    detail = str(exc.value.detail)
    assert "cannot suspend yourself" in detail
    assert "no owner" not in detail, (
        "the refusal came from assert_owner_survives, not from the self-guard "
        "— which is exactly the coincidence this ticket removes"
    )
    assert db.users["u-owner"]["status"] == "active"
    _nothing_was_written(db)


async def test_that_same_world_still_lets_the_other_owner_be_suspended(
    db: _FakeDB,
) -> None:
    """The control for the test above: identical world, identical route, and
    the only difference is whose row it is.

    If invariant 1 were doing the work, this would refuse too. It does not —
    so the refusal above is the self-guard and nothing else.
    """
    from gateway.routes.admin.members import MemberPatch, update_member

    _second_owner(db)

    entry = await update_member(
        "second@fracktal.in", MemberPatch(status="suspended"), admin=OWNER,
    )

    assert entry.status == "suspended"
    assert db.users["u-owner2"]["status"] == "suspended"
    assert db.committed == 1


async def test_the_last_owner_hears_the_specific_refusal_not_the_generic_one(
    db: _FakeDB,
) -> None:
    """One owner, so BOTH guards are true. The self-guard runs first, because
    "you cannot suspend yourself" is the actionable half — "assign another
    owner first" is advice for a different problem, and following it would
    hand the caller the very hole dw4 closes."""
    from gateway.routes.admin.members import MemberPatch, update_member

    with pytest.raises(HTTPException) as exc:
        await update_member(
            "owner@fracktal.in", MemberPatch(status="suspended"), admin=OWNER,
        )

    assert "cannot suspend yourself" in str(exc.value.detail)
    assert db.users["u-owner"]["status"] == "active"


async def test_invariant_1_still_fires_for_the_case_it_owns(db: _FakeDB) -> None:
    """The new guard must not have displaced the old one. An *admin*
    suspending the org's only owner is not a self-lockout — it is the ownerless
    org invariant 1 exists to prevent, and it still answers with its own
    wording."""
    from gateway.routes.admin.members import MemberPatch, update_member

    with pytest.raises(HTTPException) as exc:
        await update_member(
            "owner@fracktal.in", MemberPatch(status="suspended"), admin=ADMIN,
        )

    assert exc.value.status_code == 409
    assert "no owner" in str(exc.value.detail)
    assert "yourself" not in str(exc.value.detail)
    assert db.users["u-owner"]["status"] == "active"
    _nothing_was_written(db)


# ════════════════════════════════════════════════════════════════════════════
# 2. dw1 — ONE guard, called by BOTH doors
# ════════════════════════════════════════════════════════════════════════════

async def test_removing_yourself_through_delete_is_still_refused(
    db: _FakeDB,
) -> None:
    """The behaviour that already existed, now sourced from the shared helper.

    Seeded with two owners for the same reason as dw4: this must be the
    self-guard's answer, not invariant 1's.
    """
    from gateway.routes.admin.members import remove_member

    _second_owner(db)

    with pytest.raises(HTTPException) as exc:
        await remove_member("owner@fracktal.in", admin=OWNER)

    assert exc.value.status_code == 409
    assert "cannot remove yourself" in str(exc.value.detail)
    assert db.users["u-owner"]["status"] == "active"
    assert db.user_roles["u-owner"] == ["owner"]      # grants intact
    _nothing_was_written(db)


async def test_removing_a_colleague_still_drops_their_row_and_their_roles(
    db: _FakeDB,
) -> None:
    """The guard must not have made off-boarding harder than it was."""
    from gateway.routes.admin.members import remove_member

    out = await remove_member("priya@fracktal.in", admin=OWNER)

    assert out == {"status": "removed", "email": "priya@fracktal.in"}
    assert db.users["u-priya"]["status"] == "removed"
    assert db.user_roles["u-priya"] == []
    assert db.committed == 1
    assert "priya@fracktal.in" in db.invalidated


def test_every_off_boarding_door_calls_the_one_shared_guard() -> None:
    """dw1, asserted against the *router* rather than against a list of names.

    Each door must call the shared helper and must NOT carry a comparison of
    its own: the version of this check that lived inside `remove_member` alone
    is precisely why the other door never grew one.

    ⚠️ **The doors are ENUMERATED here, deliberately, and the enumeration is
    the maintenance cost.** This test's first version claimed "a third one
    added later is included automatically" — it looked at a single path, so
    ``PUT /admin/members/{email}/roles`` was invisible to it. The claim was
    retracted rather than repaired, and then N8 added a fourth door
    (``DELETE …/purge``) on a *fifth* path, which would have been invisible
    again. So: when you add a route that can take a caller's own access away,
    add it to ``DOORS`` below. Nothing will do it for you.

    The fifth route that reaches this outcome — ``PUT …/overrides`` — is
    covered by its own inline comparison and is **not** listed, because it does
    not go through the helper. That is recorded as a known inconsistency in
    ``colleague_onboarding.md`` §2 Step 5, not asserted as correct here.
    """
    from gateway.routes.admin._common import (
        assert_not_self_demotion,
        assert_not_self_lockout,
        router,
    )

    #: ``(path, method, the guard that door must call)``.
    DOORS = [
        ("/admin/members/{email}", "PATCH", "assert_not_self_lockout("),
        ("/admin/members/{email}", "DELETE", "assert_not_self_lockout("),
        ("/admin/members/{email}/purge", "DELETE", "assert_not_self_lockout("),
        ("/admin/members/{email}/roles", "PUT", "assert_not_self_demotion("),
    ]

    for path, method, guard in DOORS:
        found = [
            route
            for route in router.routes
            if getattr(route, "path", "") == path
            and method in (getattr(route, "methods", set()) or set())
        ]
        assert len(found) == 1, f"expected exactly one {method} {path}"
        route = found[0]
        name = f"{method} {path}"
        src = inspect.getsource(route.endpoint)
        assert guard in src, (
            f"{name} does not call the shared self-guard ({guard[:-1]})"
        )
        for line in src.splitlines():
            if "admin.email" in line and "_log" not in line:
                assert "==" not in line, (
                    f"{name} compares the caller's address itself ({line.strip()!r}) "
                    "instead of going through the shared guard — that is how the "
                    "two doors came to disagree in the first place"
                )

    assert inspect.iscoroutinefunction(assert_not_self_lockout) is False, (
        "the guard reads nothing from the database; keeping it synchronous is "
        "what makes it cheap to call from every door"
    )
    assert inspect.iscoroutinefunction(assert_not_self_demotion) is True, (
        "the roles door's guard reads the role permissions from the database"
    )


async def test_purging_yourself_is_refused_by_the_same_guard(db: _FakeDB) -> None:
    """The fourth door, behaviourally — and it is the most destructive one.

    Two owners, so `assert_owner_survives` has nothing to say and the refusal
    can only be the self-guard. Discriminated by wording, because both guards
    on this route answer 409 and asserting the bare code would pass against
    either.
    """
    from gateway.routes.admin.members import purge_member

    _second_owner(db)

    with pytest.raises(HTTPException) as exc:
        await purge_member("owner@fracktal.in", admin=OWNER)

    assert exc.value.status_code == 409
    detail = str(exc.value.detail)
    assert "cannot permanently delete yourself" in detail
    assert "no owner" not in detail, (
        "the refusal came from assert_owner_survives, not from the self-guard"
    )
    assert db.users["u-owner"]["status"] == "active"
    assert db.user_roles["u-owner"] == ["owner"]
    _nothing_was_written(db)


# ════════════════════════════════════════════════════════════════════════════
# 3. dw2 — what the guard does NOT refuse
# ════════════════════════════════════════════════════════════════════════════

async def test_re_activating_your_own_row_is_not_a_lockout(db: _FakeDB) -> None:
    """dw2 — `active` is the one status that gives access rather than taking
    it, so the guard has no business refusing it.

    ⚠️ **This test used to suspend the caller's own row first, and that world is
    now unreachable.** Since WS-29e the route derives the caller's tenant from
    their own ACTIVE directory row (``projects.core.resolve_organization_id``),
    so a suspended caller is refused by :func:`_common.get_org_id` before the
    self-guard is consulted — which merely makes explicit what
    ``EffectiveAccess.is_active`` already decided: a suspended member holds no
    permissions and never passes ``require_admin_user`` in the first place, so
    they could never have issued this PATCH in production either. What is left
    is the claim the test was actually written to make, and it is unchanged:
    ``status="active"`` on your own row is not a lockout and is not refused.
    """
    from gateway.routes.admin.members import MemberPatch, update_member

    entry = await update_member(
        "owner@fracktal.in", MemberPatch(status="active"), admin=OWNER,
    )

    assert entry.status == "active"
    assert db.users["u-owner"]["status"] == "active"
    assert db.committed == 1


async def test_renaming_yourself_is_not_a_lockout(db: _FakeDB) -> None:
    """dw2 — a display-name-only patch carries ``status=None`` and changes no
    access. The guard is called unconditionally and returns on `None`, so this
    is safe by construction rather than by the caller remembering to skip it."""
    from gateway.routes.admin.members import MemberPatch, update_member

    entry = await update_member(
        "owner@fracktal.in", MemberPatch(display_name="The Owner"), admin=OWNER,
    )

    assert entry.display_name == "The Owner"
    assert db.users["u-owner"]["display_name"] == "The Owner"
    assert db.users["u-owner"]["status"] == "active"
    assert db.committed == 1


async def test_suspending_somebody_else_is_untouched(db: _FakeDB) -> None:
    """The guard must fire on identity, not on the act."""
    from gateway.routes.admin.members import MemberPatch, update_member

    entry = await update_member(
        "priya@fracktal.in", MemberPatch(status="suspended"), admin=OWNER,
    )

    assert entry.status == "suspended"
    assert db.users["u-priya"]["status"] == "suspended"


async def test_putting_your_own_row_back_to_invited_is_refused_too(
    db: _FakeDB,
) -> None:
    """The third door, closed by construction rather than by enumeration.

    ``EffectiveAccess.is_active`` is ``status == "active"`` *exactly*, so
    `invited` locks the caller out exactly as `suspended` does — and it is a
    status ``VALID_STATUSES`` accepts on this route. The guard is written as
    "anything that is not `active`" for this reason: a list of destructive
    statuses would have had to remember this one, and a list is what the
    spec's own done-when enumerated.
    """
    from gateway.routes.admin.members import MemberPatch, update_member

    _second_owner(db)

    with pytest.raises(HTTPException) as exc:
        await update_member(
            "owner@fracktal.in", MemberPatch(status="invited"), admin=OWNER,
        )

    assert exc.value.status_code == 409
    assert "invited" in str(exc.value.detail)
    assert "own access" in str(exc.value.detail)
    assert db.users["u-owner"]["status"] == "active"
    _nothing_was_written(db)


def test_every_member_status_is_answered_by_the_rule() -> None:
    """The vocabulary and the rule, checked against each other directly.

    ``members.VALID_STATUSES`` is what the route accepts; the guard's rule is
    "anything that is not `active`". Asserting it over the whole vocabulary —
    rather than over the two statuses the ticket happened to name — is what
    makes a fifth status guarded on the day it is added.
    """
    from gateway.routes.admin._common import assert_not_self_lockout
    from gateway.routes.admin.members import VALID_STATUSES

    me = _caller("owner@fracktal.in")
    row: dict[str, Any] = {"id": "u-owner", "email": "owner@fracktal.in"}

    refused = []
    for status in (*VALID_STATUSES, "archived", None):
        try:
            assert_not_self_lockout(me, row, status=status)
        except HTTPException as exc:
            assert exc.status_code == 409
            refused.append(status)

    assert set(refused) == {s for s in VALID_STATUSES if s != "active"} | {
        "archived"
    }
    assert "active" not in refused
    assert None not in refused


# ════════════════════════════════════════════════════════════════════════════
# 4. dw3 — case-insensitive on both sides
# ════════════════════════════════════════════════════════════════════════════

@pytest.mark.parametrize(
    ("row_email", "caller_email"),
    [
        ("owner@fracktal.in", "Owner@Fracktal.IN"),   # IdP re-cased the UPN
        ("Owner@Fracktal.IN", "owner@fracktal.in"),   # …or the row is the odd one
        ("Owner@Fracktal.IN", "OWNER@FRACKTAL.IN"),   # neither matches literally
        ("owner@fracktal.in", " owner@fracktal.in "),  # header whitespace
    ],
)
async def test_a_change_of_upn_casing_does_not_switch_the_guard_off(
    db: _FakeDB, row_email: str, caller_email: str,
) -> None:
    """dw3. ``app_user.email`` and ``X-User-Email`` come from the same
    directory but not necessarily with the same casing, and a guard that
    compares them literally is one directory quirk away from being absent.
    Two owners again, so only the self-guard can be answering."""
    from gateway.routes.admin.members import MemberPatch, update_member

    _second_owner(db)
    db.users["u-owner"]["email"] = row_email

    with pytest.raises(HTTPException) as exc:
        await update_member(
            row_email,
            MemberPatch(status="suspended"),
            admin=_caller(caller_email),
        )

    assert "cannot suspend yourself" in str(exc.value.detail)
    assert db.users["u-owner"]["status"] == "active"


async def test_a_caller_with_no_address_of_their_own_matches_nobody(
    db: _FakeDB,
) -> None:
    """The empty string is not everybody.

    A caller whose identity header never arrived has ``email == ""``; comparing
    two blanks would refuse (or, on a row with no address, refuse everything)
    for a reason that has nothing to do with self-off-boarding. That claim is
    about :func:`_common.assert_not_self_lockout` alone and is asserted against
    the function directly, because since WS-29e the ROUTE no longer reaches it:
    an identity-less caller has no tenant to derive, so ``get_org_id`` refuses
    first. Both halves are checked here — the guard's rule, and the fact that
    the route now refuses earlier and writes nothing.
    """
    from gateway.routes.admin._common import assert_not_self_lockout
    from gateway.routes.admin.members import MemberPatch, update_member

    # The guard itself: two blanks are not a match.
    assert_not_self_lockout(
        _caller(""), {"email": ""}, status="suspended",
    )
    assert_not_self_lockout(
        _caller(""), {"email": "priya@fracktal.in"}, status="suspended",
    )

    # And the route, which no longer gets that far. R3: identity comes from the
    # authenticated context, and an absent one resolves to no organization
    # rather than to the deployment's.
    with pytest.raises(HTTPException) as exc:
        await update_member(
            "priya@fracktal.in", MemberPatch(status="suspended"),
            admin=_caller(""),
        )
    assert exc.value.status_code == 403
    assert db.users["u-priya"]["status"] == "active"
    _nothing_was_written(db)


# ════════════════════════════════════════════════════════════════════════════
# 5. dw5/dw6/dw7 — the Members page
#
# There is no jsdom/component-test harness in this repo (only `*.test.ts` over
# pure modules), so the page is fenced two ways: the RULE lives in
# `settings/members/selfGuard.ts` and is unit-tested by vitest
# (`selfGuard.test.ts`), and the WIRING — that the page actually asks it, and
# renders nothing destructive without its answer — is asserted here against the
# source. The N6a lesson applies: a bare `"something" in page` is satisfied by
# a declaration, so every assertion below is positional or scoped to the block
# it is about.
# ════════════════════════════════════════════════════════════════════════════

REPO_ROOT = Path(__file__).resolve().parents[2]
# ⚠️ Path moved by D49 (2026-08-24): the roster is now a TAB of Organisation
# (`launch_surface.md` §6.2), so the page these assertions read is
# `settings/organization/OrganizationAdmin.tsx` and the rule module is
# `settings/organization/lib/selfGuard.ts`. `settings/members/page.tsx` is a
# redirect with no markup; `settings/members/[email]` — the per-person editor —
# did NOT move. Only the paths changed here: every claim below is about the same
# JSX and is asserted the same way.
ORG_DIR = REPO_ROOT / "workbench/control_plane/src/app/settings/organization"


def _page() -> str:
    return (ORG_DIR / "OrganizationAdmin.tsx").read_text(encoding="utf-8")


def _member_row() -> str:
    """Just the row component — the only place a destructive control lives."""
    body = _page().split("function MemberRow(", 1)
    assert len(body) == 2, "the roster row is no longer its own component"
    return body[1].split("\nfunction ", 1)[0]


def test_the_roster_learns_who_is_looking_at_it() -> None:
    """dw5. The page had no idea who the viewer was, which is why it drew
    Suspend on the owner's own row like any other."""
    page = _page()

    assert "access.email" in page, (
        "the page never reads the viewer's identity from /auth/me"
    )
    assert "viewerEmail" in _member_row(), (
        "the row is rendered without being told who the viewer is"
    )


def test_no_destructive_control_is_rendered_on_the_viewers_own_row() -> None:
    """dw5, positionally: every destructive call site sits behind the guard's
    answer, and the row says whose it is instead.

    Asserted by position rather than by presence — `"canSuspend" in page` would
    pass on the import alone.
    """
    row = _member_row()

    for flag, call in (
        ("actions.canSuspend", 'setStatus(member.email, "suspended")'),
        ("actions.canRemove", "onRemove(member)"),
    ):
        assert flag in row and call in row, f"{flag} / {call} missing from the row"
        # ⚠️ Position is NOT enough, and asserting it was this test's own bug.
        # `void actions.canSuspend;` above a `{true && (` satisfies
        # index(flag) < index(call) while the control renders on the viewer's
        # own row — a reference before the call site is not a guard on it. The
        # JSX condition itself is what has to be there. (Third instance of
        # "the assertion is weaker than the docstring" in this area; see §6
        # *Repair round 3* for the other two.)
        assert f"{{{flag} && (" in row, (
            f"{call} is not rendered behind `{{{flag} && (` — merely "
            f"mentioning {flag} first does not gate anything"
        )

    # The destructive verbs appear exactly once each, so there is no second,
    # unguarded copy further down the row.
    assert row.count('"suspended")') == 1
    assert row.count("onRemove(member)") == 1
    # And the own row is labelled rather than left blank.
    assert "actions.isSelf" in row
    assert "This is you" in row


async def test_self_demotion_is_refused_even_when_another_owner_survives(
    db: _FakeDB,
) -> None:
    """The **third door**, and the one N7's first pass left open.

    ``PUT /admin/members/{email}/roles`` never touches `status`, so
    `assert_not_self_lockout` cannot see it — yet demoting yourself out of
    `admin:members:manage` is the same lockout, and the permission you just
    gave up is the floor for undoing it.

    Measured before the guard existed, in exactly this two-owner world: the
    call was **accepted**, the caller kept `status='active'` and lost the
    rights. With one owner it happened to be refused — by
    `assert_owner_survives`, i.e. by the coincidence invariant 4 exists to
    stop depending on. That is why this seeds a second owner.
    """
    from gateway.routes.admin.members import RoleAssignment, set_member_roles

    _second_owner(db)

    with pytest.raises(HTTPException) as exc:
        await set_member_roles(
            "owner@fracktal.in", RoleAssignment(roles=["member"]), admin=OWNER,
        )

    assert exc.value.status_code == 409
    detail = str(exc.value.detail)
    # Both guards on this route answer 409. Discriminate, or this test passes
    # against `assert_owner_survives` and proves nothing.
    assert "member-management rights" in detail
    assert "no owner" not in detail, (
        "this is invariant 1 firing, not the self-guard — the two-owner world "
        "was supposed to make invariant 1 silent here"
    )
    assert db.user_roles["u-owner"] == ["owner"], "the demotion was applied"
    _nothing_was_written(db)


async def test_that_same_world_still_lets_another_owner_be_demoted(
    db: _FakeDB,
) -> None:
    """The converse, in the identical world: only the identity differs.

    Without this, a guard that simply refused every role change would look
    correct. `admin:members:manage` is meant to govern *other* people's roles;
    what it may not do is take itself away.
    """
    from gateway.routes.admin.members import RoleAssignment, set_member_roles

    _second_owner(db)

    await set_member_roles(
        "second@fracktal.in", RoleAssignment(roles=["member"]), admin=OWNER,
    )

    assert db.user_roles["u-owner2"] == ["member"]
    assert db.committed == 1


async def test_a_self_edit_that_keeps_the_rights_is_allowed(
    db: _FakeDB,
) -> None:
    """The guard is about the *permission*, not about self-editing.

    A blanket "you may not touch your own roles" would have been simpler and
    wrong: an owner moving themselves to `admin` still holds
    `admin:members:manage` and can undo it, so there is no lockout to prevent.
    Checking the permissions the new roles actually grant — rather than an
    allowlist of slugs — is also what lets a *custom* role carrying the
    permission through.
    """
    from gateway.routes.admin.members import RoleAssignment, set_member_roles

    _second_owner(db)

    await set_member_roles(
        "owner@fracktal.in", RoleAssignment(roles=["admin"]), admin=OWNER,
    )

    assert db.user_roles["u-owner"] == ["admin"]
    assert db.committed == 1


async def test_the_last_owner_demoting_themselves_hears_the_self_refusal(
    db: _FakeDB,
) -> None:
    """One owner, so BOTH guards would refuse. The self-guard must answer.

    It runs first deliberately: "you cannot take your own rights away" is the
    true reason and tells the caller what to do, where "assign another owner
    first" invites them to do exactly that and then hit the real wall.
    """
    from gateway.routes.admin.members import RoleAssignment, set_member_roles

    with pytest.raises(HTTPException) as exc:
        await set_member_roles(
            "owner@fracktal.in", RoleAssignment(roles=["member"]), admin=OWNER,
        )

    assert "member-management rights" in str(exc.value.detail)
    _nothing_was_written(db)


def test_the_third_doors_guard_reads_the_permissions_not_the_slugs() -> None:
    """Structural, because the fake answers this query from a Python mapping.

    ``_FakeDB.ROLE_PERMISSIONS`` is a mirror, and a mirror only ever agrees
    with itself — every behavioural case above would stay green if the real
    statement started reading role *slugs* and an allowlist instead. Then a
    custom role carrying `admin:members:manage` would be refused and the guard
    would be enforcing something nobody wrote down.
    """
    from gateway.routes.admin._common import (
        _ROLE_PERMISSIONS_SQL,
        SELF_RECOVERY_PERMISSION,
    )

    assert "org_role_permission" in _ROLE_PERMISSIONS_SQL
    assert "permission" in _ROLE_PERMISSIONS_SQL
    assert "slug" not in _ROLE_PERMISSIONS_SQL, (
        "the guard is deciding by role name; a custom role that carries "
        f"{SELF_RECOVERY_PERMISSION} would be refused"
    )
    assert SELF_RECOVERY_PERMISSION == "admin:members:manage"


def test_removing_somebody_asks_first_and_names_them() -> None:
    """dw6. Remove drops every role assignment and is not a toggle — Activate
    does not bring the person back, an invite followed by an activation does —
    so the control that fires it must say whose access is ending."""
    page = _page()

    assert "function RemoveDialog(" in page, "there is no confirmation step"
    dialog = page.split("function RemoveDialog(", 1)[1]
    assert "member.display_name || member.email" in dialog, (
        "the confirmation does not name the person it is about"
    )
    assert "role assignment" in dialog, (
        "the confirmation does not say that role grants are dropped"
    )
    # The DELETE is fired from the dialog's confirm, never straight from a row
    # button: `onRemove` only opens it.
    assert 'method: "DELETE"' in page
    row = _member_row()
    assert "fetch(" not in row and 'method: "DELETE"' not in row, (
        "the row issues the off-boarding itself, so the Remove button IS the "
        "off-boarding and the confirmation is decorative"
    )


def test_a_refused_off_boarding_is_shown_and_the_roster_re_read() -> None:
    """dw7. Hiding the control is a courtesy (`lib/access.ts:126-129`); the
    server is the boundary, so its refusal has to reach the screen.

    `load()` runs on failure as well as success — the N6a rule: a refusal is
    almost always ABOUT the row that was clicked, so the row on screen is
    exactly the thing that is stale.
    """
    page = _page()
    body = page.split("const removeMember = async (", 1)
    assert len(body) == 2, "removeMember is gone"
    body = body[1].split("\n  };", 1)[0]

    assert "setError(body.detail" in body, (
        "the gateway's refusal is swallowed, so a 409 looks like nothing "
        "happening"
    )
    assert "await load();" in body
    assert "return;" not in body, (
        "removeMember returns early on a refusal, leaving the stale row on "
        "screen until the admin hits Refresh"
    )


def test_the_browsers_copy_of_the_rule_is_a_courtesy_not_a_second_boundary(
) -> None:
    """The client-side guard exists to stop an owner clicking a button that
    would fail; it must not be mistaken for the enforcement, which is
    ``_common.assert_not_self_lockout`` and nothing else.

    Pinned because the failure mode of a client-side mirror is that somebody
    later "simplifies" the server by trusting it.
    """
    guard = (ORG_DIR / "lib" / "selfGuard.ts").read_text(encoding="utf-8")

    assert re.search(r"toLowerCase\(\)", guard), (
        "the browser's comparison is case-sensitive while the server's is not"
    )
    assert "not a boundary" in guard or "courtesy" in guard, (
        "selfGuard.ts does not say that it is presentation only"
    )
