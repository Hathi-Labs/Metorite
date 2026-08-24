"""Deleting a member permanently — ``DELETE /admin/members/{email}/purge``.

Spec: ``project-docs/specs/colleague_onboarding.md`` §2 Step 5 (WS-24 / N8).

A **second, harder action beside** Remove, not a flag on it. Remove is a soft
off-boarding and its reasoning stands; this one destroys the identity and every
credential, grant and private session keyed to it, and leaves what the person
authored — and the audit trail — alone.

Three things this file has to keep apart, because getting any of them wrong is
silent:

* **the self-guard** (invariant 4, ``_common.assert_not_self_lockout``) — you
  may not purge yourself, whoever else holds `owner`;
* **invariant 1** (``assert_owner_survives``) — the org may not be left
  ownerless, whoever is asking;
* **what is deleted vs what is kept** — an audit trail that disappears with the
  person is not an audit trail, and a purge that silently took a shared room
  with it would be the "purge their content too" option that was rejected.

⚠️ **The first two both answer 409**, and this route calls both. Every refusal
below is discriminated by its detail text *and* by what was and was not
written; a bare ``status_code == 409`` would pass against either.

⚠️ **The third is decided in SQL**, in ``members._PURGE_DELETES`` /
``_PURGE_KEEPS``, and ``_admin_fakes._FakeDB`` can only answer from rows a test
seeded. A behavioural case here proves the route ran the statement it holds; it
cannot prove the statement names the right tables. §4 is the fence: it asserts
against the constants themselves — in particular that no audit table appears on
the delete side at all.
"""
from __future__ import annotations

import inspect
import re
from pathlib import Path

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
    permission gate is a separate, already-tested concern
    (`test_org_access_enforcement`) and every case here is about what happens
    after it has been satisfied.
    """
    return UserContext(
        email=email,
        role=UserRole.EXECUTIVE,
        access=build_access(ADMIN_PERMISSIONS, roles=roles or ["owner"]),
    )


OWNER = _caller("owner@fracktal.in")
ADMIN = _caller("admin@fracktal.in", roles=["admin"])

PRIYA = "priya@fracktal.in"

#: Her connected task workspace. Seeded with a real id because the GTD store
#: is DUAL-SOURCE: `account_id IS NULL` is a LOCAL row she authored, a set
#: `account_id` is a mirror of a provider task that the credential cascades
#: away. A fixture that seeded neither would make the two halves
#: indistinguishable, which is the state in which the counts were wrong.
TASK_ACCOUNT = "ta-1"


@pytest.fixture()
def db(monkeypatch: pytest.MonkeyPatch) -> _FakeDB:
    """An org with one owner, one admin, and one colleague who has a life.

    Priya is seeded with something in every category the purge touches, so a
    step that silently stops running shows up as a count of 0 rather than as
    an absent key.
    """
    from gateway.routes.admin import members

    fake = _FakeDB()
    bind_admin_db(monkeypatch, fake, (members,))

    fake.seed_user("u-owner", "owner@fracktal.in", joined_at="then")
    fake.user_roles["u-owner"] = ["owner"]
    fake.seed_user("u-admin", "admin@fracktal.in", joined_at="then")
    fake.user_roles["u-admin"] = ["admin"]
    fake.seed_user("u-priya", PRIYA, joined_at="then")
    fake.user_roles["u-priya"] = ["member"]

    # ── access grants ──
    fake.seed_rows("user_permission_override", {"user_id": "u-priya"})
    fake.seed_rows("org_group_member", {"user_id": "u-priya"},
                   {"user_id": "u-priya"})
    fake.seed_rows("app_grants", {"subject": PRIYA})
    fake.seed_rows("app_tool_grants", {"user_email": PRIYA})
    # ── credentials ──
    fake.seed_rows("email_accounts", {"user_id": PRIYA})
    fake.seed_rows("wa_accounts", {"user_id": PRIYA})
    fake.seed_rows("task_accounts", {"id": TASK_ACCOUNT, "user_id": PRIYA})
    # ── sessions: two private, one shared room she started ──
    fake.seed_rows(
        "chat_session",
        {"id": "s-1", "user_id": PRIYA, "visibility": "private"},
        {"id": "s-2", "user_id": PRIYA, "visibility": "private"},
        {"id": "s-room", "user_id": PRIYA, "visibility": "org"},
    )
    fake.seed_rows("chat_session_participant", {"subject": PRIYA})
    # ── the knock that let her in ──
    fake.seed_request(PRIYA, status="approved")
    # ── audit, and work she authored ──
    fake.seed_rows("app_audit", *({"user_email": PRIYA} for _ in range(47)))
    fake.seed_rows("audit_event", {"actor": f"user:{PRIYA}"},
                   {"actor": f"user:{PRIYA}"})
    fake.seed_rows("agent_run", {"user_id": PRIYA})
    fake.seed_rows("apps", {"owner_email": PRIYA})
    fake.seed_rows("workflows", {"owner_email": PRIYA})
    # ── the GTD store, both halves: 3 LOCAL items + 2 SYNCED, 1 LOCAL
    #    project + 1 SYNCED. The SYNCED rows go with `task_accounts`; the
    #    LOCAL ones are hers and survive.
    fake.seed_rows("gtd_items",
                   {"id": "i-1", "user_id": PRIYA},
                   {"id": "i-2", "user_id": PRIYA},
                   {"id": "i-3", "user_id": PRIYA},
                   {"id": "i-s1", "user_id": PRIYA, "account_id": TASK_ACCOUNT},
                   {"id": "i-s2", "user_id": PRIYA, "account_id": TASK_ACCOUNT})
    fake.seed_rows("gtd_projects",
                   {"id": "p-1", "user_id": PRIYA},
                   {"id": "p-s1", "user_id": PRIYA, "account_id": TASK_ACCOUNT})
    fake.seed_rows("meeting", {"owner_email": PRIYA})
    return fake


def _second_owner(db: _FakeDB) -> None:
    """The state §2 Step 2 exists to produce — and the one that opens the hole.

    With this row present ``assert_owner_survives`` passes for every case that
    seeds it, so any refusal that still happens can only be the self-guard.
    """
    db.seed_user("u-owner2", "second@fracktal.in", joined_at="then")
    db.user_roles["u-owner2"] = ["owner"]


def _nothing_was_written(db: _FakeDB) -> None:
    """No commit, no cache invalidation, no audit entry.

    A guard that refuses *after* something landed, or that records the act it
    declined, is only half a refusal — and for a purge the "something" is
    unrecoverable.
    """
    assert db.committed == 0
    assert db.invalidated == []
    assert db.audit == []


# ════════════════════════════════════════════════════════════════════════════
# 1. The guards — both answer 409, so every case discriminates
# ════════════════════════════════════════════════════════════════════════════

async def test_purging_yourself_is_refused_when_another_owner_survives(
    db: _FakeDB,
) -> None:
    """Two owners, so invariant 1 is satisfied and has nothing to say.

    The refusal that remains can only be the self-guard, and it is checked by
    its wording. A version of this test seeded with one owner would stay green
    with the self-guard deleted — `assert_owner_survives` would answer instead
    — and would prove nothing.

    ⚠️ Mutation-checked: removing the ``assert_not_self_lockout`` call from
    ``purge_member`` makes this fail — the purge goes through and the owner's
    row is gone from ``app_user``.
    """
    from gateway.routes.admin.members import purge_member

    _second_owner(db)

    with pytest.raises(HTTPException) as exc:
        await purge_member("owner@fracktal.in", admin=OWNER)

    assert exc.value.status_code == 409
    detail = str(exc.value.detail)
    assert "cannot permanently delete yourself" in detail
    assert "no owner" not in detail, (
        "this is invariant 1 firing, not the self-guard — the two-owner world "
        "was supposed to make invariant 1 silent here"
    )
    assert "u-owner" in db.users, "the caller's own member record was deleted"
    assert db.user_roles["u-owner"] == ["owner"]
    _nothing_was_written(db)


async def test_that_same_world_still_lets_the_other_owner_be_purged(
    db: _FakeDB,
) -> None:
    """The control: identical world, identical route, only the identity differs.

    Without it, a guard that simply refused every purge would look correct.
    """
    from gateway.routes.admin.members import purge_member

    _second_owner(db)

    out = await purge_member("second@fracktal.in", admin=OWNER)

    assert out.status == "purged"
    assert "u-owner2" not in db.users
    assert db.committed == 1


async def test_purging_the_last_owner_is_refused_by_invariant_1(
    db: _FakeDB,
) -> None:
    """An *admin* purging the org's only owner is not a self-lockout — it is
    the permanently ownerless org invariant 1 exists to prevent.

    Unlike a soft removal this cannot be undone by re-inviting: there would be
    no owner left to do the inviting and no row to promote back.
    """
    from gateway.routes.admin.members import purge_member

    with pytest.raises(HTTPException) as exc:
        await purge_member("owner@fracktal.in", admin=ADMIN)

    assert exc.value.status_code == 409
    detail = str(exc.value.detail)
    assert "no owner" in detail
    assert "yourself" not in detail, (
        "the self-guard answered for a caller who is not the target"
    )
    assert "u-owner" in db.users
    _nothing_was_written(db)


async def test_the_last_owner_purging_themselves_hears_the_self_refusal(
    db: _FakeDB,
) -> None:
    """One owner, so BOTH guards would refuse. The self-guard must answer.

    It runs first deliberately: "you cannot delete yourself" is the actionable
    half, where "assign another owner first" invites the caller to do exactly
    that and then hit the real wall.
    """
    from gateway.routes.admin.members import purge_member

    with pytest.raises(HTTPException) as exc:
        await purge_member("owner@fracktal.in", admin=OWNER)

    assert "cannot permanently delete yourself" in str(exc.value.detail)
    assert "no owner" not in str(exc.value.detail)
    _nothing_was_written(db)


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
    """The most destructive door must not be one directory quirk from absent.

    Two owners again, so only the self-guard can be answering.
    """
    from gateway.routes.admin.members import purge_member

    _second_owner(db)
    db.users["u-owner"]["email"] = row_email

    with pytest.raises(HTTPException) as exc:
        await purge_member(row_email, admin=_caller(caller_email))

    assert "cannot permanently delete yourself" in str(exc.value.detail)
    assert "u-owner" in db.users


async def test_purging_somebody_who_is_not_a_member_is_a_404(
    db: _FakeDB,
) -> None:
    """No row, nothing to purge — and no half-run statement either."""
    from gateway.routes.admin.members import purge_member

    with pytest.raises(HTTPException) as exc:
        await purge_member("stranger@fracktal.in", admin=OWNER)

    assert exc.value.status_code == 404
    _nothing_was_written(db)


# ════════════════════════════════════════════════════════════════════════════
# 2. What is deleted
# ════════════════════════════════════════════════════════════════════════════

async def test_the_identity_and_every_grant_and_credential_are_gone(
    db: _FakeDB,
) -> None:
    """The whole point: after a purge, nothing that could let the platform act
    as this person — or let this person in — is left in the database."""
    from gateway.routes.admin.members import purge_member

    await purge_member(PRIYA, admin=OWNER)

    # the identity
    assert "u-priya" not in db.users
    assert db.user_by_email(PRIYA) is None
    # access grants
    assert "u-priya" not in db.user_roles
    assert db.rows["user_permission_override"] == []
    assert db.rows["org_group_member"] == []
    assert db.rows["app_grants"] == []
    assert db.rows["chat_session_participant"] == []
    # a standing "always allow this app to act for me"
    assert db.rows["app_tool_grants"] == []
    # credentials — the live OAuth/API tokens
    assert db.rows["email_accounts"] == []
    assert db.rows["wa_accounts"] == []
    assert db.rows["task_accounts"] == []
    # the knock: they are a stranger again, so a future sign-in is visible in
    # the Requests tab instead of bumping a decided row nobody renders
    assert db.requests == {}


async def test_their_private_sessions_go_but_a_shared_room_does_not(
    db: _FakeDB,
) -> None:
    """The line the rejected "purge their content too" option would cross.

    A `people`/`org` room has OTHER participants and cascades `chat_message`,
    so deleting one person's account would silently take a shared transcript
    with it. Their participant row goes; the room stays.

    ⚠️ Mutation-checked: dropping ``AND visibility = 'private'`` from the
    delete clause makes this fail on the surviving room.
    """
    from gateway.routes.admin.members import purge_member

    out = await purge_member(PRIYA, admin=OWNER)

    surviving = [r["id"] for r in db.rows["chat_session"]]
    assert surviving == ["s-room"], (
        "a shared room was destroyed along with the private sessions"
    )
    assert out.deleted["private_chat_sessions"] == 2
    assert out.kept["shared_rooms"] == 1


async def test_the_address_is_matched_case_insensitively_when_deleting(
    db: _FakeDB,
) -> None:
    """The rows are keyed by an address the IdP may have cased differently
    from the one on the roster row.

    ⚠️ Mutation-checked: removing ``lower(...)`` from the clauses makes this
    fail — the credentials survive the purge, which is the one outcome that
    must never happen quietly.
    """
    from gateway.routes.admin.members import purge_member

    db.rows["email_accounts"] = [{"user_id": "Priya@Fracktal.IN"}]
    db.users["u-priya"]["email"] = "PRIYA@FRACKTAL.IN"

    out = await purge_member("priya@fracktal.in", admin=OWNER)

    assert out.deleted["email_accounts"] == 1
    assert db.rows["email_accounts"] == []


# ════════════════════════════════════════════════════════════════════════════
# 3. What is kept — and the report
# ════════════════════════════════════════════════════════════════════════════

async def test_the_audit_trail_survives_the_person(db: _FakeDB) -> None:
    """An audit trail that disappears when you delete the person is not an
    audit trail.

    ⚠️ Mutation-checked: moving `app_audit` from ``_PURGE_KEEPS`` to
    ``_PURGE_DELETES`` makes this fail on both halves — the rows are gone AND
    the response stops claiming they were kept.
    """
    from gateway.routes.admin.members import purge_member

    out = await purge_member(PRIYA, admin=OWNER)

    assert len(db.rows["app_audit"]) == 47
    assert len(db.rows["audit_event"]) == 2
    assert out.kept["audit_entries"] == 47
    assert out.kept["audit_events"] == 2
    assert "audit_entries" not in out.deleted
    assert "audit_events" not in out.deleted


async def test_what_they_authored_is_left_readable(db: _FakeDB) -> None:
    """"Purge the person, keep their work." Their apps, workflows, tasks,
    meetings and run traces are content, not access."""
    from gateway.routes.admin.members import purge_member

    out = await purge_member(PRIYA, admin=OWNER)

    for table, count in (("apps", 1), ("workflows", 1), ("gtd_items", 3),
                         ("gtd_projects", 1), ("meeting", 1), ("agent_run", 1)):
        assert len(db.rows[table]) == count, f"{table} was purged"
    assert out.kept["apps"] == 1
    assert out.kept["workflows"] == 1
    assert out.kept["tasks"] == 3
    assert out.kept["projects"] == 1
    assert out.kept["meetings"] == 1
    assert out.kept["agent_runs"] == 1


async def test_the_synced_half_of_the_gtd_store_is_reported_as_destroyed(
    db: _FakeDB,
) -> None:
    """The count that was a lie in the reassuring direction.

    `task_accounts` cascades BOTH `gtd_items` and `gtd_projects` (48_task_
    manager_gtd.sql:73,93). The first version of this route counted
    `gtd_items` on the KEEP side with no exclusion, so a member with 847
    synced tasks got `kept: {"tasks": 847}` while all 847 went with the
    credential — the response did not merely miss a destruction, it reported
    it as a survival. `gtd_projects` appeared on neither list.

    ⚠️ Mutation-checked: dropping ``AND account_id IS NULL`` from the KEEP
    clause makes this fail (5 tasks reported kept, 3 actually there); removing
    either delete-side row-spec makes it fail on the missing key.
    """
    from gateway.routes.admin.members import purge_member

    out = await purge_member(PRIYA, admin=OWNER)

    # Destroyed, and said so.
    assert out.deleted["synced_tasks"] == 2
    assert out.deleted["synced_projects"] == 1
    # Kept, and only the rows that really are.
    assert out.kept["tasks"] == 3
    assert out.kept["projects"] == 1
    assert sorted(r["id"] for r in db.rows["gtd_items"]) == ["i-1", "i-2", "i-3"]
    assert [r["id"] for r in db.rows["gtd_projects"]] == ["p-1"]
    # And the two halves add up to what she had, so neither is double-counted.
    assert out.deleted["synced_tasks"] + out.kept["tasks"] == 5
    assert out.deleted["synced_projects"] + out.kept["projects"] == 2


async def test_the_response_says_what_happened_table_by_table(
    db: _FakeDB,
) -> None:
    """A purge that answers ``{"status": "ok"}`` is unauditable.

    The counts are taken BEFORE anything is deleted; a route that counted
    afterwards would report zeros for everything it had just destroyed, and
    this asserts the real numbers rather than the keys being present.
    """
    from gateway.routes.admin.members import (
        _PURGE_DELETES,
        _PURGE_KEEPS,
        purge_member,
    )

    out = await purge_member(PRIYA, admin=OWNER)

    assert out.status == "purged"
    assert out.email == PRIYA
    # Every step reports, so a step that stopped running is a 0 rather than a
    # missing key nobody notices.
    assert set(out.deleted) == {r.key for r in _PURGE_DELETES}
    assert set(out.kept) == {r.key for r in _PURGE_KEEPS}
    assert out.deleted == {
        "role_grants": 1,
        "permission_overrides": 1,
        "group_memberships": 2,
        "room_participations": 1,
        "app_grants": 1,
        "app_tool_grants": 1,
        "email_accounts": 1,
        "whatsapp_accounts": 1,
        "synced_tasks": 2,
        "synced_projects": 1,
        "task_accounts": 1,
        "private_chat_sessions": 2,
        "sign_in_requests": 1,
        "member_record": 1,
    }
    assert out.kept == {
        "audit_entries": 47,
        "audit_events": 2,
        "agent_runs": 1,
        "shared_rooms": 1,
        "apps": 1,
        "workflows": 1,
        "tasks": 3,
        "projects": 1,
        "meetings": 1,
    }


async def test_purging_somebody_with_nothing_reports_zeros_not_silence(
    db: _FakeDB,
) -> None:
    """The member record is the one count that is always 1. Everything else
    may legitimately be 0, and the admin should be able to see the difference
    between "nothing to delete" and "the step did not run"."""
    from gateway.routes.admin.members import purge_member

    db.rows.clear()
    db.requests.clear()

    out = await purge_member(PRIYA, admin=OWNER)

    assert out.deleted["member_record"] == 1
    assert out.deleted["email_accounts"] == 0
    assert out.deleted["private_chat_sessions"] == 0
    assert out.kept["audit_entries"] == 0


# ════════════════════════════════════════════════════════════════════════════
# 4. The fence — asserted against the constants, not against the fake
#
# `_FakeDB` answers from rows a test seeded, so §2 and §3 prove the route ran
# the statements it holds and bound them correctly. They cannot prove those
# statements name the right tables, and a mirror can only agree with itself.
# Everything decided *in* the SQL is pinned here.
# ════════════════════════════════════════════════════════════════════════════

def test_no_audit_table_appears_on_the_delete_side_at_all() -> None:
    """The invariant behind "audit history must be kept", stated over the
    whole delete list rather than over the two tables a test remembered.

    A behavioural case only sees the tables it seeded; this sees every table
    the purge will ever touch, so an audit table added to `_PURGE_DELETES`
    later — or a new audit table added to the schema and wired to the wrong
    list — fails here.
    """
    from gateway.routes.admin.members import _PURGE_DELETES, _PURGE_KEEPS

    #: Every table in the schema whose purpose is to record what happened.
    AUDIT_TABLES = {"app_audit", "audit_event", "agent_run",
                    "agent_file_history", "pending_actions", "pending_commit"}

    deleted_tables = {r.table for r in _PURGE_DELETES}
    assert not (deleted_tables & AUDIT_TABLES), (
        f"the purge deletes audit history: "
        f"{sorted(deleted_tables & AUDIT_TABLES)}"
    )
    kept_tables = {r.table for r in _PURGE_KEEPS}
    assert {"app_audit", "audit_event"} <= kept_tables, (
        "the two audit tables are not even reported as kept, so an admin "
        "cannot tell they survived"
    )
    # And no table is on both sides except the three that are legitimately
    # split — each by a column the schema forces on us, each asserted to be an
    # exact complement below.
    assert not (deleted_tables & kept_tables) - {
        "chat_session", "gtd_items", "gtd_projects",
    }, (
        "a table is both deleted and kept; only chat_session (by visibility) "
        "and the GTD store (by account_id, because task_accounts cascades the "
        "SYNCED half) are legitimately split"
    )


def test_the_counts_and_the_deletes_are_the_same_predicate() -> None:
    """Why the numbers in the response can be trusted.

    Both statements are derived from one ``where`` clause, so the count and
    the delete cannot describe different sets. Written as two hand-maintained
    strings they could — and the drift would be invisible, because the count
    is the only evidence the admin ever sees.
    """
    from gateway.routes.admin.members import _PURGE_DELETES, _PURGE_KEEPS

    for rows in (*_PURGE_DELETES, *_PURGE_KEEPS):
        assert rows.count_sql == (
            f"SELECT count(*) FROM {rows.table} WHERE {rows.where}"
        )
        assert rows.delete_sql == f"DELETE FROM {rows.table} WHERE {rows.where}"
        assert rows.count_sql.endswith(rows.where)
        assert rows.delete_sql.endswith(rows.where)


def test_every_clause_is_scoped_to_one_person() -> None:
    """A purge clause that binds no person-parameter would empty a table.

    Each `where` must name at least one of the three bindings, and each must
    compare an address case-insensitively — an address clause without
    ``lower()`` is the mutation that leaves credentials behind (or, on the
    delete side of a differently-cased row, leaves them behind silently).
    """
    from gateway.routes.admin.members import _PURGE_DELETES, _PURGE_KEEPS

    for rows in (*_PURGE_DELETES, *_PURGE_KEEPS):
        bound = [p for p in (":uid", ":email", ":actor") if p in rows.where]
        assert bound, f"{rows.table}: clause {rows.where!r} is unscoped"
        for param in (":email", ":actor"):
            if param in rows.where:
                assert re.search(rf"lower\(\w+\) = {param}", rows.where), (
                    f"{rows.table}: {param} is compared without lower(), so a "
                    "differently-cased address slips through"
                )


def test_the_member_record_is_deleted_last() -> None:
    """Order is the readable form of the dependency graph.

    Not load-bearing for correctness — the FKs cascade and it is one
    transaction — but a list that deletes `app_user` in the middle reads as
    though the rows after it were an afterthought, which is how one gets
    forgotten.
    """
    from gateway.routes.admin.members import _PURGE_DELETES

    assert _PURGE_DELETES[-1].table == "app_user"
    assert _PURGE_DELETES[-1].key == "member_record"
    assert [r.table for r in _PURGE_DELETES].count("app_user") == 1


def test_the_two_halves_of_chat_session_partition_it() -> None:
    """The one table split by `visibility`. The two clauses must be
    complements — a gap would leave sessions belonging to nobody, an overlap
    would report a room as both destroyed and kept."""
    from gateway.routes.admin.members import _PURGE_DELETES, _PURGE_KEEPS

    deleted = next(r for r in _PURGE_DELETES if r.table == "chat_session")
    kept = next(r for r in _PURGE_KEEPS if r.table == "chat_session")

    assert deleted.where == "lower(user_id) = :email AND visibility = 'private'"
    assert kept.where == "lower(user_id) = :email AND visibility <> 'private'"


def test_the_two_halves_of_the_gtd_store_partition_it() -> None:
    """The other two split tables, and the ones the FK forces apart.

    `account_id IS NULL` is a LOCAL row the person authored here;
    `IS NOT NULL` is a mirror of a provider task that `task_accounts`
    cascades. The split is not a preference — the schema decides it — so the
    clauses are pinned literally rather than left to the complement check
    below to approve in the abstract.
    """
    from gateway.routes.admin.members import _PURGE_DELETES, _PURGE_KEEPS

    for table in ("gtd_items", "gtd_projects"):
        deleted = next(r for r in _PURGE_DELETES if r.table == table)
        kept = next(r for r in _PURGE_KEEPS if r.table == table)
        assert deleted.where == (
            "lower(user_id) = :email AND account_id IS NOT NULL"
        ), table
        assert kept.where == "lower(user_id) = :email AND account_id IS NULL", (
            table
        )


# ── The cross-table half: what the delete side cascades away ────────────────
#
# Everything above compares a row-spec to itself or to its own table's other
# half. The defect that shipped was neither: `_PURGE_KEEPS` counted `gtd_items`
# with no exclusion while `_PURGE_DELETES` took `task_accounts`, whose FK
# cascades that very table — so the count and the delete were "the same
# predicate" (they were) and the report was still wrong, because a THIRD
# statement destroyed the rows. `_FakeDB` models no foreign keys, so no
# behavioural case in this file could have seen it. These two derive the
# cascade graph from `infra/postgres/` and check the clauses against it.

#: Predicate pairs that partition a table. Order-insensitive.
_COMPLEMENTARY = (("= 'private'", "<> 'private'"), ("IS NULL", "IS NOT NULL"))


def _conjuncts(where: str) -> list[str]:
    return [c.strip() for c in where.split(" AND ")]


def _are_complements(a: str, b: str) -> bool:
    """Do `a` and `b` cover the same rows, split on exactly one predicate?"""
    ca, cb = _conjuncts(a), _conjuncts(b)
    if len(ca) != len(cb):
        return False
    differing = [(x, y) for x, y in zip(ca, cb, strict=True) if x != y]
    if len(differing) != 1:
        return False
    x, y = differing[0]
    for left, right in _COMPLEMENTARY:
        for p, q in ((left, right), (right, left)):
            if (x.endswith(f" {p}") and y.endswith(f" {q}")
                    and x[: -len(p)].strip() == y[: -len(q)].strip()):
                return True
    return False


def _blast_radius() -> set[str]:
    """Every table Postgres empties of the purged person's rows, transitively."""
    from gateway.routes.admin.members import _PURGE_DELETES

    from tests.unit._schema_cascade import cascade_closure

    radius: set[str] = set()
    for rows in _PURGE_DELETES:
        radius |= cascade_closure(rows.table)
    return radius


def test_no_keep_clause_survives_a_cascade_on_the_delete_side() -> None:
    """**"Kept" is a claim about rows that are still there when it commits.**

    Not a claim about rows this list did not name. A table three entries up
    can empty it, and then the response reports a destruction as a survival —
    the worst direction for the number to be wrong in, because `kept` is the
    reassurance that makes the irreversible half clickable.

    ⚠️ Mutation-checked: dropping `AND account_id IS NULL` from the `tasks`
    KEEP clause fails here, naming `gtd_items` and the cascade parent.
    """
    from gateway.routes.admin.members import _PURGE_DELETES, _PURGE_KEEPS

    radius = _blast_radius()
    deletes = {r.table: r for r in _PURGE_DELETES}

    for kept in _PURGE_KEEPS:
        if kept.table not in radius:
            continue
        assert kept.table in deletes, (
            f"{kept.table} is reported as KEPT, but the purge cascades it away "
            f"and no delete-side row-spec counts what goes. Either exclude the "
            f"destroyed rows from the keep clause and count them on the delete "
            f"side, or stop claiming they survive."
        )
        assert _are_complements(deletes[kept.table].where, kept.where), (
            f"{kept.table}: the kept clause {kept.where!r} is not the exact "
            f"complement of the deleted one {deletes[kept.table].where!r}, so "
            f"the two counts overlap or leave a gap — and this table is inside "
            f"the cascade blast radius, where an overlap means rows counted as "
            f"kept are destroyed anyway."
        )


def test_every_person_keyed_cascade_child_is_reported_on_one_side_or_other(
) -> None:
    """The rule that forces `gtd_projects` onto the list at all.

    Most of the blast radius is legitimately summarised rather than counted:
    the 20 tables under `email_accounts` and the 16 under `wa_accounts` are
    keyed by `account_id` alone — they are mirror-of-a-credential and nothing
    else, and "the whole mailbox goes" is the honest report.

    The exceptions are the cascade children that carry **their own person
    column**. Those are dual-source: the same table holds rows the person
    authored directly, which the purge is not entitled to take, so the split
    has to be stated and both halves have to be counted. Today that is exactly
    `gtd_items` and `gtd_projects` (plus the three access-grant tables under
    `app_user`, which are wholly deleted and already listed).

    ⚠️ Mutation-checked, and the wording matters: this fires on a table that
    is on **neither** list — removing both `gtd_projects` row-specs, which is
    the state that shipped. Removing only the delete-side one is caught by
    `test_no_keep_clause_survives_a_cascade_on_the_delete_side` instead, and
    the two together are what make a dual-source table impossible to
    half-report. A migration that hangs another dual-source table off a
    credential fails here — which is the point, since nobody will notice it
    by reading.
    """
    from gateway.routes.admin.members import _PURGE_DELETES, _PURGE_KEEPS

    from tests.unit._schema_cascade import columns_of

    listed = {r.table for r in (*_PURGE_DELETES, *_PURGE_KEEPS)}
    person_columns = {"user_id", "owner_email", "user_email"}

    unreported = sorted(
        table for table in _blast_radius()
        if (columns_of(table) & person_columns) and table not in listed
    )
    assert not unreported, (
        f"these tables are destroyed by the purge's cascades and carry their "
        f"own person column, so they hold rows the person authored — and they "
        f"appear on neither list: {unreported}"
    )


def test_the_cascade_map_is_the_one_the_schema_declares() -> None:
    """`_CREDENTIAL_CASCADES` is hand-maintained, so it is pinned.

    The first version of that comment named 15 of the 20 tables under
    `email_accounts` (missing `email_embeddings`, `email_executed_rules`,
    `email_ai_drafts`, `email_rule_guidance`), listed `wa_media` as a direct
    child of `wa_accounts` when it hangs off `wa_messages`, and omitted
    `wa_chat_status` and `wa_sync_log`. On a route whose safety argument is
    "the admin is told the blast radius before clicking", understating it is
    the wrong direction of error — so the map is compared against the
    migrations rather than reviewed.

    This failing means a migration widened a cascade. Update the map AND the
    Members-page confirmation copy that summarises it.
    """
    from gateway.routes.admin.members import _CREDENTIAL_CASCADES

    from tests.unit._schema_cascade import cascade_closure

    for parent, listed in _CREDENTIAL_CASCADES.items():
        assert set(listed) == set(cascade_closure(parent)), (
            f"{parent}: the map says {sorted(listed)}, the migrations say "
            f"{sorted(cascade_closure(parent))}"
        )
        assert list(listed) == sorted(listed), f"{parent}: keep it sorted"

    # And every credential the purge deletes has an entry, so a fourth one
    # cannot be added with no blast radius recorded at all.
    from gateway.routes.admin.members import _PURGE_DELETES

    credentials = {
        r.table for r in _PURGE_DELETES if r.table.endswith("_accounts")
    }
    assert credentials == set(_CREDENTIAL_CASCADES), (
        f"credential tables on the delete list: {sorted(credentials)}; "
        f"tables with a recorded cascade: {sorted(_CREDENTIAL_CASCADES)}"
    )


# ── The gate on the door itself ─────────────────────────────────────────────

def test_the_purge_route_is_gated_on_admin_members_manage() -> None:
    """The permission is asserted; it was previously only assumed.

    Deleting ``dependencies=[require_permission("admin:members:manage")]`` from
    this route — leaving ``Depends(require_admin_user)`` in place — left **162
    tests green**. The remaining floor is `admin:members:read`, which a seeded
    `manager` holds (D14 says so, and `_admin_fakes.ROLE_PERMISSIONS` seeds
    it), so that mutation silently hands hard-delete of any member to every
    manager. The nearest existing wiring test filters on
    ``path.startswith("/admin/members/requests")`` and cannot see this route.

    The exact slug is read out of ``require_permission``'s closure rather than
    matched by name, so widening it to a weaker permission fails too.
    """
    from gateway.routes.admin._common import require_admin_user, router

    route = next(
        r for r in router.routes
        if getattr(r, "path", "") == "/admin/members/{email}/purge"
    )
    assert sorted(route.methods) == ["DELETE"]

    granted: list[tuple[str, ...]] = []
    for dep in getattr(route, "dependencies", []):
        fn = dep.dependency
        free = getattr(fn, "__code__", None)
        if free is None or "required" not in free.co_freevars:
            continue
        cell = fn.__closure__[free.co_freevars.index("required")]
        granted.append(tuple(cell.cell_contents))

    assert granted == [("admin:members:manage",)], (
        "the most destructive route in this package does not require "
        f"admin:members:manage (found {granted!r}). Without it the floor is "
        "admin:members:read, which a manager holds."
    )
    # And the package floor, which is per-route and not a router property.
    assert any(
        getattr(p.default, "dependency", None) is require_admin_user
        for p in inspect.signature(route.endpoint).parameters.values()
    ), "the purge route declares no require_admin_user floor"


def test_the_purge_outcome_name_is_not_a_member_status() -> None:
    """``PURGE_OUTCOME`` is what the route passes to the shared self-guard.

    It is deliberately not one of ``VALID_STATUSES``: a purge deletes the row
    rather than writing the column. The guard's rule — "anything that is not
    `active`" — is what makes a delete-shaped door fall under a sentence
    written for update-shaped ones, and it is worded because otherwise the
    caller would be told they cannot set their membership to 'purged', which
    is not what they asked to do.
    """
    from gateway.routes.admin._common import (
        _SELF_LOCKOUT_WORDING,
        PURGE_OUTCOME,
    )
    from gateway.routes.admin.members import VALID_STATUSES

    assert PURGE_OUTCOME not in VALID_STATUSES
    assert PURGE_OUTCOME != "active"
    assert PURGE_OUTCOME in _SELF_LOCKOUT_WORDING
    assert "delete" in _SELF_LOCKOUT_WORDING[PURGE_OUTCOME].lower()


# ════════════════════════════════════════════════════════════════════════════
# 5. One transaction, and an audit entry that cannot be lost with the data
# ════════════════════════════════════════════════════════════════════════════

async def test_the_whole_purge_is_one_transaction(db: _FakeDB) -> None:
    """A half-purge that deleted the credentials but left the account active —
    or deleted the account and left an OAuth token — is worse than either
    outcome.

    ⚠️ Mutation-checked: adding a ``commit()`` inside the delete loop makes
    this fail with 12 commits instead of 1.
    """
    from gateway.routes.admin.members import purge_member

    await purge_member(PRIYA, admin=OWNER)

    assert db.committed == 1


def test_the_route_holds_exactly_one_commit() -> None:
    """The structural half of the test above.

    Since H2 the one commit is ``_tenant_session``'s, issued on clean exit of
    the ``async with`` block — so the structural facts to pin are that the
    route opens exactly ONE tenant-bound block, writes no commit of its own
    (a hand-rolled ``db.commit()`` inside the block would END the transaction
    early and drop the tenant GUC for everything after it), and keeps every
    destructive statement INSIDE the block, before that commit.
    """
    from gateway.routes.admin.members import purge_member

    src = inspect.getsource(purge_member)
    assert src.count("async with _tenant_session() as db:") == 1
    assert src.count("await db.commit()") == 0, (
        "a mid-block commit would end the transaction (and the tenant "
        "binding) before the wrapper's own commit"
    )
    block_indent = " " * 8
    after_block = src[src.index("async with _tenant_session() as db:"):]
    for line in after_block.splitlines()[1:]:
        if "rows.delete_sql" in line:
            assert line.startswith(block_indent), (
                "something is deleted outside the one transaction"
            )


async def test_the_purge_is_audited_before_it_is_committed(
    db: _FakeDB,
) -> None:
    """``acb_audit.record`` opens its own session, so an entry written before
    the commit survives a rollback of the purge itself.

    That trade-off is deliberate and this way round: an audit line for a purge
    that then failed is a false positive an admin can reconcile against a
    roster that still shows the person; a completed purge with no audit line
    is unreconcilable, because every row that could say who it was is gone.

    ⚠️ Mutation-checked: moving `record_admin_change` after `db.commit()` — the
    order every other write in this package uses — makes this fail, because
    the audit list is empty at commit time.
    """
    from gateway.routes.admin.members import purge_member

    await purge_member(PRIYA, admin=OWNER)

    assert db.audit_at_commit == [1], (
        "the audit entry was not written before the commit"
    )
    assert db.audit == [("org.member_purged", f"user:{PRIYA}")]


async def test_the_audit_entry_carries_the_counts(db: _FakeDB) -> None:
    """"Somebody was purged" is not a record of a purge. What was destroyed
    and what survived is the only thing anybody can act on afterwards."""
    from gateway.routes.admin.members import purge_member

    out = await purge_member(PRIYA, admin=OWNER)

    assert len(db.audit_payloads) == 1
    payload = db.audit_payloads[0]
    assert payload["deleted"] == out.deleted
    assert payload["kept"] == out.kept


async def test_the_cached_access_is_dropped(db: _FakeDB) -> None:
    """Otherwise a purged person keeps resolving for up to the resolver's 60s
    TTL — against a member row that no longer exists."""
    from gateway.routes.admin.members import purge_member

    await purge_member(PRIYA, admin=OWNER)

    assert PRIYA in db.invalidated


def test_purge_is_a_separate_route_and_remove_is_unchanged() -> None:
    """N8 adds a door; it does not widen the existing one.

    Remove's docstring reasoning — the row is kept because the rest of the
    schema refers to people by address — stays true of Remove, and a `?hard=`
    flag on it would have made the soft path one typo from the hard one.
    """
    from gateway.routes.admin._common import router
    from gateway.routes.admin.members import remove_member

    paths = {
        (route.path, tuple(sorted(route.methods)))
        for route in router.routes
        if getattr(route, "path", "").startswith("/admin/members/{email}")
    }
    assert ("/admin/members/{email}", ("DELETE",)) in paths
    assert ("/admin/members/{email}/purge", ("DELETE",)) in paths

    src = inspect.getsource(remove_member)
    assert "UPDATE app_user SET status = 'removed'" in src
    assert "DELETE FROM app_user" not in src
    assert "_PURGE_DELETES" not in src


# ════════════════════════════════════════════════════════════════════════════
# 6. The Members page
#
# There is no jsdom/component-test harness in this repo (only `*.test.ts` over
# pure modules), so the page is fenced two ways: the RULE lives in
# `settings/members/selfGuard.ts` and is unit-tested by vitest, and the WIRING
# is asserted here against the source. Every assertion is positional or scoped
# to the block it is about — a bare `"something" in page` is satisfied by a
# declaration, which is a mistake this workstream has now shipped four times.
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
    body = _page().split("function MemberRow(", 1)
    assert len(body) == 2, "the roster row is no longer its own component"
    return body[1].split("\nfunction ", 1)[0]


def test_delete_permanently_is_not_offered_on_the_viewers_own_row() -> None:
    """Reuses `rowActions`, like Suspend and Remove — the gateway refuses
    anyway, and a button whose only outcome is a 409 is worse than none.

    Asserted on the JSX condition, not on position: `void actions.canPurge;`
    above a `{true && (` satisfies `index(flag) < index(call)` while the
    control renders on the viewer's own row.
    """
    row = _member_row()

    assert "actions.canPurge" in row, "the row never asks the guard about purge"
    assert "{actions.canPurge && (" in row, (
        "Delete permanently is not rendered behind `{actions.canPurge && (` — "
        "merely mentioning the flag first does not gate anything"
    )
    assert "onPurge(member)" in row
    assert row.count("onPurge(member)") == 1, (
        "a second, ungated call site further down the row"
    )
    # The row opens the dialog; it never issues the DELETE itself.
    assert "fetch(" not in row


def test_the_confirmation_names_what_goes_and_what_stays() -> None:
    """A confirmation that says "are you sure?" is not informed consent for an
    irreversible act. It must name both halves — including that the audit
    trail survives, which is the reassurance that makes the rest clickable."""
    page = _page()

    assert "function PurgeDialog(" in page, "there is no confirmation step"
    dialog = page.split("function PurgeDialog(", 1)[1].split("\nfunction ", 1)[0]

    assert "member.display_name || member.email" in dialog, (
        "the confirmation does not name the person it is about"
    )
    for phrase in ("Deleted", "Kept", "audit"):
        assert phrase in dialog, f"the confirmation never mentions {phrase!r}"
    # The two categories that must be named because they are irreversible and
    # bigger than the admin expects.
    assert "mailbox" in dialog.lower()
    assert "role" in dialog.lower()
    # And it points at the reversible action for anybody who wanted that.
    assert "Remove" in dialog


def test_the_typed_confirmation_is_a_rule_and_not_a_paragraph() -> None:
    """Done-when 6 says the dialog "requires the address to be typed".

    The first version tested that by grepping the dialog for the word "Type",
    which is a claim about copy. Replacing the comparison itself with
    ``const confirmed = true;`` left **32 pytest and 173 vitest cases green** —
    the last barrier in front of the one irreversible action on the screen,
    with nothing behind it.

    The rule now lives in `confirmPurge.ts` (unit-tested by vitest, including
    that an empty address confirms nothing) and this asserts the WIRING: the
    dialog calls it, passes the member's own address, and holds no second
    opinion of its own.
    """
    page = _page()
    dialog = page.split("function PurgeDialog(", 1)[1].split("\nfunction ", 1)[0]

    # `./lib/confirmPurge` since D49 moved the roster into Organisation —
    # asserted as the import PATH the page actually carries, not a substring
    # that would also match a comment mentioning the module.
    assert 'from "./lib/confirmPurge"' in page, (
        "the dialog does not import the rule; a comparison written inline here "
        "is fenced by nothing but a grep for the copy around it"
    )
    assert "const confirmed = purgeConfirmed(typed, member.email);" in dialog, (
        "the confirm flag is not the rule's answer about THIS member"
    )
    # The button is disabled by it, and the input feeds it.
    assert "disabled={busy || !confirmed}" in dialog
    assert "onChange={(e) => setTyped(e.target.value)}" in dialog
    # No second copy of the comparison anywhere on the page.
    assert "member.email.toLowerCase()" not in page, (
        "an inline address comparison is back on the page"
    )


def test_the_purge_call_reads_the_counts_back_and_surfaces_refusals() -> None:
    """The gateway returns counts; throwing them away would make the response
    unauditable at exactly the place a human is looking.

    And the N6a rule: no early return on a refusal — the row on screen is the
    stale thing, so `load()` runs on every response.
    """
    page = _page()
    body = page.split("const purgeMember = async (", 1)
    assert len(body) == 2, "purgeMember is missing"
    body = body[1].split("\n  };", 1)[0]

    assert "/purge" in body, "the hard-delete route is not the one being called"
    assert 'method: "DELETE"' in body
    assert "setError(body.detail" in body, (
        "the gateway's refusal is swallowed, so a 409 looks like nothing "
        "happening"
    )
    assert "setNotice(" in body, "the counts never reach the screen"
    assert "await load();" in body
    assert "return;" not in body, (
        "purgeMember returns early on a refusal, leaving the stale row on "
        "screen"
    )


def test_the_browsers_copy_of_the_purge_rule_is_still_only_a_courtesy() -> None:
    """`canPurge` is presentation. The boundary is
    `_common.assert_not_self_lockout`, and the failure mode of a client-side
    mirror is that somebody later "simplifies" the server by trusting it."""
    guard = (ORG_DIR / "lib" / "selfGuard.ts").read_text(encoding="utf-8")

    assert "canPurge" in guard
    assert "not a boundary" in guard or "courtesy" in guard
