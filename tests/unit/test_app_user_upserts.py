"""WS-29 MT-1j slice 6 — the two ``app_user`` upserts, against the real ladder.

Spec: ``project-docs/specs/saas_multitenancy.md`` §11 MT-1j slice 6.

⚠️ **This suite exists because both statements were BROKEN in the shipped tree
and every hermetic test was green.** Migration 162 replaced ``app_user``'s
byte-exact ``app_user_email_key`` with ``app_user_email_lower_key ON app_user
(lower(email))`` and dropped the old constraint; two upserts kept naming
``ON CONFLICT (email)``, which needs an arbiter index on exactly ``(email)``.
Reproduced red on 2026-08-19 against a ladder-replayed Postgres — verbatim::

    sqlalchemy.exc.ProgrammingError: (psycopg.errors.InvalidColumnReference)
    there is no unique or exclusion constraint matching the ON CONFLICT
    specification          -- SQLSTATE 42P10

**Postgres resolves the arbiter index at PLAN time**, so this was not a
conflict-path bug: all six calls failed, the fresh-insert ones included. What
that cost, measured rather than reasoned: ``ensure_owner_bootstrap()`` catches
every exception, so an ownerless box logged ``ownership_bootstrap_failed`` and
served on with no owner and no inviter — the 2026-07-30 lockout, re-armed; and
every ``POST /admin/members`` invite and every sign-in approval raised.

Two halves, and the split is deliberate:

* the **structural** half takes no database and must never skip — it is the
  grep-shaped ratchet that stops the un-lowered idiom coming back; and
* the **R8** half answers to ``TENANT_LADDER_DATABASE_URL`` — never to
  ``DATABASE_URL``, for the reason ``test_billing_purchase_capability.py``
  records at length (that variable arms ``test_tenant_coverage.py``'s two
  DB-gated tests, which fail by construction against a freshly-replayed
  ladder). The gate reads ``tests/conftest.py``'s launch snapshot, because
  ``import litellm`` calls ``load_dotenv()`` mid-collection.

⚠️ **R8 is not optional here and a hermetic version of this file would be
worse than nothing**: a fake keyed on the address agrees with whichever
conflict target it is handed, which is precisely how ``(email)`` shipped.

Run::

    export TENANT_LADDER_DATABASE_URL=postgresql+psycopg://acb:acb@127.0.0.1:5443/acb_tenant
    uv run pytest tests/unit/test_app_user_upserts.py -v -rs
"""
from __future__ import annotations

import os
import re
from pathlib import Path

import pytest

pytest.importorskip("sqlalchemy")

#: The statements under test are IMPORTED, never transcribed — a copy here
#: would keep passing after somebody edited the real one.
from acb_auth.access import _BOOTSTRAP_OWNER_SQL
from gateway.routes.admin._common import _PROVISION_MEMBER_SQL
from sqlalchemy import create_engine, text

from tests.unit._tenant_ladder import apply_ladder

_SNAPSHOT = os.environ.get("_ACB_TENANT_LADDER_URL_AT_LAUNCH")
_URL = (
    _SNAPSHOT
    if _SNAPSHOT is not None
    else os.environ.get("TENANT_LADDER_DATABASE_URL", "")
).strip()

_DB_GATE = pytest.mark.skipif(
    not _URL,
    reason=(
        "TENANT_LADDER_DATABASE_URL unset — R8 requires a REAL Postgres with "
        "pgvector (infra/postgres/01_schema.sql needs uuid-ossp AND vector). "
        "A skip here is not a pass; CI must set it. ⚠️ Do NOT export it as "
        "DATABASE_URL — see this module's docstring."
    ),
)

_ROOT = Path(__file__).resolve().parents[2]


# ── The structural half: no database, and it must never skip ────────────────

#: Whitespace-proof on purpose. ``ON CONFLICT(email)`` and
#: ``ON  CONFLICT ( email )`` are the same statement to Postgres and would evade
#: a literal search — the same lesson MT-1j's `slug = 'default'` ratchet records.
_UNLOWERED = re.compile(r"ON\s+CONFLICT\s*\(\s*email\s*\)", re.IGNORECASE)

#: Allow-list **with a reason**, the ``_SYNC_ENGINE_ALLOWED`` discipline. Keyed
#: by repo-relative POSIX path.
#:
#: ⚠️ This is not a "known offender" list. The one entry is a DIFFERENT
#: DATABASE: the Customer Console plane has its own ladder
#: (``infra/customer_console/``), and there ``user_identity.email`` is
#: ``CITEXT NOT NULL UNIQUE`` — a real unique constraint on the bare column, so
#: ``ON CONFLICT (email)`` is correct there and ``lower(email)`` would be the
#: bug. ``test_the_allow_listed_site_really_has_a_bare_email_constraint``
#: checks that reason against the Console DDL rather than trusting this comment.
_ALLOWED_UNLOWERED = {
    "apps/services/customer_console/customer_console/store.py": (
        "Console plane, not the tenant plane: user_identity.email is CITEXT "
        "NOT NULL UNIQUE in infra/customer_console/001_customer_console.sql"
    ),
}

#: ⚠️ **`tests/` is deliberately NOT scanned, and there is a live site in it.**
#: ``tests/unit/test_owner_bootstrap.py``'s fixture carries the same
#: ``ON CONFLICT (email) DO NOTHING`` and is therefore red against a post-162
#: database — measured 2026-08-19, one test of five. It skips everywhere today
#: (it gates on ``DATABASE_URL`` reachability, which CI does not set), which is
#: precisely why the production defect this file repairs reached main unseen.
#: It is NOT allow-listed here, because it is not correct: it is a finding
#: recorded against MT-1j slice 2, whose done-when says that suite must stay
#: green *without edit* — a clause nothing can satisfy while the fixture names
#: an index that does not exist. Scoping this ratchet to the two production
#: trees is the ticket's own wording; widening it is slice 2's call to make
#: together with that repair, not a drive-by here.
_SCANNED_TREES = ("packages", "apps")


def _sites_naming_the_bare_column() -> dict[str, list[int]]:
    """Every ``.py`` line under packages/ + apps/ with an un-lowered target."""
    found: dict[str, list[int]] = {}
    for tree in _SCANNED_TREES:
        for path in sorted((_ROOT / tree).rglob("*.py")):
            try:
                body = path.read_text(encoding="utf-8")
            except (OSError, UnicodeDecodeError):  # pragma: no cover - defensive
                continue
            if not _UNLOWERED.search(body):
                continue
            lines = [
                n
                for n, line in enumerate(body.splitlines(), start=1)
                if _UNLOWERED.search(line)
            ]
            found[path.relative_to(_ROOT).as_posix()] = lines
    return found


class TestTheUnloweredIdiomIsGone:
    """The ratchet. Breaking the fix must fail here even without a database."""

    def test_no_un_allow_listed_site_names_the_bare_email_column(self):
        offenders = {
            path: lines
            for path, lines in _sites_naming_the_bare_column().items()
            if path not in _ALLOWED_UNLOWERED
        }
        assert not offenders, (
            "ON CONFLICT (email) needs a unique index on exactly (email); "
            "migration 162 dropped app_user_email_key, so on the tenant plane "
            "that is 42P10 at PLAN time — the fresh-insert path fails too. Use "
            "ON CONFLICT (lower(email)). Offenders: "
            f"{offenders}"
        )

    def test_the_allow_list_carries_no_dead_entry(self):
        """An entry whose site no longer exists hides the next real one."""
        live = set(_sites_naming_the_bare_column())
        stale = set(_ALLOWED_UNLOWERED) - live
        assert not stale, (
            f"allow-listed sites that no longer name ON CONFLICT (email): "
            f"{sorted(stale)} — delete the entry, do not carry it"
        )

    def test_the_allow_listed_site_really_has_a_bare_email_constraint(self):
        """The reason is checked against the Console DDL, not believed.

        The whole allow-list rests on one claim — that the OTHER plane has a
        unique constraint on the bare column. If migration 162's twin ever
        lands over there, this entry becomes the same defect under a different
        database and the reason must not be what keeps it green.
        """
        console_ddl = "\n".join(
            path.read_text(encoding="utf-8")
            for path in sorted((_ROOT / "infra" / "customer_console").glob("*.sql"))
        )
        assert re.search(
            r"email\s+CITEXT\s+NOT\s+NULL\s+UNIQUE", console_ddl
        ), (
            "the Console ladder no longer declares user_identity.email as "
            "CITEXT NOT NULL UNIQUE — the allow-list entry's reason is gone"
        )

    def test_both_repaired_statements_name_the_functional_index(self):
        """The two subjects, asserted positively rather than by absence."""
        for label, sql in (
            ("_BOOTSTRAP_OWNER_SQL", _BOOTSTRAP_OWNER_SQL),
            ("_PROVISION_MEMBER_SQL", _PROVISION_MEMBER_SQL),
        ):
            assert re.search(
                r"ON\s+CONFLICT\s*\(\s*lower\(email\)\s*\)", sql, re.IGNORECASE
            ), f"{label} no longer targets (lower(email))"


def _strip_sql_comments(sql: str) -> str:
    """SQL with `--` line comments removed.

    A migration's header is where the WHY lives, so prose in one file routinely
    names objects another file owns. Matching on raw text conflates "creates
    this" with "talks about this".
    """
    return "\n".join(re.sub(r"--.*$", "", line) for line in sql.splitlines())


class TestTheMigrationThatCausedIt:
    def test_the_functional_index_and_the_drop_ship_in_one_migration(self):
        """Found BY CONTENT, never by number (R1).

        A test that hard-codes ``162`` reports a merge renumber as a broken
        migration. What is pinned is that the file creating the functional
        index is also the one dropping the byte-exact constraint — the pair is
        the reason ``(email)`` stopped resolving.
        """
        # ⚠️ Comments are STRIPPED before matching, and the reason is that this
        # test failed once on a file that only MENTIONED the index. Migration
        # 189 (the WS-39 backfill) explains in prose that
        # `app_user_email_lower_key` is what makes an email resolve to one
        # person in one organization — which is exactly the kind of note a
        # later reader needs, and which the un-stripped match read as a second
        # CREATE. The docstring above already said "the file CREATING the
        # functional index"; the implementation just did not agree with it.
        # Banning the NAME would push the next author to explain the invariant
        # without naming it, which is worse than not explaining it.
        matches = [
            path
            for path in sorted((_ROOT / "infra" / "postgres").glob("*.sql"))
            if re.match(r"^\d+_", path.name)
            and "app_user_email_lower_key" in _strip_sql_comments(
                path.read_text(encoding="utf-8")
            )
        ]
        assert len(matches) == 1, (
            f"expected exactly one migration creating app_user_email_lower_key, "
            f"found {[p.name for p in matches]}"
        )
        body = matches[0].read_text(encoding="utf-8")
        assert "DROP CONSTRAINT IF EXISTS app_user_email_key" in body

    def test_this_suite_is_named_in_the_ci_skip_guard(self):
        """The hand-list discovers NOTHING (pr-check.yml's own warning).

        An R8 suite absent from it still runs in the directory step, still
        skips silently there without a database, and still leaves the job
        green — the exact failure that step exists to catch.
        """
        workflow = (_ROOT / ".github/workflows/pr-check.yml").read_text(
            encoding="utf-8")
        assert "tests/unit/test_app_user_upserts.py" in workflow

    def test_the_owning_spec_names_this_suite(self):
        spec = (_ROOT / "project-docs/specs/saas_multitenancy.md").read_text(
            encoding="utf-8")
        assert "tests/unit/test_app_user_upserts.py" in spec


# ── The R8 half: both statements, both paths, against the replayed ladder ───

@pytest.fixture(scope="module")
def replayed():
    """Build the tenant schema from ``infra/postgres/``, then replay it.

    Applied twice for the same reason ``test_billing_purchase_capability.py``
    does it: the deploy replays the whole ladder every time, and this suite's
    subject is an index that one migration creates and another drops.
    """
    eng = create_engine(_URL, future=True)
    with eng.begin() as conn:
        apply_ladder(conn)
    with eng.begin() as conn:
        apply_ladder(conn)
    yield eng
    eng.dispose()


@pytest.fixture
def conn(replayed):
    """One rolled-back transaction per test — writes never outlive a test.

    The ladder database is shared and module-scoped; the rows are not. Rolling
    back rather than deleting means a test that fails mid-way still leaves
    nothing behind for the next one to trip over.
    """
    with replayed.connect() as connection:
        trans = connection.begin()
        try:
            yield connection
        finally:
            trans.rollback()


def _default_org(conn) -> str:
    return str(
        conn.execute(
            text("SELECT id FROM organization WHERE slug = 'default'")
        ).scalar_one()
    )


def _rows_for(conn, email: str) -> list[tuple]:
    return list(
        conn.execute(
            text(
                "SELECT email, status, organization_id, display_name, joined_at "
                "  FROM app_user WHERE lower(email) = lower(:email) "
                " ORDER BY email"
            ),
            {"email": email},
        )
    )


def _provision(conn, *, email, name, org, status, by="admin@example.com"):
    return conn.execute(
        text(_PROVISION_MEMBER_SQL),
        {"email": email, "name": name, "org": org, "status": status, "by": by},
    )


@_DB_GATE
class TestTheSchemaTheStatementsMustMatch:
    def test_app_user_carries_no_unique_index_on_the_bare_column(self, conn):
        """The fact that makes ``ON CONFLICT (email)`` unsatisfiable.

        Asserted from ``pg_indexes`` rather than from the migration text: what
        an ``ON CONFLICT`` target resolves against is the live catalog.
        """
        defs = [
            row[0]
            for row in conn.execute(
                text(
                    "SELECT indexdef FROM pg_indexes "
                    " WHERE tablename = 'app_user' AND indexdef ILIKE '%UNIQUE%'"
                )
            )
        ]
        bare = [d for d in defs if re.search(r"USING btree \(email\)", d)]
        assert not bare, f"a unique index on bare (email) is back: {bare}"
        assert any("lower(email)" in d for d in defs), (
            f"app_user_email_lower_key is missing; found {defs}"
        )

    def test_the_old_conflict_target_still_raises_42P10(self, conn):
        """The repro, preserved — so this suite cannot go quietly vacuous.

        Every other test here would stay green if somebody made ``(email)``
        work by re-adding the byte-exact constraint, which is exactly the
        "fix a constraint violation against the weaker index" move migration
        162's comment refuses. This one fails in that world.
        """
        from sqlalchemy.exc import ProgrammingError

        old = _PROVISION_MEMBER_SQL.replace(
            "ON CONFLICT (lower(email))", "ON CONFLICT (email)"
        )
        assert "ON CONFLICT (email)" in old
        with pytest.raises(ProgrammingError) as caught, conn.begin_nested():
            conn.execute(
                text(old),
                {
                    "email": "ghost@example.com",
                    "name": "Ghost",
                    "org": _default_org(conn),
                    "status": "invited",
                    "by": "admin@example.com",
                },
            )
        assert caught.value.orig.sqlstate == "42P10"
        assert (
            "there is no unique or exclusion constraint matching the "
            "ON CONFLICT specification" in str(caught.value.orig)
        )


@_DB_GATE
class TestTheOwnershipBootstrapUpsert:
    """``acb_auth.access._BOOTSTRAP_OWNER_SQL`` — both paths."""

    def test_the_fresh_insert_path(self, conn):
        """It failed at PLAN time, so this path was broken too — not just the
        conflict one. That is the difference between "repeat bootstrap is
        noisy" and "no box can ever bootstrap an owner"."""
        email = "slice6.owner@example.com"
        conn.execute(
            text(_BOOTSTRAP_OWNER_SQL), {"email": email, "org_slug": "default"}
        )
        rows = _rows_for(conn, email)
        assert len(rows) == 1
        assert rows[0][1] == "active"
        assert rows[0][2] is not None

    def test_the_conflict_path_updates_and_does_not_duplicate(self, conn):
        email = "slice6.owner@example.com"
        for _ in range(2):
            conn.execute(
                text(_BOOTSTRAP_OWNER_SQL), {"email": email, "org_slug": "default"}
            )
        assert len(_rows_for(conn, email)) == 1

    def test_a_differently_cased_address_lands_on_the_same_row(self, conn):
        """The case the functional index exists for.

        Under the byte-exact index this inserted a SECOND row for one human
        (migration 162's own worked example), and a person's tenant became
        non-deterministic.
        """
        conn.execute(
            text(_BOOTSTRAP_OWNER_SQL),
            {"email": "casey@alpha.example", "org_slug": "default"},
        )
        conn.execute(
            text(_BOOTSTRAP_OWNER_SQL),
            {"email": "Casey@Alpha.Example", "org_slug": "default"},
        )
        rows = _rows_for(conn, "casey@alpha.example")
        assert len(rows) == 1, f"one address, {len(rows)} rows: {rows}"
        assert rows[0][0] == "casey@alpha.example", (
            "the stored spelling was rewritten — 162 deliberately does not "
            "normalise, because audit rows name the address as stored"
        )

    def test_the_owner_grant_lands_exactly_once(self, conn):
        """The statement's second half, which never ran while the first raised."""
        email = "slice6.owner@example.com"
        for _ in range(2):
            conn.execute(
                text(_BOOTSTRAP_OWNER_SQL), {"email": email, "org_slug": "default"}
            )
        held = conn.execute(
            text(
                "SELECT count(*) FROM user_role ur "
                "  JOIN app_user u ON u.id = ur.user_id "
                "  JOIN org_role r ON r.id = ur.role_id AND r.slug = 'owner' "
                " WHERE lower(u.email) = :email"
            ),
            {"email": email},
        ).scalar_one()
        assert held == 1


@_DB_GATE
class TestTheProvisionMemberUpsert:
    """``gateway.routes.admin._common._PROVISION_MEMBER_SQL`` — both paths."""

    def test_the_fresh_insert_path(self, conn):
        org = _default_org(conn)
        _provision(conn, email="slice6.invitee@example.com", name="Invitee",
                   org=org, status="invited")
        rows = _rows_for(conn, "slice6.invitee@example.com")
        assert len(rows) == 1
        assert rows[0][1] == "invited"
        assert rows[0][4] is None, "joined_at is only set on activation"

    def test_the_conflict_path_promotes_invited_to_active(self, conn):
        """The DO UPDATE arm, unchanged: `invited` → the caller's status, and
        `joined_at` filled on the way through (§6 done-when 7)."""
        org = _default_org(conn)
        email = "slice6.invitee@example.com"
        _provision(conn, email=email, name="Invitee", org=org, status="invited")
        _provision(conn, email=email, name="Invitee", org=org, status="active")
        rows = _rows_for(conn, email)
        assert len(rows) == 1
        assert rows[0][1] == "active"
        assert rows[0][4] is not None

    def test_a_removed_row_can_never_be_reactivated_by_this_statement(self, conn):
        """The arm that makes approve one action without making it a
        reinstatement (`removed` → anything but `active`)."""
        org = _default_org(conn)
        email = "slice6.left@example.com"
        _provision(conn, email=email, name="Left", org=org, status="invited")
        conn.execute(
            text("UPDATE app_user SET status = 'removed' WHERE lower(email) = :e"),
            {"e": email},
        )
        _provision(conn, email=email, name="Left", org=org, status="active")
        assert _rows_for(conn, email)[0][1] == "removed"

        _provision(conn, email=email, name="Left", org=org, status="invited")
        assert _rows_for(conn, email)[0][1] == "invited"

    def test_the_display_name_arm_never_blanks_a_stored_name(self, conn):
        org = _default_org(conn)
        email = "slice6.named@example.com"
        _provision(conn, email=email, name="Real Name", org=org, status="invited")
        _provision(conn, email=email, name="", org=org, status="invited")
        assert _rows_for(conn, email)[0][3] == "Real Name"

    def test_a_differently_cased_address_updates_rather_than_duplicating(self, conn):
        """S1-1's write leak, from the other side.

        With the byte-exact index this wrote a second row and the ``WHERE``
        fence below never saw a conflict to refuse. The count is the assertion:
        one address, one row.
        """
        org = _default_org(conn)
        _provision(conn, email="casey@alpha.example", name="Casey", org=org,
                   status="invited")
        _provision(conn, email="Casey@Alpha.Example", name="Casey", org=org,
                   status="active")
        rows = _rows_for(conn, "casey@alpha.example")
        assert len(rows) == 1, f"one address, {len(rows)} rows: {rows}"
        assert rows[0][1] == "active"

    def test_the_tenant_fence_still_refuses_another_organization(self, conn):
        """S1-1's repair must not change meaning (slice 6's own warning).

        The trailing WHERE is the only place a conflicting row can be seen, and
        without it this statement MOVED a person into the inviting admin's
        tenant. Now that a differently-cased address conflicts too, the fence
        is reachable on more inputs than before — so it is worth asserting on
        exactly that input.
        """
        org_one = _default_org(conn)
        org_two = str(
            conn.execute(
                text(
                    "INSERT INTO organization (slug, display_name) "
                    "VALUES ('slice6-second', 'Second Org') RETURNING id"
                )
            ).scalar_one()
        )
        email = "casey@alpha.example"
        _provision(conn, email=email, name="Casey", org=org_one, status="active")

        for attempt in (email, "Casey@Alpha.Example"):
            result = _provision(conn, email=attempt, name="Poached",
                                org=org_two, status="active")
            assert result.rowcount == 0, (
                f"{attempt!r} was written into another tenant — the WHERE "
                "fence on the DO UPDATE arm is the whole of S1-1's repair"
            )

        rows = _rows_for(conn, email)
        assert len(rows) == 1
        assert str(rows[0][2]) == org_one
        assert rows[0][3] == "Casey", "the other tenant rewrote the display name"

    def test_the_ladder_dsn_does_not_leak_out_of_this_suite(self):
        """``DATABASE_URL`` must be untouched by anything above — setting it
        re-arms ``test_tenant_coverage.py``'s two DB-gated tests, which fail by
        construction against a freshly-replayed ladder."""
        assert os.environ.get("DATABASE_URL", "") != _URL
