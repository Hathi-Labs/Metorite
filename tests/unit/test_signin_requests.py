"""The sign-in request queue — capture the knock, and let the owner answer it.

Spec: ``project-docs/specs/colleague_onboarding.md`` §6 (WS-24 / N6a).

Before N6a an authenticated stranger produced one artefact — a journald
warning nobody read back — so the owner's only way to learn that a colleague
was locked out was for that colleague to say so. This file is the fence around
the fix, and it is deliberately DB-free: the only test in the tree that
exercised ``access.py``'s unprovisioned branch is
``test_owner_bootstrap.py::test_unprovisioned_signin_is_cached``, which is
``@_needs_db`` and unrunnable by an agent (``work_plan.md`` §6 forbids pointing
it at prod). N6a adds a **write** to that branch, so the fence has to be built
here, not assumed.

What is locked, in the order the spec's done-whens state it:

1. dw8 — **characterisation of the existing invite**, written before the
   ``_provision_member`` extraction and unchanged by it. ``POST /admin/members``
   was completely untested; refactoring an unfenced route on the auth path
   blind is how a provisioning bug ships.
2. dw2 — the unprovisioned branch upserts a request, best-effort: a failing
   write changes neither the refusal nor the log line.
3. dw3 — **the write fires only on the sign-in path.** ``resolve_access`` is
   not sign-in-only; ``routes/rooms.py`` fans it out over room participants'
   emails and ``access.py`` folds it over session subjects. Neither is a knock.
4. dw4 — an email with an ``app_user`` row in ANY status is never recorded.
5. dw5 — write volume is bounded by the 60s resolution cache.
6. dw6/7/9 — the three admin routes, their per-route auth floor, and the
   approve path that provisions AND activates in one action.
7. dw11 — an ``invited`` row is labelled "never signed in", not rendered live.
"""
from __future__ import annotations

import re
from pathlib import Path
from typing import Any

import pytest
from acb_auth import UserContext, UserRole, build_access
from fastapi import HTTPException

from tests.unit._admin_fakes import ORG, _FakeDB, _Rows, bind_admin_db

REPO_ROOT = Path(__file__).resolve().parents[2]

FULL_ADMIN = UserContext(
    email="admin@fracktal.in",
    role=UserRole.EXECUTIVE,
    access=build_access(
        ["admin:members:read", "admin:members:invite", "admin:members:manage"],
        roles=["admin"],
    ),
)


# ── Fakes ───────────────────────────────────────────────────────────────────
#
# ``_FakeDB``/``_Rows`` live in ``_admin_fakes.py``: this file's world is also
# ``test_admin_member_offboarding.py``'s, and two copies of a DB mirror drift.
# The mirror's warning travels with it — read the class docstring before
# trusting a behavioural case here to see inside a SQL statement.


@pytest.fixture()
def db(monkeypatch: pytest.MonkeyPatch) -> _FakeDB:
    """A shared fake session bound to both admin submodules under test."""
    from gateway.routes.admin import access_requests, members

    fake = _FakeDB()
    bind_admin_db(monkeypatch, fake, (members, access_requests))

    # The admin doing the inviting must outrank what they assign.
    fake.seed_user("u-admin", "admin@fracktal.in")
    fake.user_roles["u-admin"] = ["admin"]
    # A real deployment always has an owner (the seed migration guarantees it,
    # and `ensure_owner_bootstrap` restores it). Without one here, invariant 1
    # would refuse every lifecycle write in this file for the wrong reason —
    # "this would leave the org with no owner" is true of an org that never
    # had one, and it would hide the cases these tests are about.
    fake.seed_user("u-owner", "owner@fracktal.in")
    fake.user_roles["u-owner"] = ["owner"]
    return fake


# ════════════════════════════════════════════════════════════════════════════
# 1. dw8 — characterisation of the EXISTING invite, written before the
#    `_provision_member` extraction. These assertions describe behaviour that
#    predates this ticket and must survive it byte-for-byte.
# ════════════════════════════════════════════════════════════════════════════

async def test_invite_creates_an_invited_row_with_the_default_member_role(
    db: _FakeDB,
) -> None:
    from gateway.routes.admin.members import InviteRequest, invite_member

    entry = await invite_member(
        InviteRequest(email="  Priya@Fracktal.IN  ", display_name="Priya"),
        admin=FULL_ADMIN,
    )

    assert entry.email == "priya@fracktal.in"       # normalised, lowercased
    assert entry.status == "invited"
    assert entry.roles == ["member"]                # `req.roles or ["member"]`
    assert entry.invited_by == "admin@fracktal.in"

    row = db.user_by_email("priya@fracktal.in")
    assert row is not None and row["status"] == "invited"
    # Invite does NOT stamp joined_at — that is what makes `invited` mean
    # "provisioned, never signed in".
    assert row["joined_at"] is None
    assert db.committed == 1


async def test_invite_of_a_removed_member_returns_them_to_invited(
    db: _FakeDB,
) -> None:
    """The one status the ON CONFLICT arm rewrites."""
    from gateway.routes.admin.members import InviteRequest, invite_member

    db.seed_user("u-9", "gone@fracktal.in", status="removed")
    await invite_member(
        InviteRequest(email="gone@fracktal.in"), admin=FULL_ADMIN,
    )
    assert db.users["u-9"]["status"] == "invited"


async def test_invite_never_downgrades_an_active_member(db: _FakeDB) -> None:
    """Re-inviting somebody already in must not take their access away."""
    from gateway.routes.admin.members import InviteRequest, invite_member

    db.seed_user("u-8", "live@fracktal.in", status="active")
    entry = await invite_member(
        InviteRequest(email="live@fracktal.in", roles=["manager"]),
        admin=FULL_ADMIN,
    )
    assert db.users["u-8"]["status"] == "active"
    assert entry.roles == ["manager"]


async def test_invite_rejects_an_address_that_is_not_one(db: _FakeDB) -> None:
    from gateway.routes.admin.members import InviteRequest, invite_member

    for bad in ("not-an-address", "", "x" * 250 + "@fracktal.in"):
        with pytest.raises(HTTPException) as exc:
            await invite_member(InviteRequest(email=bad), admin=FULL_ADMIN)
        assert exc.value.status_code == 400


async def test_invite_refuses_a_role_that_outranks_the_caller(db: _FakeDB) -> None:
    """Invariant 2 — an admin cannot mint an owner through the invite path."""
    from gateway.routes.admin.members import InviteRequest, invite_member

    with pytest.raises(HTTPException) as exc:
        await invite_member(
            InviteRequest(email="new@fracktal.in", roles=["owner"]),
            admin=FULL_ADMIN,
        )
    assert exc.value.status_code == 403
    assert db.user_by_email("new@fracktal.in") is None


async def test_invite_drops_the_resolver_cache_and_records_the_audit_action(
    db: _FakeDB,
) -> None:
    """A new member whose refusal is still cached would be locked out for 60s."""
    from gateway.routes.admin.members import InviteRequest, invite_member

    await invite_member(
        InviteRequest(email="new@fracktal.in"), admin=FULL_ADMIN,
    )
    assert "new@fracktal.in" in db.invalidated
    assert ("org.member_invited", "user:new@fracktal.in") in db.audit


# ════════════════════════════════════════════════════════════════════════════
# 2. dw2/dw3/dw4/dw5 — the write on the unprovisioned branch
# ════════════════════════════════════════════════════════════════════════════

class _FakeSession:
    """Minimal async session for :func:`acb_auth.access.resolve_access`."""

    def __init__(self, owner: _AccessWorld):
        self._owner = owner

    async def __aenter__(self) -> _FakeSession:
        return self

    async def __aexit__(self, *exc: Any) -> None:
        return None

    async def commit(self) -> None:
        self._owner.commits += 1

    async def execute(self, sql: Any, params: dict | None = None) -> _Rows:
        s = " ".join(str(sql).split())
        p = params or {}
        if "INSERT INTO access_request" in s:
            self._owner.writes.append(p["email"])
            if self._owner.write_raises:
                raise RuntimeError("access_request write exploded")
            return _Rows([], rowcount=1)
        if "FROM app_user u" in s or "FROM app_user WHERE" in s:
            row = self._owner.rows.get(p["email"])
            return _Rows([row] if row else [])
        raise AssertionError(f"unhandled SQL in access fake: {s}")


class _AccessWorld:
    """The world ``resolve_access`` sees: known members, and what it wrote."""

    def __init__(self) -> None:
        self.rows: dict[str, dict[str, Any]] = {}
        self.writes: list[str] = []
        self.commits = 0
        self.write_raises = False

    def seed_member(self, email: str, *, status: str = "active") -> None:
        self.rows[email.lower()] = {
            "user_id": "u-1", "organization_id": ORG, "status": status,
            "legacy_role": "employee", "roles": ["member"],
            "role_permissions": ["feature:chat"], "overrides": [],
        }

    def factory(self) -> Any:
        return lambda: _FakeSession(self)


@pytest.fixture()
def world(monkeypatch: pytest.MonkeyPatch) -> _AccessWorld:
    import acb_auth.access as access_mod

    w = _AccessWorld()
    monkeypatch.setattr(access_mod, "_get_session_factory", w.factory)
    monkeypatch.setattr(access_mod, "_tables_missing", False)
    access_mod.invalidate()
    yield w
    access_mod.invalidate()


async def test_an_unprovisioned_signin_is_recorded_as_a_request(
    world: _AccessWorld,
) -> None:
    """dw2 — the knock is persisted instead of discarded."""
    from acb_auth.access import resolve_access

    access = await resolve_access("stranger@fracktal.in", record_request=True)

    assert not access.is_active
    assert world.writes == ["stranger@fracktal.in"]
    assert world.commits == 1


async def test_a_failing_request_write_leaves_the_refusal_untouched(
    world: _AccessWorld,
) -> None:
    """dw2 — best-effort. The queue is a convenience; the refusal is the
    security answer, and a broken table must never change it or raise."""
    from acb_auth.access import resolve_access

    world.write_raises = True
    access = await resolve_access("stranger@fracktal.in", record_request=True)

    assert not access.is_active
    assert access.granted == frozenset()
    assert world.writes == ["stranger@fracktal.in"]   # attempted, and swallowed


async def test_resolve_access_records_nothing_unless_asked(
    world: _AccessWorld,
) -> None:
    """dw3 — the default is silence. Every caller that is not a sign-in gets
    exactly today's behaviour without opting out of anything."""
    from acb_auth.access import resolve_access

    access = await resolve_access("stranger@fracktal.in")

    assert not access.is_active
    assert world.writes == []


async def test_a_room_participant_fanout_over_unknown_emails_writes_nothing(
    world: _AccessWorld,
) -> None:
    """dw3, the P1 — ``routes/rooms.py:215`` shape.

    ``resolve_access`` is fanned out over *room participants'* emails to work
    out what a shared run's authority is capped by. A participant with no
    ``app_user`` row is not somebody knocking at the front door, and filing
    them as one would put people in the owner's queue who never tried to sign
    in — precisely the harm the separate-table decision exists to prevent.
    """
    from acb_auth.access import resolve_access

    emails = ["a@fracktal.in", "b@fracktal.in", "c@fracktal.in"]
    resolved = {e: await resolve_access(e) for e in emails}

    assert all(not a.is_active for a in resolved.values())
    assert world.writes == []


async def test_the_session_authority_fold_writes_nothing(
    world: _AccessWorld, monkeypatch: pytest.MonkeyPatch,
) -> None:
    """dw3 — ``access.py``'s own participant fold is the second non-knock."""
    import acb_auth.access as access_mod

    world.seed_member("actor@fracktal.in")

    async def _fake_resolve_session(session_id, actor_email):
        # Exercise the real fold's inner call shape: the actor plus two
        # subjects nobody ever provisioned.
        folded = await access_mod.resolve_access(actor_email)
        for email in ("ghost1@fracktal.in", "ghost2@fracktal.in"):
            folded = folded.intersect(await access_mod.resolve_access(email))
        return folded

    access = await _fake_resolve_session("sess-1", "actor@fracktal.in")
    assert not access.is_active
    assert world.writes == []


async def test_the_signin_path_is_the_only_caller_that_opts_in() -> None:
    """dw3, pinned at the source: exactly one call site passes the flag.

    A future caller that copy-pastes ``record_request=True`` onto a fan-out
    would re-open the P1 silently, so this reads the tree rather than trusting
    a convention.
    """
    hits: list[str] = []
    for path in sorted(REPO_ROOT.glob("**/*.py")):
        parts = set(path.parts)
        # `.claude` carries agent WORKTREES — full checkouts of this same repo
        # under the repo root. Without it, this repo-wide glob finds
        # `packages/acb_auth/acb_auth/deps.py` once per worktree in flight and
        # fails with a list that looks like three new opt-in call sites. That
        # is a false P1 report, which is worse than no test: it trains the next
        # reader to dismiss this failure. Measured 2026-08-10 with two agent
        # worktrees present.
        if parts & {".venv", "node_modules", "__pycache__", "site-packages", ".claude"}:
            continue
        if path.name == Path(__file__).name:
            continue
        text = path.read_text(encoding="utf-8", errors="ignore")
        if "record_request=True" in text:
            hits.append(str(path.relative_to(REPO_ROOT)).replace("\\", "/"))

    assert hits == ["packages/acb_auth/acb_auth/deps.py"], hits


async def test_a_suspended_member_never_generates_a_request(
    world: _AccessWorld,
) -> None:
    """dw4 — an email with an ``app_user`` row in ANY status is not a stranger."""
    from acb_auth.access import resolve_access

    for status in ("active", "invited", "suspended", "removed"):
        world.rows.clear()
        world.writes.clear()
        world.seed_member("known@fracktal.in", status=status)

        access = await resolve_access(
            "known@fracktal.in", record_request=True, use_cache=False,
        )

        assert access.is_active is (status == "active")
        assert world.writes == [], f"{status} member filed as a request"


async def test_a_second_knock_inside_the_cache_ttl_writes_once(
    world: _AccessWorld,
) -> None:
    """dw5 — volume is bounded by the existing 60s resolution cache, not by
    the request rate. A locked-out colleague reloading the page must not turn
    into a write per request."""
    from acb_auth.access import resolve_access

    for _ in range(5):
        await resolve_access("stranger@fracktal.in", record_request=True)

    assert world.writes == ["stranger@fracktal.in"]


async def test_the_refusal_is_still_logged_when_a_request_is_recorded(
    world: _AccessWorld, monkeypatch: pytest.MonkeyPatch,
) -> None:
    """dw2 — the queue is additive; the journald line stays exactly as it was."""
    import acb_auth.access as access_mod

    seen: list[tuple[str, dict[str, Any]]] = []
    monkeypatch.setattr(
        access_mod._log, "warning",
        lambda event, **kw: seen.append((event, kw)),
    )

    await access_mod.resolve_access("stranger@fracktal.in", record_request=True)

    assert [e for e, _ in seen] == ["access_unprovisioned_signin"]
    assert seen[0][1]["email"] == "stranger@fracktal.in"


# ════════════════════════════════════════════════════════════════════════════
# 3. dw6/dw7/dw9 — the admin surface
# ════════════════════════════════════════════════════════════════════════════

async def test_the_queue_lists_undecided_requests_with_their_attempt_count(
    db: _FakeDB,
) -> None:
    """dw6, the half a fake can answer: which rows appear, and what they carry.

    It used to be called "…newest knock first" while comparing a **set** —
    the fake ignores ``ORDER BY``, so the ordering half was never checked here
    and cannot be. It is asserted against the statement instead, in
    ``test_the_pending_list_asks_the_database_for_the_order_it_promises``.
    """
    from gateway.routes.admin.access_requests import list_access_requests

    db.seed_request("old@fracktal.in", attempts=2)
    db.seed_request("new@fracktal.in", attempts=53)
    db.seed_request("decided@fracktal.in", status="approved")

    out = await list_access_requests(admin=FULL_ADMIN)

    assert {r.email for r in out} == {"old@fracktal.in", "new@fracktal.in"}
    assert next(r for r in out if r.email == "new@fracktal.in").attempt_count == 53


async def test_approving_provisions_the_member_ACTIVE_not_invited(
    db: _FakeDB,
) -> None:
    """dw7 — the whole point. An approval IS the decision to let somebody in,
    and they are already standing at the door; leaving them ``invited`` would
    re-create §2's two-click trap inside the fix for it."""
    from gateway.routes.admin.access_requests import (
        ApproveRequest,
        approve_access_request,
    )

    db.seed_request("ishaan@fracktal.in", attempts=53)

    out = await approve_access_request(
        "ishaan@fracktal.in", ApproveRequest(), admin=FULL_ADMIN,
    )

    assert out.status == "active"
    assert out.roles == ["member"]                       # `members.py:165` default
    row = db.user_by_email("ishaan@fracktal.in")
    assert row is not None and row["status"] == "active"
    assert row["joined_at"] is not None                  # activation stamps it
    assert db.requests["ishaan@fracktal.in"]["status"] == "approved"
    assert db.requests["ishaan@fracktal.in"]["decided_by"] == "admin@fracktal.in"
    # The cached refusal must not outlive the approval.
    assert "ishaan@fracktal.in" in db.invalidated


async def test_approving_honours_an_explicit_role_choice(db: _FakeDB) -> None:
    from gateway.routes.admin.access_requests import (
        ApproveRequest,
        approve_access_request,
    )

    db.seed_request("lead@fracktal.in")
    out = await approve_access_request(
        "lead@fracktal.in", ApproveRequest(roles=["manager"]), admin=FULL_ADMIN,
    )
    assert out.roles == ["manager"]


async def test_approving_cannot_assign_a_role_above_the_caller(db: _FakeDB) -> None:
    """Invariant 2 reaches the new route because it shares the invite's helper."""
    from gateway.routes.admin.access_requests import (
        ApproveRequest,
        approve_access_request,
    )

    db.seed_request("climber@fracktal.in")
    with pytest.raises(HTTPException) as exc:
        await approve_access_request(
            "climber@fracktal.in", ApproveRequest(roles=["owner"]),
            admin=FULL_ADMIN,
        )
    assert exc.value.status_code == 403
    assert db.user_by_email("climber@fracktal.in") is None
    assert db.requests["climber@fracktal.in"]["status"] == "pending"


async def test_approving_twice_refuses_and_creates_no_second_member(
    db: _FakeDB,
) -> None:
    """dw7's invariant — never two members — with a truthful second answer.

    The original shape returned 200 twice. That reads as "approved" while
    having done nothing, and it is the same 200 that let a DECIDED row be
    replayed (see the test below). A 409 naming the decision keeps the
    invariant and tells the admin who lost the race what actually happened.
    """
    from gateway.routes.admin.access_requests import (
        ApproveRequest,
        approve_access_request,
    )

    db.seed_request("twice@fracktal.in")
    first = await approve_access_request(
        "twice@fracktal.in", ApproveRequest(), admin=FULL_ADMIN,
    )
    assert first.status == "active"

    with pytest.raises(HTTPException) as exc:
        await approve_access_request(
            "twice@fracktal.in", ApproveRequest(), admin=FULL_ADMIN,
        )
    assert exc.value.status_code == 409
    assert "already approved" in str(exc.value.detail)
    assert len([u for u in db.users.values()
                if u["email"] == "twice@fracktal.in"]) == 1


async def test_approving_never_un_suspends_somebody(db: _FakeDB) -> None:
    """A suspension is an ``admin:members:manage`` decision; approve is gated on
    the weaker ``admin:members:invite``. Provisioning must not become a way to
    reverse the stronger act — and it must not *pretend* to, either.

    The earlier version of this test asserted only ``out.status``, so it passed
    while approve answered **200** on a person it had not admitted, marked the
    request `approved`, and re-granted `['member']` through ``set_roles``. The
    row then left a queue that renders only `pending` and the resolver's upsert
    never puts it back, so the suspended colleague was invisible for good. All
    four facts are asserted now, and the refusal is the loud one.
    """
    from gateway.routes.admin.access_requests import (
        ApproveRequest,
        approve_access_request,
    )

    db.seed_user("u-s", "suspended@fracktal.in", status="suspended")
    db.user_roles["u-s"] = ["admin"]
    db.seed_request("suspended@fracktal.in")

    with pytest.raises(HTTPException) as exc:
        await approve_access_request(
            "suspended@fracktal.in", ApproveRequest(), admin=FULL_ADMIN,
        )

    assert exc.value.status_code == 409
    assert "suspended" in str(exc.value.detail)
    assert db.users["u-s"]["status"] == "suspended"
    assert db.user_roles["u-s"] == ["admin"]           # not rewritten
    # Still in the owner's queue — the whole point of refusing rather than
    # filing it away as answered.
    assert db.requests["suspended@fracktal.in"]["status"] == "pending"


async def test_approving_never_reinstates_a_removed_member(db: _FakeDB) -> None:
    """The other half of the same rule, and the one that was actually open.

    Off-boarding is ``DELETE /admin/members/{email}`` on
    ``admin:members:manage``: `status='removed'`, role grants dropped. If
    provisioning treats `removed` as re-activatable, the weaker
    ``admin:members:invite`` puts them back — live, re-granted and
    re-``joined_at``. `removed → active` has exactly one door, and it is
    ``PATCH /admin/members/{email}``.

    ⚠️ The previous version asserted only ``out.status`` and ``joined_at``, and
    so was green against a **200** that left the member `removed` while
    ``set_roles`` re-granted `['member']` and the request went to `approved`.
    Lock B (the SQL) held the status; nothing held the rest, and the row's
    departure from the queue was permanent — the 53-knock incident recreated by
    its own fix. Approve must refuse, symmetrically with lock A.
    """
    from gateway.routes.admin.access_requests import (
        ApproveRequest,
        approve_access_request,
    )

    db.seed_user("u-x", "gone@fracktal.in", status="removed")
    db.seed_request("gone@fracktal.in")

    with pytest.raises(HTTPException) as exc:
        await approve_access_request(
            "gone@fracktal.in", ApproveRequest(), admin=FULL_ADMIN,
        )

    assert exc.value.status_code == 409
    assert "off-boarded" in str(exc.value.detail)
    assert db.users["u-x"]["status"] == "removed"
    assert db.users["u-x"]["joined_at"] is None
    assert db.user_roles.get("u-x", []) == []          # nothing re-granted
    assert db.requests["gone@fracktal.in"]["status"] == "pending"
    assert db.audit == []                              # no approval recorded


async def test_approving_an_existing_active_member_never_rewrites_their_roles(
    db: _FakeDB,
) -> None:
    """They already have access, so there is nothing to provision — and the
    role picker's default must not become a demotion.

    ``provision_member`` ends in ``set_roles``, which REPLACES a member's
    assignments. Approving a stale request over a live `admin` with the
    picker's default therefore rewrote them to `member` — on
    ``admin:members:invite``, while roles are otherwise governed by
    ``PUT /admin/members/{email}/roles`` on ``admin:members:manage``.

    Resolving the request as `approved` IS truthful here (they do have
    access), so the row leaves the queue — but the response has to say what
    actually happened, or a 200 that ignored the caller's role choice is
    indistinguishable from one that applied it.
    """
    from gateway.routes.admin.access_requests import (
        ApproveRequest,
        approve_access_request,
    )

    db.seed_user("u-a", "already@fracktal.in", status="active")
    db.user_roles["u-a"] = ["admin"]
    db.seed_request("already@fracktal.in")

    out = await approve_access_request(
        "already@fracktal.in", ApproveRequest(roles=["member"]),
        admin=FULL_ADMIN,
    )

    assert out.status == "active"
    assert out.roles == ["admin"]                      # theirs, not the ask
    assert db.user_roles["u-a"] == ["admin"]
    assert "already an active member" in out.detail
    assert db.requests["already@fracktal.in"]["status"] == "approved"


async def test_approving_an_invited_member_activates_them_with_the_roles(
    db: _FakeDB,
) -> None:
    """`invited` is the one existing status approve provisions over, and that
    is exactly what approving somebody means: they were provisioned but never
    admitted (§2 Step 1b), and they are standing at the door right now."""
    from gateway.routes.admin.access_requests import (
        ApproveRequest,
        approve_access_request,
    )

    db.seed_user("u-i", "waiting@fracktal.in", status="invited")
    db.user_roles["u-i"] = ["member"]
    db.seed_request("waiting@fracktal.in")

    out = await approve_access_request(
        "waiting@fracktal.in", ApproveRequest(roles=["manager"]),
        admin=FULL_ADMIN,
    )

    assert out.status == "active"
    assert out.roles == ["manager"]
    assert out.detail == ""                            # the normal path
    assert db.users["u-i"]["joined_at"] is not None
    assert db.requests["waiting@fracktal.in"]["status"] == "approved"


async def test_a_decided_request_cannot_be_replayed_to_undo_an_off_boarding(
    db: _FakeDB,
) -> None:
    """The P1, end to end, in the order it actually happens.

    Knock → approve → off-board. The `access_request` row survives the
    off-boarding (decided rows are kept on purpose) and the Requests tab
    renders only `pending`, so the row is invisible **and** still addressable.
    Re-POSTing approve on it must not be a second, weaker route back in.
    """
    from gateway.routes.admin.access_requests import (
        ApproveRequest,
        approve_access_request,
    )
    from gateway.routes.admin.members import remove_member

    db.seed_request("ex@fracktal.in")
    await approve_access_request(
        "ex@fracktal.in", ApproveRequest(), admin=FULL_ADMIN,
    )
    await remove_member("ex@fracktal.in", admin=FULL_ADMIN)
    row = db.user_by_email("ex@fracktal.in")
    assert row is not None and row["status"] == "removed"

    with pytest.raises(HTTPException) as exc:
        await approve_access_request(
            "ex@fracktal.in", ApproveRequest(), admin=FULL_ADMIN,
        )

    assert exc.value.status_code == 409
    assert row["status"] == "removed"
    assert db.user_roles[row["id"]] == []      # not re-granted


async def test_a_decision_that_loses_a_race_commits_nothing(
    db: _FakeDB, monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The read and the write are two statements; this is the gap between them.

    Another admin's transaction decides the same row after `_load_request`
    returned it. The UPDATE's own status condition then matches nothing, and
    the 409 is raised **before** ``db.commit()`` — so the provisioning done in
    between never lands.

    ⚠️ The fake has no rollback, so the in-memory `app_user` row survives here.
    ``db.committed`` is the honest assertion: in Postgres an uncommitted
    transaction is discarded when the session closes, and that is what this
    checks.
    """
    from gateway.routes.admin import access_requests as mod

    db.seed_request("race@fracktal.in")
    real_load = mod._load_request

    async def _load_then_lose(
        session: Any, email: str, *, allowed_statuses: tuple[str, ...]
    ) -> dict[str, Any]:
        row = await real_load(session, email, allowed_statuses=allowed_statuses)
        db.requests["race@fracktal.in"]["status"] = "approved"   # the loser
        return row

    monkeypatch.setattr(mod, "_load_request", _load_then_lose)

    with pytest.raises(HTTPException) as exc:
        await mod.approve_access_request(
            "race@fracktal.in", mod.ApproveRequest(), admin=FULL_ADMIN,
        )

    assert exc.value.status_code == 409
    assert "in flight" in str(exc.value.detail)
    assert db.committed == 0
    assert db.invalidated == []
    assert db.audit == []


async def test_approving_an_unknown_request_404s(db: _FakeDB) -> None:
    from gateway.routes.admin.access_requests import (
        ApproveRequest,
        approve_access_request,
    )

    with pytest.raises(HTTPException) as exc:
        await approve_access_request(
            "ghost@fracktal.in", ApproveRequest(), admin=FULL_ADMIN,
        )
    assert exc.value.status_code == 404


async def test_denying_marks_the_request_and_provisions_nobody(
    db: _FakeDB,
) -> None:
    """dw9."""
    from gateway.routes.admin.access_requests import deny_access_request

    db.seed_request("nope@fracktal.in")
    out = await deny_access_request("nope@fracktal.in", admin=FULL_ADMIN)

    assert out["status"] == "denied"
    assert db.requests["nope@fracktal.in"]["decided_by"] == "admin@fracktal.in"
    assert db.user_by_email("nope@fracktal.in") is None


async def test_re_denying_a_denied_request_is_harmless(db: _FakeDB) -> None:
    """Deny is the one decision that is safe to replay.

    It provisions nobody, revokes nothing and takes no access away, so a stale
    tab or a double click changes only `decided_by`/`decided_at`. Refusing it
    would be an error message about a state the caller already wanted.
    """
    from gateway.routes.admin.access_requests import deny_access_request

    db.seed_request("nope@fracktal.in", status="denied")
    out = await deny_access_request("nope@fracktal.in", admin=FULL_ADMIN)

    assert out["status"] == "denied"
    assert db.requests["nope@fracktal.in"]["status"] == "denied"
    assert db.user_by_email("nope@fracktal.in") is None


async def test_denying_an_approved_request_is_refused(db: _FakeDB) -> None:
    """Deny does not reach the member it created, so replaying it over an
    approval could only produce a queue record that contradicts the roster —
    a "denied" row for somebody who is signed in. Suspend them instead."""
    from gateway.routes.admin.access_requests import deny_access_request

    db.seed_user("u-live", "live@fracktal.in", status="active")
    db.seed_request("live@fracktal.in", status="approved")

    with pytest.raises(HTTPException) as exc:
        await deny_access_request("live@fracktal.in", admin=FULL_ADMIN)

    assert exc.value.status_code == 409
    assert db.requests["live@fracktal.in"]["status"] == "approved"
    assert db.users["u-live"]["status"] == "active"


async def test_a_denied_address_that_keeps_knocking_never_returns_to_pending(
    db: _FakeDB, world: _AccessWorld,
) -> None:
    """dw9 — the upsert bumps ``last_seen_at``/``attempt_count`` and leaves
    ``status`` alone, so a denial is not undone by persistence."""
    from acb_auth.access import _ACCESS_REQUEST_UPSERT_SQL

    normalised = " ".join(_ACCESS_REQUEST_UPSERT_SQL.split())
    conflict = normalised.split("DO UPDATE", 1)[1]
    assert "last_seen_at" in conflict
    assert "attempt_count" in conflict
    assert "status" not in conflict, (
        "the upsert's DO UPDATE arm touches status — a denied address would "
        "return to pending on its next sign-in"
    )


# ════════════════════════════════════════════════════════════════════════════
# 4. Invariant 1 — the org always has an owner — inside the shared helper
#
# `set_roles` REPLACES a member's assignments. Both provisioning callers
# default to `["member"]`, so provisioning the last owner deletes the org's
# only owner grant. Every sibling write in `members.py` calls
# `assert_owner_survives`; the extracted helper now does too, or the extraction
# would have hung a second route off a gap that only invite had.
# ════════════════════════════════════════════════════════════════════════════

async def test_inviting_the_last_owner_refuses_rather_than_demoting_them(
    db: _FakeDB,
) -> None:
    from gateway.routes.admin.members import InviteRequest, invite_member

    with pytest.raises(HTTPException) as exc:
        await invite_member(
            InviteRequest(email="owner@fracktal.in"), admin=FULL_ADMIN,
        )

    assert exc.value.status_code == 409
    assert "no owner" in str(exc.value.detail)
    assert db.user_roles["u-owner"] == ["owner"]      # grant intact


async def test_approving_an_invited_last_owner_refuses_rather_than_demoting(
    db: _FakeDB,
) -> None:
    """The reason it belongs in the helper and not in `members.py`: the
    approve route reaches the same `set_roles` by a different door, and an
    ownerless org is recovered with SQL on the production box.

    The owner is `invited` here because that is now the **only** existing
    status approve provisions over (:data:`APPROVE_MATRIX`) — over an `active`
    owner the roles are not touched at all, which the test below pins.
    """
    from gateway.routes.admin.access_requests import (
        ApproveRequest,
        approve_access_request,
    )

    db.users["u-owner"]["status"] = "invited"
    db.seed_request("owner@fracktal.in")

    with pytest.raises(HTTPException) as exc:
        await approve_access_request(
            "owner@fracktal.in", ApproveRequest(), admin=FULL_ADMIN,
        )

    assert exc.value.status_code == 409
    assert "no owner" in str(exc.value.detail)
    assert db.user_roles["u-owner"] == ["owner"]
    assert db.requests["owner@fracktal.in"]["status"] == "pending"


async def test_approving_over_an_active_owner_leaves_the_grant_alone(
    db: _FakeDB,
) -> None:
    """The same lockout, closed one layer earlier and without an error.

    An `active` member's roles are never rewritten by approve, so the org's
    only owner grant is not even a candidate for deletion here. Invariant 1
    stays the fence for the paths that DO call `set_roles`.
    """
    from gateway.routes.admin.access_requests import (
        ApproveRequest,
        approve_access_request,
    )

    db.seed_request("owner@fracktal.in")

    out = await approve_access_request(
        "owner@fracktal.in", ApproveRequest(), admin=FULL_ADMIN,
    )

    assert out.roles == ["owner"]
    assert db.user_roles["u-owner"] == ["owner"]
    assert out.detail                      # says nothing was provisioned


async def test_provisioning_a_second_owner_is_never_blocked_by_invariant_1(
    db: _FakeDB,
) -> None:
    """The check must not fire when nothing is being taken away — an org with
    two owners can still have one of them re-provisioned."""
    from gateway.routes.admin.members import InviteRequest, invite_member

    db.seed_user("u-owner2", "owner2@fracktal.in")
    db.user_roles["u-owner2"] = ["owner"]

    entry = await invite_member(
        InviteRequest(email="owner@fracktal.in"), admin=FULL_ADMIN,
    )
    assert entry.roles == ["member"]
    assert db.user_roles["u-owner"] == ["member"]


async def test_provisioning_is_not_blocked_in_an_org_that_has_no_owner(
    db: _FakeDB,
) -> None:
    """The narrowing in ``provision_member``'s invariant-1 check, fenced.

    The check asks ``assert_owner_survives`` only when the target **actually
    holds** `owner` and the new role set does not — i.e. only when a grant is
    about to be lost. Without that condition every re-provision in an org with
    no owner yet (the bootstrap state, where nothing is being taken away)
    would 409 with "this would leave the organization with no owner", which is
    true of an org that never had one and blocks the only way back.

    ⚠️ This test exists because the `db` fixture always seeds `u-owner`:
    deleting the ``"owner" in await roles_for_user(...)`` clause left every
    other case in this file green. The owner is removed here on purpose.
    """
    from gateway.routes.admin.members import InviteRequest, invite_member

    del db.user_roles["u-owner"]
    del db.users["u-owner"]
    db.seed_user("u-p", "priya@fracktal.in", status="invited")
    db.user_roles["u-p"] = ["member"]

    entry = await invite_member(
        InviteRequest(email="priya@fracktal.in"), admin=FULL_ADMIN,
    )
    assert entry.roles == ["member"]
    assert db.users["u-p"]["status"] == "invited"


# ── An address from outside the company's own domain ────────────────────────

async def test_an_off_domain_request_is_flagged_for_the_admin(
    db: _FakeDB, monkeypatch: pytest.MonkeyPatch,
) -> None:
    """`deps.get_current_user` LOGS an off-domain identity and carries on, and
    an Entra B2B guest is a directory member like anybody else — so the tenant
    pin does not keep them out of this queue. Approve provisions `active`
    immediately, so the row is the last place the difference is visible."""
    from gateway.routes.admin.access_requests import list_access_requests

    monkeypatch.setenv("ALLOWED_EMAIL_DOMAIN", "fracktal.in")
    db.seed_request("colleague@fracktal.in")
    db.seed_request("guest@contoso.com")

    flags = {r.email: r.is_external for r in
             await list_access_requests(admin=FULL_ADMIN)}

    assert flags == {"colleague@fracktal.in": False, "guest@contoso.com": True}


# ════════════════════════════════════════════════════════════════════════════
# 5. THE SQL ITSELF — the assertions the fake cannot make
#
# `_FakeDB` re-implements `ON CONFLICT DO UPDATE` in Python and ignores
# `ORDER BY`. A mirror agrees with itself: widening the provisioning guard to
# `app_user.status <> 'active'` left all 28 behavioural cases green while
# approve gained the power to reinstate an off-boarded member. Claims that live
# INSIDE a statement are therefore asserted against the statement string.
# ════════════════════════════════════════════════════════════════════════════

#: A comparison whose LEFT side is the EXISTING row's status. `:status` (the
#: caller's argument) is deliberately not matched — the rule is about which
#: rows may be rewritten, not about what they are rewritten to.
_EXISTING_STATUS_TEST = re.compile(
    r"app_user\.status\s*(=|<>|!=|NOT\s+IN|IN)\s*(\([^)]*\)|'[a-z]+')",
    re.IGNORECASE,
)


def test_provisioning_only_ever_rewrites_a_status_it_names() -> None:
    """The fence the verifier's mutation walked through.

    **The guard NAMES the statuses it rewrites; it never names the ones it does
    not.** `IN ('removed','invited')` and `<> 'active'` are both ways of saying
    "and everything else that happens to qualify" — the first let approve
    reinstate a `removed` member, the second would let it un-suspend one. Only
    equality against a literal we chose is allowed, and the only two literals
    we chose are `invited` and `removed`.
    """
    from gateway.routes.admin._common import _PROVISION_MEMBER_SQL

    sql = " ".join(_PROVISION_MEMBER_SQL.split())
    conflict = sql.split("DO UPDATE", 1)[1]

    tests = [(op.upper().replace("  ", " "), operand)
             for op, operand in _EXISTING_STATUS_TEST.findall(conflict)]
    assert tests, "the DO UPDATE arm guards on nothing at all"
    for op, operand in tests:
        assert op == "=", (
            f"`app_user.status {op} {operand}` is a NEGATIVE guard: it rewrites "
            "every status except the named one, including statuses set under a "
            "stronger permission than approve holds. Name what you rewrite."
        )
        assert operand in ("'invited'", "'removed'"), (
            f"{operand} is not a status this helper may rewrite"
        )

    # Nothing else in the arm reads the existing status except the ELSE
    # branches that hand it straight back unchanged.
    assert len(re.findall(r"app_user\.status", conflict)) == (
        len(tests) + conflict.count("ELSE app_user.status")
    ), "an unrecognised reference to app_user.status appeared in the guard"

    # `invited` is the ONLY route to `active` — dw7's one-action approval.
    assert ("=", "'invited'") in tests

    # `removed` may come back as `invited` (that is invite's pre-extraction
    # behaviour) but never as `active`: reinstating an off-boarded member is an
    # `admin:members:manage` act and approve holds only `:invite`.
    assert re.search(
        r"app_user\.status\s*=\s*'removed'\s+AND\s+:status\s*<>\s*'active'",
        conflict, re.IGNORECASE,
    ), (
        "the `removed` arm does not exclude :status='active' — approving a "
        "request for an off-boarded address would put them back, live"
    )

    # And the join stamp follows the same one door.
    joined = conflict.split("joined_at", 1)[1]
    assert re.search(
        r"app_user\.status\s*=\s*'invited'\s+AND\s+:status\s*=\s*'active'",
        joined, re.IGNORECASE,
    )


def test_the_approve_matrix_answers_every_member_status() -> None:
    """The matrix is the whole of approve's answer about an existing row, so
    it must have an answer for every status a row can be in.

    ``members.VALID_STATUSES`` is the vocabulary; a fifth one added there
    without a decision here would otherwise fall through
    ``APPROVE_MATRIX.get(status, "refuse")`` and be silently refused with a
    generic message — safe, but nobody would have decided it. The exact
    dispositions are pinned too: `invited` is the ONLY status approve
    provisions over, which is what makes "approve never rewrites the roles of
    a member who already exists in a state other than `invited`" checkable
    rather than a comment.
    """
    from gateway.routes.admin.access_requests import APPROVE_MATRIX
    from gateway.routes.admin.members import VALID_STATUSES

    assert set(APPROVE_MATRIX) == set(VALID_STATUSES)
    assert [s for s, a in APPROVE_MATRIX.items() if a == "provision"] == [
        "invited"
    ]
    assert APPROVE_MATRIX["active"] == "already-a-member"
    assert {s for s, a in APPROVE_MATRIX.items() if a == "refuse"} == {
        "suspended", "removed",
    }


def test_the_decision_write_repeats_the_filter_the_read_checked() -> None:
    """`_load_request` reads and `_decide` writes: two statements, so two
    admins deciding the same row both pass the read. The UPDATE carries the
    same status condition, so the loser updates zero rows and — because that
    happens before ``db.commit()`` — its provisioning is discarded with it.

    Asserted against the statement because the fake DB re-implements the
    UPDATE in Python; a mirror can only agree with itself.
    """
    from gateway.routes.admin.access_requests import _DECIDE_SQL

    sql = " ".join(_DECIDE_SQL.split())
    assert "status = ANY(:allowed)" in sql, (
        "the decision write does not re-check the status it read, so two "
        "concurrent decisions would both succeed"
    )
    assert "RETURNING" in sql, (
        "without RETURNING there is no way to tell a lost race from a win"
    )


def test_each_decision_write_is_given_its_own_route_s_read_filter() -> None:
    """The condition above is only a lock if the two call sites agree.

    Approve may act on `pending` only; deny also accepts `denied`, because
    re-denying grants nothing. Each route must hand `_decide` the SAME tuple
    it handed `_load_request` — a wider one at the write would re-open exactly
    what the read refused.
    """
    import inspect

    from gateway.routes.admin import access_requests as mod

    for fn, expected in (
        (mod.approve_access_request, 'allowed_statuses=("pending",)'),
        (mod.deny_access_request, 'allowed_statuses=("pending", "denied")'),
    ):
        src = inspect.getsource(fn)
        assert src.count(expected) == 2, (
            f"{fn.__name__} does not pass {expected} to BOTH _load_request "
            f"and _decide (found {src.count(expected)} occurrence(s))"
        )


def test_the_pending_list_asks_the_database_for_the_order_it_promises() -> None:
    """dw6 says "newest ``last_seen_at`` first". The fake ignores ``ORDER BY``
    and the behavioural case below compares a **set**, so this is the only
    place that claim is actually checked."""
    from gateway.routes.admin.access_requests import _PENDING_REQUESTS_SQL

    sql = " ".join(_PENDING_REQUESTS_SQL.split())
    assert "WHERE status = 'pending'" in sql
    assert "ORDER BY last_seen_at DESC" in sql


# ── Wiring: the /admin floor is per-route, not a package property ────────────

def test_every_new_request_route_declares_the_admin_floor() -> None:
    """dw6 — ``_common.py:31`` creates the router with **no** ``dependencies=``.

    Every existing route carries ``Depends(require_admin_user)`` in its own
    signature; a route added here that omits it inherits no floor at all and is
    reachable by any authenticated member. That is the easiest hole to ship in
    this package, so it is pinned rather than reviewed.
    """
    import inspect

    from gateway.routes.admin._common import require_admin_user, router

    found = 0
    for route in router.routes:
        path = getattr(route, "path", "")
        if not path.startswith("/admin/members/requests"):
            continue
        found += 1
        params = inspect.signature(route.endpoint).parameters.values()
        assert any(
            getattr(p.default, "dependency", None) is require_admin_user
            for p in params
        ), f"{sorted(route.methods)} {path} declares no require_admin_user floor"
    assert found == 3, f"expected 3 request routes, found {found}"


def test_the_two_request_writes_carry_the_invite_permission() -> None:
    """dw6/dw7 — no new permission slug: a slug nobody has been granted is
    nobody's grant until an admin creates it (the N4 lesson). Approve and deny
    reuse ``admin:members:invite``, which the roles seed already gives out."""
    from gateway.routes.admin._common import router

    writes = {
        route.path: route
        for route in router.routes
        if route.path.startswith("/admin/members/requests/")
    }
    assert len(writes) == 2
    for path, route in writes.items():
        deps = [
            getattr(d.dependency, "__qualname__", "")
            for d in getattr(route, "dependencies", [])
        ]
        assert any("require_permission" in q for q in deps), (
            f"{path} carries no require_permission gate"
        )


def test_the_requests_list_route_is_not_shadowed_by_the_member_routes() -> None:
    """``/admin/members/requests`` and ``/admin/members/{email}/access`` live on
    the same router; a future single-segment ``GET /members/{email}`` added
    above it would swallow the list."""
    from gateway.routes.admin._common import router

    for route in router.routes:
        if route.path == "/admin/members/requests":
            return
        if "GET" in (getattr(route, "methods", set()) or set()):
            assert not re.fullmatch(r"/admin/members/\{[^/]+\}", route.path), (
                f"{route.path} is registered before /admin/members/requests "
                f"and would shadow it"
            )
    raise AssertionError("GET /admin/members/requests is not registered")


# ── dw11: the Members list stops rendering `invited` as though it were live ──

def _members_page() -> str:
    # ⚠️ Path moved by D49 (2026-08-24): the roster is a TAB of Organisation
    # (`launch_surface.md` §6.2). Same JSX, same claims — new file.
    return (
        REPO_ROOT
        / "workbench/control_plane/src/app/settings/organization/OrganizationAdmin.tsx"
    ).read_text(encoding="utf-8")


def test_a_failed_queue_fetch_is_never_rendered_as_nobody_is_waiting() -> None:
    """The queue's own failure mode, and the reason it matters.

    ``setRequests(q.ok ? await q.json() : [])`` swallowed a failed fetch, so a
    deployment where migration 143 had not run rendered "Nobody is waiting." —
    **the broken state was indistinguishable from the working one**, which is
    the exact silence this tab exists to end.
    """
    page = _members_page()

    assert "setQueueError" in page
    assert "The sign-in queue could not be loaded" in page
    assert "The sign-in queue is unavailable" in page
    # The empty-state copy must be reachable only when the queue answered.
    assert "if (failed)" in page


def test_the_queue_is_re_read_after_every_decision_including_a_refused_one() -> (
    None
):
    """A refusal is almost always ABOUT the row the admin clicked — already
    decided, suspended, off-boarded, or decided by somebody else mid-flight.

    The first version returned early on `!res.ok`, so the tab kept rendering a
    row whose state the server had just corrected, and the only way out was
    Refresh. With the approve matrix adding three more 409 paths, that stale
    row is now the common case rather than the rare one, so `decide` has no
    early return: every response — 200 or 409 — is followed by `load()`.
    """
    body = _members_page().split("const decide = async (", 1)[1]
    body = body.split("\n  };", 1)[0]

    assert "await load();" in body
    assert "return;" not in body, (
        "decide() returns early on a failed decision, so a refused 409 leaves "
        "the stale row on screen until the admin hits Refresh"
    )


def test_an_approval_that_changed_nothing_says_so_on_screen() -> None:
    """Approving over an EXISTING active member deliberately does not touch
    their roles, so the picker's selection is ignored. That is correct — and
    invisible unless the page renders the `detail` the gateway returns, which
    would leave a 200 that did something other than what was asked looking
    exactly like one that did.

    Both halves are asserted — the decision handler must SET it and the page
    must RENDER it. A bare ``"setNotice" in page`` passes on the declaration
    alone, which is a mirror of the code rather than a check on it: it stayed
    green under a mutation that deleted the assignment.
    """
    page = _members_page()
    body = page.split("const decide = async (", 1)[1].split("\n  };", 1)[0]

    assert "setNotice(body.detail)" in body, (
        "decide() never surfaces the gateway's `detail`, so an approval that "
        "left an existing member's roles alone looks like one that applied "
        "the caller's choice"
    )
    assert "{notice && (" in page, "the notice is set but never rendered"


def test_the_requests_tab_marks_an_address_from_outside_the_company() -> None:
    """`is_external` is resolved by the gateway (the domain is server policy)
    and rendered here, because approve provisions `active` immediately and a
    directory guest would otherwise be indistinguishable from a colleague."""
    page = _members_page()

    assert "request.is_external" in page
    assert "outside the company domain" in page


def test_the_members_list_labels_an_invited_row_as_never_signed_in() -> None:
    """dw11 (carried over from the retracted N6b). ``invited`` renders today as
    a bare status word, which reads as a state of membership rather than the
    dead end it is — that ambiguity is what let the owner believe the job was
    done on 2026-08-04."""
    assert "never signed in" in _members_page()


async def test_approve_verifies_the_write_instead_of_trusting_the_matrix(
    db: _FakeDB,
) -> None:
    """The third instance of the silent-success shape — a race, not a sequence.

    ``APPROVE_MATRIX`` is read BEFORE the write, which closed the two
    sequential holes. It does not close the concurrent one:
    ``_PROVISION_MEMBER_SQL``'s CASE arms are re-evaluated by Postgres against
    the latest committed row (``ON CONFLICT DO UPDATE`` waits on a concurrent
    writer, then re-reads). A second admin off-boarding this person between
    approve's ``find_member`` and its upsert therefore lands every arm on
    ``ELSE app_user.status``, the provisioning declines **in silence**, and
    approve went on to stamp `approved` and record an audit entry for it.

    That is the same three consequences the two earlier rounds fixed: 200
    returned, person still locked out, and the request gone from a queue that
    renders only `pending` — so they can never reappear, because the
    resolver's upsert deliberately never rewrites `status` (dw9).

    Approve must therefore VERIFY, not predict: the person is `active` before
    the decision is stamped, or nothing is.
    """
    from gateway.routes.admin.access_requests import (
        ApproveRequest,
        approve_access_request,
    )

    db.seed_user("u-race", "priya@fracktal.in", status="invited")
    db.seed_request("priya@fracktal.in")

    # The concurrent off-boarding, committed from OUTSIDE the route: it lands
    # after approve has read the row and decided `provision`, but before the
    # upsert. The SUT is not patched — only the world underneath it moves.
    inner = db.execute
    seen = {"reads": 0}

    async def racing_execute(sql: Any, params: dict | None = None) -> Any:
        s = " ".join(str(sql).split())
        if "FROM app_user WHERE lower(email)" in s:
            seen["reads"] += 1
            if seen["reads"] == 1:
                result = await inner(sql, params)
                db.users["u-race"]["status"] = "removed"
                return result
        return await inner(sql, params)

    db.execute = racing_execute  # type: ignore[method-assign]

    with pytest.raises(HTTPException) as exc:
        await approve_access_request(
            "priya@fracktal.in", ApproveRequest(), admin=FULL_ADMIN,
        )

    assert exc.value.status_code == 409
    assert "in flight" in str(exc.value.detail)
    # Nothing declared, nothing lost: the request is still in the queue, no
    # approval was recorded, and the transaction never reached commit.
    assert db.requests["priya@fracktal.in"]["status"] == "pending"
    assert db.audit == []
    assert db.committed == 0


async def test_an_unrecognised_member_status_fails_closed(db: _FakeDB) -> None:
    """The matrix's sixth row — the one no shipped status exercises.

    ``APPROVE_MATRIX.get(status, "refuse")`` is the only thing standing
    between a future ``app_user.status`` and provisioning-by-default. Nothing
    pinned it: flipping the fallback to `"provision"` left all 50 cases green.
    The lesson of round 1 was that a test mirroring the code is not a check on
    it, so this asserts the behaviour a new status must meet, not the dict.

    ⚠️ **Asserting only ``status_code == 409`` is not enough**, and the first
    version of this test made exactly that mistake. With the fallback flipped
    to `provision`, ``provision_member`` runs, the CASE arms decline the
    unknown status in silence, and the terminal read-back check then raises
    its own 409 — so a bare status-code assertion passes while the matrix is
    wide open. The two refusals are told apart by *when* they fire: this one
    happens before anything is written, so no role may have been granted.
    """
    from gateway.routes.admin.access_requests import (
        ApproveRequest,
        approve_access_request,
    )

    db.seed_user("u-odd", "future@fracktal.in", status="archived")
    db.seed_request("future@fracktal.in")

    with pytest.raises(HTTPException) as exc:
        await approve_access_request(
            "future@fracktal.in", ApproveRequest(), admin=FULL_ADMIN,
        )

    assert exc.value.status_code == 409
    assert "does not know how to answer" in str(exc.value.detail), (
        "the refusal came from somewhere further down — the matrix let an "
        "unrecognised status through to provisioning"
    )
    # The discriminator: nothing was written at all. `set_roles` runs inside
    # `provision_member`, so a granted role proves the matrix fell through.
    assert db.user_roles.get("u-odd", []) == []
    assert db.users["u-odd"]["status"] == "archived"
    assert db.requests["future@fracktal.in"]["status"] == "pending"
    assert db.audit == []
    assert db.committed == 0
