"""WS-PKG PKG-5b part 1 — the install store, with no database (hermetic).

Spec: ``project-docs/specs/package_host.md`` §4, §5.1 and the PKG-5b row of
§10. Code: ``gateway/package_host/store.py``. The R8 twin is
``tests/unit/test_package_host_store_r8.py``.

What this suite holds:

a. Each check of the arguments runs before the first statement. A refusal
   raises a typed ``InstallError`` with a code and a status, and no
   statement runs. ``GROUP:x@y.test`` asked for as private is a 422.
b. The order of the statements: the read of the bound org, then the lock of
   the org, then each read that decides the write, then the inserts. No
   INSERT names an org.
c. A key that a system package holds is refused (``load_contributes``).
d. Only ``store.py`` writes an install or a claim (a source scan of
   ``apps/`` and ``packages/``).
e. The R8 twin is named in the CI skip guard.
f. A claim of an install of the same app is no conflict. The groups come
   from the one group query, which counts active members only.
g. A route sends ``detail_for``, which hides the private install of another
   member.
h. The store refuses a transaction that is not READ COMMITTED. The value
   check of a payload is the one copy in ``studio/packages_store.py``.
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
from gateway.package_host import store
from gateway.package_host.store import (
    ClaimTaken,
    Contribution,
    ContributionRefused,
    InstallError,
    ScopeRefused,
)

_ROOT = Path(__file__).resolve().parents[2]
ORG = "00000000-0000-0000-0000-00000000000a"
APP = str(uuid.uuid4())
ANA = "ana@x.test"


class _Result:
    def __init__(self, value: Any, row: tuple[Any, ...] | None = None) -> None:
        self._value = value
        self._row = row

    def scalar(self) -> Any:
        return self._value

    def scalar_one(self) -> Any:
        assert self._value is not None
        return self._value

    def first(self) -> Any:
        if self._row is not None:
            return self._row
        return None if self._value is None else (self._value,)


#: A held claim, as the held read answers it: (point, key, install id, app
#: id, scope kind, scope subject).
HeldRow = tuple[str, str, str, str, str, str]


class _FakeDb:
    """Records each statement. Answers the bound org and the isolation
    level, a live app, no install at the scope, the held claims of *held*,
    and the id of the new install.

    The held read answers the first row of *held* on a key of the request.
    It drops a row of the same app only when the statement holds
    ``store.SAME_APP_EXEMPT``. So a statement that drops the exemption
    changes the answer here."""

    def __init__(self, org: str | None = ORG, app: bool = True,
                 level: str = "read committed", held: tuple[HeldRow, ...] = ()) -> None:
        self.calls: list[tuple[str, Any]] = []
        self.org = org
        self.app = app
        self.level = level
        self.held = held

    async def execute(self, stmt: Any, params: Any = None) -> _Result:
        sql = str(stmt)
        self.calls.append((sql, params))
        if sql.startswith("SELECT CAST(CAST(NULLIF"):
            return _Result(self.org, row=(self.org, self.level))
        if sql.startswith("SELECT 1 FROM apps"):
            return _Result(1 if self.app else None)
        if sql.startswith("SELECT c.point, c.key,"):
            wanted = set(zip(params["points"], params["keys"], strict=True))
            rows = [r for r in self.held if (r[0], r[1]) in wanted]
            if store.SAME_APP_EXEMPT in sql:
                rows = [r for r in rows if r[3] != params["app"]]
            return _Result(None, row=rows[0] if rows else None)
        if sql.startswith("INSERT INTO package_install"):
            return _Result("install-1")
        return _Result(None)


def _run(coro: Any) -> Any:
    import asyncio

    return asyncio.run(coro)


def _args(**kw: Any) -> dict[str, Any]:
    return {"app_id": APP, "version": 1, "scope_subject": "org", "scope_kind": "org",
            "installed_by": ANA, "ends_at": None, "contributions": [], **kw}


def _refused(exc_type: type[InstallError], **kw: Any) -> InstallError:
    db = _FakeDb()
    with pytest.raises(exc_type) as exc:
        _run(store.install(db, **_args(**kw)))
    assert db.calls == [], "a refusal of an argument must run no statement"
    return exc.value


# ── a. The scope, before the database ──────────────────────────────────────


@pytest.mark.parametrize(("kw", "code"), [
    # The email branch of normalize_grant_subject folds it to `group:x@y.test`.
    ({"scope_subject": "GROUP:x@y.test", "scope_kind": "private",
      "installed_by": "x@y.test"}, "scope_kind_mismatch"),
    ({"scope_subject": "org", "scope_kind": "group"}, "scope_kind_mismatch"),
    ({"scope_subject": "group:ops", "scope_kind": "org"}, "scope_kind_mismatch"),
    ({"scope_subject": ANA, "scope_kind": "group"}, "scope_kind_mismatch"),
    ({"scope_subject": ANA, "scope_kind": "PRIVATE"}, "scope_kind_mismatch"),
    ({"scope_subject": "ben@x.test", "scope_kind": "private"}, "private_not_owner"),
    ({"scope_subject": "org", "scope_kind": "org", "version": None}, "draft_not_private"),
    ({"scope_subject": "group:ops", "scope_kind": "group", "version": None},
     "draft_not_private"),
    ({"scope_subject": "ORG", "scope_kind": "org"}, "bad_scope"),
    ({"scope_subject": "", "scope_kind": "org"}, "bad_scope"),
    ({"scope_subject": "agent:x", "scope_kind": "private"}, "bad_scope"),
    ({"scope_subject": "a\x00@x.test", "scope_kind": "private"}, "bad_scope"),
    ({"scope_subject": "group:" + "a" * 400, "scope_kind": "group"}, "bad_scope"),
    ({"installed_by": "org"}, "bad_member"),
    ({"installed_by": "group:ops"}, "bad_member"),
    ({"installed_by": "GROUP:x@y.test"}, "bad_member"),
    ({"installed_by": ""}, "bad_member"),
    ({"installed_by": None}, "bad_member"),
])
def test_a_bad_scope_is_a_typed_422_before_the_database(kw: dict[str, Any], code: str) -> None:
    err = _refused(ScopeRefused, **kw)
    assert (err.code, err.status) == (code, 422)


@pytest.mark.parametrize(("subject", "kind", "stored"), [
    ("GROUP:x@y.test", "group", "group:x@y.test"),
    (" org ", "org", "org"),
    ("group:Ops", "group", "group:ops"),
    ("Ana@X.Test", "private", ANA),
])
def test_the_kind_comes_from_the_normalised_scope(subject: str, kind: str, stored: str) -> None:
    """A kind taken from the raw text would read ``GROUP:x@y.test`` and
    `` org `` as private."""
    db = _FakeDb()
    got = _run(store.install(db, **_args(scope_subject=subject, scope_kind=kind,
                                         installed_by="ANA@x.test")))
    assert (got.scope_kind, got.scope_subject, got.installed_by) == (kind, stored, ANA)
    [insert] = [p for s, p in db.calls if s.startswith("INSERT INTO package_install")]
    assert (insert["kind"], insert["subject"], insert["by"]) == (kind, stored, ANA)


@pytest.mark.parametrize(("kw", "code"), [
    ({"app_id": "not-a-uuid"}, "bad_app_id"),
    ({"version": 0}, "bad_version"),
    ({"version": True}, "bad_version"),
    ({"version": 2**31}, "bad_version"),
    ({"ends_at": datetime(2026, 12, 1)}, "bad_ends_at"),
    ({"ends_at": "2026-12-01"}, "bad_ends_at"),
])
def test_a_bad_argument_is_refused_before_the_database(kw: dict[str, Any], code: str) -> None:
    err = _refused(InstallError, **kw)
    assert (err.code, err.status) == (code, 422)


@pytest.mark.parametrize(("claims", "code"), [
    ("jobs", "bad_contributions"),
    ([{"point": "jobs", "key": "a"}], "bad_contributions"),
    ([Contribution("menu", "a")], "bad_point"),
    ([Contribution("jobs", "")], "bad_key"),
    ([Contribution("jobs", "a\x00b")], "bad_key"),
    ([Contribution("jobs", "\ud800")], "bad_key"),
    ([Contribution("jobs", "k" * 1025)], "bad_key"),
    ([Contribution("jobs", "a", position=True)], "bad_position"),
    ([Contribution("jobs", "a", position=2**31)], "bad_position"),
    ([Contribution("jobs", "a"), Contribution("jobs", "a")], "duplicate_claim"),
    ([Contribution("jobs", "a", payload=[1])], "bad_payload"),
    ([Contribution("jobs", "a", payload={"t": "a\x00"})], "bad_payload"),
    ([Contribution("jobs", "a", payload={"t\x00": 1})], "bad_payload"),
    ([Contribution("jobs", "a", payload={1: "a"})], "bad_payload"),
    ([Contribution("jobs", "a", payload={"t": 1e20})], "bad_payload"),
    ([Contribution("jobs", "a", payload={"t": float("nan")})], "bad_payload"),
    ([Contribution("jobs", "a", payload={"t": "\udc00"})], "bad_payload"),
    ([Contribution("jobs", "a", payload={"t": {1, 2}})], "bad_payload"),
    ([Contribution("jobs", "a", payload={"t": "x" * 65536})], "payload_too_large"),
])
def test_a_bad_claim_is_refused_before_the_database(claims: Any, code: str) -> None:
    err = _refused(ContributionRefused, contributions=claims)
    assert (err.code, err.status) == (code, 422)


def test_a_payload_counts_bytes_as_the_check_does() -> None:
    """``{"t": "<n chars>"}`` is n + 9 bytes. Each ``é`` is two bytes."""
    assert len(store._payload_text({"t": "x" * 65527}).encode("utf-8")) == 65536
    with pytest.raises(ContributionRefused):
        store._payload_text({"t": "x" * 65528})
    with pytest.raises(ContributionRefused):
        store._payload_text({"t": "é" * 32764})
    assert store._payload_text({"a": [1, 2], "b": None}) == '{"a": [1, 2], "b": null}'


def test_a_deep_payload_is_refused() -> None:
    deep: Any = 1
    for _ in range(store.MAX_PAYLOAD_DEPTH + 1):
        deep = {"d": deep}
    with pytest.raises(ContributionRefused):
        store._payload_text(deep)


# ── b. The order of the statements ─────────────────────────────────────────


def test_the_lock_comes_before_each_read_that_decides_the_write() -> None:
    db = _FakeDb()
    _run(store.install(db, **_args(contributions=[
        Contribution("jobs", "quote", payload={"label": "Q"}, position=3),
        Contribution("triggers", "crm.lead.created")])))
    order = [
        "SELECT CAST(CAST(NULLIF(current_setting('app.tenant_id', true)",
        "SELECT pg_advisory_xact_lock(hashtextextended(:key, 0))",
        "SELECT 1 FROM apps ",
        "SELECT 1 FROM package_install ",
        "SELECT c.point, c.key,",
        "INSERT INTO package_install ",
        "INSERT INTO package_contribution ",
    ]
    assert len(db.calls) == len(order)
    for (sql, _), head in zip(db.calls, order, strict=True):
        assert sql.startswith(head), (head, sql)
    assert db.calls[1][1] == {"key": f"package_install:{ORG}"}
    # A claim on `triggers` is never checked, and every claim is written.
    assert db.calls[4][1]["points"] == ["jobs"] and db.calls[4][1]["keys"] == ["quote"]
    # The held read exempts the claims of the app itself.
    assert db.calls[4][1]["app"] == APP and store.SAME_APP_EXEMPT in db.calls[4][0]
    rows = db.calls[6][1]
    assert [(r["point"], r["key"], r["payload"], r["position"]) for r in rows] == [
        ("jobs", "quote", '{"label": "Q"}', 3), ("triggers", "crm.lead.created", "{}", 0)]


def test_each_read_names_the_bound_org_and_no_insert_names_an_org() -> None:
    bound = "NULLIF(current_setting('app.tenant_id', true), '')"
    for sql in (store._APP_SQL, store._SCOPE_TAKEN_SQL, store._HELD_SQL,
                store._GROUPS_OF_MEMBER, store._GROUPS_OF_HOLDER):
        assert f"organization_id = CAST({bound} AS uuid)" in sql, sql
    for sql in (store._INSERT_INSTALL_SQL, store._INSERT_CONTRIBUTION_SQL):
        assert "organization_id" not in sql
    assert "format = 'manifest'" in store._APP_SQL
    assert "status <> 'archived'" in store._APP_SQL


def test_the_overlap_of_each_kind_reads_both_sides() -> None:
    """§4 as one relation. A new group claim also meets the private claims of
    its members, and a new private claim meets the claims of its groups."""
    assert store.OVERLAP["org"] == "TRUE"
    assert "i.scope_kind = 'org'" in store.OVERLAP["group"]
    assert "i.scope_kind = 'group' AND i.scope_subject = :subject" in store.OVERLAP["group"]
    assert f":subject IN ({store._GROUPS_OF_HOLDER})" in store.OVERLAP["group"]
    assert "i.scope_kind = 'org'" in store.OVERLAP["private"]
    assert "i.scope_kind = 'private' AND i.scope_subject = :subject" in store.OVERLAP["private"]
    assert f"i.scope_subject IN ({store._GROUPS_OF_MEMBER})" in store.OVERLAP["private"]
    assert {"triggers", "slot"} == store.MANY_CLAIMS


# ── f. The same app and the groups ─────────────────────────────────────────


def test_the_groups_are_the_one_group_query_with_active_members_only() -> None:
    """§4: a member of a group is an active member. The store reuses
    ``_MY_GROUPS_SQL`` in both directions and keeps no copy, so the rule
    ``au.status = 'active'`` and the fold ``lower(au.email)`` have one home.
    The R8 twin holds the behaviour: a removed member and a mixed-case
    address."""
    from gateway.routes.projects.core import _MY_GROUPS_SQL

    one = _MY_GROUPS_SQL.strip()
    assert "lower(au.email) = :email AND au.status = 'active'" in one
    bound = f"g.organization_id = {store._BOUND_ORG}"
    of_member = f"{one.replace(':email', ':subject')} AND {bound}"
    of_holder = f"{one.replace(':email', 'i.scope_subject')} AND {bound}"
    assert (of_member, of_holder) == (store._GROUPS_OF_MEMBER, store._GROUPS_OF_HOLDER)
    source = (_ROOT / "apps/services/gateway/gateway/package_host/store.py").read_text(
        encoding="utf-8")
    assert not re.search(r"\b(FROM|JOIN)\s+org_group", source), (
        "the store must not copy the group query")


#: A claim of the key ``quote`` by another install.
_HELD_QUOTE = ("jobs", "quote", "install-0")


@pytest.mark.parametrize(("kind", "subject"), [
    ("org", "org"), ("group", "group:ops"), ("private", ANA)])
def test_a_claim_of_the_same_app_is_no_conflict_at_any_scope(kind: str, subject: str) -> None:
    """§4: two installs of one app never conflict. Ben holds the app Q
    privately, and an install of Q at each scope passes. An install of
    another app on the same key is still refused. The R8 twin holds the
    same cases on a real database."""
    claim = [Contribution("jobs", "quote")]
    same = _FakeDb(held=((*_HELD_QUOTE, APP, "private", "ben@x.test"),))
    got = _run(store.install(same, **_args(scope_subject=subject, scope_kind=kind,
                                           contributions=claim)))
    assert got.id == "install-1"
    other = _FakeDb(held=((*_HELD_QUOTE, str(uuid.uuid4()), "private", "ben@x.test"),))
    with pytest.raises(ClaimTaken):
        _run(store.install(other, **_args(scope_subject=subject, scope_kind=kind,
                                          contributions=claim)))


def test_an_unbound_session_takes_no_lock_and_writes_nothing() -> None:
    db = _FakeDb(org=None)
    with pytest.raises(TenantUnbound):
        _run(store.install(db, **_args()))
    assert len(db.calls) == 1


def test_an_app_that_is_not_installable_writes_nothing() -> None:
    db = _FakeDb(app=False)
    with pytest.raises(store.AppNotInstallable) as exc:
        _run(store.install(db, **_args()))
    assert exc.value.status == 404
    assert not any(s.startswith("INSERT") for s, _ in db.calls)


# ── c. The keys of the system packages ─────────────────────────────────────


@pytest.mark.parametrize(("point", "key", "package"), [
    ("jobs", "capture", "projects"), ("nav", "/projects", "projects"),
    ("cards", "today", "home"), ("nav", "/settings/appearance", "appearance")])
def test_a_key_of_a_system_package_is_refused(point: str, key: str, package: str) -> None:
    db = _FakeDb()
    with pytest.raises(ClaimTaken) as exc:
        _run(store.install(db, **_args(contributions=[Contribution(point, key)])))
    err = exc.value
    assert (err.point, err.key, err.status) == (point, key, 409)
    assert err.holder == store.Holder(source="system", package=package)
    assert not any(s.startswith("INSERT") for s, _ in db.calls)


def test_a_system_key_at_a_point_of_many_claims_is_no_conflict() -> None:
    db = _FakeDb()
    got = _run(store.install(db, **_args(contributions=[Contribution("slot", "today")])))
    assert got.id == "install-1"


# ── d. The one writer ──────────────────────────────────────────────────────


_WRITE = re.compile(r"INSERT\s+INTO\s+package_(install|contribution)\b", re.I)


def test_only_the_store_writes_an_install_or_a_claim() -> None:
    """The member purge deletes and counts rows, and that stays legal. A
    second INSERT under ``apps/`` or ``packages/`` fails here."""
    writers = sorted(
        str(p.relative_to(_ROOT)).replace("\\", "/")
        for folder in ("apps", "packages")
        for p in (_ROOT / folder).rglob("*.py")
        if "node_modules" not in p.parts and _WRITE.search(p.read_text(encoding="utf-8")))
    assert writers == ["apps/services/gateway/gateway/package_host/store.py"]
    members = (_ROOT / "apps/services/gateway/gateway/routes/admin/members.py").read_text(
        encoding="utf-8")
    assert "package_install" in members and not _WRITE.search(members)


def test_the_store_reads_apps_and_never_writes_it() -> None:
    """Settle 2 of the PKG-5b audit. ``studio/packages_store.py`` owns the
    rows of a Studio package, so the install store only reads one."""
    source = (_ROOT / "apps/services/gateway/gateway/package_host/store.py").read_text(
        encoding="utf-8")
    assert not re.search(r"\b(INSERT\s+INTO|UPDATE|DELETE\s+FROM)\s+apps\b", source, re.I)
    assert "SELECT 1 FROM apps " in store._APP_SQL


def test_the_r8_twin_is_named_in_the_ci_skip_guard() -> None:
    """The `--must-run` list of pr-check.yml is kept by hand. Without the
    entry, the R8 twin skips in CI with no database and the job stays green."""
    workflow = (_ROOT / ".github" / "workflows" / "pr-check.yml").read_text(encoding="utf-8")
    assert "tests/unit/test_package_host_store_r8.py" in workflow


def test_an_install_records_its_end_date() -> None:
    ends = datetime(2026, 12, 1, tzinfo=UTC)
    db = _FakeDb()
    got = _run(store.install(db, **_args(scope_subject=ANA, scope_kind="private",
                                         version=None, ends_at=ends)))
    assert (got.ends_at, got.version) == (ends, None)


# ── g. What a route may send ───────────────────────────────────────────────


_BEN = "ben@x.test"
_Q = str(uuid.uuid4())


def _taken(source: str, subject: str | None, package: str = _Q) -> ClaimTaken:
    return ClaimTaken("nav", "/studio/q", store.Holder(
        source=source, package=package, install_id="install-9", scope_subject=subject))


def test_another_members_private_install_shows_as_a_private_install_only() -> None:
    """``get_app_or_404`` hides a private app behind a 404. So the 409 for
    Ana names the point, the key and "a private install", and never Ben's
    address, the app id or the install id."""
    err = _taken("private", _BEN)
    seen = err.detail_for(ANA)
    assert seen == {"code": "claim_taken", "point": "nav", "key": "/studio/q",
                    "holder": {"source": "private"},
                    "message": "The nav key '/studio/q' is taken by a private install."}
    for secret in (_BEN, _Q, "install-9"):
        assert secret not in repr(seen) and secret not in str(err)
    assert err.holder_for("") == {"source": "private"}


def test_the_owner_and_a_shared_scope_see_the_holder_whole() -> None:
    whole = {"source": "private", "package": _Q, "install_id": "install-9",
             "scope_subject": _BEN}
    assert _taken("private", _BEN).detail_for(" Ben@X.test ")["holder"] == whole
    assert _taken("org", "org").holder_for(ANA) == {
        "source": "org", "package": _Q, "install_id": "install-9", "scope_subject": "org"}
    assert _taken("group", "group:ops").holder_for(ANA)["scope_subject"] == "group:ops"
    system = ClaimTaken("jobs", "capture", store.Holder(source="system", package="projects"))
    assert system.detail_for(ANA)["holder"] == {
        "source": "system", "package": "projects", "install_id": None, "scope_subject": None}


def test_each_refusal_has_a_detail_for_the_route() -> None:
    err = ScopeRefused("bad_scope", "The scope must be org, group:<slug> or an address.")
    assert err.detail_for(ANA) == {"code": "bad_scope", "message": str(err)}
    assert "detail_for" in (InstallError.__doc__ or "")


# ── h. READ COMMITTED and the one value check ──────────────────────────────


@pytest.mark.parametrize("level", ["repeatable read", "serializable"])
def test_a_transaction_that_is_not_read_committed_is_refused_before_the_lock(
        level: str) -> None:
    """Under REPEATABLE READ the snapshot comes before the lock, so a second
    install would miss the commit of the first one."""
    db = _FakeDb(level=level)
    with pytest.raises(RuntimeError, match="read committed"):
        _run(store.install(db, **_args()))
    assert len(db.calls) == 1


def test_the_value_check_of_a_payload_is_the_one_copy() -> None:
    """The store calls ``packages_store.first_bad_value`` and keeps no copy."""
    from gateway.studio import packages_store

    assert store.first_bad_value is packages_store.first_bad_value
    assert not hasattr(store, "MAX_FLOAT") and not hasattr(store, "_first_bad_value")
    source = (_ROOT / "apps/services/gateway/gateway/package_host/store.py").read_text(
        encoding="utf-8")
    assert "float.__repr__" not in source and "isfinite" not in source
