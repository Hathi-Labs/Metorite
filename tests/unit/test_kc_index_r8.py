"""R8 — the text of a Drive document on a real database (WS-61 KC-5a).

Spec: ``project-docs/specs/knowledge_curation.md`` §7.2 step 4, §7.3, §8.4,
§16 and the KC-5a row of §22.

What this proves against a REAL Postgres, as the NON-privileged role of the
phase-4 rehearsal (no superuser, no BYPASSRLS), on a database with the whole
ladder, the four generated phases and two organizations. It reuses the
fixtures of ``test_kc_sources_r8.py``: a source is connected through the real
routes, and discovery runs on the fake Google there. The index pass runs
against :class:`Docs`, a fake adapter that serves the bytes of each file. The
real Google fetch is KC-5b.

The acceptance items of the KC-5a contract:

* A1 — an included folder of 50 files: after ONE ``run_tick``, the owner finds
  them with ``query_documents``;
* A6 — a gone node loses its document and chunks in one pass. A disconnect, a
  member purge and a discovery clear leave zero document chunks (the trigger);
* A7 — a member who reads space S but is no proven reader of node N gets no
  hit. A proven reader (a ``user`` row) does. The source owner does. A stale
  ``synced_at`` (over 24 h) gets none;
* A10 — org B reads and writes nothing of org A in either new table. Both FORCE
  row level security. The widened CHECK keeps the old values legal;
* A11 — the include walk handles a deep tree and a cycle.

And the edges: the migration replays, the trigger on each delete path, the
unique keys, and the cascade from the source.
"""

from __future__ import annotations

import asyncio
import uuid
from pathlib import Path
from typing import Any

import pytest

pytest.importorskip("sqlalchemy")

from sqlalchemy import text
from sqlalchemy.exc import DBAPIError

from tests.unit._tenant_ladder import tenant_engine_scope
from tests.unit.test_email_purge_wiring import recorder  # noqa: F401
from tests.unit.test_h3_rls_promotion_rehearsal import (  # noqa: F401
    _APP_ROLE,
    _DB_GATE,
    app_engine,
    promoted,
)
from tests.unit.test_kb_pages_r8 import (  # noqa: F401
    CAPABILITY,
    _add_member,
    _app_dsn,
    _assert_non_priv,
    _principal,
    ns,
)
from tests.unit.test_kc_access_r8 import _IDS, DOMAIN, _member, _set_domain  # noqa: F401
from tests.unit.test_kc_sources_r8 import _connect, kc  # noqa: F401

# ⚠️ Every test here needs the real database. Without this gate the suite
# ERRORS where no ladder URL is set (the deploy run), and the deploy stops.
pytestmark = _DB_GATE

_MIGRATIONS = Path(__file__).resolve().parents[2] / "infra" / "postgres"

FOLDER = "application/vnd.google-apps.folder"


def _documents_migration() -> Path:
    """The migration that creates ``kb_documents``, found by content (R1)."""
    found = [
        p for p in sorted(_MIGRATIONS.glob("*.sql"))
        if p.name != "schema.generated.sql"
        and "CREATE TABLE IF NOT EXISTS kb_documents" in p.read_text(encoding="utf-8")
    ]
    assert len(found) == 1, [p.name for p in found]
    return found[0]


def _files(acct: str, count: int = 2, *, folder: str | None = None,
           prefix: str = "doc") -> list[dict[str, Any]]:
    """A Drive tree: the folder ``inc-<acct>`` and *count* text files in it."""
    root = f"root-{acct}"
    inc = folder or f"inc-{acct}"
    out: list[dict[str, Any]] = [
        {"id": inc, "name": "Plans", "parents": [root], "mimeType": FOLDER}]
    for i in range(count):
        out.append({
            "id": f"{prefix}{i}-{acct}", "name": f"{prefix}{i}.txt", "parents": [inc],
            "mimeType": "text/plain", "size": "40",
            "modifiedTime": "2026-10-20T08:00:00.000Z",
            "md5Checksum": f"{i:032x}",
        })
    return out


async def _indexed_source(p: Any, acct: str, files: list[dict[str, Any]]) -> tuple[Any, str]:
    """Connect Drive for a NEW active member of org A on *files*. Returns the
    owner and the source id."""
    owner = _member(p, "owner")
    sid = await _connect(p, owner, email=f"own-{acct.lower()}@kc1a-g.test",
                         account_id=acct, files=files)
    return owner, sid


def _node_id(p: Any, sid: str, ext: str) -> str:
    with p.admin_engine.connect() as c:
        return str(c.execute(text(
            "SELECT id FROM kb_source_nodes WHERE source_id = CAST(:s AS uuid) "
            "AND ext_id = :e"), {"s": sid, "e": ext}).scalar())


def _include(p: Any, sid: str, folder: str, org: str | None = None) -> None:
    with p.admin_engine.begin() as c:
        c.execute(text(
            "INSERT INTO kb_source_includes (organization_id, source_id, folder_ext_id, "
            "included_by) VALUES (CAST(:o AS uuid), CAST(:s AS uuid), :f, 'r8@kc5a.test') "
            "ON CONFLICT DO NOTHING"), {"o": org or p.org_a, "s": sid, "f": folder})


def _seed_doc(p: Any, node: str, *, chunks: int = 2, org: str | None = None) -> str:
    """A document row for *node* and *chunks* chunk rows, written as the admin."""
    org = org or p.org_a
    space = str(uuid.uuid4())
    with p.admin_engine.begin() as c:
        doc = str(c.execute(text(
            "INSERT INTO kb_documents (organization_id, node_id, text, sha, parser) "
            "VALUES (CAST(:o AS uuid), CAST(:n AS uuid), 'body', 'sha-1', 'txt') "
            "RETURNING id"), {"o": org, "n": node}).scalar())
        for i in range(chunks):
            c.execute(text(
                "INSERT INTO kb_chunks (organization_id, space_id, source_kind, source_id, "
                "ordinal, heading_path, raw, content_sha) VALUES (CAST(:o AS uuid), "
                "CAST(:sp AS uuid), 'document', CAST(:d AS uuid), :i, 'p', 'raw', :sha)"),
                {"o": org, "sp": space, "d": doc, "i": i, "sha": f"{doc}-{i}"})
    return doc


def _chunk_count(p: Any, doc: str | None = None) -> int:
    with p.admin_engine.connect() as c:
        if doc is None:
            return int(c.execute(text(
                "SELECT count(*) FROM kb_chunks WHERE source_kind = 'document'")).scalar())
        return int(c.execute(text(
            "SELECT count(*) FROM kb_chunks WHERE source_kind = 'document' "
            "AND source_id = CAST(:d AS uuid)"), {"d": doc}).scalar())


def _doc_count(p: Any, sid: str) -> int:
    with p.admin_engine.connect() as c:
        return int(c.execute(text(
            "SELECT count(*) FROM kb_documents d JOIN kb_source_nodes n ON n.id = d.node_id "
            "WHERE n.source_id = CAST(:s AS uuid)"), {"s": sid}).scalar())


# ── A10: the two new tables ─────────────────────────────────────────────────

@pytest.mark.parametrize("table", ["kb_documents", "kb_source_includes"])
def test_a10_the_table_forces_rls_and_the_unbound_session_reads_nothing(
    kc, app_engine, table: str,  # noqa: F811
) -> None:
    _assert_non_priv(app_engine)
    with kc.admin_engine.connect() as c:
        row = c.execute(text(
            "SELECT relrowsecurity, relforcerowsecurity, pg_get_userbyid(relowner) AS owner "
            "FROM pg_class WHERE relname = :t"), {"t": table}).one()
        [policy] = c.execute(text(
            "SELECT qual, with_check FROM pg_policies WHERE tablename = :t"),
            {"t": table}).fetchall()
    assert row.relrowsecurity and row.relforcerowsecurity and row.owner != _APP_ROLE
    assert "app.tenant_id" in policy.qual and "app.tenant_id" in policy.with_check
    with app_engine.connect() as c, c.begin():
        assert c.execute(text(f"SELECT count(*) FROM {table}")).scalar() == 0


async def test_a10_org_b_reads_and_writes_nothing_of_org_a_in_either_table(
    kc, app_engine,  # noqa: F811
) -> None:
    from gateway.db import tenant_session

    _assert_non_priv(app_engine)
    _owner, sid = await _indexed_source(kc, "T1", _files("T1", 1))
    node = _node_id(kc, sid, "doc0-T1")
    _include(kc, sid, "inc-T1")
    doc = _seed_doc(kc, node)

    def counts(org: str) -> tuple[int, int]:
        with app_engine.connect() as c, c.begin():
            c.execute(text("SELECT set_config('app.tenant_id', :o, true)"), {"o": org})
            return (int(c.execute(text("SELECT count(*) FROM kb_documents")).scalar()),
                    int(c.execute(text("SELECT count(*) FROM kb_source_includes")).scalar()))

    assert counts(kc.org_b) == (0, 0) and counts(kc.org_a) >= (1, 1)
    async with tenant_engine_scope(_app_dsn(kc)), tenant_session(kc.org_b) as db:
        changed = (await db.execute(text(
            "UPDATE kb_documents SET text = 'stolen' WHERE node_id = CAST(:n AS uuid)"),
            {"n": node})).rowcount
        gone = (await db.execute(text(
            "DELETE FROM kb_documents WHERE node_id = CAST(:n AS uuid)"), {"n": node})).rowcount
        left = (await db.execute(text(
            "DELETE FROM kb_source_includes WHERE source_id = CAST(:s AS uuid)"),
            {"s": sid})).rowcount
    assert (changed, gone, left) == (0, 0, 0)
    assert _doc_count(kc, sid) == 1 and _chunk_count(kc, doc) == 2
    # WITH CHECK: bound to one org, a row that names the other is refused.
    for bound, named in ((kc.org_b, kc.org_a), (kc.org_a, kc.org_b)):
        with pytest.raises(DBAPIError), app_engine.connect() as c, c.begin():
            c.execute(text("SELECT set_config('app.tenant_id', :o, true)"), {"o": bound})
            c.execute(text(
                "INSERT INTO kb_source_includes (organization_id, source_id, folder_ext_id, "
                "included_by) VALUES (CAST(:o AS uuid), CAST(:s AS uuid), 'x', 'y@z.test')"),
                {"o": named, "s": sid})
        with pytest.raises(DBAPIError), app_engine.connect() as c, c.begin():
            c.execute(text("SELECT set_config('app.tenant_id', :o, true)"), {"o": bound})
            c.execute(text(
                "INSERT INTO kb_documents (organization_id, node_id, sha, parser) "
                "VALUES (CAST(:o AS uuid), CAST(:n AS uuid), 's', 'txt')"),
                {"o": named, "n": node})


async def test_a10_the_widened_kind_takes_index_and_keeps_the_old_values(
    kc, app_engine,  # noqa: F811
) -> None:
    _owner, sid = await _indexed_source(kc, "W2", _files("W2", 0))
    with kc.admin_engine.connect() as c:
        [row] = c.execute(text(
            "SELECT pg_get_constraintdef(oid), convalidated FROM pg_constraint "
            "WHERE conname = 'kb_sync_runs_kind_check'")).fetchall()
        cursor_def = c.execute(text(
            "SELECT pg_get_constraintdef(oid) FROM pg_constraint "
            "WHERE conname = 'kb_sync_cursors_kind_check'")).scalar()
    for kind in ("discover", "changes", "reconcile", "access", "index"):
        assert f"'{kind}'" in row[0], kind
    assert row[1] is True
    # The cursor kinds do NOT widen: the index pass keeps no cursor.
    assert "'index'" not in cursor_def, cursor_def
    ids = {"o": kc.org_a, "s": sid}
    for kind in ("index", "discover", "changes", "reconcile", "access"):
        with app_engine.connect() as c, c.begin():
            c.execute(text("SELECT set_config('app.tenant_id', :o, true)"), {"o": kc.org_a})
            c.execute(text("INSERT INTO kb_sync_runs (organization_id, source_id, kind, status) "
                           "VALUES (CAST(:o AS uuid), CAST(:s AS uuid), :k, 'ok')"),
                      {**ids, "k": kind})
    with pytest.raises(DBAPIError, match="kind_check"), kc.admin_engine.begin() as c:
        c.execute(text("INSERT INTO kb_sync_runs (organization_id, source_id, kind, status) "
                       "VALUES (CAST(:o AS uuid), CAST(:s AS uuid), 'bogus', 'ok')"), ids)


async def test_one_document_for_each_node_and_one_include_for_each_folder(
    kc, app_engine,  # noqa: F811
) -> None:
    _owner, sid = await _indexed_source(kc, "U1", _files("U1", 1))
    node = _node_id(kc, sid, "doc0-U1")
    _seed_doc(kc, node, chunks=0)
    with pytest.raises(DBAPIError, match="kb_documents_node_id_key"), kc.admin_engine.begin() as c:
        c.execute(text(
            "INSERT INTO kb_documents (organization_id, node_id, sha, parser) "
            "VALUES (CAST(:o AS uuid), CAST(:n AS uuid), 's2', 'txt')"),
            {"o": kc.org_a, "n": node})
    _include(kc, sid, "inc-U1")
    with pytest.raises(DBAPIError, match="kb_source_includes_key"), kc.admin_engine.begin() as c:
        c.execute(text(
            "INSERT INTO kb_source_includes (organization_id, source_id, folder_ext_id, "
            "included_by) VALUES (CAST(:o AS uuid), CAST(:s AS uuid), 'inc-U1', 'b@z.test')"),
            {"o": kc.org_a, "s": sid})


def _shape(c: Any) -> dict[str, Any]:
    return {
        "columns": sorted(tuple(r) for r in c.execute(text(
            "SELECT table_name, column_name, data_type, is_nullable FROM "
            "information_schema.columns WHERE table_name IN ('kb_documents', "
            "'kb_source_includes')"))),
        "indexes": sorted(tuple(r) for r in c.execute(text(
            "SELECT tablename, indexname, indexdef FROM pg_indexes "
            "WHERE tablename IN ('kb_documents', 'kb_source_includes')"))),
        "policies": sorted(tuple(r) for r in c.execute(text(
            "SELECT tablename, policyname, qual, with_check FROM pg_policies "
            "WHERE tablename IN ('kb_documents', 'kb_source_includes')"))),
        "checks": sorted(tuple(r) for r in c.execute(text(
            "SELECT conname, pg_get_constraintdef(oid), convalidated FROM pg_constraint "
            "WHERE conrelid = ANY(ARRAY['kb_documents'::regclass, "
            "'kb_source_includes'::regclass, 'kb_sync_runs'::regclass])"))),
        "trigger": sorted(tuple(r) for r in c.execute(text(
            "SELECT tgname, pg_get_triggerdef(oid) FROM pg_trigger "
            "WHERE tgrelid = 'kb_documents'::regclass AND NOT tgisinternal"))),
    }


def test_the_migration_replays_with_no_change(kc) -> None:  # noqa: F811
    from tests.unit._tenant_ladder import _exec_file

    with kc.admin_engine.connect() as c:
        before = _shape(c)
    assert any(i[1] == "kb_documents_org_node_idx" for i in before["indexes"])
    assert [t[0] for t in before["trigger"]] == ["kb_documents_delete_chunks"]
    for _ in range(2):
        with kc.admin_engine.begin() as c:
            _exec_file(c, str(_documents_migration()))
    with kc.admin_engine.connect() as c:
        after = _shape(c)
    assert after == before


# ── A6 (part): the trigger on each delete path ──────────────────────────────

async def test_a6_a_deleted_node_takes_its_document_and_chunks(
    kc, app_engine,  # noqa: F811
) -> None:
    """The discovery clear and a node delete: the foreign key cascades to the
    document, and the trigger takes the chunks, as the app role."""
    from gateway.db import tenant_session

    _assert_non_priv(app_engine)
    _owner, sid = await _indexed_source(kc, "D1", _files("D1", 2))
    docs = [_seed_doc(kc, _node_id(kc, sid, f"doc{i}-D1")) for i in range(2)]
    assert _chunk_count(kc) >= 4
    async with tenant_engine_scope(_app_dsn(kc)), tenant_session(kc.org_a) as db:
        await db.execute(text(
            "DELETE FROM kb_source_nodes WHERE source_id = CAST(:s AS uuid) "
            "AND ext_id = 'doc0-D1'"), {"s": sid})
    assert _chunk_count(kc, docs[0]) == 0 and _chunk_count(kc, docs[1]) == 2
    async with tenant_engine_scope(_app_dsn(kc)), tenant_session(kc.org_a) as db:
        await db.execute(text(
            "DELETE FROM kb_source_nodes WHERE source_id = CAST(:s AS uuid)"), {"s": sid})
    assert _chunk_count(kc, docs[1]) == 0 and _doc_count(kc, sid) == 0


async def test_a6_a_disconnect_leaves_zero_document_chunks(
    kc, app_engine,  # noqa: F811
) -> None:
    from tests.unit.test_kc_sync_r8 import _disconnect

    _assert_non_priv(app_engine)
    owner, sid = await _indexed_source(kc, "D2", _files("D2", 2))
    _include(kc, sid, "inc-D2")
    docs = [_seed_doc(kc, _node_id(kc, sid, f"doc{i}-D2")) for i in range(2)]
    assert sum(_chunk_count(kc, d) for d in docs) == 4
    async with tenant_engine_scope(_app_dsn(kc)):
        out = await _disconnect(owner, sid)()
    assert out["state"] == "disconnected", out
    assert [_chunk_count(kc, d) for d in docs] == [0, 0] and _doc_count(kc, sid) == 0
    with kc.admin_engine.connect() as c:
        assert c.execute(text(
            "SELECT count(*) FROM kb_source_includes WHERE source_id = CAST(:s AS uuid)"),
            {"s": sid}).scalar() == 1, "a disconnect keeps the source row and its includes"


async def test_a6_a_member_purge_leaves_zero_document_chunks(
    kc, app_engine, recorder,  # noqa: F811
) -> None:
    from tests.unit.test_email_purge_wiring import member_purge, seed_member, wipe
    from tests.unit.test_kb_pages_r8 import _address
    from tests.unit.test_kc_sources_r8 import _grant_both_to_app_role

    _assert_non_priv(app_engine)
    _grant_both_to_app_role(kc.admin_engine)
    keeper, leaver = _address("keeper"), _address("leaver-ix")
    seed_member(kc.admin_engine, kc.org_b, keeper, owner=True)
    seed_member(kc.admin_engine, kc.org_b, leaver)
    m = _principal(leaver, kc.org_b)
    try:
        sid = await _connect(kc, m, email="leaver-ix@kc1a-g.test", account_id="D3",
                             files=_files("D3", 2))
        docs = [_seed_doc(kc, _node_id(kc, sid, f"doc{i}-D3"), org=kc.org_b)
                for i in range(2)]
        assert sum(_chunk_count(kc, d) for d in docs) == 4
        await member_purge(kc, leaver.lower())
        assert [_chunk_count(kc, d) for d in docs] == [0, 0]
        assert _doc_count(kc, sid) == 0
    finally:
        wipe(kc.admin_engine, keeper, leaver)


def test_the_trigger_is_the_only_path_a_chunk_leaves_with_its_document(
    kc,  # noqa: F811
) -> None:
    """Without the trigger the chunks would stay: ``kb_chunks.source_id`` has
    no foreign key. Dropping the trigger must turn the cascade tests red."""
    with kc.admin_engine.connect() as c:
        assert c.execute(text(
            "SELECT count(*) FROM pg_constraint WHERE conrelid = 'kb_chunks'::regclass "
            "AND contype = 'f' AND pg_get_constraintdef(oid) ILIKE '%kb_documents%'"
        )).scalar() == 0
        assert c.execute(text(
            "SELECT count(*) FROM pg_trigger WHERE tgrelid = 'kb_documents'::regclass "
            "AND tgname = 'kb_documents_delete_chunks' AND NOT tgisinternal")).scalar() == 1


# ═══════════════════════════════════════════════════════════════════════════
# The index pass on a real database (KC-5a). The adapter is a fake: the real
# Google fetch is KC-5b.
# ═══════════════════════════════════════════════════════════════════════════

class Docs:
    """A fake adapter. It serves the bytes of each file and records each call."""

    provider = "google_drive"

    def __init__(self) -> None:
        self.files: dict[str, Any] = {}
        self.calls: list[str] = []
        self.gate: Any = None

    async def fetch_document(self, cred: Any, node: Any) -> Any:
        from gateway.kb_sources.base import FetchedFile

        if self.gate is not None:
            await self.gate.wait()
        self.calls.append(node.ext_id)
        item = self.files[node.ext_id]
        if isinstance(item, BaseException):
            raise item
        return FetchedFile(data=item[0], suffix=item[1])

    async def refresh(self, cred: Any) -> Any:
        return cred

    async def list_access(self, cred: Any, cursor: Any, *, budget: int) -> Any:
        raise AssertionError("the index pass read the access listing")

    async def discover(self, cred: Any, cursor: Any = None) -> Any:
        raise AssertionError("the index pass read the tree")

    async def list_changed(self, cred: Any, cursor: Any, folders: Any) -> Any:
        raise AssertionError("the index pass read the change feed")


@pytest.fixture
def idx(kc, monkeypatch):  # noqa: F811 — a fixture
    """The fixture of the other R8 suites with the index flags on, and a fake
    adapter. ``extracted`` counts each call of the extraction."""
    from acb_common import get_settings
    from gateway.kb_sync import index, scheduler

    settings = get_settings()
    for name, value in (("knowledge_enabled", True), ("knowledge_orgs", "*"),
                        ("knowledge_sync", "*"), ("knowledge_index", "*")):
        monkeypatch.setattr(settings, name, value, raising=False)
    scheduler._stopping.clear()
    kc.docs = Docs()
    kc.extracted = []
    real = index._read_file

    def counted(data: bytes, suffix: str, name: str) -> Any:
        kc.extracted.append(name)
        return real(data, suffix, name)

    monkeypatch.setattr(index, "_read_file", counted)
    return kc


def _settle(p: Any, sid: str) -> None:
    """Make the change feed and the access pass NOT due, so only the index pass is."""
    with p.admin_engine.begin() as c:
        c.execute(text(
            "INSERT INTO kb_sync_runs (organization_id, source_id, kind, status, started_at, "
            "finished_at) VALUES (CAST(:o AS uuid), CAST(:s AS uuid), 'changes', 'ok', "
            "now() - interval '1 minute', now() - interval '1 minute')"),
            {"o": p.org_a, "s": sid})
        c.execute(text("UPDATE kb_sources SET access_read_at = now() "
                       "WHERE id = CAST(:s AS uuid)"), {"s": sid})


async def _tick(p: Any) -> int:
    from gateway.kb_sync import scheduler

    async with tenant_engine_scope(_app_dsn(p)):
        return await scheduler.run_tick(lambda: p.docs)


async def _pass(p: Any, sid: str, org: str | None = None) -> Any:
    from gateway.kb_sync import index

    async with tenant_engine_scope(_app_dsn(p)):
        return await index.run_index(org or p.org_a, sid, source=p.docs)


async def _hits(p: Any, user: Any, q: str, k: int = 60, spaces: Any = None) -> list[Any]:
    from gateway.db import tenant_session
    from gateway.kb_index.query import query_documents

    async with tenant_engine_scope(_app_dsn(p)), tenant_session(user.organization_id) as db:
        return list((await query_documents(db, user, q, k, spaces))["hits"])


def _doc_row(p: Any, sid: str, ext: str) -> Any:
    with p.admin_engine.connect() as c:
        return c.execute(text(
            "SELECT d.id, d.sha, d.parser, d.truncated, d.text, n.content_hash "
            "FROM kb_documents d JOIN kb_source_nodes n ON n.id = d.node_id "
            "WHERE n.source_id = CAST(:s AS uuid) AND n.ext_id = :e"),
            {"s": sid, "e": ext}).first()


def _chunk_ids(p: Any, doc: str) -> dict[str, int]:
    with p.admin_engine.connect() as c:
        return {r.content_sha: int(r.id) for r in c.execute(text(
            "SELECT id, content_sha FROM kb_chunks WHERE source_kind = 'document' "
            "AND source_id = CAST(:d AS uuid)"), {"d": doc})}


def _node_set(p: Any, sid: str, ext: str, sql: str) -> None:
    with p.admin_engine.begin() as c:
        c.execute(text(f"UPDATE kb_source_nodes SET {sql} WHERE "
                       "source_id = CAST(:s AS uuid) AND ext_id = :e"), {"s": sid, "e": ext})


def _text_file(i: int, acct: str) -> tuple[bytes, str]:
    return f"Quarterly plan number {i} with unique{i}word in it".encode(), ".txt"


# ── A1: one tick indexes an included folder, and the owner finds it ─────────

async def test_a1_one_tick_indexes_fifty_files_and_the_owner_finds_them(
    idx, app_engine,  # noqa: F811
) -> None:
    _assert_non_priv(app_engine)
    owner, sid = await _indexed_source(idx, "I1", _files("I1", 50))
    _include(idx, sid, "inc-I1")
    _settle(idx, sid)
    for i in range(50):
        idx.docs.files[f"doc{i}-I1"] = _text_file(i, "I1")
    assert await _tick(idx) == 1
    assert sorted(idx.docs.calls) == sorted(f"doc{i}-I1" for i in range(50))
    hits = await _hits(idx, owner, "quarterly")
    assert len({h["document_id"] for h in hits}) == 50, len(hits)
    assert {h["name"] for h in hits} == {f"doc{i}.txt" for i in range(50)}
    one = await _hits(idx, owner, "unique7word")
    assert [h["heading_path"] for h in one] == ["doc7.txt"] and one[0]["locator"] == {}
    assert set(one[0]) == {"document_id", "node_id", "name", "web_url", "heading_path",
                           "text", "locator", "score"}
    # The chunks sit in the OWNER's me space, and the run is an 'index' run.
    with idx.admin_engine.connect() as c:
        spaces = c.execute(text(
            "SELECT DISTINCT s.kind, s.owner_email FROM kb_chunks k JOIN kb_spaces s "
            "ON s.id = k.space_id WHERE k.source_kind = 'document'")).fetchall()
        kinds = [r[0] for r in c.execute(text(
            "SELECT kind FROM kb_sync_runs WHERE source_id = CAST(:s AS uuid) "
            "ORDER BY started_at"), {"s": sid})]
    assert [tuple(r) for r in spaces] == [("me", owner.email.lower())], spaces
    assert sorted(kinds) == ["changes", "discover", "index"], kinds
    # A second tick has nothing to do: every stamp is read.
    calls = len(idx.docs.calls)
    assert await _tick(idx) == 0 and len(idx.docs.calls) == calls


async def test_a1_a_member_who_is_not_the_owner_finds_nothing_in_the_owner_space(
    idx, app_engine,  # noqa: F811
) -> None:
    owner, sid = await _indexed_source(idx, "I1b", _files("I1b", 2))
    _include(idx, sid, "inc-I1b")
    for i in range(2):
        idx.docs.files[f"doc{i}-I1b"] = _text_file(i, "I1b")
    assert (await _pass(idx, sid)).status == "ok"
    other = _member(idx, "other")
    # Even a proven reader cannot read another member's me space.
    node = _node_id(idx, sid, "doc0-I1b")
    with idx.admin_engine.begin() as c:
        c.execute(text(
            "INSERT INTO kb_node_access (organization_id, node_id, principal_kind, principal, "
            "role) VALUES (CAST(:o AS uuid), CAST(:n AS uuid), 'user', :p, 'reader')"),
            {"o": idx.org_a, "n": node, "p": other.email.lower()})
    assert await _hits(idx, other, "quarterly") == []
    assert len(await _hits(idx, owner, "quarterly")) == 2


# ── A2: an unchanged file costs nothing ─────────────────────────────────────

async def test_a2_an_unchanged_file_makes_zero_fetch_and_zero_extract_calls(
    idx, app_engine,  # noqa: F811
) -> None:
    _owner, sid = await _indexed_source(idx, "I2", _files("I2", 3))
    _include(idx, sid, "inc-I2")
    for i in range(3):
        idx.docs.files[f"doc{i}-I2"] = _text_file(i, "I2")
    first = await _pass(idx, sid)
    assert first.status == "ok" and first.counts["files"] == 3, first
    assert len(idx.docs.calls) == 3 and len(idx.extracted) == 3
    second = await _pass(idx, sid)
    assert second.status == "ok" and second.counts["pending"] == 0, second
    assert len(idx.docs.calls) == 3 and len(idx.extracted) == 3, (
        "an unchanged file was fetched or extracted again")
    row = _doc_row(idx, sid, "doc0-I2")
    assert row.sha == row.content_hash == f"{0:032x}", "the sha is the provider stamp"


async def test_a2_a_node_with_no_hash_uses_its_modified_time_and_a_name_only_node_is_skipped(
    idx, app_engine,  # noqa: F811
) -> None:
    files = _files("I2b", 0)
    files += [
        {"id": "m-I2b", "name": "m.txt", "parents": ["inc-I2b"], "mimeType": "text/plain",
         "size": "40", "modifiedTime": "2026-10-20T08:00:00.000Z"},
        {"id": "n-I2b", "name": "n.txt", "parents": ["inc-I2b"], "mimeType": "text/plain",
         "size": "40"},
    ]
    _owner, sid = await _indexed_source(idx, "I2b", files)
    _include(idx, sid, "inc-I2b")
    idx.docs.files["m-I2b"] = (b"stamped by time", ".txt")
    idx.docs.files["n-I2b"] = (b"never read", ".txt")
    out = await _pass(idx, sid)
    assert out.status == "ok" and idx.docs.calls == ["m-I2b"], (out, idx.docs.calls)
    assert _doc_row(idx, sid, "m-I2b").sha == "m:2026-10-20T08:00:00.000000Z"
    assert _doc_row(idx, sid, "n-I2b") is None


# ── A3 and A5: a deck, and an edit that keeps the unchanged chunks ──────────

def _deck_files(acct: str) -> list[dict[str, Any]]:
    files = _files(acct, 0)
    files.append({"id": f"deck-{acct}", "name": "Pitch.pptx", "parents": [f"inc-{acct}"],
                  "mimeType": "application/vnd.openxmlformats-officedocument."
                              "presentationml.presentation",
                  "size": "5000", "modifiedTime": "2026-10-20T08:00:00.000Z",
                  "md5Checksum": "a" * 32})
    return files


async def test_a3_and_a5_a_deck_hit_names_its_slide_and_an_edit_keeps_the_other_chunks(
    idx, app_engine,  # noqa: F811
) -> None:
    from tests.unit.test_attachment_formats import _pptx

    owner, sid = await _indexed_source(idx, "I3", _deck_files("I3"))
    _include(idx, sid, "inc-I3")
    idx.docs.files["deck-I3"] = (_pptx("Launch alpha", "Pricing is flat", "Risks and owners"),
                                 ".pptx")
    assert (await _pass(idx, sid)).status == "ok"
    [hit] = await _hits(idx, owner, "flat")
    assert hit["heading_path"] == "Pitch.pptx > Slide 2" and hit["locator"] == {"slide": 2}
    assert hit["name"] == "Pitch.pptx" and "Pricing is flat" in hit["text"]
    doc = _doc_row(idx, sid, "deck-I3")
    assert (doc.parser, doc.truncated) == ("pptx", False)
    before = _chunk_ids(idx, str(doc.id))
    assert len(before) == 3
    # The file is edited: a new stamp, and slide 2 has new words.
    _node_set(idx, sid, "deck-I3", "content_hash = '" + "b" * 32 + "'")
    idx.docs.files["deck-I3"] = (_pptx("Launch alpha", "Pricing is tiered", "Risks and owners"),
                                 ".pptx")
    out = await _pass(idx, sid)
    assert out.counts["files"] == 1 and idx.docs.calls.count("deck-I3") == 2, out
    after = _chunk_ids(idx, str(doc.id))
    kept = set(before) & set(after)
    assert len(kept) == 2 and len(after) == 3, "only the changed chunk may be replaced"
    assert all(before[s] == after[s] for s in kept), "a kept chunk got a new id"
    assert out.counts["chunks"] == 1 and out.counts["removed"] == 1, out.counts
    assert await _hits(idx, owner, "flat") == []
    [new] = await _hits(idx, owner, "tiered")
    assert new["heading_path"] == "Pitch.pptx > Slide 2"
    assert _doc_row(idx, sid, "deck-I3").sha == "b" * 32


# ── A4: the caps ────────────────────────────────────────────────────────────

async def test_a4_a_node_over_25_mb_is_never_fetched_and_a_pdf_keeps_100_pages(
    idx, app_engine,  # noqa: F811
) -> None:
    pymupdf = pytest.importorskip("pymupdf")
    files = _files("I4", 1)
    files[1]["size"] = str(25 * 1024 * 1024 + 1)
    files.append({"id": "pdf-I4", "name": "Long.pdf", "parents": ["inc-I4"],
                  "mimeType": "application/pdf", "size": "9000",
                  "modifiedTime": "2026-10-20T08:00:00.000Z", "md5Checksum": "c" * 32})
    owner, sid = await _indexed_source(idx, "I4", files)
    _include(idx, sid, "inc-I4")
    pdf = pymupdf.open()
    for n in range(1, 121):
        pdf.new_page().insert_text((72, 72), f"Clause number {n} alpha")
    idx.docs.files["pdf-I4"] = (pdf.tobytes(), ".pdf")
    idx.docs.files["doc0-I4"] = _text_file(0, "I4")  # never served: over the cap
    out = await _pass(idx, sid)
    assert out.status == "ok", out
    assert idx.docs.calls == ["pdf-I4"], "a node over 25 MB was fetched"
    big = _doc_row(idx, sid, "doc0-I4")
    assert (big.parser, big.text, big.truncated) == ("none", "", True), big
    assert big.sha == f"{0:032x}", "the refusal keeps the stamp, so it is not pending again"
    doc = _doc_row(idx, sid, "pdf-I4")
    assert doc.truncated is True and doc.parser == "pdf"
    with idx.admin_engine.connect() as c:
        pages = [r[0] for r in c.execute(text(
            "SELECT (metadata->>'page')::int FROM kb_chunks WHERE source_kind = 'document' "
            "AND source_id = CAST(:d AS uuid) ORDER BY ordinal"), {"d": str(doc.id)})]
        refused_chunks = c.execute(text(
            "SELECT count(*) FROM kb_chunks WHERE source_id = CAST(:d AS uuid)"),
            {"d": str(big.id)}).scalar()
    assert pages == list(range(1, 101)), (pages[:3], pages[-3:])
    assert refused_chunks == 0
    assert (await _pass(idx, sid)).counts["pending"] == 0, "a refusal row stayed pending"
    [last] = await _hits(idx, owner, "clause number 100")
    assert last["locator"] == {"page": 100}
    assert await _hits(idx, owner, "clause number 101") == []


async def test_a4_a_refusal_is_read_again_only_when_the_stamp_changes(
    idx, app_engine,  # noqa: F811
) -> None:
    from gateway.kb_sources.base import SourceError

    _owner, sid = await _indexed_source(idx, "I4b", _files("I4b", 2))
    _include(idx, sid, "inc-I4b")
    gone = SourceError("Google answered 404")
    gone.code = "http_404"
    idx.docs.files["doc0-I4b"] = gone
    idx.docs.files["doc1-I4b"] = (b"MZ....", ".exe")
    out = await _pass(idx, sid)
    assert out.status == "ok" and out.counts["refused"] == 2, out
    assert (await _pass(idx, sid)).counts["pending"] == 0
    assert sorted(idx.docs.calls) == ["doc0-I4b", "doc1-I4b"]
    _node_set(idx, sid, "doc0-I4b", "content_hash = '" + "d" * 32 + "'")
    idx.docs.files["doc0-I4b"] = _text_file(0, "I4b")
    again = await _pass(idx, sid)
    assert again.counts["files"] == 1 and _doc_row(idx, sid, "doc0-I4b").parser == "txt"


# ── A6: a gone node, and a folder that left scope ───────────────────────────

async def test_a6_a_gone_node_and_an_excluded_folder_lose_documents_and_chunks_in_one_pass(
    idx, app_engine,  # noqa: F811
) -> None:
    owner, sid = await _indexed_source(idx, "I6", _files("I6", 3))
    _include(idx, sid, "inc-I6")
    for i in range(3):
        idx.docs.files[f"doc{i}-I6"] = _text_file(i, "I6")
    assert (await _pass(idx, sid)).counts["files"] == 3
    docs = [str(_doc_row(idx, sid, f"doc{i}-I6").id) for i in range(3)]
    assert all(_chunk_count(idx, d) >= 1 for d in docs)
    _node_set(idx, sid, "doc1-I6", "state = 'gone'")
    out = await _pass(idx, sid)
    assert out.status == "ok" and out.counts["deleted"] == 1, out
    assert _doc_row(idx, sid, "doc1-I6") is None and _chunk_count(idx, docs[1]) == 0
    assert _chunk_count(idx, docs[0]) >= 1 and _chunk_count(idx, docs[2]) >= 1
    assert len(await _hits(idx, owner, "quarterly")) == 2
    # The folder loses its include row: every document of it goes.
    with idx.admin_engine.begin() as c:
        c.execute(text("DELETE FROM kb_source_includes WHERE source_id = CAST(:s AS uuid)"),
                  {"s": sid})
    left = await _pass(idx, sid)
    assert left.counts["deleted"] == 2 and _doc_count(idx, sid) == 0, left
    assert _chunk_count(idx) == 0 and await _hits(idx, owner, "quarterly") == []


# ── A8 and A9: the flag, and no include row ─────────────────────────────────

async def test_a8_with_the_flag_empty_no_index_run_no_provider_call(
    idx, app_engine, monkeypatch,  # noqa: F811
) -> None:
    from acb_common import get_settings

    monkeypatch.setattr(get_settings(), "knowledge_index", "", raising=False)
    _owner, sid = await _indexed_source(idx, "I8", _files("I8", 2))
    _include(idx, sid, "inc-I8")
    _settle(idx, sid)
    idx.docs.files["doc0-I8"] = _text_file(0, "I8")
    assert await _tick(idx) == 0
    assert idx.docs.calls == [] and _doc_count(idx, sid) == 0
    with idx.admin_engine.connect() as c:
        assert c.execute(text(
            "SELECT count(*) FROM kb_sync_runs WHERE kind = 'index' "
            "AND source_id = CAST(:s AS uuid)"), {"s": sid}).scalar() == 0


async def test_a9_with_no_include_row_nothing_is_fetched_and_no_space_is_made(
    idx, app_engine,  # noqa: F811
) -> None:
    owner, sid = await _indexed_source(idx, "I9", _files("I9", 4))
    _settle(idx, sid)
    for i in range(4):
        idx.docs.files[f"doc{i}-I9"] = _text_file(i, "I9")
    assert await _tick(idx) == 0, "a source with no include row was due"
    out = await _pass(idx, sid)
    assert out.status == "ok" and out.counts["pending"] == 0, out
    assert idx.docs.calls == [] and _doc_count(idx, sid) == 0
    with idx.admin_engine.connect() as c:
        assert c.execute(text(
            "SELECT count(*) FROM kb_spaces WHERE kind = 'me' AND owner_email = :e"),
            {"e": owner.email.lower()}).scalar() == 0, "a me space was made for nothing"
    # An include row of ANOTHER source does not widen this one.
    other_owner, other = await _indexed_source(idx, "I9o", _files("I9o", 1))
    _include(idx, other, "inc-I9")  # the folder id of the first source
    out = await _pass(idx, sid)
    assert out.counts["pending"] == 0 and idx.docs.calls == []
    assert other_owner.email != owner.email


# ── A11: the include walk ───────────────────────────────────────────────────

def _put_nodes(p: Any, sid: str, nodes: list[tuple[str, str | None, str, str]]) -> None:
    """``(ext_id, parent_ext_id, kind, state)`` rows, each with a content hash."""
    with p.admin_engine.begin() as c:
        for ext, parent, kind, state in nodes:
            c.execute(text(
                "INSERT INTO kb_source_nodes (organization_id, source_id, ext_id, "
                "parent_ext_id, kind, name, state, content_hash) VALUES (CAST(:o AS uuid), "
                "CAST(:s AS uuid), :e, :p, :k, :n, :st, :h)"),
                {"o": p.org_a, "s": sid, "e": ext, "p": parent, "k": kind, "n": ext,
                 "st": state, "h": None if kind == "folder" else f"h-{ext}"})


async def _scope(p: Any, sid: str) -> set[str]:
    from gateway.db import tenant_session
    from gateway.kb_sync import index

    async with tenant_engine_scope(_app_dsn(p)), tenant_session(p.org_a) as db:
        rows = (await db.execute(text(index._PENDING_SQL), {
            "org": p.org_a, "source": sid, "depth": index.DEPTH, "n": 1000})).fetchall()
        count = await index.pending_count(db, p.org_a, sid)
    assert count == len(rows), "the count and the read disagree"
    return {r.ext_id for r in rows}


async def test_a11_the_walk_handles_a_deep_tree_a_cycle_a_ghost_folder_and_gone_nodes(
    idx, app_engine,  # noqa: F811
) -> None:
    _owner, sid = await _indexed_source(idx, "I11", _files("I11", 0))
    nodes: list[tuple[str, str | None, str, str]] = [("f0", "somewhere", "folder", "live")]
    nodes += [(f"f{i}", f"f{i - 1}", "folder", "live") for i in range(1, 150)]
    nodes += [
        ("file-near", "f5", "file", "live"),
        ("file-edge", "f99", "file", "live"),   # the last level inside the cap
        ("file-over", "f100", "file", "live"),  # one level past the cap
        ("file-far", "f130", "file", "live"),
        # A cycle: X is a child of Y and Y is a child of X.
        ("cx", "cy", "folder", "live"), ("cy", "cx", "folder", "live"),
        ("file-cx", "cx", "file", "live"), ("file-cy", "cy", "file", "live"),
        # A folder that is gone, with a live file below it, and a gone file.
        ("gone-dir", "f0", "folder", "gone"), ("file-under-gone", "gone-dir", "file", "live"),
        ("file-gone", "f0", "file", "gone"),
        # A file in a folder that no include row names.
        ("elsewhere", "other-root", "folder", "live"), ("file-else", "elsewhere", "file", "live"),
        # Under a ghost folder: the include row names a folder that is no node.
        ("file-ghost", "ghost-folder", "file", "live"),
    ]
    _put_nodes(idx, sid, nodes)
    for folder in ("f0", "cx", "ghost-folder"):
        _include(idx, sid, folder)
    scope = await asyncio.wait_for(_scope(idx, sid), 30)
    assert scope == {"file-near", "file-edge", "file-cx", "file-cy", "file-ghost"}, scope


async def test_a11_a_node_with_no_hash_and_no_time_is_in_scope_but_never_pending(
    idx, app_engine,  # noqa: F811
) -> None:
    _owner, sid = await _indexed_source(idx, "I11b", _files("I11b", 0))
    _put_nodes(idx, sid, [("bare", "inc", "file", "live")])
    with idx.admin_engine.begin() as c:
        c.execute(text("UPDATE kb_source_nodes SET content_hash = NULL WHERE ext_id = 'bare'"))
    _include(idx, sid, "inc")
    assert await _scope(idx, sid) == set()


# ── A7: who may read the text ───────────────────────────────────────────────

async def test_a7_the_space_and_the_provider_both_gate_the_text(
    idx, app_engine,  # noqa: F811
) -> None:
    from tests.unit.test_kc_access_r8 import _space

    owner, sid = await _indexed_source(idx, "I7", _files("I7", 1))
    _include(idx, sid, "inc-I7")
    idx.docs.files["doc0-I7"] = (b"The merger plan has a codename", ".txt")
    assert (await _pass(idx, sid)).counts["files"] == 1
    node = _node_id(idx, sid, "doc0-I7")
    doc = str(_doc_row(idx, sid, "doc0-I7").id)
    reader, stale, plain, boss = (_member(idx, "r"), _member(idx, "s"), _member(idx, "m"),
                                  _member(idx, "boss", CAPABILITY))
    with idx.admin_engine.begin() as c:
        for who, hours in ((reader, 0), (stale, 25)):
            c.execute(text(
                "INSERT INTO kb_node_access (organization_id, node_id, principal_kind, "
                "principal, role, synced_at) VALUES (CAST(:o AS uuid), CAST(:n AS uuid), "
                "'user', :p, 'reader', now() - make_interval(hours => :h))"),
                {"o": idx.org_a, "n": node, "p": who.email.lower(), "h": hours})
    # In the owner's me space only the owner reads it, a proven reader too.
    assert len(await _hits(idx, owner, "codename")) == 1
    for who in (reader, stale, plain, boss):
        assert await _hits(idx, who, "codename") == [], who.email
    # Filed to a team space that the whole org reads (what KC-6 will do): the
    # space half is open, so the PROVIDER half decides.
    team = _space(idx, grants=["org"])
    with idx.admin_engine.begin() as c:
        c.execute(text("UPDATE kb_chunks SET space_id = CAST(:sp AS uuid) "
                       "WHERE source_kind = 'document' AND source_id = CAST(:d AS uuid)"),
                  {"sp": team, "d": doc})
    assert len(await _hits(idx, owner, "codename")) == 1, "the source owner is a proven reader"
    assert len(await _hits(idx, reader, "codename")) == 1, "a user row proves its reader"
    assert await _hits(idx, plain, "codename") == [], "a space reader with no proof got a hit"
    assert await _hits(idx, stale, "codename") == [], "a stale synced_at (over 24 h) got a hit"
    assert await _hits(idx, boss, "codename") == [], "knowledge:manage proves nothing"
    # The space filter narrows and never widens.
    assert len(await _hits(idx, reader, "codename", spaces=[team])) == 1
    assert await _hits(idx, reader, "codename", spaces=[str(uuid.uuid4())]) == []
    # A source that is not ready proves nobody, the owner too.
    with idx.admin_engine.begin() as c:
        c.execute(text("UPDATE kb_sources SET state = 'needs_reconnect' "
                       "WHERE id = CAST(:s AS uuid)"), {"s": sid})
    assert await _hits(idx, owner, "codename") == []
    assert await _hits(idx, reader, "codename") == []


async def test_a7_org_b_finds_nothing_of_org_a(idx, app_engine) -> None:  # noqa: F811
    owner, sid = await _indexed_source(idx, "I7b", _files("I7b", 1))
    _include(idx, sid, "inc-I7b")
    idx.docs.files["doc0-I7b"] = (b"The merger plan has a codename", ".txt")
    assert (await _pass(idx, sid)).counts["files"] == 1
    assert len(await _hits(idx, owner, "codename")) == 1
    b_member = _principal(f"b.{uuid.uuid4().hex[:6]}@{DOMAIN}", idx.org_b)
    assert await _hits(idx, b_member, "codename") == []
    # Org B cannot run the pass of a source of org A.
    calls = len(idx.docs.calls)
    out = await _pass(idx, sid, org=idx.org_b)
    assert out.status == "skipped" and len(idx.docs.calls) == calls


# ── The lock order on a real database ───────────────────────────────────────

@pytest.mark.parametrize("first", ["index", "disconnect"])
async def test_the_lock_order_a_file_write_and_a_disconnect_never_deadlock(
    idx, app_engine, monkeypatch, first: str,  # noqa: F811
) -> None:
    """The file transaction takes the source row first (the guard), then the
    node, the document and the chunks. The disconnect takes the connection
    row, then the source row ``FOR UPDATE``, then deletes the nodes. Without
    the guard first the write holds the document rows and asks for the source
    row that the disconnect holds, while the disconnect waits on the rows."""
    from gateway.connections import store
    from gateway.kb_sync import index, runs

    from tests.unit.test_kc_sync_r8 import (
        _deadlocks,
        _disconnect,
        _overlap,
        _Pause,
        _pause,
    )

    _assert_non_priv(app_engine)
    owner, sid = await _indexed_source(idx, f"L{first[0]}", _files(f"L{first[0]}", 3))
    _include(idx, sid, f"inc-L{first[0]}")
    for i in range(3):
        idx.docs.files[f"doc{i}-L{first[0]}"] = _text_file(i, "L")

    def page() -> Any:
        async def go() -> Any:
            return await index.run_index(idx.org_a, sid, source=idx.docs)
        return go

    if first == "index":
        # The third heartbeat is the one of the first file: the guard, the node,
        # the document and the chunks are written, and the locks are held.
        pause = _Pause()
        real = runs.progress
        count = {"n": 0}

        async def held(*a: Any, **k: Any) -> Any:
            out = await real(*a, **k)
            count["n"] += 1
            if count["n"] == 3:
                await pause.wait()
            return out

        monkeypatch.setattr(runs, "progress", held)
        got, gone = await _overlap(idx, page(), pause, _disconnect(owner, sid))
    else:
        hold = _pause(monkeypatch, store, "disconnect")
        idx.docs.gate = _Pause()
        gone, got = await _overlap(idx, _disconnect(owner, sid), hold, page(),
                                   start=idx.docs.gate)
    assert _deadlocks(got, gone) == [], "DeadlockDetected (40P01)"
    assert isinstance(gone, dict) and gone["state"] == "disconnected", gone
    assert not isinstance(got, BaseException), got
    assert got.status in ("stopped", "ok", "failed"), got
    assert _doc_count(idx, sid) == 0, "a document outlived the disconnect"
    assert _chunk_count(idx) == 0, "a chunk outlived the disconnect"
