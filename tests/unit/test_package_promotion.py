"""WS-PKG PKG-11a — the promotion and the share, with no database (hermetic).

Spec: ``project-docs/specs/package_host.md`` §5.1 decision 3, §6.2 and the
PKG-11a row of §10. Code: ``store.promote_to_group`` and
``package_host/promotion.py``. The R8 twin is
``tests/unit/test_package_promotion_r8.py``.

What this suite holds:

a. Each check of the arguments runs before the first statement, with its
   own code. A group subject goes through ``normalize_grant_subject``.
b. The order: the read of the bound org, the org lock, the read of the
   install FOR UPDATE, then each read that decides the write, then one
   write.
c. Each refusal of the install row has its own code and writes nothing.
d. Every statement names the bound org. ``promotion.py`` writes
   ``app_grants`` only, and neither module writes ``apps`` or
   ``app_versions``. The share keeps the role and the consent hash on a
   conflict, and sets ``granted_by`` (PK-7) only on a consent row or a row
   of the sharer. The one write of ``app_grants`` in ``store.py`` is the
   DELETE of the share rows of the promoted install.
e. The R8 twin is in the ``--must-run`` lists of pr-check.yml and
   deploy.yml.
"""

from __future__ import annotations

import re
import uuid
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import pytest

pytest.importorskip("sqlalchemy")

from acb_common.db import TenantUnbound
from gateway.package_host import promotion, sharing, store
from gateway.package_host.promotion import NotTheOwner, ShareRefused
from gateway.package_host.store import (
    AlreadyInstalled,
    ClaimTaken,
    InstallError,
    InstallNotFound,
    PromotionRefused,
    ScopeRefused,
)

_ROOT = Path(__file__).resolve().parents[2]
_PKG = _ROOT / "apps/services/gateway/gateway/package_host"
ORG = "00000000-0000-0000-0000-00000000000a"
APP = str(uuid.uuid4())
INSTALL = str(uuid.uuid4())
ANA = "ana@x.test"
BEN = "ben@x.test"
OPS = "group:ops"
BOUND = "organization_id = CAST(NULLIF(current_setting('app.tenant_id', true), '') AS uuid)"


class _Result:
    def __init__(self, rows: list[tuple[Any, ...]]) -> None:
        self._rows = rows

    def first(self) -> Any:
        return self._rows[0] if self._rows else None

    def all(self) -> list[tuple[Any, ...]]:
        return list(self._rows)


class _FakeDb:
    """Records each statement. Answers the bound org, the install row, the
    group read, the grantee read, the scope read, the claims, the held read
    and the active members, each from a field."""

    def __init__(self, *, org: str | None = ORG, row: tuple[Any, ...] | None = None,
                 in_group: bool = True, outside: bool = False, taken: bool = False,
                 claims: tuple[tuple[Any, ...], ...] = (), held: tuple[Any, ...] | None = None,
                 members: tuple[str, ...] = (BEN, "cat@x.test"),
                 met: tuple[tuple[str, str], ...] = ()) -> None:
        self.calls: list[tuple[str, Any]] = []
        self.org = org
        self.row = row if row is not None else (APP, 1, "private", ANA, ANA, None, "active")
        self.in_group = in_group
        self.outside = outside
        self.taken = taken
        self.claims = claims
        self.held = held
        self.members = members
        self.met = met

    async def execute(self, stmt: Any, params: Any = None) -> _Result:
        sql = str(stmt)
        self.calls.append((sql, params))
        if sql.startswith("SELECT CAST(CAST(NULLIF"):
            return _Result([(self.org, "read committed")])
        if sql == store._INSTALL_FOR_UPDATE_SQL:
            return _Result([self.row] if self.row != () else [])
        if sql == store._MEMBER_IN_GROUP_SQL:
            return _Result([(1,)] if self.in_group else [])
        if sql == store._GRANTEE_OUTSIDE_SQL:
            return _Result([(1,)] if self.outside else [])
        if sql == store._SCOPE_TAKEN_SQL:
            return _Result([(1,)] if self.taken else [])
        if sql == store._CLAIMS_OF_SQL:
            return _Result(list(self.claims))
        if sql.startswith("SELECT c.point, c.key,"):
            return _Result([self.held] if self.held else [])
        if sql == promotion._ACTIVE_MEMBERS_SQL:
            return _Result([(m,) for m in self.members if m in params["emails"]])
        if sql == promotion._MET_ROWS_SQL:
            return _Result([r for r in self.met if r[0] in params["people"]])
        return _Result([])


def _run(coro: Any) -> Any:
    import asyncio

    return asyncio.run(coro)


def _writes(db: _FakeDb) -> list[str]:
    return [s for s, _ in db.calls if s.startswith(("UPDATE", "INSERT", "DELETE"))]


def _promote(db: _FakeDb, group: str = OPS, install: str = INSTALL, actor: Any = ANA) -> Any:
    return _run(store.promote_to_group(db, install_id=install, group=group, actor=actor))


def _share(db: _FakeDb, emails: Any = (BEN,), sharer: str = ANA) -> Any:
    return _run(promotion.share_with_people(db, install_id=INSTALL, emails=emails,
                                            sharer=sharer))


# ── a. The arguments, before the database ─────────────────────────────────


@pytest.mark.parametrize(("group", "code"), [
    ("org", "not_a_group"), (ANA, "not_a_group"), ("agent:x", "not_a_group"),
    ("group:", "not_a_group"), ("", "not_a_group"), (None, "not_a_group"),
    ("group:" + "a" * 400, "not_a_group"), ("group:a\x00", "not_a_group"),
])
def test_a_scope_that_is_not_a_group_is_refused_before_the_database(group: Any,
                                                                      code: str) -> None:
    db = _FakeDb()
    with pytest.raises(ScopeRefused) as exc:
        _promote(db, group=group)
    assert (exc.value.code, exc.value.status) == (code, 422)
    assert db.calls == []


def test_a_bad_install_id_is_refused_before_the_database() -> None:
    for call in (lambda db: _promote(db, install="x"),
                 lambda db: _run(promotion.share_with_people(
                     db, install_id="x", emails=[BEN], sharer=ANA))):
        db = _FakeDb()
        with pytest.raises(InstallError) as exc:
            call(db)
        assert exc.value.code == "bad_install_id" and db.calls == []


def test_the_group_is_normalised() -> None:
    db = _FakeDb()
    got = _promote(db, group=" group:Ops ")
    assert (got.scope_kind, got.scope_subject) == ("group", OPS)
    [(_, params)] = [(s, p) for s, p in db.calls if s == store._PROMOTE_SQL]
    assert params == {"install": INSTALL, "subject": OPS}


@pytest.mark.parametrize(("emails", "code"), [
    ("ben@x.test", "bad_people"),
    ([], "no_people"),
    ([f"p{i}@x.test" for i in range(51)], "too_many_people"),
    (["not an address"], "bad_people"),
    ([None], "bad_people"),
    (["b\x00@x.test"], "bad_people"),
    (["org"], "not_a_person"),
    (["group:ops"], "not_a_person"),
    (["GROUP:x@y.test"], "not_a_person"),
    (["agent:bot@x.test"], "not_a_person"),
    (["agents:*@x.test"], "not_a_person"),
    (["agent:helper"], "bad_people"),
    ([" Ana@X.test "], "own_address"),
])
def test_a_bad_list_of_people_is_refused_before_the_database(emails: Any, code: str) -> None:
    db = _FakeDb()
    with pytest.raises(ShareRefused) as exc:
        _share(db, emails=emails)
    assert (exc.value.code, exc.value.status) == (code, 422)
    assert db.calls == []


def test_a_bad_sharer_is_refused_before_the_database() -> None:
    for sharer in ("org", "group:ops", "", None):
        db = _FakeDb()
        with pytest.raises(ScopeRefused):
            _share(db, sharer=sharer)  # type: ignore[arg-type]
        assert db.calls == []


def test_the_people_are_normalised_with_no_copy() -> None:
    db = _FakeDb()
    got = _share(db, emails=[" Ben@X.Test", "ben@x.test", "CAT@x.test"], sharer="ANA@x.test")
    assert got == (BEN, "cat@x.test")
    [(_, rows)] = [(s, p) for s, p in db.calls if s == promotion._SHARE_SQL]
    assert rows == [{"app": APP, "subject": BEN, "sharer": ANA},
                    {"app": APP, "subject": "cat@x.test", "sharer": ANA}]


def test_fifty_people_is_the_limit() -> None:
    people = [f"p{i}@x.test" for i in range(50)]
    db = _FakeDb(members=tuple(people))
    assert len(_share(db, emails=people)) == 50


# ── b. The order of the statements ─────────────────────────────────────────


def test_the_promotion_reads_under_the_locks_then_writes_once() -> None:
    db = _FakeDb(claims=(("jobs", "quote", {"label": "Q"}, 2), ("triggers", "t", {}, 0)))
    got = _promote(db)
    order = [
        "SELECT CAST(CAST(NULLIF", "SELECT pg_advisory_xact_lock(", store._INSTALL_FOR_UPDATE_SQL,
        store._MEMBER_IN_GROUP_SQL, store._GRANTEE_OUTSIDE_SQL, store._SCOPE_TAKEN_SQL,
        store._CLAIMS_OF_SQL, "SELECT c.point, c.key,", store._PROMOTE_SQL,
        store._UNSHARE_SQL,
    ]
    assert len(db.calls) == len(order)
    for (sql, _), head in zip(db.calls, order, strict=True):
        assert sql.startswith(head), (head, sql)
    assert db.calls[1][1] == {"key": f"package_install:{ORG}"}
    assert db.calls[3][1] == {"subject": OPS, "by": ANA}
    assert db.calls[5][1] == {"app": APP, "kind": "group", "subject": OPS}
    # The held read runs for the group scope, with the same-app exemption,
    # and skips a point of many claims.
    held_sql, held = db.calls[7]
    assert store.OVERLAP["group"] in held_sql and store.SAME_APP_EXEMPT in held_sql
    assert (held["points"], held["keys"], held["subject"], held["app"]) == (
        ["jobs"], ["quote"], OPS, APP)
    assert (got.id, got.version, got.installed_by, got.ends_at) == (INSTALL, 1, ANA, None)
    assert [(c.point, c.key, c.position) for c in got.contributions] == [
        ("jobs", "quote", 2), ("triggers", "t", 0)]


def test_the_share_reads_under_the_same_locks_then_writes_once() -> None:
    db = _FakeDb()
    _share(db)
    order = ["SELECT CAST(CAST(NULLIF", "SELECT pg_advisory_xact_lock(",
             store._INSTALL_FOR_UPDATE_SQL, promotion._ACTIVE_MEMBERS_SQL,
             promotion._MET_ROWS_SQL, promotion._SHARE_SQL]
    assert [s for s, _ in db.calls][2:] == order[2:]
    assert db.calls[0][0].startswith(order[0]) and db.calls[1][0].startswith(order[1])
    assert db.calls[1][1] == {"key": f"package_install:{ORG}"}


def test_an_unbound_session_writes_nothing() -> None:
    for call in (_promote, _share):
        db = _FakeDb(org=None)
        with pytest.raises(TenantUnbound):
            call(db)
        assert len(db.calls) == 1


# ── c. The refusals of the install row ─────────────────────────────────────


_KEPT = (APP, 1, "private", ANA, ANA, None, "active")
_LATER = datetime(2026, 12, 1, tzinfo=UTC)


@pytest.mark.parametrize(("row", "code"), [
    ((APP, 1, "group", OPS, ANA, None, "active"), "not_private"),
    ((APP, 1, "org", "org", ANA, None, "active"), "not_private"),
    ((APP, 1, "private", ANA, ANA, None, "paused"), "not_active"),
    ((APP, 1, "private", ANA, ANA, None, "archived"), "not_active"),
    ((APP, None, "private", ANA, ANA, None, "active"), "draft"),
    ((APP, 1, "private", ANA, ANA, _LATER, "active"), "not_kept"),
])
def test_an_install_that_cannot_move_is_refused_and_nothing_is_written(row: tuple[Any, ...],
                                                                        code: str) -> None:
    db = _FakeDb(row=row)
    with pytest.raises(PromotionRefused) as exc:
        _promote(db)
    assert (exc.value.code, exc.value.status) == (code, 409)
    assert _writes(db) == []


@pytest.mark.parametrize(("row", "code"), [
    ((APP, 1, "group", OPS, ANA, None, "active"), "not_private"),
    ((APP, 1, "private", ANA, ANA, None, "paused"), "not_active"),
    ((APP, 1, "private", ANA, ANA, _LATER, "active"), "not_kept"),
])
def test_an_install_that_cannot_be_shared_is_refused(row: tuple[Any, ...], code: str) -> None:
    db = _FakeDb(row=row)
    with pytest.raises(PromotionRefused) as exc:
        _share(db)
    assert exc.value.code == code and _writes(db) == []


def test_a_missing_install_is_not_found() -> None:
    for call in (_promote, _share):
        db = _FakeDb(row=())
        with pytest.raises(InstallNotFound):
            call(db)
        assert _writes(db) == []


@pytest.mark.parametrize(("kw", "exc_type", "code"), [
    ({"in_group": False}, PromotionRefused, "owner_not_in_group"),
    ({"outside": True}, PromotionRefused, "grantee_outside_group"),
    ({"taken": True}, AlreadyInstalled, "already_installed"),
])
def test_a_promotion_that_the_group_refuses_writes_nothing(kw: dict[str, Any],
                                                            exc_type: type[InstallError],
                                                            code: str) -> None:
    db = _FakeDb(**kw)
    with pytest.raises(exc_type) as exc:
        _promote(db)
    assert exc.value.code == code and _writes(db) == []


def test_an_overlapping_claim_of_another_app_is_taken() -> None:
    other = str(uuid.uuid4())
    db = _FakeDb(claims=(("jobs", "quote", {}, 0),),
                 held=("jobs", "quote", "i-9", other, "private", BEN))
    with pytest.raises(ClaimTaken) as exc:
        _promote(db)
    assert exc.value.holder.package == other and _writes(db) == []


def test_a_key_of_a_system_package_is_taken() -> None:
    db = _FakeDb(claims=(("jobs", "capture", {}, 0),))
    with pytest.raises(ClaimTaken) as exc:
        _promote(db)
    assert exc.value.holder.source == "system" and _writes(db) == []


def test_a_share_by_another_member_is_refused() -> None:
    db = _FakeDb()
    with pytest.raises(NotTheOwner) as exc:
        _share(db, emails=["cat@x.test"], sharer=BEN)
    assert (exc.value.code, exc.value.status) == ("not_owner", 403)
    assert _writes(db) == []


def test_a_promotion_by_another_member_is_refused() -> None:
    db = _FakeDb()
    with pytest.raises(NotTheOwner) as exc:
        _promote(db, actor=BEN)
    assert (exc.value.code, exc.value.status) == ("not_owner", 403)
    assert _writes(db) == []
    # The owner passes, in any case of the address.
    assert _promote(_FakeDb(), actor=" ANA@x.test ").installed_by == ANA


def test_a_bad_actor_is_refused_before_the_database() -> None:
    for actor in ("org", "group:ops", "", None):
        db = _FakeDb()
        with pytest.raises(ScopeRefused):
            _promote(db, actor=actor)
        assert db.calls == []


def test_not_the_owner_is_one_class() -> None:
    assert promotion.NotTheOwner is store.NotTheOwner


@pytest.mark.parametrize("met", [
    (("ben@x.test", "ben@x.test"),),
    (("ben@x.test", "ANA@x.test"),),
])
def test_a_share_onto_a_consent_row_or_its_own_row_is_written(met: tuple[Any, ...]) -> None:
    db = _FakeDb(met=met)
    assert _share(db) == (BEN,)
    assert any(s == promotion._SHARE_SQL for s in _writes(db))


def test_a_share_onto_a_row_of_another_sharer_writes_no_row() -> None:
    db = _FakeDb(met=(("cat@x.test", "ben@x.test"),))
    with pytest.raises(ShareRefused) as exc:
        _share(db, emails=[BEN, "cat@x.test"])
    assert (exc.value.code, exc.value.status) == ("shared_by_another_owner", 422)
    assert _writes(db) == []
    assert "cat" not in str(exc.value) and "ben" not in str(exc.value)


def test_a_person_outside_the_org_is_refused() -> None:
    db = _FakeDb(members=(BEN,))
    with pytest.raises(ShareRefused) as exc:
        _share(db, emails=[BEN, "zed@y.test"])
    assert exc.value.code == "not_a_member" and _writes(db) == []
    assert "zed" not in str(exc.value)


# ── d. The fences ──────────────────────────────────────────────────────────


def test_every_statement_names_the_bound_org() -> None:
    for sql in (store._INSTALL_FOR_UPDATE_SQL, store._CLAIMS_OF_SQL, store._PROMOTE_SQL,
                store._MEMBER_IN_GROUP_SQL, promotion._ACTIVE_MEMBERS_SQL,
                promotion._MET_ROWS_SQL, promotion._SHARE_SQL):
        assert BOUND in sql, sql
    # The grantee read names the bound org for its three tables and the
    # groups. The DELETE names it for both tables.
    assert store._GRANTEE_OUTSIDE_SQL.count(BOUND) == 4
    assert store._UNSHARE_SQL.count(BOUND) == 2
    assert "FOR UPDATE" in promotion._MET_ROWS_SQL
    assert "FOR UPDATE" in store._INSTALL_FOR_UPDATE_SQL
    assert "organization_id" not in promotion._SHARE_SQL.split("ON CONFLICT")[0]


def test_the_grantee_read_is_the_one_share_rule() -> None:
    assert sharing.SHARE_ROW in store._GRANTEE_OUTSIDE_SQL
    # Only an active member of the org blocks (a suspended grantee does not).
    assert "AND u.status = 'active'" in store._GRANTEE_OUTSIDE_SQL
    # The DELETE of the promotion takes exactly the rows of the share rule.
    assert store._UNSHARE_SQL.startswith("DELETE FROM app_grants AS g USING package_install ")
    assert store._UNSHARE_SQL.endswith(f"AND {sharing.SHARE_ROW}")
    assert store._groups_of("shared.who") in store._GRANTEE_OUTSIDE_SQL
    assert store._groups_of(":by") in store._MEMBER_IN_GROUP_SQL


def test_the_promotion_changes_the_scope_only() -> None:
    sql = store._PROMOTE_SQL
    assert sql.startswith("UPDATE package_install SET scope_kind = 'group', "
                          "scope_subject = :subject, updated_at = now() WHERE")
    for kept in ("version =", "installed_by =", "created_at =", " id =", "app_id ="):
        assert kept not in sql.split("WHERE")[0], kept
    assert "scope_kind = 'private' AND state = 'active'" in sql


def test_the_share_keeps_the_role_and_the_consent_hash_on_a_conflict() -> None:
    sql = promotion._SHARE_SQL
    assert sql.startswith("INSERT INTO app_grants (app_id, subject, role, granted_by) ")
    assert "VALUES (CAST(:app AS uuid), :subject, 'use', :sharer)" in sql
    update = sql.split("DO UPDATE SET")[1]
    assert update.startswith(" granted_by = excluded.granted_by ")
    assert "role" not in update and "consented_scope_hash" not in update
    # The guard changes only a consent row or a row of this sharer.
    assert update.endswith("AND (lower(app_grants.granted_by) = lower(app_grants.subject) "
                           "OR lower(app_grants.granted_by) = excluded.granted_by)")


_ANY_WRITE = re.compile(r"\b(?:INSERT\s+INTO|DELETE\s+FROM|(?<!DO )UPDATE)\s+(\w+)", re.I)


def _source(name: str) -> str:
    return (_PKG / name).read_text(encoding="utf-8")


def test_promotion_writes_app_grants_only() -> None:
    """``store.py`` stays the one writer of ``package_install``, and the
    share never calls ``routes/apps/grants.py`` (``test_app_grants.py`` pins
    its SQL)."""
    source = _source("promotion.py")
    assert set(_ANY_WRITE.findall(source)) == {"app_grants"}
    assert "routes.apps" not in source and "grants import" not in source


def test_the_store_writes_app_grants_only_to_delete_the_share_rows() -> None:
    """The narrow allowance of PKG-11a: ``store.py`` deletes the share rows
    of a promoted install in the transaction of the promotion, and writes
    ``app_grants`` in no other way."""
    source = _source("store.py")
    writes = re.findall(r"\b(INSERT\s+INTO|DELETE\s+FROM|(?<!DO )UPDATE)\s+app_grants\b",
                        source, re.I)
    assert writes == ["DELETE FROM"]
    assert source.count("text(_UNSHARE_SQL)") == 1


def test_neither_module_writes_apps_or_app_versions() -> None:
    for name in ("store.py", "promotion.py"):
        source = _source(name)
        assert not re.search(
            r"\b(INSERT\s+INTO|UPDATE|DELETE\s+FROM)\s+(apps|app_versions)\b", source, re.I), name


# ── e. The R8 twin is in both --must-run lists ─────────────────────────────


@pytest.mark.parametrize("workflow", ["pr-check.yml", "deploy.yml"])
def test_the_r8_twin_is_in_the_must_run_list(workflow: str) -> None:
    lines = (_ROOT / ".github" / "workflows" / workflow).read_text(
        encoding="utf-8").splitlines()
    start = next(i for i, line in enumerate(lines) if line.strip() == "--must-run \\")
    listed: list[str] = []
    for line in lines[start + 1:]:
        listed.append(line.strip().removesuffix("\\").strip())
        if not line.rstrip().endswith("\\"):
            break
    assert "tests/unit/test_package_promotion_r8.py" in listed
