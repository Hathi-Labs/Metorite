"""WS-29 H6 slice 1 (MT-1a-2) — the identity-shadow dual-write + catch-up backfill.

Spec: ``project-docs/specs/saas_multitenancy_handover.md`` §H6 ·
``saas_multitenancy.md`` §11 / §1.5 · D15.

**What this slice is.** After H3's phase-4 RLS, identity resolution reads
``app_user`` on an UNBOUND session and bricks; H6 moves it onto the RLS-EXEMPT
tables ``user_identity`` + ``org_membership``. Before ANY read can move, those
tables must be COMPLETE and CURRENT. Migration 159 seeded them ONCE; nothing kept
them current since. Slice 1 closes that gap WITHOUT moving a read — it is DARK:

* a **dual-write** on the two ``app_user`` write paths
  (``acb_auth.access.mirror_identity_membership``, invoked from
  ``_BOOTSTRAP_OWNER_SQL``'s caller and ``_PROVISION_MEMBER_SQL``'s), and
* a **catch-up backfill** migration that re-runs 159's idempotent seed for every
  member added since.

``app_user`` stays authoritative and its writes are byte-identical; no read moves.

⚠️ **The invariant this suite fences (§H6:672-674).** The invite path is "an
account-takeover primitive under two orgs": one email may hold membership in TWO
organizations, so ``org_membership`` is a CREATE-ONLY insert keyed on
``(organization_id, user_id)`` and an identity is NEVER moved between orgs. The
two-org membership test IS that fence (R7).

Two halves, and the split is deliberate (the ``test_app_user_upserts.py`` model):

* the **structural** half takes no database and must never skip — it pins the
  create-only guard, the functional-index target, the app_user statements staying
  un-polluted, and the backfill's idempotent shape; and
* the **R8** half answers to ``TENANT_LADDER_DATABASE_URL`` — never to
  ``DATABASE_URL`` (``test_app_user_upserts.py`` records the reason at length) —
  and proves against a REAL Postgres that one email holds membership in two orgs,
  that re-inviting never rewrites the first, and that the backfill reconciles and
  is idempotent. A fake agrees with any SQL it is handed, which is precisely how
  slice 6's ``ON CONFLICT (email)`` shipped green (R8).

Run::

    export TENANT_LADDER_DATABASE_URL=postgresql+psycopg://acb:acb@127.0.0.1:5443/acb_tenant
    uv run pytest tests/unit/test_h6_identity_shadow.py -v -rs
"""
from __future__ import annotations

import inspect
import os
import re
import uuid
from pathlib import Path

import pytest

pytest.importorskip("sqlalchemy")

#: The subjects are IMPORTED, never transcribed — a copy here would keep passing
#: after somebody edited the real statement.
from acb_auth.access import (
    _BOOTSTRAP_OWNER_SQL,
    _MIRROR_IDENTITY_SQL,
    _MIRROR_MEMBERSHIP_SQL,
    _MIRROR_ORG_BY_SLUG_SQL,
    _MIRROR_STATUS_SQL,
    _PURGE_MEMBERSHIP_SQL,
    mirror_identity_membership,
    mirror_membership_status,
    purge_identity_shadow,
)
from gateway.routes.admin._common import _PROVISION_MEMBER_SQL
from sqlalchemy import create_engine, text

from tests.unit._tenant_ladder import apply_ladder, tenant_engine_scope

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
_LADDER_DIR = _ROOT / "infra" / "postgres"


# ── The structural half: no database, and it must never skip ────────────────

def _backfill_migration() -> Path:
    """The catch-up migration, found BY CONTENT never by number (R1).

    159 and the catch-up are the only two ``.sql`` files that seed BOTH shadow
    tables from ``app_user``. 159 also ``CREATE TABLE``s them; the catch-up only
    seeds — that is the content discriminator, so a merge renumber cannot make
    this fence point at the wrong file or go vacuous.
    """
    seeds = []
    for path in sorted(_LADDER_DIR.glob("*.sql")):
        if not re.match(r"^\d+_", path.name):
            continue
        body = path.read_text(encoding="utf-8")
        if "INSERT INTO user_identity" in body and "INSERT INTO org_membership" in body:
            seeds.append((path, body))
    catchup = [p for p, body in seeds if "CREATE TABLE" not in body]
    assert len(catchup) == 1, (
        "expected exactly one catch-up backfill migration (seeds both shadow "
        f"tables, creates neither); found {[p.name for p in catchup]}"
    )
    return catchup[0]


def _reconcile_migration() -> Path:
    """The status reconcile migration, found BY CONTENT never by number (R1).

    It is the only ``.sql`` file that UPDATEs ``org_membership.status`` from
    ``app_user`` under an ``IS DISTINCT FROM`` guard — that trio is the content
    discriminator, so a merge renumber cannot make this fence point at the wrong
    file or go vacuous.
    """
    matches = []
    for path in sorted(_LADDER_DIR.glob("*.sql")):
        if not re.match(r"^\d+_", path.name):
            continue
        body = path.read_text(encoding="utf-8")
        if (
            "UPDATE org_membership" in body
            and "app_user" in body
            and "IS DISTINCT FROM" in body
        ):
            matches.append(path)
    assert len(matches) == 1, (
        "expected exactly one status-reconcile migration (UPDATEs "
        "org_membership.status from app_user, guarded by IS DISTINCT FROM); "
        f"found {[p.name for p in matches]}"
    )
    return matches[0]


def _set_clause(sql: str) -> str:
    """The assignment list of an UPDATE — everything between SET and the first
    of FROM/WHERE. Isolating it is what lets a fence assert the key columns are
    never WRITTEN even though they legitimately appear in the predicate."""
    after_set = re.split(r"\bSET\b", sql, maxsplit=1, flags=re.IGNORECASE)[1]
    return re.split(r"\bFROM\b|\bWHERE\b", after_set, maxsplit=1,
                    flags=re.IGNORECASE)[0]


def _sql_only(body: str) -> str:
    """The executable SQL of a migration, with ``--`` comment content stripped.

    A header comment may legitimately NAME a column to say it is NOT touched
    (e.g. ``resolved_at``); a fence must read the statements, not the prose."""
    lines = []
    for line in body.splitlines():
        code = re.split(r"--", line, maxsplit=1)[0]
        if code.strip():
            lines.append(code)
    return "\n".join(lines)


class TestTheDualWriteShape:
    """The ratchet. Breaking the invariant must fail here even without a database."""

    def test_the_membership_mirror_is_create_only(self):
        """§H6:672-674: INSERT, never an UPDATE of an identity's org.

        ``ON CONFLICT (organization_id, user_id) DO NOTHING`` and NO
        ``organization_id`` in any SET clause — the account-takeover primitive is
        exactly ``DO UPDATE SET organization_id = …``.
        """
        assert re.search(
            r"ON\s+CONFLICT\s*\(\s*organization_id\s*,\s*user_id\s*\)\s*DO\s+NOTHING",
            _MIRROR_MEMBERSHIP_SQL,
            re.IGNORECASE,
        ), "the membership mirror is not create-only on (organization_id, user_id)"
        assert "DO UPDATE" not in _MIRROR_MEMBERSHIP_SQL.upper(), (
            "the membership mirror rewrites on conflict — it must DO NOTHING"
        )
        assert not re.search(
            r"SET[\s\S]*organization_id", _MIRROR_MEMBERSHIP_SQL, re.IGNORECASE
        ), "the membership mirror sets organization_id — it must never move an org"

    def test_the_identity_mirror_targets_the_functional_index(self):
        """One row per ``lower(email)`` (162's index; R10)."""
        assert re.search(
            r"ON\s+CONFLICT\s*\(\s*lower\(email\)\s*\)", _MIRROR_IDENTITY_SQL,
            re.IGNORECASE,
        ), "the identity mirror does not target (lower(email))"

    def test_the_app_user_statements_are_not_polluted_by_the_dual_write(self):
        """The regression fence: ``app_user`` writes stay byte-identical.

        The dual-write is purely additive and lives in a SEPARATE statement on a
        SEPARATE session, so neither authoritative upsert may name a shadow
        table. If one does, the ``app_user`` write is no longer what it was.
        """
        for label, sql in (
            ("_BOOTSTRAP_OWNER_SQL", _BOOTSTRAP_OWNER_SQL),
            ("_PROVISION_MEMBER_SQL", _PROVISION_MEMBER_SQL),
        ):
            assert "user_identity" not in sql, f"{label} now names user_identity"
            assert "org_membership" not in sql, f"{label} now names org_membership"

    def test_the_mirror_is_best_effort(self):
        """Mirrors ``_record_signin_request``: it catches and never re-raises, so
        a failed shadow write can never break the authoritative ``app_user`` one."""
        src = inspect.getsource(mirror_identity_membership)
        assert "except Exception" in src, "the mirror does not swallow its errors"
        # No bare `raise` anywhere in the body — a re-raise would defeat the
        # best-effort posture and could surface on the app_user write path.
        assert not re.search(r"\braise\b", src), (
            "the mirror re-raises — it must be best-effort/log-and-continue"
        )

    def test_the_backfill_reruns_159s_seed_idempotently(self):
        """The catch-up is additive and never rewrites (R6)."""
        body = _backfill_migration().read_text(encoding="utf-8")
        assert body.count("ON CONFLICT") >= 2, (
            "the backfill's two inserts are not both conflict-guarded"
        )
        assert "DO NOTHING" in body, "the backfill is not idempotent (no DO NOTHING)"
        assert "CREATE TABLE" not in body, (
            "the backfill creates a table — it must only seed (R5: no new table)"
        )
        assert not re.search(r"SET[\s\S]*organization_id", body, re.IGNORECASE), (
            "the backfill rewrites organization_id — it must never move an org"
        )
        # No `role` column on the tenant-plane org_membership — do not invent one.
        assert "role" not in body.lower().split("org_membership", 1)[-1][:400], (
            "the backfill references a role column org_membership does not have"
        )

    def test_the_status_mirror_is_a_scoped_update_never_a_move(self):
        """§H6:672-674 (slice 3a, D48): the forward status mirror sets ONLY
        ``status`` (+ ``joined_at`` on activation) and NEVER the key columns, so
        it can never move an identity between orgs — the account-takeover
        primitive is ``SET organization_id`` / ``SET user_id``.
        """
        assert _MIRROR_STATUS_SQL.strip().upper().startswith("UPDATE"), (
            "the status mirror is not an UPDATE — existence is not its job"
        )
        set_clause = _set_clause(_MIRROR_STATUS_SQL)
        assert not re.search(r"\borganization_id\b", set_clause, re.IGNORECASE), (
            "the status mirror writes organization_id — it must never move an org"
        )
        assert not re.search(r"\buser_id\b", set_clause, re.IGNORECASE), (
            "the status mirror writes user_id — it must never move an identity"
        )
        # Create-only stays the existence path's job — the status path must
        # never mint or delete a row.
        assert "INSERT" not in _MIRROR_STATUS_SQL.upper(), (
            "the status mirror INSERTs — it must be a scoped UPDATE only"
        )
        assert "DELETE" not in _MIRROR_STATUS_SQL.upper(), (
            "the status mirror DELETEs — it must be a scoped UPDATE only"
        )
        # The authoritative write stays byte-identical: the mirror is a separate
        # statement on a separate session and names no `app_user`.
        assert "app_user" not in _MIRROR_STATUS_SQL, (
            "the status mirror names app_user — the authoritative write must "
            "stay byte-identical on its own statement/session"
        )

    def test_the_status_mirror_is_best_effort(self):
        """Mirrors ``mirror_identity_membership``: catches and never re-raises,
        so a failed shadow write can never break the authoritative one."""
        src = inspect.getsource(mirror_membership_status)
        assert "except Exception" in src, "the status mirror does not swallow errors"
        assert not re.search(r"\braise\b", src), (
            "the status mirror re-raises — it must be best-effort/log-and-continue"
        )

    def test_the_reconcile_is_status_only_forward_only_and_idempotent_by_shape(self):
        """The reconcile migration (183) aligns status and nothing else (R6)."""
        sql = _sql_only(_reconcile_migration().read_text(encoding="utf-8"))
        assert "IS DISTINCT FROM" in sql, (
            "the reconcile has no distinct guard — a re-run would rewrite rows "
            "(not idempotent)"
        )
        set_clause = _set_clause(sql)
        assert not re.search(r"\borganization_id\b", set_clause, re.IGNORECASE), (
            "the reconcile writes organization_id — it must never move an org"
        )
        assert not re.search(r"\buser_id\b", set_clause, re.IGNORECASE), (
            "the reconcile writes user_id — it must never move an identity"
        )
        assert "INSERT INTO org_membership" not in sql, (
            "the reconcile INSERTs a membership — existence is 159/182's job"
        )
        assert "DELETE FROM org_membership" not in sql, (
            "the reconcile DELETEs a membership — it must never remove a row"
        )
        assert "CREATE TABLE" not in sql.upper(), (
            "the reconcile creates a table — it must only align status (R5)"
        )
        assert "resolved_at" not in sql.lower(), (
            "the reconcile touches resolved_at — that column is console_resolve's"
        )

    def test_this_suite_is_named_in_the_ci_skip_guard(self):
        """The hand-list discovers NOTHING (pr-check.yml's own warning): an R8
        suite absent from it skips silently and leaves the job green."""
        workflow = (_ROOT / ".github/workflows/pr-check.yml").read_text(
            encoding="utf-8")
        assert "tests/unit/test_h6_identity_shadow.py" in workflow

    def test_the_owning_spec_names_this_suite(self):
        spec = (_ROOT / "project-docs/specs/saas_multitenancy_handover.md").read_text(
            encoding="utf-8")
        assert "test_h6_identity_shadow.py" in spec


# ── The orphan-closure slice: post-commit ordering, purge shape, pinned SQL ──
#
# WS-29 H6 orphan-closure (D48). Three named fences (R7), none needs a database:
#   * post-commit-ordering — the invite/approve mirror runs only AFTER its
#     `_tenant_session` commits (moved OUT of `provision_member`), so a rolled-
#     back caller txn commits NO active shadow orphan;
#   * purge-removes-shadow / no-user_identity-delete-on-purge (shape) — a member
#     purge deletes ONLY the org's `org_membership` row and NEVER touches the
#     GLOBAL `user_identity` (deleting it on the last membership was a
#     check-then-cascade cross-tenant erasure race; the R8 proof is
#     `TestThePurgeClosure` below);
#   * constant-equality — the identity-write SQL is byte-identical to
#     `console_resolve`'s (a TEST importing it does not trip that module's
#     PRODUCTION importer cap — `test_console_dependency_boundary` sweeps
#     `packages/` + `apps/`, never `tests/`).


def _tenant_block_end(lines: list[str], with_idx: int, with_indent: int) -> int:
    """Index of the first non-blank line dedented back OUT of the
    `_tenant_session` block opened at ``with_idx`` — i.e. where the block (and
    its single commit) ends. ``len(lines)`` if it never dedents."""
    for i in range(with_idx + 1, len(lines)):
        if not lines[i].strip():
            continue
        if (len(lines[i]) - len(lines[i].lstrip())) <= with_indent:
            return i
    return len(lines)


def _all_calls_are_post_commit(src: str, call: str) -> bool:
    """Every occurrence of ``call`` sits AFTER the one `_tenant_session` block
    ends — never inside the still-open transaction."""
    lines = src.splitlines()
    with_lines = [
        i for i, ln in enumerate(lines) if "async with _tenant_session()" in ln
    ]
    assert len(with_lines) == 1, (
        f"expected exactly one _tenant_session block; found {len(with_lines)}"
    )
    with_idx = with_lines[0]
    with_indent = len(lines[with_idx]) - len(lines[with_idx].lstrip())
    end_idx = _tenant_block_end(lines, with_idx, with_indent)
    hits = [i for i, ln in enumerate(lines) if call in ln]
    assert hits, f"{call} is not called at all"
    return all(i >= end_idx for i in hits)


class TestTheOrphanClosure:
    """The ratchet for the orphan-closure slice — no database required."""

    def test_provision_member_does_not_mirror_inside_its_transaction(self):
        """The SOURCE of the APPROVE-ROLLBACK + create-only over-count orphans:
        the mirror used to commit on its OWN session INSIDE the caller's
        still-open txn, so a concurrent-approve 409 (`access_requests._decide`)
        rolled back `app_user` while the shadow stayed. `provision_member` must
        now name NEITHER mirror — its callers own the mirror, post-commit."""
        from gateway.routes.admin._common import provision_member

        src = inspect.getsource(provision_member)
        assert "mirror_identity_membership(" not in src, (
            "provision_member still mirrors inside the caller's transaction"
        )
        assert "mirror_membership_status(" not in src, (
            "provision_member still mirrors inside the caller's transaction"
        )

    def test_invite_mirrors_only_after_its_tenant_session_commits(self):
        """`members.invite_member` calls both mirrors, and every call is AFTER
        its `_tenant_session` block — so a rolled-back invite commits no
        shadow."""
        from gateway.routes.admin.members import invite_member

        src = inspect.getsource(invite_member)
        assert _all_calls_are_post_commit(src, "mirror_identity_membership(")
        assert _all_calls_are_post_commit(src, "mirror_membership_status(")

    def test_approve_mirrors_only_after_its_tenant_session_commits(self):
        """`access_requests.approve_access_request` mirrors only post-commit —
        the concurrent-approve 409 in `_decide` (and the `status != active`
        guard) rolls the whole block back BEFORE this point, so no active shadow
        orphan is ever committed. This IS the fence for acceptance's
        'the mirror is NOT called until after the authoritative commit'."""
        from gateway.routes.admin.access_requests import approve_access_request

        src = inspect.getsource(approve_access_request)
        assert _all_calls_are_post_commit(src, "mirror_identity_membership(")
        assert _all_calls_are_post_commit(src, "mirror_membership_status(")

    def test_purge_deletes_the_shadow_after_its_transaction_commits(self):
        """`members.purge_member` closes the PURGE orphan, and does so AFTER the
        irreversible purge committed — a failed shadow delete cannot roll it
        back (best-effort, own session)."""
        from gateway.routes.admin.members import purge_member

        src = inspect.getsource(purge_member)
        assert _all_calls_are_post_commit(src, "purge_identity_shadow(")

    def test_the_purge_membership_delete_is_scoped_to_one_org(self):
        """It deletes at most THIS org's membership for the identity — never
        another org's row for the same human — resolving the identity by
        `lower(email)` (162's functional index; R10)."""
        sql = _PURGE_MEMBERSHIP_SQL
        assert sql.strip().upper().startswith("DELETE FROM ORG_MEMBERSHIP"), (
            "the membership purge is not a DELETE on org_membership"
        )
        assert re.search(
            r"organization_id\s*=\s*CAST\(:org", sql, re.IGNORECASE
        ), (
            "the membership purge is not scoped to one organization — it would "
            "delete the identity's membership in EVERY org"
        )
        assert re.search(
            r"lower\(ui\.email\)\s*=\s*lower\(:email\)", sql, re.IGNORECASE
        ), "the identity is not resolved by lower(email)"
        assert "app_user" not in sql, (
            "the shadow purge names app_user — it must touch only the shadow"
        )

    def test_the_purge_never_deletes_user_identity(self):
        """R7 fence `no-user_identity-delete-on-purge`: the purge path touches
        ONLY `org_membership`. Deleting the GLOBAL `user_identity` on the human's
        LAST membership was a check-then-cascade RACE — a concurrent (other-org,
        same-human) membership committed between the `NOT EXISTS` re-eval and the
        delete was CASCADE-erased (org A's purge wiping org B's member). The race
        is eliminated BY CONSTRUCTION: no `user_identity` DELETE exists in the
        purge path at all — no constant, no statement. A membership-less identity
        is harmless (the identity leg reads `user_identity ⋈ org_membership` → no
        org → no access) and re-used on re-join via `ON CONFLICT (lower(email))`.
        """
        import acb_auth.access as access_mod

        assert not hasattr(access_mod, "_PURGE_IDENTITY_IF_ORPHAN_SQL"), (
            "a user_identity-delete purge constant is back — that reintroduces "
            "the check-then-cascade cross-tenant erasure race"
        )

        src = inspect.getsource(purge_identity_shadow)
        assert "_PURGE_IDENTITY_IF_ORPHAN_SQL" not in src, (
            "purge_identity_shadow still references the removed identity-delete SQL"
        )
        assert not re.search(r"DELETE\s+FROM\s+user_identity", src, re.IGNORECASE), (
            "purge_identity_shadow deletes user_identity — the cross-tenant "
            "erasure race is back"
        )
        assert src.count("session.execute(") == 1, (
            "purge_identity_shadow issues more than the single membership DELETE"
        )
        # The one statement it does issue is a DELETE on org_membership, never
        # on user_identity (which it only JOINs to resolve the email).
        assert re.search(
            r"DELETE\s+FROM\s+org_membership", _PURGE_MEMBERSHIP_SQL, re.IGNORECASE
        ), "the membership purge is not a DELETE on org_membership"
        assert not re.search(
            r"DELETE\s+FROM\s+user_identity", _PURGE_MEMBERSHIP_SQL, re.IGNORECASE
        ), "the membership purge deletes user_identity"

    def test_the_purge_shadow_is_best_effort(self):
        """Mirrors `mirror_identity_membership`: catches and never re-raises, so
        a failed shadow delete can never roll back the irreversible purge."""
        src = inspect.getsource(purge_identity_shadow)
        assert "except Exception" in src, "the purge shadow does not swallow errors"
        assert not re.search(r"\braise\b", src), (
            "the purge shadow re-raises — it must be best-effort/log-and-continue"
        )

    def test_the_identity_write_sql_is_pinned_equal_to_the_console_upserts(self):
        """§H6 cutover-checklist item 4: the two identity/org SQL constants are
        byte-identical to `console_resolve`'s and MUST stay so — a silent drift
        would split the tenant-plane identity write from the Console projection's
        upsert. A module-level import is blocked by
        `test_console_dependency_boundary`'s PRODUCTION importer cap, but that
        sweep never reads `tests/`, so pinning the equality from HERE is the
        alternative that spec §H6 names to lifting them to a shared module."""
        from acb_auth.console_resolve import (
            _ORG_BY_SLUG_SQL,
            _UPSERT_IDENTITY_SQL,
        )

        assert _MIRROR_IDENTITY_SQL == _UPSERT_IDENTITY_SQL, (
            "the identity mirror upsert drifted from console_resolve's"
        )
        assert _MIRROR_ORG_BY_SLUG_SQL == _ORG_BY_SLUG_SQL, (
            "the org-by-slug read drifted from console_resolve's"
        )


# ── The read-cutover flag (slice 3b) — no database, must never skip ──────────
#
# The DARK read cutover: `resolve_identity` reads the RLS-EXEMPT
# `user_identity ⋈ org_membership` under `IDENTITY_CUTOVER`, and is byte-
# identical to today (the `app_user` read) while the flag is unset. The R8
# red/green + suspended-not-admitted fences live in
# `test_h3_rls_promotion_rehearsal.py` (they need the phase-4-promoted RLS
# catalog); this DB-free fence pins the FLAG SWITCH itself — which source each
# state reads — so a change to the dark default fails here without a database.


class _SqlCapture:
    """A minimal session factory that records the SQL a call issues and returns
    an empty result — enough to see which statement `resolve_identity` chose."""

    def __init__(self) -> None:
        self.statements: list[str] = []

    def __call__(self) -> _SqlCapture:
        return self

    async def __aenter__(self) -> _SqlCapture:
        return self

    async def __aexit__(self, *exc: object) -> None:
        return None

    async def execute(self, sql: object, params: dict | None = None) -> object:
        self.statements.append(" ".join(str(sql).split()))

        class _Result:
            def mappings(self) -> object:
                class _M:
                    def first(self) -> None:
                        return None

                    def all(self) -> list:
                        # The flag-ON identity leg fetches up to two rows
                        # (P2a multi-org-safe COUNT) via `.all()`.
                        return []

                return _M()

        return _Result()


class _RowsCapture:
    """A session factory returning a fixed set of mapping rows — enough to
    exercise `resolve_identity`'s flag-ON COUNT decision (P2a) without a DB."""

    def __init__(self, rows: list[dict]) -> None:
        self._rows = rows

    def __call__(self) -> _RowsCapture:
        return self

    async def __aenter__(self) -> _RowsCapture:
        return self

    async def __aexit__(self, *exc: object) -> None:
        return None

    async def execute(self, sql: object, params: dict | None = None) -> object:
        rows = self._rows

        class _Result:
            def mappings(self) -> object:
                class _M:
                    def all(self) -> list:
                        return rows

                    def first(self) -> dict | None:
                        return rows[0] if rows else None

                return _M()

        return _Result()


class TestTheReadCutoverFlag:
    """The ratchet for slice 3b's flag switch — no database required."""

    async def test_resolve_identity_reads_app_user_when_the_flag_is_off(
        self, monkeypatch
    ):
        """Flag OFF is byte-identical to today: `resolve_identity` issues the
        `app_user` statement and NEVER touches the shadow — the dark default."""
        import acb_auth.access as access_mod

        monkeypatch.delenv("IDENTITY_CUTOVER", raising=False)
        cap = _SqlCapture()
        monkeypatch.setattr(access_mod, "_get_session_factory", lambda: cap)

        await access_mod.resolve_identity("someone@example.com")

        assert len(cap.statements) == 1
        assert "FROM app_user" in cap.statements[0], (
            "flag OFF must read app_user — the dark default changed"
        )
        assert "user_identity" not in cap.statements[0]
        assert "org_membership" not in cap.statements[0]

    async def test_resolve_identity_reads_the_exempt_shadow_when_the_flag_is_on(
        self, monkeypatch
    ):
        """Flag ON: `resolve_identity` issues the identity-leg join over the
        RLS-EXEMPT shadow tables and NEVER app_user (the brick it replaces)."""
        import acb_auth.access as access_mod

        monkeypatch.setenv("IDENTITY_CUTOVER", "1")
        cap = _SqlCapture()
        monkeypatch.setattr(access_mod, "_get_session_factory", lambda: cap)

        await access_mod.resolve_identity("someone@example.com")

        assert len(cap.statements) == 1
        stmt = cap.statements[0]
        assert "user_identity" in stmt and "org_membership" in stmt
        assert "status = 'active'" in stmt
        assert "app_user" not in stmt, (
            "flag ON must read the exempt shadow, never app_user (the brick)"
        )

    def test_the_flag_defaults_off_and_reads_the_env_idiom(self, monkeypatch):
        """UNSET = OFF, fail-closed, and only the truthy tokens flip it on — the
        same idiom as `deps._refuse_llm_key_identity`."""
        from acb_auth.access import identity_cutover_enabled

        monkeypatch.delenv("IDENTITY_CUTOVER", raising=False)
        assert identity_cutover_enabled() is False
        for off in ("", "0", "no", "off", "false", "  ", "maybe"):
            monkeypatch.setenv("IDENTITY_CUTOVER", off)
            assert identity_cutover_enabled() is False, f"{off!r} enabled it"
        for on in ("1", "true", "yes", "on", "TRUE", " On "):
            monkeypatch.setenv("IDENTITY_CUTOVER", on)
            assert identity_cutover_enabled() is True, f"{on!r} did not enable it"

    async def test_a_single_active_membership_binds_when_the_flag_is_on(
        self, monkeypatch
    ):
        """P2a, the common case (DB-free): exactly ONE active membership binds
        it. The R8 twin (two real memberships) lives in
        `test_h3_rls_promotion_rehearsal.py::TestTheReadCutoverEndToEnd`."""
        import acb_auth.access as access_mod

        monkeypatch.setenv("IDENTITY_CUTOVER", "1")
        monkeypatch.setattr(
            access_mod, "_get_session_factory",
            lambda: _RowsCapture([{"id": "uid-1", "org": "org-1"}]))

        user_id, org = await access_mod.resolve_identity("solo@example.com")
        assert (user_id, org) == ("uid-1", "org-1"), (
            "a single active membership must bind — the common case broke")

    async def test_a_multi_org_human_is_denied_not_bound_when_the_flag_is_on(
        self, monkeypatch
    ):
        """P2a, the safety property (DB-free): TWO active memberships resolve to
        `(None, None)` — the deny / chooser case — NEVER an arbitrary bind."""
        import acb_auth.access as access_mod

        monkeypatch.setenv("IDENTITY_CUTOVER", "1")
        monkeypatch.setattr(
            access_mod, "_get_session_factory",
            lambda: _RowsCapture([
                {"id": "uid-a", "org": "org-a"},
                {"id": "uid-b", "org": "org-b"},
            ]))

        user_id, org = await access_mod.resolve_identity("multi@example.com")
        assert (user_id, org) == (None, None), (
            "a human with two active memberships was bound instead of denied "
            "(P2a — the identity leg must never arbitrarily pick one)")


# ── The R8 half: the dual-write + backfill against the replayed ladder ───────

@pytest.fixture(scope="module")
def replayed():
    """Build the tenant schema from ``infra/postgres/`` (incl. the catch-up
    migration), then replay it — twice, to prove replay-safety like the deploy."""
    eng = create_engine(_URL, future=True)
    with eng.begin() as conn:
        apply_ladder(conn)
    with eng.begin() as conn:
        apply_ladder(conn)
    yield eng
    eng.dispose()


@pytest.fixture
def conn(replayed):
    """One rolled-back transaction per test — writes never outlive a test."""
    with replayed.connect() as connection:
        trans = connection.begin()
        try:
            yield connection
        finally:
            trans.rollback()


def _new_org(conn, slug: str) -> str:
    return str(
        conn.execute(
            text(
                "INSERT INTO organization (slug, display_name) "
                "VALUES (:s, :s) RETURNING id"
            ),
            {"s": slug},
        ).scalar_one()
    )


def _mirror_identity(conn, email: str, name: str = "") -> str:
    return str(
        conn.execute(
            text(_MIRROR_IDENTITY_SQL), {"email": email, "name": name}
        ).mappings().one()["id"]
    )


def _mirror_membership(conn, org: str, uid: str, status: str = "active") -> int:
    return conn.execute(
        text(_MIRROR_MEMBERSHIP_SQL), {"org": org, "uid": uid, "status": status}
    ).rowcount


def _identity_count(conn, email: str) -> int:
    return conn.execute(
        text("SELECT count(*) FROM user_identity WHERE lower(email) = lower(:e)"),
        {"e": email},
    ).scalar_one()


def _membership_orgs(conn, uid: str) -> list[str]:
    return [
        str(r[0])
        for r in conn.execute(
            text(
                "SELECT organization_id FROM org_membership "
                " WHERE user_id = CAST(:u AS uuid) ORDER BY organization_id"
            ),
            {"u": uid},
        )
    ]


def _membership_for_org(conn, org: str) -> int:
    return conn.execute(
        text(
            "SELECT count(*) FROM org_membership "
            " WHERE organization_id = CAST(:o AS uuid)"
        ),
        {"o": org},
    ).scalar_one()


def _run_backfill(conn) -> None:
    """Execute the catch-up migration file verbatim, inside ``conn``'s txn.

    Via the DBAPI cursor for the reason ``_tenant_ladder._exec_file`` documents:
    one file, many statements, and no client-side ``%`` placeholder pass.
    """
    sql = _backfill_migration().read_text(encoding="utf-8")
    with conn.connection.dbapi_connection.cursor() as cur:
        cur.execute(sql)


def _mirror_status(conn, org: str, email: str, status: str) -> int:
    """Drive ``_MIRROR_STATUS_SQL`` and return the rows it touched."""
    return conn.execute(
        text(_MIRROR_STATUS_SQL),
        {"org": org, "email": email, "status": status},
    ).rowcount


def _membership_status(conn, org: str, uid: str) -> str | None:
    row = conn.execute(
        text(
            "SELECT status FROM org_membership "
            " WHERE organization_id = CAST(:o AS uuid) "
            "   AND user_id = CAST(:u AS uuid)"
        ),
        {"o": org, "u": uid},
    ).first()
    return None if row is None else row[0]


def _app_user_row(conn, email: str) -> tuple:
    """Every column of an ``app_user`` row, for a byte-identical comparison."""
    return conn.execute(
        text("SELECT * FROM app_user WHERE lower(email) = lower(:e)"),
        {"e": email},
    ).one()


def _run_reconcile(conn) -> int:
    """Execute the reconcile migration (183) verbatim; return rows changed."""
    sql = _reconcile_migration().read_text(encoding="utf-8")
    with conn.connection.dbapi_connection.cursor() as cur:
        cur.execute(sql)
        return cur.rowcount


def _purge_membership(conn, org: str, email: str) -> int:
    """Drive ``_PURGE_MEMBERSHIP_SQL`` and return the rows it removed."""
    return conn.execute(
        text(_PURGE_MEMBERSHIP_SQL), {"org": org, "email": email}
    ).rowcount


@_DB_GATE
class TestOneEmailInTwoOrgs:
    """§H6 done-when 2 + 3 — the load-bearing invariant, against real Postgres."""

    def test_one_email_holds_membership_in_two_orgs(self, conn):
        org_a = _new_org(conn, "h6-two-a")
        org_b = _new_org(conn, "h6-two-b")
        email = "shared.human@h6.example"

        uid = _mirror_identity(conn, email, "Shared Human")
        assert _mirror_membership(conn, org_a, uid) == 1
        assert _mirror_membership(conn, org_b, uid) == 1

        assert _identity_count(conn, email) == 1, "one email must be one identity"
        orgs = _membership_orgs(conn, uid)
        assert set(orgs) == {org_a, org_b}, (
            f"one identity must hold TWO memberships; got {orgs}"
        )
        assert len(orgs) == 2

    def test_re_inviting_into_a_different_org_never_rewrites_the_first(self, conn):
        """The create-only guard: a second org INSERTs a second row and the first
        is left EXACTLY as it was — the identity is never moved."""
        org_a = _new_org(conn, "h6-move-a")
        org_b = _new_org(conn, "h6-move-b")
        email = "poach.target@h6.example"
        uid = _mirror_identity(conn, email)

        assert _mirror_membership(conn, org_a, uid, status="active") == 1
        # Re-mirroring the SAME (org, uid) writes nothing — create-only.
        assert _mirror_membership(conn, org_a, uid, status="invited") == 0, (
            "the second mirror into the SAME org rewrote the row"
        )
        # A DIFFERENT org is a second membership, not a move.
        assert _mirror_membership(conn, org_b, uid) == 1
        assert _membership_orgs(conn, uid) == sorted([org_a, org_b])

        # org A's row is byte-unchanged — still 'active', still org A.
        row = conn.execute(
            text(
                "SELECT status, organization_id::text FROM org_membership "
                " WHERE user_id = CAST(:u AS uuid) "
                "   AND organization_id = CAST(:o AS uuid)"
            ),
            {"u": uid, "o": org_a},
        ).one()
        assert row[0] == "active", "org A's membership status was rewritten"
        assert row[1] == org_a, "the identity was moved between orgs"

    def test_the_identity_mirror_keeps_one_row_per_email(self, conn):
        """A UPN case change must not mint a second human (R10)."""
        uid1 = _mirror_identity(conn, "casey@alpha.example", "Casey")
        uid2 = _mirror_identity(conn, "Casey@Alpha.Example", "")
        assert uid1 == uid2, "a case-variant address minted a second identity"
        assert _identity_count(conn, "casey@alpha.example") == 1


@_DB_GATE
class TestTheCatchUpBackfill:
    """§H6 done-when: the backfill reconciles and is idempotent (R6)."""

    def _seed_app_user(self, conn, org: str, email: str, status: str = "active"):
        conn.execute(
            text(
                "INSERT INTO app_user (email, display_name, role, status, "
                "                      organization_id, joined_at) "
                "VALUES (:e, :e, 'employee', :s, CAST(:o AS uuid), now())"
            ),
            {"e": email, "s": status, "o": org},
        )

    def test_the_backfill_reconciles_members_added_since_159(self, conn):
        """Members added AFTER 159 (no dual-write) get shadow rows on backfill."""
        org = _new_org(conn, "h6-backfill")
        emails = [f"bf-{i}@h6.example" for i in range(4)]
        for e in emails:
            self._seed_app_user(conn, org, e)
        # Before the backfill: no shadow rows for these post-159 members.
        assert _membership_for_org(conn, org) == 0

        _run_backfill(conn)

        assert _membership_for_org(conn, org) == 4, (
            "count(org_membership) must reconcile to app_user rows with a tenant"
        )
        for e in emails:
            assert _identity_count(conn, e) == 1

    def test_the_backfill_is_idempotent(self, conn):
        """Re-run = zero net change, and no existing row is rewritten."""
        org = _new_org(conn, "h6-idem")
        self._seed_app_user(conn, org, "idem@h6.example")
        _run_backfill(conn)
        first = _membership_for_org(conn, org)
        created = conn.execute(
            text(
                "SELECT created_at FROM org_membership "
                " WHERE organization_id = CAST(:o AS uuid)"
            ),
            {"o": org},
        ).scalar_one()

        _run_backfill(conn)
        assert _membership_for_org(conn, org) == first == 1, (
            "the re-run changed the membership count — not idempotent"
        )
        created_again = conn.execute(
            text(
                "SELECT created_at FROM org_membership "
                " WHERE organization_id = CAST(:o AS uuid)"
            ),
            {"o": org},
        ).scalar_one()
        assert created_again == created, "the re-run rewrote an existing row"

    def test_the_dual_write_and_the_backfill_converge(self, conn):
        """A dual-written row is not double-counted by a later backfill."""
        org = _new_org(conn, "h6-converge")
        email = "converge@h6.example"
        self._seed_app_user(conn, org, email)
        # The dual-write already mirrored this member.
        uid = _mirror_identity(conn, email, "Converge")
        _mirror_membership(conn, org, uid)
        assert _membership_for_org(conn, org) == 1

        _run_backfill(conn)
        assert _membership_for_org(conn, org) == 1, (
            "the backfill duplicated a row the dual-write already wrote"
        )

    def test_the_ladder_dsn_does_not_leak_out_of_this_suite(self):
        """``DATABASE_URL`` must be untouched — setting it re-arms
        ``test_tenant_coverage.py``'s two DB-gated tests."""
        assert os.environ.get("DATABASE_URL", "") != _URL


def _seed_app_user(conn, org: str, email: str, status: str = "active") -> None:
    conn.execute(
        text(
            "INSERT INTO app_user (email, display_name, role, status, "
            "                      organization_id, joined_at) "
            "VALUES (:e, :e, 'employee', :s, CAST(:o AS uuid), now())"
        ),
        {"e": email, "s": status, "o": org},
    )


@_DB_GATE
class TestTheForwardStatusMirror:
    """§H6 slice 3a (D48) — the forward status mirror, against real Postgres.

    Slice 1 mirrored membership EXISTENCE create-only; this proves a later
    status change propagates into the RLS-EXEMPT ``org_membership.status`` and
    that doing so never moves an identity between orgs.
    """

    def test_a_suspend_propagates_to_the_shadow(self, conn):
        org = _new_org(conn, "h6-status-suspend")
        email = "suspend.me@h6.example"
        uid = _mirror_identity(conn, email)
        _mirror_membership(conn, org, uid, status="active")
        assert _membership_status(conn, org, uid) == "active"

        assert _mirror_status(conn, org, email, "suspended") == 1
        assert _membership_status(conn, org, uid) == "suspended", (
            "a suspend did not propagate app_user.status → org_membership.status"
        )

    def test_a_remove_propagates_to_the_shadow(self, conn):
        org = _new_org(conn, "h6-status-remove")
        email = "remove.me@h6.example"
        uid = _mirror_identity(conn, email)
        _mirror_membership(conn, org, uid, status="active")

        assert _mirror_status(conn, org, email, "removed") == 1
        assert _membership_status(conn, org, uid) == "removed", (
            "a remove did not propagate to org_membership.status"
        )

    def test_a_reactivate_propagates_and_stamps_joined_at(self, conn):
        org = _new_org(conn, "h6-status-reactivate")
        email = "reactivate.me@h6.example"
        uid = _mirror_identity(conn, email)
        # An invited member: created without joined_at (see _MIRROR_MEMBERSHIP_SQL,
        # which stamps joined_at only when status='active').
        _mirror_membership(conn, org, uid, status="invited")
        assert _membership_status(conn, org, uid) == "invited"

        assert _mirror_status(conn, org, email, "active") == 1
        assert _membership_status(conn, org, uid) == "active", (
            "a reactivate/activate did not propagate to org_membership.status"
        )
        # Activation stamps joined_at, exactly as the authoritative app_user
        # write does — the shadow tracks it rather than diverging.
        joined = conn.execute(
            text(
                "SELECT joined_at FROM org_membership "
                " WHERE organization_id = CAST(:o AS uuid) "
                "   AND user_id = CAST(:u AS uuid)"
            ),
            {"o": org, "u": uid},
        ).scalar_one()
        assert joined is not None, "activation did not stamp joined_at"

    def test_the_status_mirror_never_moves_an_identity(self, conn):
        """The load-bearing invariant (R7): a status change in org A leaves the
        SAME identity's membership in org B untouched — no cross-org move, no
        bleed. This is the runtime companion to
        ``test_the_status_mirror_is_a_scoped_update_never_a_move``.
        """
        org_a = _new_org(conn, "h6-status-move-a")
        org_b = _new_org(conn, "h6-status-move-b")
        email = "multi.org@h6.example"
        uid = _mirror_identity(conn, email)
        _mirror_membership(conn, org_a, uid, status="active")
        _mirror_membership(conn, org_b, uid, status="active")

        # Suspend in org A only.
        assert _mirror_status(conn, org_a, email, "suspended") == 1
        assert _membership_status(conn, org_a, uid) == "suspended"
        # Org B is untouched — the identity was not moved and its other
        # membership did not change.
        assert _membership_status(conn, org_b, uid) == "active", (
            "the status mirror bled into the identity's OTHER org"
        )
        assert _membership_orgs(conn, uid) == sorted([org_a, org_b]), (
            "the status mirror changed which orgs the identity belongs to"
        )

    def test_the_status_mirror_no_ops_when_no_membership_row_exists(self, conn):
        """A member who predates the shadow (or whose slice-1 mirror failed) has
        no ``org_membership`` row; the status path must NOT create one."""
        org = _new_org(conn, "h6-status-norow")
        email = "no.row.yet@h6.example"
        _mirror_identity(conn, email)  # identity exists; membership does NOT

        assert _mirror_status(conn, org, email, "suspended") == 0, (
            "the status mirror touched a row that should not exist"
        )
        assert _membership_for_org(conn, org) == 0, (
            "the status path created a membership row — existence is not its job"
        )

    def test_app_user_is_byte_identical_after_the_status_mirror(self, conn):
        """The status mirror is a separate statement on the shadow table only;
        the authoritative ``app_user`` row is untouched."""
        org = _new_org(conn, "h6-status-appuser")
        email = "appuser.byte@h6.example"
        _seed_app_user(conn, org, email, status="active")
        uid = _mirror_identity(conn, email)
        _mirror_membership(conn, org, uid, status="active")

        before = _app_user_row(conn, email)
        _mirror_status(conn, org, email, "suspended")
        after = _app_user_row(conn, email)
        assert before == after, "the status mirror mutated the app_user row"


@_DB_GATE
class TestTheStatusReconcile:
    """§H6 slice 3a (D48) — the reconcile migration (183): aligns a drifted row
    and is idempotent (R6)."""

    def test_the_reconcile_aligns_a_deliberately_drifted_row(self, conn):
        org = _new_org(conn, "h6-reconcile-align")
        email = "drift@h6.example"
        # app_user is authoritative and says 'suspended'...
        _seed_app_user(conn, org, email, status="suspended")
        uid = _mirror_identity(conn, email)
        # ...but the shadow drifted to 'active' (slice-1 create-only left it
        # stale, which is exactly the P0 this migration closes).
        _mirror_membership(conn, org, uid, status="active")
        assert _membership_status(conn, org, uid) == "active"

        changed = _run_reconcile(conn)
        assert changed >= 1, "the reconcile aligned nothing"
        assert _membership_status(conn, org, uid) == "suspended", (
            "the reconcile did not align org_membership.status to app_user.status"
        )

    def test_the_reconcile_is_idempotent(self, conn):
        org = _new_org(conn, "h6-reconcile-idem")
        email = "idem-drift@h6.example"
        _seed_app_user(conn, org, email, status="removed")
        uid = _mirror_identity(conn, email)
        _mirror_membership(conn, org, uid, status="active")

        first = _run_reconcile(conn)
        assert first == 1, f"expected to align exactly one row, changed {first}"
        assert _membership_status(conn, org, uid) == "removed"
        # Re-run = zero net change.
        second = _run_reconcile(conn)
        assert second == 0, (
            f"the reconcile re-run rewrote {second} rows — not idempotent"
        )
        assert _membership_status(conn, org, uid) == "removed"

    def test_the_reconcile_never_moves_an_identity(self, conn):
        """A drift fixed in org A leaves the same identity's org-B row alone."""
        org_a = _new_org(conn, "h6-reconcile-move-a")
        org_b = _new_org(conn, "h6-reconcile-move-b")
        email = "reconcile.multi@h6.example"
        # app_user is globally unique on lower(email), so the authoritative row
        # belongs to org A; org B's membership is a second row that app_user
        # cannot speak for, so the reconcile must leave it exactly as it is.
        _seed_app_user(conn, org_a, email, status="suspended")
        uid = _mirror_identity(conn, email)
        _mirror_membership(conn, org_a, uid, status="active")
        _mirror_membership(conn, org_b, uid, status="active")

        _run_reconcile(conn)
        assert _membership_status(conn, org_a, uid) == "suspended"
        assert _membership_status(conn, org_b, uid) == "active", (
            "the reconcile changed a membership app_user does not speak for"
        )
        assert _membership_orgs(conn, uid) == sorted([org_a, org_b])


@_DB_GATE
class TestThePurgeClosure:
    """WS-29 H6 orphan-closure (D48) — a member PURGE removes the identity shadow
    at the SOURCE, against real Postgres (the ``purge-removes-shadow`` fence).

    The org's ``org_membership`` row goes UNCONDITIONALLY (so no ACTIVE shadow
    orphan survives a purge, which reconcile 183 could never fix — it joins the
    now-deleted ``app_user``), while the GLOBAL ``user_identity`` is KEPT: it is
    cross-org, and deleting it on the human's last membership was a
    check-then-cascade cross-tenant erasure race
    (``no-user_identity-delete-on-purge``). A membership-less identity is
    harmless and re-used verbatim on a later re-join.
    """

    def test_purging_an_active_member_removes_the_org_membership_shadow(self, conn):
        org = _new_org(conn, "h6-purge-active")
        email = "purge.me@h6.example"
        uid = _mirror_identity(conn, email)
        _mirror_membership(conn, org, uid, status="active")
        assert _membership_for_org(conn, org) == 1

        assert _purge_membership(conn, org, email) == 1, (
            "the purge did not remove the org's org_membership row"
        )
        assert _membership_for_org(conn, org) == 0, (
            "an ACTIVE shadow orphan survived the purge — the read cutover would "
            "wrong-ADMIT it"
        )

    def test_the_identity_is_kept_even_when_it_was_the_last_membership(self, conn):
        """`user_identity` is GLOBAL and outlives its memberships: purging the
        human's LAST membership removes the `org_membership` row but KEEPS the
        identity (the race-free construction — ``no-user_identity-delete-on-purge``).
        A membership-less identity resolves to no access (the identity leg finds
        no org) and is re-used verbatim on a later re-join."""
        org = _new_org(conn, "h6-purge-last")
        email = "last.member@h6.example"
        uid = _mirror_identity(conn, email)
        _mirror_membership(conn, org, uid, status="active")

        assert _purge_membership(conn, org, email) == 1, (
            "the purge did not remove the org's org_membership row"
        )
        assert _membership_for_org(conn, org) == 0, (
            "an ACTIVE shadow orphan survived the purge — the read cutover would "
            "wrong-ADMIT it"
        )
        # The identity is KEPT — no user_identity delete happens on purge.
        assert _identity_count(conn, email) == 1, (
            "purge deleted the GLOBAL user_identity — the removed cross-tenant "
            "erasure race is back"
        )
        # It now holds zero memberships: harmless, resolves to no access.
        assert _membership_orgs(conn, uid) == []

    def test_the_identity_is_kept_when_another_org_still_has_the_member(self, conn):
        """`user_identity` is GLOBAL/cross-org — a purge in one tenant must NEVER
        erase a human another tenant still employs (the inverse of the invite
        path's account-takeover primitive)."""
        org_a = _new_org(conn, "h6-purge-keep-a")
        org_b = _new_org(conn, "h6-purge-keep-b")
        email = "two.orgs@h6.example"
        uid = _mirror_identity(conn, email)
        _mirror_membership(conn, org_a, uid, status="active")
        _mirror_membership(conn, org_b, uid, status="active")

        # Purge from org A only.
        assert _purge_membership(conn, org_a, email) == 1
        # The identity is kept — purge never deletes user_identity at all.
        assert _identity_count(conn, email) == 1, (
            "the identity was deleted while another org still had the member"
        )
        # Org A's membership is gone; org B's is untouched and the human still
        # belongs to org B alone.
        assert _membership_for_org(conn, org_a) == 0
        assert _membership_for_org(conn, org_b) == 1
        assert _membership_orgs(conn, uid) == [org_b]

    def test_the_purge_never_touches_another_orgs_membership(self, conn):
        """Scoped to `(org, identity)`: a purge in org A leaves the SAME human's
        org-B membership byte-unchanged, status and all."""
        org_a = _new_org(conn, "h6-purge-scope-a")
        org_b = _new_org(conn, "h6-purge-scope-b")
        email = "scoped@h6.example"
        uid = _mirror_identity(conn, email)
        _mirror_membership(conn, org_a, uid, status="active")
        _mirror_membership(conn, org_b, uid, status="suspended")

        _purge_membership(conn, org_a, email)
        assert _membership_status(conn, org_b, uid) == "suspended", (
            "the purge changed the identity's membership in another org"
        )

    def test_the_purge_is_idempotent(self, conn):
        """A second purge of an already-purged member removes nothing and does
        not raise (R6) — the closure never leaves a half-state to trip over. The
        identity survives every purge (it is never deleted)."""
        org = _new_org(conn, "h6-purge-idem")
        email = "idem.purge@h6.example"
        uid = _mirror_identity(conn, email)
        _mirror_membership(conn, org, uid, status="active")

        assert _purge_membership(conn, org, email) == 1
        # Re-run: nothing left to remove, no error, zero rows.
        assert _purge_membership(conn, org, email) == 0
        # The identity is kept throughout — membership-less but harmless.
        assert _identity_count(conn, email) == 1


# ── The R8 half, through the mirror's OWN call path (tenant_engine_scope) ─────
#
# The class above proves the SQL; this proves ``mirror_identity_membership``
# wires it correctly — resolves the org, upserts the identity, inserts the
# membership, commits — and that it is best-effort. Modelled on
# ``test_org_provisioning.TestSliceFourTheSeamCaller``: these COMMIT, so they use
# uuid'd identifiers and clean up after themselves.

@_DB_GATE
class TestTheMirrorThroughItsOwnCallPath:

    async def _read_back(self, factory, email):
        async with factory() as session:
            ident = (
                await session.execute(
                    text("SELECT id::text AS id FROM user_identity "
                         " WHERE lower(email) = lower(:e)"),
                    {"e": email},
                )
            ).mappings().first()
            memberships = (
                await session.execute(
                    text(
                        "SELECT count(*) FROM org_membership m "
                        "  JOIN user_identity ui ON ui.id = m.user_id "
                        " WHERE lower(ui.email) = lower(:e)"
                    ),
                    {"e": email},
                )
            ).scalar_one()
        return ident, memberships

    async def test_the_mirror_writes_both_tables_through_its_own_call_path(self):
        from acb_common.db import get_session_factory

        slug = f"h6-callpath-{uuid.uuid4().hex[:8]}"
        email = f"callpath-{uuid.uuid4().hex[:8]}@h6.example"

        async with tenant_engine_scope(_URL):
            factory = get_session_factory()
            async with factory() as session:
                org_id = str(
                    (
                        await session.execute(
                            text(
                                "INSERT INTO organization (slug, display_name) "
                                "VALUES (:s, :s) RETURNING id"
                            ),
                            {"s": slug},
                        )
                    ).scalar_one()
                )
                await session.commit()
            try:
                await mirror_identity_membership(
                    email=email, display_name="Call Path",
                    org_id=org_id, status="active",
                )
                ident, memberships = await self._read_back(factory, email)
                assert ident is not None, "the mirror wrote no user_identity row"
                assert memberships == 1, "the mirror wrote no org_membership row"
            finally:
                async with factory() as session:
                    # CASCADE removes the membership with the org.
                    await session.execute(
                        text("DELETE FROM organization WHERE slug = :s"),
                        {"s": slug},
                    )
                    await session.execute(
                        text("DELETE FROM user_identity WHERE lower(email) = lower(:e)"),
                        {"e": email},
                    )
                    await session.commit()

    async def test_the_mirror_never_raises_on_a_bad_org(self):
        """Best-effort: a non-existent org (FK violation) is swallowed, and a
        call with neither org_id nor org_slug is a quiet no-op."""
        async with tenant_engine_scope(_URL):
            # A random UUID names no organization → the membership INSERT's FK
            # would fail; the mirror must catch it and return None.
            assert await mirror_identity_membership(
                email=f"ghost-{uuid.uuid4().hex[:8]}@h6.example",
                org_id=str(uuid.uuid4()), status="active",
            ) is None
            # No org named at all — nothing to write, no raise.
            assert await mirror_identity_membership(
                email=f"ghost2-{uuid.uuid4().hex[:8]}@h6.example",
            ) is None

    async def test_purge_identity_shadow_removes_membership_keeps_identity(self):
        """`purge_identity_shadow` wires its single statement correctly: it
        removes the org's `org_membership` row and commits on its own session —
        and it KEEPS the GLOBAL `user_identity` (no identity delete at all, the
        race-free construction; ``no-user_identity-delete-on-purge``)."""
        from acb_common.db import get_session_factory

        slug = f"h6-purgepath-{uuid.uuid4().hex[:8]}"
        email = f"purgepath-{uuid.uuid4().hex[:8]}@h6.example"

        async with tenant_engine_scope(_URL):
            factory = get_session_factory()
            async with factory() as session:
                org_id = str(
                    (
                        await session.execute(
                            text(
                                "INSERT INTO organization (slug, display_name) "
                                "VALUES (:s, :s) RETURNING id"
                            ),
                            {"s": slug},
                        )
                    ).scalar_one()
                )
                await session.commit()
            try:
                await mirror_identity_membership(
                    email=email, display_name="Purge Path",
                    org_id=org_id, status="active",
                )
                ident, memberships = await self._read_back(factory, email)
                assert ident is not None and memberships == 1

                await purge_identity_shadow(email=email, org_id=org_id)
                ident2, memberships2 = await self._read_back(factory, email)
                assert memberships2 == 0, "the membership shadow survived the purge"
                assert ident2 is not None, (
                    "purge deleted the GLOBAL user_identity — it must be KEPT "
                    "(no user_identity delete on purge)"
                )
            finally:
                async with factory() as session:
                    await session.execute(
                        text("DELETE FROM organization WHERE slug = :s"),
                        {"s": slug},
                    )
                    await session.execute(
                        text("DELETE FROM user_identity WHERE lower(email) = lower(:e)"),
                        {"e": email},
                    )
                    await session.commit()

    async def test_purge_identity_shadow_never_raises(self):
        """Best-effort: a blank email is a quiet no-op, and a purge against a
        shadow that does not exist returns None without raising."""
        async with tenant_engine_scope(_URL):
            assert await purge_identity_shadow(
                email="", org_id=str(uuid.uuid4()),
            ) is None
            assert await purge_identity_shadow(
                email=f"ghost-{uuid.uuid4().hex[:8]}@h6.example",
                org_id=str(uuid.uuid4()),
            ) is None
