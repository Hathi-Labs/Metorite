"""Groups admin API (Centers Phase B — work_plan.md WS-13).

Spec: project-docs/specs/department_centers.md §3 Phase B;
      groups_sessions_authority.md §1/§6 (org_group is the primitive, the
      admin surface was the flagged gap).

Route-level tests call the async route functions directly with a fake,
in-memory DB session (the ``test_app_grants.py`` convention: no TestClient,
no live Postgres — the DB seam ``groups.get_db`` is monkeypatched on the SUT
submodule so every test stays hermetic and fast). What is locked:

1. The grant-center-access bridge writes the SAME override row the member
   editor uses — permission ``feature:center.<slug>``, effect allow, reason
   ``group membership: <slug>``, ``set_by`` the acting admin — and requires
   ``admin:access:manage`` on top of membership's ``admin:members:manage``.
2. An existing override (including an explicit deny) is never clobbered.
3. Removing membership never touches the override — revocation is an
   explicit admin decision, not a roster side effect.
4. The six Center groups cannot be deleted; non-empty groups cannot either.
5. Every write route actually carries its permission gate (wiring test).
"""
from __future__ import annotations

from types import SimpleNamespace
from typing import Any

import pytest
from acb_auth import UserContext, UserRole, build_access
from fastapi import HTTPException
from gateway.routes.admin import groups
from gateway.routes.admin.groups import (
    CENTER_GROUP_SLUGS,
    GroupCreate,
    GroupMemberAdd,
    GroupPatch,
    add_group_member,
    create_group,
    delete_group,
    list_groups,
    remove_group_member,
    update_group,
)

ORG = "00000000-0000-0000-0000-00000000000a"

FULL_ADMIN = UserContext(
    email="admin@fracktal.in", role=UserRole.EXECUTIVE,
    access=build_access(
        ["admin:members:read", "admin:members:manage", "admin:access:manage"],
        roles=["admin"],
    ),
)
#: Holds membership management but NOT access management — may edit rosters,
#: must not be able to mint feature overrides through the group shortcut.
ROSTER_ADMIN = UserContext(
    email="roster@fracktal.in", role=UserRole.EXECUTIVE,
    access=build_access(
        ["admin:members:read", "admin:members:manage"], roles=["manager"],
    ),
)


# ── Fakes ───────────────────────────────────────────────────────────────────

class _Rows:
    """Result shim covering the access patterns the routes use."""

    def __init__(self, rows: list[dict[str, Any]], rowcount: int = 0):
        self._rows = rows
        self.rowcount = rowcount

    def mappings(self) -> _Rows:
        return self

    def first(self) -> dict[str, Any] | None:
        return self._rows[0] if self._rows else None

    def fetchone(self) -> Any:
        """`resolve_organization_id` reads the tenant by ATTRIBUTE, not by key."""
        if not self._rows:
            return None
        return SimpleNamespace(**self._rows[0])

    def all(self) -> list[dict[str, Any]]:
        return self._rows

    def scalar(self) -> Any:
        if not self._rows:
            return None
        return next(iter(self._rows[0].values()))


class _FakeDB:
    """In-memory org_group / org_group_member / user_permission_override.

    State lives on the instance and each test constructs one fake shared by
    every ``get_db()`` call, so writes persist call-to-call the way a real
    table would. SQL is matched on normalized substrings — the same strings
    the routes render — so a query-shape change fails loudly here.
    """

    def __init__(self) -> None:
        self.users: dict[str, dict[str, Any]] = {}     # id → row
        self.groups: dict[str, dict[str, Any]] = {}    # id → row
        self.members: dict[tuple[str, str], dict[str, Any]] = {}  # (gid, uid)
        self.overrides: dict[tuple[str, str], dict[str, Any]] = {}  # (uid, perm)
        self.committed = 0

    # helpers -----------------------------------------------------------
    def seed_user(self, uid: str, email: str, name: str = "",
                  organization_id: str = ORG) -> None:
        self.users[uid] = {
            "id": uid, "email": email, "display_name": name,
            "avatar_url": "", "status": "active", "legacy_role": "employee",
            "invited_by": "", "invited_at": None, "joined_at": None,
            "last_login_at": None, "last_active_at": None, "created_at": None,
            "organization_id": organization_id,
        }

    def seed_group(self, gid: str, slug: str, name: str | None = None,
                   description: str = "") -> None:
        self.groups[gid] = {
            "id": gid, "slug": slug, "display_name": name or slug.title(),
            "description": description, "created_by": "seed",
        }

    def _group_by_slug(self, slug: str) -> dict[str, Any] | None:
        return next(
            (g for g in self.groups.values() if g["slug"] == slug), None
        )

    # SQLAlchemy-session surface ---------------------------------------
    async def __aenter__(self) -> _FakeDB:
        return self

    async def __aexit__(self, *exc: Any) -> None:
        return None

    async def commit(self) -> None:
        self.committed += 1

    async def execute(  # noqa: C901 — one branch per SQL statement, by design
        self, sql: Any, params: dict | None = None
    ) -> _Rows:
        s = " ".join(str(sql).split())
        p = params or {}

        # The caller's tenant (WS-29e / S1-1). There is no `FROM organization
        # WHERE slug` branch any more: `get_org_id` no longer asks that
        # question, and a fake that kept answering it would keep agreeing that
        # the deployment's organization and the caller's are the same row.
        if "FROM app_user au" in s and "organization_id" in s:
            row = next(
                (u for u in self.users.values()
                 if u["email"].lower() == p["email"]
                 and u["status"] == "active"), None,
            )
            return _Rows([{"organization_id": row["organization_id"]}]
                         if row and row["organization_id"] else [])

        if "FROM organization LIMIT 1" in s:
            return _Rows([{"one": 1}])

        if "FROM app_user WHERE lower(email)" in s:
            row = next(
                (u for u in self.users.values()
                 if u["email"].lower() == p["email"]), None,
            )
            # `find_member`'s tenant predicate, read from the statement so
            # dropping it changes the answer rather than being shrugged at.
            if (
                row is not None
                and "organization_id = CAST(:org AS uuid)" in s
                and row["organization_id"] != p.get("org")
            ):
                row = None
            return _Rows([row] if row else [])

        if "SELECT 1 FROM org_group " in s:
            g = self._group_by_slug(p["slug"])
            return _Rows([{"one": 1}] if g else [])

        if s.startswith("SELECT id::text AS id, slug") and "FROM org_group" in s:
            g = self._group_by_slug(p["slug"])
            return _Rows([dict(g)] if g else [])

        if s.startswith("SELECT g.id::text") and "FROM org_group g" in s:
            rows = sorted(self.groups.values(), key=lambda g: g["slug"])
            return _Rows([dict(g) for g in rows])

        if "FROM org_group_member gm JOIN app_user u" in s:
            out = []
            for (gid, uid), m in sorted(self.members.items()):
                if gid != p["gid"]:
                    continue
                u = self.users[uid]
                out.append({
                    "email": u["email"], "display_name": u["display_name"],
                    "role": m["role"], "added_by": m["added_by"],
                    "added_at": None,
                })
            return _Rows(sorted(out, key=lambda r: (r["role"], r["email"])))

        if "SELECT count(*) FROM org_group_member" in s:
            n = sum(1 for (gid, _uid) in self.members if gid == p["gid"])
            return _Rows([{"count": n}])

        if "INSERT INTO org_group_member" in s:
            key = (p["gid"], p["uid"])
            existing = self.members.get(key)
            if existing is None:
                self.members[key] = {"role": p["role"], "added_by": p["by"]}
            else:
                existing["role"] = p["role"]  # ON CONFLICT … UPDATE role only
            return _Rows([], rowcount=1)

        if "DELETE FROM org_group_member" in s:
            key = (p["gid"], p["uid"])
            hit = self.members.pop(key, None)
            return _Rows([], rowcount=1 if hit else 0)

        if "INSERT INTO user_permission_override" in s:
            key = (p["uid"], p["perm"])
            if key in self.overrides:          # ON CONFLICT DO NOTHING
                return _Rows([], rowcount=0)
            self.overrides[key] = {
                "effect": "allow", "reason": p["reason"], "set_by": p["by"],
            }
            return _Rows([], rowcount=1)

        if "INSERT INTO org_group" in s:
            gid = f"g-{len(self.groups) + 1}"
            self.groups[gid] = {
                "id": gid, "slug": p["slug"], "display_name": p["name"],
                "description": p["desc"], "created_by": p["by"],
            }
            return _Rows([], rowcount=1)

        if "UPDATE org_group SET" in s:
            g = self.groups[p["gid"]]
            if p["name"] is not None:
                g["display_name"] = p["name"]
            if p["desc"] is not None:
                g["description"] = p["desc"]
            return _Rows([], rowcount=1)

        if "DELETE FROM org_group WHERE" in s:
            self.groups.pop(p["gid"], None)
            return _Rows([], rowcount=1)

        raise AssertionError(f"unhandled SQL in fake: {s}")


@pytest.fixture()
def db(monkeypatch: pytest.MonkeyPatch) -> _FakeDB:
    fake = _FakeDB()

    from contextlib import asynccontextmanager

    @asynccontextmanager
    async def _tenant_session(organization_id=None):
        # Commit-on-clean-exit, like the real `tenant_session` wrapper (H2) —
        # a refusal that raises mid-block commits nothing.
        yield fake
        await fake.commit()

    monkeypatch.setattr(groups, "_tenant_session", _tenant_session)
    # Cache invalidation reaches into acb_auth's resolver cache; keep the
    # tests hermetic (and assert it fires for access-affecting writes).
    fake.invalidated: list[str] = []
    monkeypatch.setattr(
        groups, "invalidate_for", lambda *e: fake.invalidated.extend(
            x for x in e if x
        ),
    )
    monkeypatch.setattr(groups, "record_admin_change", lambda *a, **k: None)
    # The acting admins need directory rows of their own: since WS-29e the
    # routes derive the tenant from the CALLER (R3), so an admin the directory
    # does not know has no organization and is refused 403 before any group is
    # touched. `test_admin_tenancy.py` is where that refusal is asserted.
    fake.seed_user("u-full-admin", FULL_ADMIN.email or "", "Admin")
    fake.seed_user("u-roster-admin", ROSTER_ADMIN.email or "", "Roster")
    return fake


def _seed_center_world(db: _FakeDB) -> None:
    db.seed_group("g-sales", "sales", "Sales")
    db.seed_group("g-people", "people", "People")
    db.seed_user("u-1", "priya@fracktal.in", "Priya")
    db.seed_user("u-2", "bob@fracktal.in", "Bob")


# ── List ────────────────────────────────────────────────────────────────────

async def test_list_groups_carries_members_counts_and_center_flag(db: _FakeDB) -> None:
    _seed_center_world(db)
    db.seed_group("g-x", "night-shift", "Night shift")
    db.members[("g-sales", "u-1")] = {"role": "lead", "added_by": "admin@x"}
    db.members[("g-sales", "u-2")] = {"role": "member", "added_by": "admin@x"}

    out = await list_groups(admin=FULL_ADMIN)

    by_slug = {g.slug: g for g in out}
    assert set(by_slug) == {"sales", "people", "night-shift"}
    sales = by_slug["sales"]
    assert sales.is_center and not by_slug["night-shift"].is_center
    assert sales.member_count == 2
    assert [(m.email, m.role) for m in sales.members] == [
        ("priya@fracktal.in", "lead"), ("bob@fracktal.in", "member"),
    ]


# ── CRUD ────────────────────────────────────────────────────────────────────

async def test_create_group_normalizes_slug_and_refuses_duplicates(db: _FakeDB) -> None:
    entry = await create_group(
        GroupCreate(slug="Night Shift", display_name="Night Shift"),
        admin=FULL_ADMIN,
    )
    assert entry.slug == "night-shift" and not entry.is_center
    assert db._group_by_slug("night-shift") is not None

    with pytest.raises(HTTPException) as exc:
        await create_group(GroupCreate(slug="night-shift"), admin=FULL_ADMIN)
    assert exc.value.status_code == 409


@pytest.mark.parametrize("bad", ["", "  ", "has/slash", "x" * 60])
async def test_create_group_rejects_bad_slugs(db: _FakeDB, bad: str) -> None:
    with pytest.raises(HTTPException) as exc:
        await create_group(GroupCreate(slug=bad), admin=FULL_ADMIN)
    assert exc.value.status_code == 400


# Task 2886. The team scope uses the word "all" for every team, so no group
# may take the slug "all", in any case and with any space around it.
@pytest.mark.parametrize("reserved", ["all", "ALL", " All ", "aLl"])
def test_clean_slug_refuses_the_reserved_slug(reserved: str) -> None:
    with pytest.raises(HTTPException) as exc:
        groups._clean_slug(reserved)
    assert exc.value.status_code == 400
    assert "reserved" in str(exc.value.detail)


@pytest.mark.parametrize("allowed", ["all-hands", "allies", "all_staff", "small"])
def test_clean_slug_allows_a_slug_that_only_holds_the_word(allowed: str) -> None:
    assert groups._clean_slug(allowed) == allowed


async def test_create_group_refuses_the_reserved_slug_and_writes_nothing(
    db: _FakeDB,
) -> None:
    with pytest.raises(HTTPException) as exc:
        await create_group(GroupCreate(slug=" All ", display_name="All"), admin=FULL_ADMIN)
    assert exc.value.status_code == 400
    assert db._group_by_slug("all") is None
    # The positive control: the same call with a slug that only holds the word.
    entry = await create_group(GroupCreate(slug="All Hands"), admin=FULL_ADMIN)
    assert entry.slug == "all-hands"
    assert db._group_by_slug("all-hands") is not None


def test_the_reserved_slugs_are_one_set() -> None:
    """The team filter of T-1 imports this set, so the word lives once."""
    assert "all" in groups.RESERVED_GROUP_SLUGS
    assert not set(groups.RESERVED_GROUP_SLUGS) & set(CENTER_GROUP_SLUGS)


async def test_update_group_edits_name_and_description_only(db: _FakeDB) -> None:
    _seed_center_world(db)
    entry = await update_group(
        "sales", GroupPatch(description="The deal closers"), admin=FULL_ADMIN,
    )
    assert entry.display_name == "Sales"          # untouched (COALESCE)
    assert entry.description == "The deal closers"
    assert db.groups["g-sales"]["slug"] == "sales"  # slug immutable


async def test_delete_refuses_center_groups_and_nonempty_groups(db: _FakeDB) -> None:
    _seed_center_world(db)
    with pytest.raises(HTTPException) as exc:
        await delete_group("sales", admin=FULL_ADMIN)
    assert exc.value.status_code == 403

    db.seed_group("g-x", "night-shift")
    db.members[("g-x", "u-1")] = {"role": "member", "added_by": "a@x"}
    with pytest.raises(HTTPException) as exc:
        await delete_group("night-shift", admin=FULL_ADMIN)
    assert exc.value.status_code == 409

    db.members.clear()
    out = await delete_group("night-shift", admin=FULL_ADMIN)
    assert out["status"] == "deleted"
    assert db._group_by_slug("night-shift") is None


async def test_unknown_group_404s(db: _FakeDB) -> None:
    with pytest.raises(HTTPException) as exc:
        await update_group("ghost", GroupPatch(), admin=FULL_ADMIN)
    assert exc.value.status_code == 404


# ── Membership + the center-access bridge ───────────────────────────────────

async def test_add_member_grants_the_center_override_with_provenance(db: _FakeDB) -> None:
    """The Phase B 'one admin action': membership + feature override, and the
    override row is byte-for-byte what the member editor would have written."""
    _seed_center_world(db)
    out = await add_group_member(
        "sales", GroupMemberAdd(email="priya@fracktal.in"), admin=FULL_ADMIN,
    )
    assert out["center_access_granted"] is True
    assert ("g-sales", "u-1") in db.members
    row = db.overrides[("u-1", "feature:center.sales")]
    assert row == {
        "effect": "allow",
        "reason": "group membership: sales",
        "set_by": "admin@fracktal.in",
    }
    # Access changed → the resolver cache for that member must be dropped.
    assert "priya@fracktal.in" in db.invalidated


async def test_add_member_grant_needs_access_manage_permission(db: _FakeDB) -> None:
    """Membership is admin:members:manage; the override is an ACCESS write and
    keeps the override endpoint's gate — no privilege shortcut through groups."""
    _seed_center_world(db)
    with pytest.raises(HTTPException) as exc:
        await add_group_member(
            "sales", GroupMemberAdd(email="priya@fracktal.in"), admin=ROSTER_ADMIN,
        )
    assert exc.value.status_code == 403
    assert "admin:access:manage" in exc.value.detail
    # Refused atomically: no membership row either.
    assert db.members == {}

    # Opting out of the grant lets a roster admin manage membership normally.
    out = await add_group_member(
        "sales",
        GroupMemberAdd(email="priya@fracktal.in", grant_center_access=False),
        admin=ROSTER_ADMIN,
    )
    assert out["center_access_granted"] is False
    assert db.overrides == {}


async def test_add_member_never_clobbers_an_existing_override(db: _FakeDB) -> None:
    """An explicit deny (or any prior override) is an admin decision the
    shortcut must not flip — ON CONFLICT DO NOTHING, reported as not granted."""
    _seed_center_world(db)
    db.overrides[("u-1", "feature:center.sales")] = {
        "effect": "deny", "reason": "left the team", "set_by": "owner@x",
    }
    out = await add_group_member(
        "sales", GroupMemberAdd(email="priya@fracktal.in"), admin=FULL_ADMIN,
    )
    assert out["center_access_granted"] is False
    assert db.overrides[("u-1", "feature:center.sales")]["effect"] == "deny"


async def test_add_member_to_non_center_group_writes_no_override(db: _FakeDB) -> None:
    _seed_center_world(db)
    db.seed_group("g-x", "night-shift")
    out = await add_group_member(
        "night-shift", GroupMemberAdd(email="bob@fracktal.in"), admin=FULL_ADMIN,
    )
    assert out["center_access_granted"] is False
    assert db.overrides == {}
    assert ("g-x", "u-2") in db.members


async def test_add_member_upserts_the_lead_role(db: _FakeDB) -> None:
    _seed_center_world(db)
    await add_group_member(
        "sales", GroupMemberAdd(email="priya@fracktal.in"), admin=FULL_ADMIN,
    )
    await add_group_member(
        "sales", GroupMemberAdd(email="priya@fracktal.in", role="lead"),
        admin=FULL_ADMIN,
    )
    assert db.members[("g-sales", "u-1")]["role"] == "lead"


async def test_add_member_rejects_unknown_roles_and_people(db: _FakeDB) -> None:
    _seed_center_world(db)
    with pytest.raises(HTTPException) as exc:
        await add_group_member(
            "sales", GroupMemberAdd(email="priya@fracktal.in", role="driver"),
            admin=FULL_ADMIN,
        )
    assert exc.value.status_code == 400

    with pytest.raises(HTTPException) as exc:
        await add_group_member(
            "sales", GroupMemberAdd(email="stranger@elsewhere.com"),
            admin=FULL_ADMIN,
        )
    assert exc.value.status_code == 404


async def test_remove_member_leaves_the_center_override_alone(db: _FakeDB) -> None:
    """Removal is a roster change; revoking access is a separate, explicit
    admin decision (see the route's docstring). The override stays."""
    _seed_center_world(db)
    await add_group_member(
        "sales", GroupMemberAdd(email="priya@fracktal.in"), admin=FULL_ADMIN,
    )
    out = await remove_group_member(
        "sales", "priya@fracktal.in", admin=FULL_ADMIN,
    )
    assert out["status"] == "removed"
    assert db.members == {}
    assert ("u-1", "feature:center.sales") in db.overrides   # untouched


async def test_remove_member_404s_when_not_a_member(db: _FakeDB) -> None:
    _seed_center_world(db)
    with pytest.raises(HTTPException) as exc:
        await remove_group_member("sales", "bob@fracktal.in", admin=FULL_ADMIN)
    assert exc.value.status_code == 404


# ── Wiring ──────────────────────────────────────────────────────────────────

def test_center_group_slugs_match_the_centers_registry() -> None:
    """One slug, three namespaces (department_centers.md §1 rule 1): the
    gateway's list must be exactly the six the feature seed + UI registry use."""
    assert CENTER_GROUP_SLUGS == (
        "sales", "marketing", "finance", "operations", "people", "company",
    )


def test_every_group_write_route_carries_the_members_manage_gate() -> None:
    """Guards the wiring: a write route that lost its require_permission
    dependency fails here instead of shipping open."""
    from gateway.routes.admin._common import router

    for route in router.routes:
        path = getattr(route, "path", "")
        methods = getattr(route, "methods", set()) or set()
        if not path.startswith("/admin/groups"):
            continue
        if methods == {"GET"}:
            continue  # reads sit on the package's admin:members:read floor
        deps = [
            getattr(d.dependency, "__qualname__", "")
            for d in getattr(route, "dependencies", [])
        ]
        assert any("require_permission" in q for q in deps), (
            f"{sorted(methods)} {path} carries no require_permission gate"
        )
