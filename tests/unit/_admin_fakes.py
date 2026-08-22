"""In-memory doubles for the ``gateway.routes.admin`` package.

Extracted from ``test_signin_requests.py`` when a second file
(``test_admin_member_offboarding.py``) needed the same ``app_user`` /
``user_role`` / ``access_request`` world. There is one org-admin fake, not one
per test file: two copies of a DB mirror drift, and the copy that drifts is the
one that stops failing.

Not named ``test_*``, so pytest imports it without collecting it.

Same convention as ``test_admin_groups.py``: the route functions are called
directly with the DB seam monkeypatched onto the SUT submodule, so nothing here
touches Postgres.
"""
from __future__ import annotations

import re
from contextlib import asynccontextmanager
from types import SimpleNamespace
from typing import Any, ClassVar

ORG = "00000000-0000-0000-0000-00000000000a"

#: A SECOND tenant. Every fixture in the existing files seeds only :data:`ORG`,
#: which is exactly why they could not have caught S1-1: a one-organization
#: world cannot tell a route that resolves the caller's tenant from one that
#: resolves a hard-coded slug — both answer `ORG`. ``test_admin_tenancy.py``
#: seeds both.
ORG_B = "00000000-0000-0000-0000-00000000000b"

# ── Person-scoped counts and deletes (members.purge_member) ─────────────────
#
# The purge addresses twenty-odd tables with two statements each, built from
# one `where` clause. Rather than a branch per table — twenty mirrors, twenty
# chances to drift — the branch below reads the **real statement text** to
# decide which seeded rows it addresses: which column the clause names, which
# parameter it binds it to, and whether it filters on `visibility`. A route
# that starts scoping to the wrong column, or drops the `visibility` filter,
# changes what these return. That is a weaker mirror than a hand-written copy,
# on purpose.

#: ``SELECT count(*) FROM <table> WHERE`` / ``DELETE FROM <table> WHERE``, and
#: nothing with a join or an alias (``FROM user_role ur JOIN …`` must fall
#: through to `owner_count`'s own branch).
_PERSON_STMT = re.compile(r"^(SELECT count\(\*\) FROM|DELETE FROM) (\w+) WHERE ")
#: ``lower(<col>) = :email`` / ``lower(<col>) = :actor``
_PERSON_ADDR = re.compile(r"lower\((\w+)\) = :(email|actor)")
#: The same comparison **without** ``lower()``. Modelled deliberately rather
#: than left unrecognised: an unparsed clause used to fall through to "every
#: seeded row matches", which made dropping ``lower()`` — the mutation that
#: leaves a departed colleague's credentials behind — look like a pass.
_PERSON_ADDR_CS = re.compile(r"(?<!lower\()\b(\w+) = :(email|actor)")
#: ``<col> = CAST(:uid AS uuid)``
_PERSON_UID = re.compile(r"(\w+) = CAST\(:uid AS uuid\)")
#: ``visibility = 'private'`` / ``visibility <> 'private'``
_PERSON_VIS = re.compile(r"visibility (=|<>) 'private'")
#: ``<col> IS NULL`` / ``<col> IS NOT NULL`` — how the GTD store's LOCAL half
#: is told from its SYNCED half (`account_id`). Read from the statement like
#: everything else here, so a clause that drops the filter changes the answer.
_PERSON_NULL = re.compile(r"(\w+) IS (NOT )?NULL")


class _Scalars:
    """``.scalars()`` yields the first column, the way SQLAlchemy does."""

    def __init__(self, rows: list[dict[str, Any]]):
        self._rows = rows

    def all(self) -> list[Any]:
        return [next(iter(r.values())) for r in self._rows]


class _Rows:
    """Result shim covering the access patterns the admin routes use."""

    def __init__(self, rows: list[dict[str, Any]], rowcount: int = 0):
        self._rows = rows
        self.rowcount = rowcount

    def mappings(self) -> _Rows:
        return self

    def first(self) -> dict[str, Any] | None:
        return self._rows[0] if self._rows else None

    def all(self) -> list[dict[str, Any]]:
        return self._rows

    def fetchall(self) -> list[Any]:
        return [tuple(r.values()) for r in self._rows]

    def fetchone(self) -> Any:
        """Attribute access, the way ``resolve_organization_id`` reads it.

        ``projects.core.resolve_organization_id`` — the ONE tenant lookup this
        package now shares — does ``getattr(row, "organization_id", None)``,
        not ``row["organization_id"]``. A shim that only spoke mappings would
        make it return ``None`` for every caller, i.e. make the whole admin
        surface 403 in tests while passing in production.
        """
        if not self._rows:
            return None
        return SimpleNamespace(**self._rows[0])

    def scalars(self) -> _Scalars:
        return _Scalars(self._rows)

    def scalar(self) -> Any:
        if not self._rows:
            return None
        return next(iter(self._rows[0].values()))


class _FakeDB:
    """In-memory ``app_user`` / ``org_role`` / ``user_role`` / ``access_request``.

    SQL is matched on normalised substrings — the exact strings the routes
    render — so a query-shape change fails loudly here rather than silently in
    production.

    ⚠️ **This is a MIRROR, not Postgres, and a mirror can only agree with
    itself.** The ``INSERT INTO app_user`` branch below re-implements the
    ``ON CONFLICT DO UPDATE`` arms in Python, the ``joined_at`` ``CASE`` in the
    status ``UPDATE`` likewise, and ``ORDER BY`` is ignored entirely. So every
    claim that lives *inside* a SQL statement — which statuses provisioning
    rewrites, what order the queue is served in — is asserted **structurally
    against the statement string** instead, in the "the SQL itself" section of
    ``test_signin_requests.py``. A behavioural case here proves the route calls
    the right statement with the right parameters; it cannot prove the
    statement says what we think.

    That gap is not hypothetical: widening the guard at
    ``_common._PROVISION_MEMBER_SQL`` to ``app_user.status <> 'active'``
    (which lets approve reinstate an off-boarded member on the weaker
    ``admin:members:invite``) left every behavioural case green, because the
    mirror below kept the old rule. Keep the two in step — and when they
    disagree, the structural assertion is the one that is right.

    A guard written in **Python inside a route** — ``assert_not_self_lockout``,
    ``assert_owner_survives`` — is the opposite case: the SUT runs for real and
    the fake only has to record what was written, so a behavioural assertion
    here is a genuine check on it. The discriminator to keep in mind there is
    that both of those raise **409**, so a test must assert the detail text or
    what was and was not written, never the bare status code.

    The person-scoped branch at the bottom (``members.purge_member``) is the
    least mirror-like part of this class: it reads the **statement text** to
    decide which seeded rows a clause addresses, rather than restating the
    predicate in Python. It still cannot prove *which tables* the purge names
    or that it never names ``app_audit`` — those live in the route's constants
    and are pinned structurally in
    ``tests/unit/test_admin_member_purge.py``.

    ⚠️ **This fake models NO foreign keys and therefore NO ``ON DELETE
    CASCADE``.** Deleting a seeded ``task_accounts`` row here leaves the
    seeded ``gtd_items`` rows exactly where they were; Postgres would take the
    SYNCED half with it. That is not an oversight to be fixed by a hand-written
    cascade map — a hand-written map is another mirror, and this class already
    documents what mirrors are worth. It is the reason a whole class of defect
    is **invisible to every behavioural case in this file**: a KEEP clause can
    count rows that the delete side cascades away and report them as survivors,
    and the fake will agree, because it kept them. That claim is cross-table
    and is fenced structurally instead —
    ``test_admin_member_purge.py::test_no_keep_clause_survives_a_cascade_on_the_delete_side``
    re-derives the cascade graph from ``infra/postgres/`` and checks the
    clauses against it. It shipped once without that fence and reported 847
    destroyed tasks as kept.
    """

    ROLE_RANKS: ClassVar[dict[str, int]] = {
        "owner": 0, "admin": 10, "manager": 20, "member": 30,
    }

    #: Only what invariant 4's third door needs to decide: does this role still
    #: let its holder undo what they just did? `owner` holds `*`, so it is
    #: matched through `permission_matches`, not by string equality — an
    #: allowlist of slugs would have refused a custom role that legitimately
    #: carries the permission. `manager` deliberately holds only
    #: `admin:members:read` (see D14: it can see the roster, not change it).
    ROLE_PERMISSIONS: ClassVar[dict[str, tuple[str, ...]]] = {
        "owner": ("*",),
        "admin": ("admin:members:read", "admin:members:manage"),
        "manager": ("admin:members:read",),
        "member": (),
    }

    def __init__(self) -> None:
        self.users: dict[str, dict[str, Any]] = {}          # id → row
        self.user_roles: dict[str, list[str]] = {}          # uid → [slug]
        self.requests: dict[str, dict[str, Any]] = {}       # lower(email) → row
        #: Everything else a person owns — one list per table name, rows as
        #: plain dicts carrying whatever columns the purge's clauses name.
        #: `user_role`, `app_user` and `access_request` are NOT here: they are
        #: modelled above and the purge branch reads and writes those, so a
        #: test sees one world rather than two.
        self.rows: dict[str, list[dict[str, Any]]] = {}
        #: ``organization`` — id → row. Seeded with the single tenant the
        #: pre-retrofit files assume; ``test_admin_tenancy.py`` adds a second.
        self.organizations: dict[str, dict[str, Any]] = {
            ORG: {"id": ORG, "slug": "default", "display_name": "Default Org"},
        }
        #: ``org_group`` — id → row, each carrying its own ``organization_id``.
        #: Group slugs are UNIQUE **per organization**, so `engineering` is a
        #: legal slug in every tenant at once and matching on the bare slug
        #: spans them (leak audit S2-5).
        self.groups: dict[str, dict[str, Any]] = {}
        #: ``org_group_member`` — (group_id, user_id) → row.
        self.group_members: dict[tuple[str, str], dict[str, Any]] = {}
        #: ``user_permission_override`` — (user_id, permission) → row.
        self.overrides: dict[tuple[str, str], dict[str, Any]] = {}
        #: Does the deployment have an ``organization`` row at all? Only
        #: ``get_org_id``'s failure path asks, and only to tell an operator
        #: whose migration never ran (503) apart from a caller whose account is
        #: not attached (403). Set ``False`` to model the unprovisioned box.
        self.provisioned = True
        self.committed = 0
        self.invalidated: list[str] = []
        #: Audit calls, in order, as ``(action, target)``. Ordered because the
        #: purge must record BEFORE it commits, and a set cannot show that.
        self.audit: list[tuple[str, str]] = []
        #: The same calls with their keyword payload, for the writes whose
        #: audit entry has to carry more than "it happened" — a purge that
        #: records no counts leaves nothing to reconcile against.
        self.audit_payloads: list[dict[str, Any]] = []
        self.statements: list[str] = []
        #: ``len(self.audit)`` at each ``commit()`` — how a test proves the
        #: audit entry was written first rather than merely written.
        self.audit_at_commit: list[int] = []

    # helpers -----------------------------------------------------------
    def seed_rows(self, table: str, *rows: dict[str, Any]) -> None:
        """Seed person-scoped rows for a table the purge addresses."""
        self.rows.setdefault(table, []).extend(rows)

    def _person_rows(self, s: str, table: str, p: dict) -> list[dict[str, Any]]:
        """The rows in `table` that THIS statement addresses.

        Decided from the statement text: which column the clause names, which
        parameter it is compared against, and the optional `visibility`
        filter. Nothing here restates the route's predicate in Python, so a
        clause that changes column or drops a filter changes the answer.
        """
        if table == "user_role":
            return [{"user_id": p.get("uid"), "slug": slug}
                    for slug in self.user_roles.get(p.get("uid", ""), [])]
        if table == "app_user":
            row = self.users.get(p.get("uid", ""))
            return [row] if row else []
        if table == "access_request":
            want = str(p.get("email", "")).lower()
            return [r for r in self.requests.values()
                    if r["email"].lower() == want]

        found = list(self.rows.get(table, []))
        scoped = False
        addr = _PERSON_ADDR.search(s)
        if addr:
            scoped = True
            col, key = addr.group(1), addr.group(2)
            want = str(p[key]).lower()
            found = [r for r in found if str(r.get(col, "")).lower() == want]
        else:
            # No `lower()`. Postgres would compare byte-for-byte, and so does
            # this — modelling it is what makes "drop the lower()" a failing
            # test rather than a clause the fake shrugs at.
            raw = _PERSON_ADDR_CS.search(s)
            if raw:
                scoped = True
                col, key = raw.group(1), raw.group(2)
                want = str(p[key])
                found = [r for r in found if str(r.get(col, "")) == want]
        uid = _PERSON_UID.search(s)
        if uid:
            scoped = True
            col = uid.group(1)
            found = [r for r in found if str(r.get(col, "")) == p["uid"]]
        if not scoped:
            # A clause this fake cannot read must never mean "every row".
            # Matching everything is exactly what a person-scoped DELETE must
            # not do, so an unreadable predicate is an error, not a pass.
            raise AssertionError(f"unscoped person clause in fake: {s}")
        vis = _PERSON_VIS.search(s)
        if vis:
            wants_private = vis.group(1) == "="
            found = [
                r for r in found
                if (r.get("visibility", "private") == "private") is wants_private
            ]
        for nul in _PERSON_NULL.finditer(s):
            col, negated = nul.group(1), bool(nul.group(2))
            found = [r for r in found if (r.get(col) is not None) is negated]
        return found

    def _person_delete(self, table: str, matched: list[dict[str, Any]]) -> None:
        if table == "app_user":
            for row in matched:
                self.users.pop(row["id"], None)
                self.user_roles.pop(row["id"], None)
        elif table == "access_request":
            for row in matched:
                self.requests.pop(row["email"].lower(), None)
        else:
            keep = [r for r in self.rows.get(table, []) if r not in matched]
            self.rows[table] = keep

    # helpers -----------------------------------------------------------
    def seed_user(self, uid: str, email: str, *, status: str = "active",
                  name: str = "", joined_at: str | None = None,
                  organization_id: str | None = ORG) -> None:
        """Seed a directory row. ``organization_id`` defaults to :data:`ORG`.

        Defaulted rather than required so the single-tenant files that predate
        the retrofit read unchanged; ``None`` models the legacy row migration
        130 left unattached, which is the only row a provisioning upsert may
        adopt into a tenant.
        """
        self.users[uid] = {
            "id": uid, "email": email, "display_name": name,
            "avatar_url": "", "status": status, "legacy_role": "employee",
            "invited_by": "", "invited_at": None, "joined_at": joined_at,
            "last_login_at": None, "last_active_at": None, "created_at": None,
            "organization_id": organization_id,
        }

    def seed_organization(self, org_id: str, slug: str, name: str) -> None:
        self.organizations[org_id] = {
            "id": org_id, "slug": slug, "display_name": name,
        }

    def seed_group(self, gid: str, slug: str, *, organization_id: str = ORG,
                   name: str | None = None) -> None:
        self.groups[gid] = {
            "id": gid, "slug": slug, "display_name": name or slug.title(),
            "description": "", "organization_id": organization_id,
        }

    def seed_request(self, email: str, *, status: str = "pending",
                     attempts: int = 1) -> None:
        self.requests[email.lower()] = {
            "id": f"r-{len(self.requests) + 1}", "email": email,
            "display_name": "", "first_seen_at": None, "last_seen_at": None,
            "attempt_count": attempts, "status": status,
            "decided_by": "", "decided_at": None,
        }

    def user_by_email(self, email: str) -> dict[str, Any] | None:
        return next(
            (u for u in self.users.values()
             if u["email"].lower() == email.lower()), None,
        )

    # SQLAlchemy-session surface ---------------------------------------
    async def __aenter__(self) -> _FakeDB:
        return self

    async def __aexit__(self, *exc: Any) -> None:
        return None

    async def commit(self) -> None:
        self.committed += 1
        self.audit_at_commit.append(len(self.audit))

    async def execute(  # noqa: C901 — one branch per statement, by design
        self, sql: Any, params: dict | None = None
    ) -> _Rows:
        s = " ".join(str(sql).split())
        p = params or {}
        self.statements.append(s)

        # ── The caller's tenant (WS-29e / S1-1) ─────────────────────────────
        #
        # ⚠️ There is deliberately NO `FROM organization WHERE slug` branch any
        # more. It used to answer `ORG` unconditionally, which is what made the
        # hard-coded-slug bug invisible to every test in this suite: the fake
        # agreed that the deployment's org and the caller's org were the same
        # thing, because in a one-organization world they are.
        if "FROM app_user au" in s and "organization_id" in s:
            # `projects.core._MY_ORGANIZATION_SQL`, read for real: the ACTIVE
            # row for this address, and its tenant. An address with no row, or
            # an inactive one, resolves to nothing — which is what makes
            # `get_org_id` fail closed rather than fall back.
            row = self.user_by_email(p["email"])
            if row is None or row.get("status") != "active":
                return _Rows([])
            org = row.get("organization_id")
            return _Rows([{"organization_id": org}] if org else [])

        if "FROM organization LIMIT 1" in s:
            return _Rows([{"one": 1}] if self.provisioned else [])

        if "FROM organization WHERE id" in s:
            # `/auth/me` naming the caller's org back to the browser. Answered
            # BY ID, which is the whole change: it used to be answered by the
            # literal slug `default` for every signed-in member of every tenant.
            row = self.organizations.get(p["id"])
            return _Rows([dict(row)] if row else [])

        if "FROM feature_catalog" in s:
            return _Rows([{"slug": "projects"}])

        if "FROM app_user u" in s and "u.organization_id = CAST(:org AS uuid)" in s:
            # `members.list_members` — the roster. The tenant predicate is read
            # from the statement, so a route that stops scoping the roster
            # shows this fake's other organization and fails.
            rows = [
                u for u in self.users.values()
                if u.get("organization_id") == p.get("org")
            ]
            if "u.status <> 'removed'" in s:
                rows = [u for u in rows if u["status"] != "removed"]
            return _Rows([
                dict(u) | {"roles": list(self.user_roles.get(u["id"], []))}
                for u in sorted(rows, key=lambda u: u["email"])
            ])

        if "FROM org_group" in s and "AND slug = :slug" in s:
            # `groups._get_group` — by slug WITHIN one organization.
            row = next(
                (g for g in self.groups.values()
                 if g["slug"] == p["slug"]
                 and g["organization_id"] == p.get("org")), None,
            )
            return _Rows([dict(row)] if row else [])

        if "INSERT INTO org_group_member" in s:
            key = (p["gid"], p["uid"])
            existing = self.group_members.get(key)
            if existing is None:
                self.group_members[key] = {"role": p["role"], "added_by": p["by"]}
            else:
                existing["role"] = p["role"]
            return _Rows([], rowcount=1)

        if "INSERT INTO user_permission_override" in s:
            key = (p["uid"], p["perm"])
            if key in self.overrides:          # ON CONFLICT DO NOTHING
                return _Rows([], rowcount=0)
            self.overrides[key] = {
                "effect": p.get("effect", "allow"), "reason": p.get("reason", ""),
                "set_by": p.get("by", ""),
            }
            return _Rows([], rowcount=1)

        if "MIN(r.rank)" in s:
            me = self.user_by_email(p["email"])
            # `caller_rank`'s SQL joins `org_role` on the org, so a caller's
            # rank in a tenant they do not belong to is no rank at all.
            if me is not None and me.get("organization_id") != p.get("org"):
                return _Rows([{"rank": None}])
            slugs = self.user_roles.get(me["id"], []) if me else []
            ranks = [self.ROLE_RANKS[x] for x in slugs if x in self.ROLE_RANKS]
            return _Rows([{"rank": min(ranks) if ranks else None}])

        if "UPDATE app_user SET role = :role" in s:
            # members.set_member_roles keeps the legacy coarse column truthful
            # (spec §7). Only the accept path reaches it, which is why the
            # refusal cases never needed this branch.
            self.users[p["uid"]]["legacy_role"] = p["role"]
            return _Rows([], rowcount=1)

        if "FROM org_role_permission" in s:
            # Mirror of `_common._ROLE_PERMISSIONS_SQL`, answering from
            # ROLE_PERMISSIONS. ⚠️ A mirror agrees with itself: a behavioural
            # test here cannot notice the real statement changing shape, so
            # `assert_not_self_demotion`'s SQL is *also* pinned structurally.
            out: list[dict[str, Any]] = []
            for rid in p["ids"]:
                slug = str(rid).removeprefix("role-")
                out += [{"permission": x}
                        for x in self.ROLE_PERMISSIONS.get(slug, ())]
            return _Rows(out)

        if "FROM org_role WHERE organization_id" in s and "ANY(:slugs)" in s:
            return _Rows([
                {"id": f"role-{slug}", "slug": slug,
                 "rank": self.ROLE_RANKS[slug]}
                for slug in p["slugs"] if slug in self.ROLE_RANKS
            ])

        if "SELECT organization_id::text AS org, email FROM app_user" in s:
            # `_ADDRESS_TENANT_SQL` — the one deliberately cross-tenant read.
            want = str(p["email"]).lower()
            return _Rows([
                {"org": u.get("organization_id"), "email": u["email"]}
                for u in self.users.values() if u["email"].lower() == want
            ])

        if "INSERT INTO app_user" in s:
            # ⚠️ **BYTE-EXACT**, mirroring `app_user_email_key`, which is
            # `UNIQUE (email)` and NOT `UNIQUE (lower(email))`. A fake that
            # matched case-insensitively here would agree that a lower-cased
            # invite of `Casey@Alpha.Example` conflicts — it does not, and the
            # duplicate row Postgres writes instead is a live finding this
            # class was previously unable to express.
            existing = next(
                (u for u in self.users.values() if u["email"] == p["email"]),
                None,
            )
            if existing is None:
                uid = f"u-{len(self.users) + 1}"
                self.seed_user(uid, p["email"], status=p.get("status", "invited"),
                               name=p.get("name", ""),
                               organization_id=p.get("org"))
                self.users[uid]["invited_by"] = p.get("by", "")
                if p.get("status") == "active":
                    self.users[uid]["joined_at"] = "now()"
                return _Rows([], rowcount=1)

            # ⚠️ The DO UPDATE arm's own `WHERE`, read from the STATEMENT and
            # not restated as a rule: a conflicting row belonging to another
            # tenant is not written at all. `app_user.email` is globally UNIQUE
            # (D-MT-1 (a)), so this arm is the only place a cross-tenant row can
            # be reached by an INSERT, and deleting the fence from the SQL
            # changes what this branch does rather than being shrugged at.
            # Both arms are read separately, not as one "is it fenced" flag:
            # dropping the `IS NULL` arm is a different defect from dropping
            # the whole fence — it locks out the pre-130 rows that have no
            # tenant yet, which looks identical to a correct refusal.
            allows_null = "app_user.organization_id IS NULL" in s
            allows_match = "app_user.organization_id = EXCLUDED.organization_id" in s
            org = existing.get("organization_id")
            if allows_null or allows_match:
                writable = (
                    (allows_null and org is None)
                    or (allows_match and org is not None and org == p.get("org"))
                )
                if not writable:
                    return _Rows([], rowcount=0)

            # `SET organization_id = COALESCE(app_user.organization_id, …)` —
            # also read from the statement, so reverting it to the bare
            # `EXCLUDED.organization_id` (the tenant STEAL) is visible here.
            keeps_tenant = "COALESCE(app_user.organization_id" in s
            if org is None or not keeps_tenant:
                existing["organization_id"] = p.get("org")

            # ON CONFLICT (email) DO UPDATE — mirror of _PROVISION_MEMBER_SQL's
            # CASE arms. Keep in step with it; the structural test is the fence.
            if p.get("name"):
                existing["display_name"] = p["name"]
            wanted = p.get("status", "invited")
            prior = existing["status"]
            if prior == "invited" or (prior == "removed" and wanted != "active"):
                existing["status"] = wanted
                if prior == "invited" and wanted == "active" \
                        and not existing["joined_at"]:
                    existing["joined_at"] = "now()"
            return _Rows([], rowcount=1)

        if "FROM app_user WHERE lower(email)" in s:
            row = self.user_by_email(p["email"])
            # `find_member`'s tenant predicate, read from the statement for the
            # same reason as the fence above: a member lookup that drops it
            # hands every member-targeted route in the package a row from
            # another organization, and a caller-derived `get_org_id` in front
            # of it changes nothing about that.
            if (
                row is not None
                and "organization_id = CAST(:org AS uuid)" in s
                and row.get("organization_id") != p.get("org")
            ):
                row = None
            return _Rows([dict(row)] if row else [])

        if "UPDATE app_user SET status = :status" in s:
            # members.update_member — the lifecycle PATCH. Mirror of the
            # statement's `joined_at` CASE: activation stamps it once and no
            # other transition touches it.
            row = self.users[p["uid"]]
            row["status"] = p["status"]
            if p["status"] == "active" and not row["joined_at"]:
                row["joined_at"] = "now()"
            return _Rows([], rowcount=1)

        if "UPDATE app_user SET display_name = :name" in s:
            self.users[p["uid"]]["display_name"] = p["name"]
            return _Rows([], rowcount=1)

        if "UPDATE app_user SET status = 'removed'" in s:
            # members.remove_member — the off-boarding half of the P1 scenario.
            self.users[p["uid"]]["status"] = "removed"
            return _Rows([], rowcount=1)

        if "DELETE FROM user_role WHERE user_id" in s:
            self.user_roles[p["uid"]] = []
            return _Rows([], rowcount=1)

        if "INSERT INTO user_role" in s:
            slug = str(p["rid"]).removeprefix("role-")
            self.user_roles.setdefault(p["uid"], []).append(slug)
            return _Rows([], rowcount=1)

        if "r.slug = 'owner'" in s:
            # owner_count(): how many ACTIVE members would still hold `owner`
            # IN THIS ORGANIZATION — the real statement joins `org_role` on it,
            # and invariant 1 is per-tenant: another company having an owner
            # does not stop this one going ownerless.
            excluded = p.get("uid")
            return _Rows([{"count": sum(
                1 for uid, slugs in self.user_roles.items()
                if "owner" in slugs and uid != excluded
                and (self.users.get(uid) or {}).get("status") == "active"
                and (self.users.get(uid) or {}).get("organization_id")
                == p.get("org")
            )}])

        if "SELECT r.slug FROM user_role ur" in s:
            slugs = self.user_roles.get(p["uid"], [])
            return _Rows([
                {"slug": x}
                for x in sorted(slugs, key=lambda y: self.ROLE_RANKS.get(y, 999))
            ])

        # members.purge_member — ONE branch for every person-scoped count and
        # delete, driven by the statement text (see the module header).
        #
        # Positioned here on purpose: **after** the specific branches whose
        # statements it would otherwise swallow (`set_roles`' own DELETE FROM
        # user_role) and **before** the `FROM access_request` reader, which
        # matches on a substring and would answer a `SELECT count(*) FROM
        # access_request` with a request ROW.
        stmt = _PERSON_STMT.match(s)
        if stmt:
            table = stmt.group(2)
            matched = self._person_rows(s, table, p)
            if stmt.group(1).startswith("SELECT"):
                return _Rows([{"count": len(matched)}])
            self._person_delete(table, matched)
            return _Rows([], rowcount=len(matched))

        if "UPDATE access_request SET" in s:
            row = self.requests.get(str(p["email"]).lower())
            # Mirrors `_DECIDE_SQL`'s `AND status = ANY(:allowed)` — the
            # write's own copy of the filter the read already checked, which
            # is what makes two concurrent decisions resolve to one. No rows
            # updated ⇒ the RETURNING clause yields nothing.
            if row is None or row["status"] not in p["allowed"]:
                return _Rows([], rowcount=0)
            row["status"] = p["status"]
            row["decided_by"] = p["by"]
            row["decided_at"] = "now()"
            return _Rows([{"id": row["id"]}], rowcount=1)

        if "FROM access_request" in s:
            if "lower(email) = :email" in s:
                row = self.requests.get(str(p["email"]).lower())
                return _Rows([dict(row)] if row else [])
            rows = [
                dict(r) for r in self.requests.values() if r["status"] == "pending"
            ]
            return _Rows(rows)

        raise AssertionError(f"unhandled SQL in fake: {s}")


def bind_admin_db(monkeypatch: Any, fake: _FakeDB, modules: tuple[Any, ...]) -> None:
    """Point each admin submodule's DB / cache / audit seams at ``fake``.

    Per-module and not per-package because the routes import the seams by name
    (``from _common import _tenant_session``), so patching ``_common`` alone
    would not reach them.

    H2 note: the real seam is ``acb_common.db.tenant_session`` — an async
    context manager that begins a transaction, issues ``SET LOCAL
    app.tenant_id`` and commits on clean exit. The fake mirrors the SHAPE plus
    commit-on-clean-exit, so "one transaction, committed once" stays an
    OBSERVABLE fact (``db.committed == 1``) and a refusal that raises
    mid-block commits nothing here just as it commits nothing against
    Postgres. GUC plumbing stays out of the mirror — ``test_tenant_session.py``
    owns that.
    """
    @asynccontextmanager
    async def _tenant_session(organization_id: str | None = None) -> Any:
        yield fake
        await fake.commit()

    # WS-29 H6 (DARK): the RLS-EXEMPT identity-shadow seams
    # (`acb_auth.access.mirror_identity_membership` / `mirror_membership_status` /
    # `purge_identity_shadow`) each open their OWN session (mirroring
    # `_record_signin_request`). They are best-effort and never touch this fake or
    # its observable state (`committed`, `statements`), but left un-patched they
    # would attempt a REAL connection from a hermetic test. The H6 orphan-closure
    # slice moved the invite/approve mirror calls OUT of `provision_member` into
    # its callers (`members.invite_member`, `access_requests.approve_access_request`)
    # and added a purge-shadow delete to `members.purge_member`, so the seams are
    # now imported by the ROUTE modules, not `_common` — no-op them wherever they
    # resolve. Their behaviour is proven against real Postgres in
    # `tests/unit/test_h6_identity_shadow.py`.
    from gateway.routes.admin import _common as _common_mod

    async def _no_mirror(**_kw: Any) -> None:
        return None

    _shadow_seams = (
        "mirror_identity_membership",
        "mirror_membership_status",
        "purge_identity_shadow",
    )
    for module in (_common_mod, *modules):
        for seam in _shadow_seams:
            if hasattr(module, seam):
                monkeypatch.setattr(module, seam, _no_mirror)

    for module in modules:
        monkeypatch.setattr(module, "_tenant_session", _tenant_session)
        monkeypatch.setattr(
            module, "invalidate_for",
            lambda *e: fake.invalidated.extend(x for x in e if x),
        )
        def _record(actor: str, action: str, target: str, **kw: Any) -> None:
            fake.audit.append((action, target))
            fake.audit_payloads.append(kw)

        monkeypatch.setattr(module, "record_admin_change", _record)
