"""WS-STU G17 — the save history of a Studio draft, with no database (hermetic).

Spec: ``project-docs/specs/ai_studio.md`` §14 G17 row (task 2390). Code:
``gateway/studio/packages_store.py`` (``update_manifest``, ``create_package``,
``revert_to_revision``, ``list_revisions``, ``get_revision`` and
``publish_version`` with ``name_revision``). The R8 twin is
``tests/unit/test_studio_draft_revisions_r8.py``.

What this suite holds (the R7 fences of the rules that G17 adds):

a. Opt-in. A save, a create and a publish with no author write the
   statements of before G17, and no statement names the revision table.
b. A save with an author appends its revision after the UPDATE, in the same
   session. A draft that no revision holds is kept first, as ``system``.
c. Each revision statement filters on the bound org. The INSERT names no org.
   List and get filter on the format and the member too.
d. Append-only. No statement of the store updates or deletes a revision.
e. The server sets the author. A bad kind, a false Mira and a bad message
   are refused before the database.
f. The caps of the store equal the CHECKs of the migration, and the migration
   is expand-only with FORCE RLS.
"""

from __future__ import annotations

import copy
import json
import re
import uuid
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import pytest

pytest.importorskip("sqlalchemy")

from acb_common.studio_manifest import manifest_hash
from gateway.studio import packages_store as store

from tests.unit.test_studio_packages_store import MANIFEST

_ROOT = Path(__file__).resolve().parents[2]
#: Found by content, never by number (R1).
_MIGRATION = next(
    p for p in (_ROOT / "infra" / "postgres").glob("*.sql")
    if "CREATE TABLE IF NOT EXISTS studio_draft_revisions" in p.read_text(encoding="utf-8"))
_STORE = _ROOT / "apps" / "services" / "gateway" / "gateway" / "studio" / "packages_store.py"

ANA = "ana@x.test"


def _manifest(**patch: Any) -> dict[str, Any]:
    d = copy.deepcopy(MANIFEST)
    d.update(patch)
    return d


class _Result:
    def __init__(self, row: Any) -> None:
        self._row = row

    def first(self) -> Any:
        return self._row

    def all(self) -> list[Any]:
        return [] if self._row is None else [self._row]


class _FakeDb:
    """Records each statement. Answers the queued rows in order, then None."""

    def __init__(self, *rows: Any) -> None:
        self.calls: list[tuple[str, dict[str, Any]]] = []
        self._rows = list(rows)

    async def execute(self, stmt: Any, params: dict[str, Any] | None = None) -> _Result:
        self.calls.append((str(stmt), dict(params or {})))
        return _Result(self._rows.pop(0) if self._rows else None)


def _pkg_row(manifest: dict[str, Any]) -> tuple[Any, ...]:
    now = datetime.now(UTC)
    return (str(uuid.uuid4()), "quotes-00000000", manifest["name"], manifest["purpose"],
            ANA, "private", "draft", json.dumps(manifest), None, now, now)


def _rev_row(seq: int, manifest: dict[str, Any], kind: str = "member",
             author: str = ANA) -> tuple[Any, ...]:
    return (str(uuid.uuid4()), seq, None, manifest_hash(manifest), None, "m", kind, author,
            ANA, None, datetime.now(UTC))


def _latest(seq: int, manifest: dict[str, Any]) -> tuple[Any, ...]:
    return (seq, json.dumps(manifest), manifest_hash(manifest))


def _run(coro: Any) -> Any:
    import asyncio

    return asyncio.run(coro)


def _verbs(db: _FakeDb) -> list[str]:
    return [" ".join(c[0].split()[:3]) for c in db.calls]


# ── a. Opt-in ──────────────────────────────────────────────────────────────


def test_a_save_with_no_author_writes_no_revision() -> None:
    db = _FakeDb(_pkg_row(MANIFEST), _pkg_row(_manifest(name="Two")))
    got = _run(store.update_manifest(db, ANA, str(uuid.uuid4()), _manifest(name="Two")))
    assert got is not None
    assert [c[0].split()[0] for c in db.calls] == ["SELECT", "UPDATE"]
    assert not any("studio_draft_revisions" in c[0] for c in db.calls)


def test_a_create_with_no_author_writes_no_revision() -> None:
    db = _FakeDb(_pkg_row(MANIFEST))
    assert _run(store.create_package(db, ANA, _manifest())) is not None
    assert len(db.calls) == 1 and "studio_draft_revisions" not in db.calls[0][0]


def test_a_publish_with_no_name_revision_reads_no_revision() -> None:
    doc = _manifest()
    ver = (str(uuid.uuid4()), 1, json.dumps(doc), "sha", "", ANA, datetime.now(UTC), "auto",
           None)
    db = _FakeDb(_pkg_row(doc), ver)
    got = _run(store.publish_version(db, ANA, str(uuid.uuid4()), "", catalog={}))
    assert got is not None and got.draft_revision is None
    assert not any("studio_draft_revisions" in c[0] for c in db.calls)
    assert db.calls[1][1]["rev"] is None


def test_a_version_row_from_before_g17_reads_no_revision() -> None:
    """A fake or an old caller can give the 8 columns of A4."""
    row = (str(uuid.uuid4()), 1, None, "sha", "", ANA, datetime.now(UTC), "auto")
    assert store._version(row).draft_revision is None


# ── b. A save with an author ───────────────────────────────────────────────


def test_a_save_with_an_author_appends_one_revision_after_the_update() -> None:
    old, new = _manifest(), _manifest(name="Two")
    db = _FakeDb(_pkg_row(old), _pkg_row(new), _latest(1, old), _rev_row(2, new))
    got = _run(store.update_manifest(db, "Ana@X.test", str(uuid.uuid4()), new,
                                     author=store.member_author("Ana@X.test"),
                                     message="Renamed."))
    assert got is not None
    assert _verbs(db) == ["SELECT id::text, slug,", "UPDATE apps SET",
                          "SELECT seq, manifest,", "INSERT INTO studio_draft_revisions"]
    assert db.calls[0][0].endswith(" FOR UPDATE")
    params = db.calls[3][1]
    assert (params["kind"], params["author"], params["m"]) == ("member", ANA, ANA)
    assert params["message"] == "Renamed." and params["restores"] is None
    assert params["hash"] == manifest_hash(new)
    assert params["diff"].startswith("--- revision 1\n+++ revision 2")
    assert '"name": "Two"' in params["diff"]


def test_a_save_by_mira_names_mira_and_the_member() -> None:
    old, new = _manifest(), _manifest(name="Two")
    db = _FakeDb(_pkg_row(old), _pkg_row(new), _latest(1, old), _rev_row(2, new, "mira", "mira"))
    _run(store.update_manifest(db, ANA, str(uuid.uuid4()), new, author=store.MIRA_AUTHOR))
    params = db.calls[3][1]
    assert (params["kind"], params["author"], params["m"]) == ("mira", "mira", ANA)
    assert params["message"] == store.SAVED_TEXT


def test_a_draft_that_no_revision_holds_is_kept_first_as_system() -> None:
    old, new = _manifest(), _manifest(name="Two")
    db = _FakeDb(_pkg_row(old), _pkg_row(new), None, _rev_row(1, old, "system", "metorite"),
                 _rev_row(2, new))
    _run(store.update_manifest(db, ANA, str(uuid.uuid4()), new,
                               author=store.member_author(ANA)))
    inserts = [c[1] for c in db.calls if c[0].startswith("INSERT INTO studio_draft_revisions")]
    assert [(p["kind"], p["author"]) for p in inserts] == [("system", "metorite"), ("member", ANA)]
    assert inserts[0]["hash"] == manifest_hash(old) and inserts[0]["diff"] == ""
    assert inserts[0]["message"] == store.OUTSIDE_TEXT
    assert inserts[1]["hash"] == manifest_hash(new)


def test_a_save_that_changes_nothing_appends_no_revision() -> None:
    doc = _manifest()
    db = _FakeDb(_pkg_row(doc), _pkg_row(doc), _latest(3, doc))
    _run(store.update_manifest(db, ANA, str(uuid.uuid4()), doc, author=store.member_author(ANA)))
    assert not any(c[0].startswith("INSERT") for c in db.calls)


def test_a_create_with_an_author_writes_revision_1() -> None:
    doc = _manifest()
    db = _FakeDb(_pkg_row(doc), _rev_row(1, doc))
    _run(store.create_package(db, ANA, doc, author=store.member_author(ANA)))
    assert _verbs(db)[1] == "INSERT INTO studio_draft_revisions"
    params = db.calls[1][1]
    assert params["message"] == store.CREATED_TEXT and params["diff"] == ""


def test_a_revert_appends_a_new_revision_that_names_the_one_it_copied() -> None:
    one, now = _manifest(), _manifest(name="Two")
    full = (str(uuid.uuid4()), 1, json.dumps(one), manifest_hash(one), "", "m", "member", ANA,
            ANA, None, datetime.now(UTC))
    db = _FakeDb(_pkg_row(now), full, _pkg_row(one), _latest(2, now), _rev_row(3, one))
    got = _run(store.revert_to_revision(db, ANA, str(uuid.uuid4()), 1,
                                        author=store.member_author(ANA)))
    assert got is not None
    _pkg, revision = got
    assert revision.seq == 3
    assert _verbs(db) == ["SELECT id::text, slug,", "SELECT r.app_id::text, r.seq,",
                          "UPDATE apps SET", "SELECT seq, manifest,",
                          "INSERT INTO studio_draft_revisions"]
    params = db.calls[4][1]
    assert params["restores"] == 1 and params["message"] == "Reverted to revision 1."
    assert params["hash"] == manifest_hash(one)


def test_a_revert_to_the_draft_it_holds_writes_nothing() -> None:
    doc = _manifest()
    full = (str(uuid.uuid4()), 1, json.dumps(doc), manifest_hash(doc), "", "m", "member", ANA,
            ANA, None, datetime.now(UTC))
    db = _FakeDb(_pkg_row(doc), full)
    with pytest.raises(store.PackageStoreError) as exc:
        _run(store.revert_to_revision(db, ANA, str(uuid.uuid4()), 1,
                                      author=store.member_author(ANA)))
    assert str(exc.value) == store.REVERT_SAME
    assert not any(c[0].split()[0] in {"UPDATE", "INSERT"} for c in db.calls)


# ── c. Tenancy and the member ──────────────────────────────────────────────


def test_the_revision_insert_names_no_org_and_filters_on_the_bound_org() -> None:
    sql = store._REVISION_INSERT_SQL
    columns = sql.split("INSERT INTO studio_draft_revisions", 1)[1].split(")", 1)[0]
    assert "organization_id" not in columns
    assert f"organization_id = {store._BOUND_ORG}" in sql
    assert f"organization_id = {store._BOUND_ORG}" in store._REVISION_LATEST_SQL


@pytest.mark.parametrize("name", ["_REVISION_LIST_SQL", "_REVISION_GET_SQL"])
def test_each_revision_read_filters_on_org_format_and_member(name: str) -> None:
    sql = getattr(store, name)
    assert f"a.organization_id = {store._BOUND_ORG}" in sql
    assert "a.organization_id = r.organization_id" in sql
    assert "format = 'manifest'" in sql and "owner_email = :m" in sql
    assert ":org" not in sql


def test_the_revision_functions_take_no_org_argument() -> None:
    import inspect

    for fn in (store.list_revisions, store.get_revision, store.revert_to_revision):
        params = inspect.signature(fn).parameters
        assert list(params)[:2] == ["db", "member"], fn.__name__
        assert not [p for p in params if "org" in p], fn.__name__


# ── d. Append-only ─────────────────────────────────────────────────────────


def test_no_statement_updates_or_deletes_a_revision() -> None:
    source = _STORE.read_text(encoding="utf-8")
    assert not re.search(
        r"UPDATE\s+studio_draft_revisions|DELETE\s+FROM\s+studio_draft_revisions", source, re.I)


# ── e. The author and the message ──────────────────────────────────────────


@pytest.mark.parametrize("author", [
    None, "ana@x.test", store.Author("admin", "ana@x.test"), store.Author("mira", "ana@x.test"),
    store.Author("system", "mira"), store.Author("member", "")])
def test_a_bad_author_is_refused_before_the_db(author: Any) -> None:
    db = _FakeDb()
    with pytest.raises(store.PackageStoreError):
        _run(store.update_manifest(db, ANA, str(uuid.uuid4()), _manifest(), author=author))
    assert db.calls == []


@pytest.mark.parametrize("message", [
    "x" * (store.MAX_MESSAGE_CHARS + 1), "a\x00b", 7, "\ud800",
    "token ghp_" + "A" * 36])
def test_a_bad_message_is_refused_before_the_db(message: Any) -> None:
    db = _FakeDb()
    with pytest.raises(store.PackageStoreError) as exc:
        _run(store.update_manifest(db, ANA, str(uuid.uuid4()), _manifest(),
                                   author=store.member_author(ANA), message=message))
    assert db.calls == []
    assert exc.value.paths == ["$.message"]


@pytest.mark.parametrize("seq", [0, -1, True, "1", 2**31])
def test_a_bad_revision_number_is_refused_before_the_db(seq: Any) -> None:
    db = _FakeDb()
    with pytest.raises(store.PackageStoreError):
        _run(store.get_revision(db, ANA, str(uuid.uuid4()), seq))
    with pytest.raises(store.PackageStoreError):
        _run(store.revert_to_revision(db, ANA, str(uuid.uuid4()), seq,
                                      author=store.member_author(ANA)))
    assert db.calls == []


def test_a_diff_over_the_cap_is_replaced_by_a_note() -> None:
    big = _manifest(purpose="p" * (store.MAX_DIFF_BYTES + 10))
    assert store.revision_diff(_manifest(), big, 1) == store.DIFF_TOO_LARGE
    assert store.revision_diff(None, big, None) == ""


# ── f. The migration ───────────────────────────────────────────────────────


def test_the_caps_of_the_store_are_the_checks_of_the_migration() -> None:
    sql = _MIGRATION.read_text(encoding="utf-8")
    assert f"char_length(message) <= {store.MAX_MESSAGE_CHARS}" in sql
    assert f"octet_length(diff) <= {store.MAX_DIFF_BYTES}" in sql
    assert f"octet_length(manifest::text) <= {store.MAX_MANIFEST_BYTES}" in sql
    kinds = re.search(r"author_kind IN \(([^)]*)\)", sql)
    assert kinds is not None
    assert {k.strip(" '") for k in kinds.group(1).split(",")} == store.AUTHOR_KINDS


def test_the_migration_is_expand_only_with_force_rls() -> None:
    sql = _MIGRATION.read_text(encoding="utf-8")
    body = "\n".join(line for line in sql.splitlines() if not line.lstrip().startswith("--"))
    assert "FORCE  ROW LEVEL SECURITY" in body or "FORCE ROW LEVEL SECURITY" in body
    assert "WITH CHECK (organization_id = current_setting('app.tenant_id', true)::uuid)" in body
    assert not re.search(r"\bDROP\s+(TABLE|COLUMN)\b|\bRENAME\b|\bUPDATE\s+\w", body, re.I)
    assert "ADD COLUMN IF NOT EXISTS draft_revision INTEGER NULL" in body


def test_the_r8_twin_is_named_in_the_ci_skip_guard() -> None:
    for name in ("pr-check.yml", "deploy.yml"):
        workflow = (_ROOT / ".github" / "workflows" / name).read_text(encoding="utf-8")
        assert "tests/unit/test_studio_draft_revisions_r8.py" in workflow, name
