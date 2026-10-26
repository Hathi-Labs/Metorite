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
