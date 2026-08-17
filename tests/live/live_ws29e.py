"""WS-29e (S1-1) against a REAL Postgres. Two organizations, two admins, the
real admin route functions.

What only a database can answer:

* does `organization_id = CAST(:org AS uuid)` bind a Python `str` on `app_user`
  without asyncpg complaining about the inferred type?
* does `ON CONFLICT (email) DO UPDATE … WHERE` actually SKIP the arm — Postgres
  is the only thing that can say whether the row was left alone or silently
  rewritten, because the statement reports success either way?
* is `app_user_email_key` really global, i.e. does inviting another tenant's
  address really CONFLICT rather than insert a second row?
* and the whole point: can admin B see, invite into, or grant a role in
  organization A through the real route functions?
"""
import asyncio
import os
import sys

os.environ["DATABASE_URL"] = (
    "postgresql+asyncpg://postgres@/cc?host=/var/tmp&port=55432"
)
sys.path.insert(0, "/home/user/Metorite/apps/services/gateway")

from acb_auth import UserContext, UserRole, build_access  # noqa: E402
from fastapi import HTTPException  # noqa: E402
from gateway.db import get_db  # noqa: E402
from gateway.routes.admin import _common  # noqa: E402
from gateway.routes.admin import access_requests as ar  # noqa: E402
from gateway.routes.admin import groups as gr  # noqa: E402
from gateway.routes.admin import me as me_mod  # noqa: E402
from gateway.routes.admin import members as mb  # noqa: E402
from gateway.routes.admin import roles as rl  # noqa: E402
from sqlalchemy import text  # noqa: E402

ANA = "ana@alpha.example"        # admin of alpha
PRIYA = "priya@alpha.example"    # member of alpha
BEN = "ben@beta.example"         # admin of beta
BOB = "bob@beta.example"         # member of beta
ORPHAN = "orphan@nowhere.example"  # a row with NO organization_id (pre-130)
STRANGER = "nobody@nowhere.example"  # no app_user row at all

ADMIN_PERMS = [
    "admin:members:read", "admin:members:invite",
    "admin:members:manage", "admin:access:manage",
]

failures: list[str] = []


def check(label, got, want):
    ok = got == want
    print(f"{'ok  ' if ok else 'FAIL'} {label}: got {got!r}, want {want!r}")
    if not ok:
        failures.append(label)


def admin(email):
    return UserContext(email=email, role=UserRole.EXECUTIVE,
                       access=build_access(ADMIN_PERMS, roles=["admin"]))


async def status_of(call):
    """Run a route and report the HTTP status it produced (200 = accepted)."""
    try:
        await call
    except HTTPException as exc:
        return exc.status_code
    return 200


async def seed():
    db = await get_db()
    try:
        emails = (ANA, PRIYA, BEN, BOB, ORPHAN, STRANGER, "new@beta.example")
        await db.execute(
            text("DELETE FROM app_user WHERE email = ANY(:e)"),
            {"e": list(emails)},
        )
        await db.execute(
            text("DELETE FROM access_request WHERE email = ANY(:e)"),
            {"e": list(emails)},
        )
        await db.execute(
            text("DELETE FROM organization WHERE slug IN ('alpha', 'beta')"))
        orgs = {}
        for slug in ("alpha", "beta"):
            row = (await db.execute(text(
                "INSERT INTO organization (slug, display_name) "
                "VALUES (:s, :s) RETURNING id"), {"s": slug})).fetchone()
            orgs[slug] = str(row.id)
            # Each tenant gets its own role rows — `org_role` is UNIQUE
            # (organization_id, slug), so `owner` is a legal slug in both.
            for role_slug, name, rank in (
                ("owner", "Owner", 0), ("admin", "Admin", 10),
                ("member", "Member", 30),
            ):
                await db.execute(text(
                    "INSERT INTO org_role (organization_id, slug, display_name,"
                    " is_system, rank) VALUES (CAST(:o AS uuid), :s, :n, true, :r)"
                ), {"o": orgs[slug], "s": role_slug, "n": name, "r": rank})
            await db.execute(text(
                "INSERT INTO org_group (organization_id, slug, display_name, "
                "created_by) VALUES (CAST(:o AS uuid), 'people', 'People', 'seed')"
            ), {"o": orgs[slug]})

        for email, slug, role in (
            (ANA, "alpha", "owner"), (PRIYA, "alpha", "member"),
            (BEN, "beta", "owner"), (BOB, "beta", "member"),
        ):
            row = (await db.execute(text(
                "INSERT INTO app_user (email, display_name, role, status, "
                "organization_id) VALUES (:e, :e, 'employee', 'active', "
                "CAST(:o AS uuid)) RETURNING id"),
                {"e": email, "o": orgs[slug]})).fetchone()
            await db.execute(text(
                "INSERT INTO user_role (user_id, role_id) SELECT :u, id "
                "FROM org_role WHERE organization_id = CAST(:o AS uuid) "
                "AND slug = :s"),
                {"u": row.id, "o": orgs[slug], "s": role})

        # ⚠️ A row with NO tenant — what migration 130 left behind. The fence's
        # `IS NULL` arm is the only thing that lets it ever be provisioned.
        await db.execute(text(
            "INSERT INTO app_user (email, display_name, role, status) "
            "VALUES (:e, :e, 'employee', 'invited')"), {"e": ORPHAN})

        # A knock at the door: `access_request` has no tenant column, by design.
        await db.execute(text(
            "INSERT INTO access_request (email, display_name, status) "
            "VALUES (:e, :e, 'pending')"), {"e": PRIYA})
        await db.commit()
        return orgs
    finally:
        await db.close()


async def row_of(email):
    db = await get_db()
    try:
        row = (await db.execute(text(
            "SELECT organization_id::text AS org, status FROM app_user "
            "WHERE lower(email) = :e"), {"e": email})).mappings().first()
        return dict(row) if row else None
    finally:
        await db.close()


async def roles_of(email):
    db = await get_db()
    try:
        rows = (await db.execute(text(
            "SELECT r.slug FROM app_user u JOIN user_role ur ON ur.user_id = u.id"
            " JOIN org_role r ON r.id = ur.role_id WHERE lower(u.email) = :e"
            " ORDER BY r.slug"), {"e": email})).scalars().all()
        return sorted(rows)
    finally:
        await db.close()


async def main():
    orgs = await seed()
    a, b = admin(ANA), admin(BEN)

    # ── 1. The resolver, against real uuid binding ──────────────────────────
    db = await get_db()
    try:
        check("get_org_id(ana) is alpha",
              await _common.get_org_id(db, a), orgs["alpha"])
        check("get_org_id(ben) is beta",
              await _common.get_org_id(db, b), orgs["beta"])
        check("a stranger is refused",
              await status_of(_common.get_org_id(db, admin(STRANGER))), 403)
        check("a caller with a NULL organization_id is refused",
              await status_of(_common.get_org_id(db, admin(ORPHAN))), 403)
    finally:
        await db.close()

    # ── 2. SEE ─────────────────────────────────────────────────────────────
    check("alpha's roster",
          sorted(m.email for m in await mb.list_members(admin=a)),
          [ANA, PRIYA])
    check("beta's roster",
          sorted(m.email for m in await mb.list_members(admin=b)),
          [BEN, BOB])
    check("ben reading alpha's member",
          await status_of(mb.get_member_access(PRIYA, b)), 404)
    check("/auth/me names ana's own org",
          (await me_mod.get_me(user=a))["organization"].get("slug"), "alpha")
    check("/auth/me names ben's own org",
          (await me_mod.get_me(user=b))["organization"].get("slug"), "beta")
    check("alpha's roles are alpha's",
          sorted(r.slug for r in await rl.list_roles(admin=a)),
          ["admin", "member", "owner"])
    check("alpha's groups are alpha's",
          [g.slug for g in await gr.list_groups(admin=a)], ["people"])

    # ── 3. INVITE INTO ─────────────────────────────────────────────────────
    check("ben invites a new address into beta",
          await status_of(mb.invite_member(
              mb.InviteRequest(email="new@beta.example"), admin=b)), 200)
    check("...and it landed in beta",
          (await row_of("new@beta.example"))["org"], orgs["beta"])

    check("ben invites ALPHA's member",
          await status_of(mb.invite_member(
              mb.InviteRequest(email=PRIYA, roles=["member"]), admin=b)), 404)
    check("...priya is still in alpha",
          (await row_of(PRIYA))["org"], orgs["alpha"])
    check("...priya is still active", (await row_of(PRIYA))["status"], "active")
    check("...priya's roles are untouched", await roles_of(PRIYA), ["member"])

    check("ben approves ALPHA's member from the shared queue",
          await status_of(ar.approve_access_request(
              PRIYA, ar.ApproveRequest(roles=["member"]), admin=b)), 404)
    check("...priya is STILL in alpha", (await row_of(PRIYA))["org"], orgs["alpha"])

    check("ben provisions the tenant-less legacy row",
          await status_of(mb.invite_member(
              mb.InviteRequest(email=ORPHAN), admin=b)), 200)
    check("...and it was adopted into beta",
          (await row_of(ORPHAN))["org"], orgs["beta"])

    # ── 4. GRANT A ROLE IN ─────────────────────────────────────────────────
    check("ben grants owner in alpha",
          await status_of(mb.set_member_roles(
              PRIYA, mb.RoleAssignment(roles=["owner"]), admin=b)), 404)
    check("...priya is not an owner", await roles_of(PRIYA), ["member"])

    check("ben writes an override on alpha's member",
          await status_of(mb.set_member_overrides(
              PRIYA, mb.OverrideRequest(overrides=[
                  mb.OverrideEntry(permission="feature:email", effect="deny")]),
              admin=b)), 404)
    check("ben suspends alpha's member",
          await status_of(mb.update_member(
              PRIYA, mb.MemberPatch(status="suspended"), admin=b)), 404)
    check("ben removes alpha's member",
          await status_of(mb.remove_member(PRIYA, admin=b)), 404)
    check("ben purges alpha's member",
          await status_of(mb.purge_member(PRIYA, admin=b)), 404)
    check("...priya survived all four", (await row_of(PRIYA))["status"], "active")

    check("ben adds alpha's member to beta's group",
          await status_of(gr.add_group_member(
              "people", gr.GroupMemberAdd(email=PRIYA), admin=b)), 404)

    # `org_group` is UNIQUE (organization_id, slug), so `people` exists in both.
    check("ben renames HIS people group, not alpha's",
          await status_of(gr.update_group(
              "people", gr.GroupPatch(display_name="Beta People"), admin=b)), 200)
    db = await get_db()
    try:
        rows = (await db.execute(text(
            "SELECT o.slug AS org, g.display_name FROM org_group g "
            "JOIN organization o ON o.id = g.organization_id "
            "WHERE g.slug = 'people' AND o.slug IN ('alpha','beta') "
            "ORDER BY o.slug"))).mappings().all()
        check("...alpha's group is untouched",
              {r["org"]: r["display_name"] for r in rows},
              {"alpha": "People", "beta": "Beta People"})
    finally:
        await db.close()

    # ── 4b. Custom roles: reached by SLUG, which is unique per tenant ───────
    db = await get_db()
    try:
        await db.execute(text(
            "INSERT INTO org_role (organization_id, slug, display_name, rank) "
            "VALUES (CAST(:o AS uuid), 'auditor', 'Auditor', 25)"),
            {"o": orgs["alpha"]})
        await db.commit()
    finally:
        await db.close()
    check("ben edits alpha's custom role by slug",
          await status_of(rl.update_role(
              "auditor", rl.RolePatch(display_name="Pwned"), admin=b)), 404)
    check("ben deletes alpha's custom role by slug",
          await status_of(rl.delete_role("auditor", admin=b)), 404)
    check("ben assigns alpha's role slug in his own org",
          await status_of(mb.set_member_roles(
              BOB, mb.RoleAssignment(roles=["auditor"]), admin=b)), 400)

    # ── 4c. ⚠️ The conflict is BYTE-exact; `find_member` is case-INsensitive ─
    #
    # `app_user_email_key` is UNIQUE (email), not UNIQUE (lower(email)). A row
    # stored with mixed case therefore does NOT conflict with its own lowercase
    # form, and `provision_member` lowercases before the upsert. Only a real
    # unique index can answer whether that is a second row.
    db = await get_db()
    try:
        await db.execute(text("DELETE FROM app_user WHERE lower(email) = :e"),
                         {"e": "casey@alpha.example"})
        await db.execute(text(
            "INSERT INTO app_user (email, display_name, role, status, "
            "organization_id) VALUES ('Casey@Alpha.Example', 'Casey', "
            "'employee', 'active', CAST(:o AS uuid))"), {"o": orgs["alpha"]})
        await db.commit()
    finally:
        await db.close()
    check("ben invites alpha's member spelled in lower case",
          await status_of(mb.invite_member(
              mb.InviteRequest(email="casey@alpha.example"), admin=b)), 404)
    db = await get_db()
    try:
        n = (await db.execute(text(
            "SELECT count(*) FROM app_user WHERE lower(email) = :e"),
            {"e": "casey@alpha.example"})).scalar()
        check("...and there is still exactly ONE Casey", int(n), 1)
        orgs_of_casey = (await db.execute(text(
            "SELECT DISTINCT organization_id::text FROM app_user "
            "WHERE lower(email) = :e"), {"e": "casey@alpha.example"})).scalars().all()
        check("...still in alpha", sorted(orgs_of_casey), [orgs["alpha"]])
    finally:
        await db.close()

    # ── 4d. The sign-in queue: shared by schema, and what that costs ───────
    knocks = [r.email for r in await ar.list_access_requests(admin=b)]
    check("ben SEES alpha's pending knock (access_request has no tenant column)",
          PRIYA in knocks, True)
    check("ben can DENY alpha's knock",
          await status_of(ar.deny_access_request(PRIYA, admin=b)), 200)

    # ── 5. The control: the same admin still runs their own tenant ─────────
    check("ben suspends HIS OWN member",
          await status_of(mb.update_member(
              BOB, mb.MemberPatch(status="suspended"), admin=b)), 200)
    check("...bob is suspended", (await row_of(BOB))["status"], "suspended")
    check("ben grants a role in beta",
          await status_of(mb.set_member_roles(
              BOB, mb.RoleAssignment(roles=["admin"]), admin=b)), 200)
    check("...bob is an admin of beta", await roles_of(BOB), ["admin"])
    check("ben reads HIS OWN member's resolved access",
          (await mb.get_member_access(BOB, b))["email"], BOB)
    # (purging your own member is WS-24's ticket and needs `audit_event`,
    #  which this box's migration set does not have — out of scope here.)

    print()
    print("FAILURES:", failures or "none")
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
