"""WS-60 AM-1 — the artifact registry: the table, the one writer, the reads.

Spec: ``project-docs/specs/artifact_management.md`` §4, §4.5, §4.6, §5.2
(rules 1 and 2), §8 and §11 AM-1. Fences of §15 that land here: "only
``register_artifact`` writes ``artifacts``", "the classifier has one home",
and "no bytes without ``can_read``" for the two read routes.

Two halves.

**Hermetic** (no database). The kind table, the title and the index text are
pure functions. The writer fence greps the tree. The migration is found by
CONTENT (R1). The viewer's extension sets in ``lib/artifactKind.ts`` and the
registry's kinds must agree. The write-through builds a provenance only with
the flag on and an owner known.

**R8** (a real Postgres, the phase-4 rehearsal's dedicated database with the
whole ladder, the generated phases and two organizations, as the
non-privileged ``acb_app`` role). A hermetic fake agrees with any SQL it is
handed, and the register upsert's ON CONFLICT arbiter, the version CASE and
the tsvector are exactly that class of thing:

* a write leaves one row with owner, session, run, kind, title and sha;
* the same path twice moves ``version`` to 2 on a new sha and not on the
  same sha, and keeps the title;
* a path under ``agent-data/`` and a write with no owner leave no row;
* the flag off leaves no row;
* a trash marks the row and a new write re-activates it;
* another organization reads nothing (RLS), and the list and get functions
  show a second member only an ``org``-scoped row;
* a word inside a markdown body finds the row.

The R8 class carries ``_DB_GATE``, so it skips where no ladder URL is set
(the deploy run) and its fixtures never start there.
"""
from __future__ import annotations

import re
import uuid
from pathlib import Path
from types import SimpleNamespace

import pytest

pytest.importorskip("sqlalchemy")

from acb_memory import artifacts as reg
from sqlalchemy import text

from tests.unit.test_h3_rls_promotion_rehearsal import (  # noqa: F401
    _DB_GATE,
    promoted,
)

_REPO = Path(__file__).resolve().parents[2]
_MIGRATIONS = _REPO / "infra" / "postgres"
_MODULE = _REPO / "packages" / "acb_memory" / "acb_memory" / "artifacts.py"
_TS_KIND = _REPO / "workbench" / "control_plane" / "src" / "lib" / "artifactKind.ts"


# ── Hermetic ────────────────────────────────────────────────────────────────


@pytest.mark.parametrize(
    ("path", "mime", "kind"),
    [
        ("outputs/report.md", "text/markdown", "document"),
        ("outputs/page.html", "text/html", "html"),
        ("outputs/app.tsx", "", "react"),
        ("inputs/app.tsx", "", "code"),
        ("outputs/chart.png", "image/png", "image"),
        ("outputs/deck.pdf", "application/pdf", "pdf"),
        ("inputs/rows.csv", "text/csv", "data"),
        ("outputs/script.py", "text/x-python", "code"),
        ("outputs/bundle.zip", "application/zip", "archive"),
        ("outputs/brief.docx", "", "document"),
        ("outputs/blob.bin", "application/octet-stream", "other"),
    ],
)
def test_classify_kind_is_one_table(path: str, mime: str, kind: str) -> None:
    assert reg.classify_kind(path, mime) == kind
    assert kind in reg.KINDS


def test_title_comes_from_the_first_heading_else_the_file_name() -> None:
    assert reg.title_of("outputs/q3.md", b"intro\n\n## Q3 numbers ##\nbody", "document") == "Q3 numbers"
    assert reg.title_of("outputs/q3.md", b"no heading here", "document") == "q3"
    assert reg.title_of("outputs/p.html", b"<html><title> The  page </title>", "html") == "The page"
    assert reg.title_of("outputs/p.html", b"<h1>Hello <b>there</b></h1>", "html") == "Hello there"
    assert reg.title_of("inputs/rows.csv", b"a,b", "data") == "rows"
    assert len(reg.title_of("outputs/x.md", b"# " + b"y" * 900, "document")) == 200


def test_extract_text_reads_text_kinds_only_and_strips_tags() -> None:
    assert reg.extract_text("html", b"<p>Hello <b>world</b></p>") == "Hello world"
    assert reg.extract_text("image", b"\x89PNG") == ""
    assert reg.extract_text("archive", b"PK") == ""
    big = b"x" * (reg._INDEX_BYTES + 5000)
    assert len(reg.extract_text("document", big)) == reg._INDEX_BYTES


def test_only_the_registry_module_inserts_into_artifacts() -> None:
    """§15: only ``register_artifact`` writes ``artifacts``."""
    pattern = re.compile(r"INSERT\s+INTO\s+artifacts\b", re.IGNORECASE)
    offenders: list[str] = []
    for root in ("packages", "apps", "scripts"):
        for py in (_REPO / root).rglob("*.py"):
            if py == _MODULE or "node_modules" in py.parts:
                continue
            if pattern.search(py.read_text(encoding="utf-8", errors="ignore")):
                offenders.append(str(py.relative_to(_REPO)))
    assert not offenders, offenders


def test_the_migration_is_found_by_content_and_forces_rls() -> None:
    """R1: by content, never by number. R5: three tables, each FORCE RLS."""
    hits = [
        p for p in _MIGRATIONS.glob("*.sql")
        if "CREATE TABLE IF NOT EXISTS artifacts (" in p.read_text(encoding="utf-8")
    ]
    assert len(hits) == 1, hits
    sql = hits[0].read_text(encoding="utf-8")
    for table in ("artifacts", "artifact_member", "artifact_shares"):
        assert f"CREATE TABLE IF NOT EXISTS {table} (" in sql
        assert re.search(r"REFERENCES organization \(id\) ON DELETE CASCADE\s+DEFAULT current_setting", sql)
    assert "ARRAY['artifacts', 'artifact_member', 'artifact_shares']" in sql
    assert "FORCE  ROW LEVEL SECURITY" in sql
    policies = (_MIGRATIONS / "generated" / "04_policies.sql").read_text(encoding="utf-8")
    for table in ("artifacts", "artifact_member", "artifact_shares"):
        assert f"ALTER TABLE {table} FORCE  ROW LEVEL SECURITY" in policies


def _ts_set(name: str) -> set[str]:
    src = _TS_KIND.read_text(encoding="utf-8")
    m = re.search(rf"const {name} = new Set\(\[(.*?)\]\);", src, re.DOTALL)
    assert m, name
    return set(re.findall(r'"([a-z0-9]+)"', m.group(1)))


def test_the_viewer_extension_sets_agree_with_the_registry_kinds() -> None:
    """§15: the classifier has one home. The viewer's sets map onto the
    library's kinds with no extension the registry calls ``other``."""
    for ext in _ts_set("IMAGE_EXTS"):
        assert reg.classify_kind(f"outputs/x.{ext}") == "image", ext
    for ext in _ts_set("CODE_EXTS"):
        got = reg.classify_kind(f"outputs/x.{ext}")
        assert got in ("code", "data", "html", "react"), (ext, got)
    for ext in _ts_set("TEXT_EXTS"):
        got = reg.classify_kind(f"outputs/x.{ext}")
        assert got in ("document", "data", "code"), (ext, got)
    src = _TS_KIND.read_text(encoding="utf-8")
    for ext in re.findall(r'"([a-z]+)"', src.split("MARKDOWN_EXTS")[1].split(";")[0]):
        assert reg.classify_kind(f"outputs/x.{ext}") == "document", ext


def test_provenance_needs_the_flag_and_an_owner(monkeypatch: pytest.MonkeyPatch) -> None:
    import importlib

    from acb_common import get_settings

    wa = importlib.import_module("acb_skills.write_artifact")

    settings = get_settings()
    ctx = {"member": "", "run_id": "r1"}
    monkeypatch.setattr(settings, "artifact_registry_enabled", False)
    assert wa._provenance(ctx, "writer", "u:ann@x.test", "s1", "agent") is None
    monkeypatch.setattr(settings, "artifact_registry_enabled", True)
    assert wa._provenance(ctx, "writer", "o:" + "0" * 36, "s1", "agent") is None
    p = wa._provenance(ctx, "writer", "u:ann@x.test", "s1", "agent")
    assert p is not None and p.owner_email == "ann@x.test" and p.made_by == "writer"
    assert p.via == "chat" and p.session_id == "s1" and p.run_id == "r1"
    p2 = wa._provenance({"member": "bob@x.test"}, "writer", "", "s2", "user")
    assert p2 is not None and p2.owner_email == "bob@x.test" and p2.made_by == "member"


def test_can_read_rules_one_and_two() -> None:
    row = {"owner_email": "ann@x.test", "scope": "personal"}
    assert reg.can_read(row, "ann@x.test")
    assert not reg.can_read(row, "bob@x.test")
    assert reg.can_read({"owner_email": "ann@x.test", "scope": "org"}, "bob@x.test")
    assert not reg.can_read({"owner_email": "ann@x.test", "scope": "project"}, "bob@x.test")
    assert not reg.can_read(row, "")


# ── R8 ───────────────────────────────────────────────────────────────────────

ANN = "ann@h3rls.test"
BOB = "bob@h3rls.test"
AGENT = "pytest-artifacts"


def _session(promoted_ns: SimpleNamespace, org: str):
    from sqlalchemy import create_engine
    from sqlalchemy.orm import Session

    eng = create_engine(promoted_ns.app_url, future=True)
    s = Session(eng)
    s.execute(text("SELECT set_config('app.tenant_id', :t, true)"), {"t": org})
    return eng, s


def _put(s, *, path: str, data: bytes, owner: str = ANN, instance: str = f"u:{ANN}",
         session_id: str | None = "chat-1", mime: str = "text/markdown", via: str = "chat"):
    import hashlib

    prov = reg.Provenance(owner_email=owner, made_by=AGENT, via=via, session_id=session_id, run_id="run-1")
    return reg.register_in_session(
        s, agent_name=AGENT, instance=instance, path=path, data=data,
        sha256=hashlib.sha256(data).hexdigest(), mime_type=mime, prov=prov,
    )


def _row(s, artifact_id: str) -> dict:
    r = s.execute(text(f"SELECT {reg._COLUMNS} FROM artifacts WHERE id = CAST(:i AS uuid)"), {"i": artifact_id}).fetchone()
    return dict(r._mapping) if r else {}


@_DB_GATE
class TestRegistryR8:
    @pytest.fixture(autouse=True)
    def _flag_on(self, monkeypatch: pytest.MonkeyPatch):
        from acb_common import get_settings

        monkeypatch.setattr(get_settings(), "artifact_registry_enabled", True)

    def test_a_write_leaves_one_row_and_a_rewrite_moves_the_version(self, promoted) -> None:  # noqa: F811
        eng, s = _session(promoted, promoted.org_a)
        try:
            path = f"outputs/{uuid.uuid4().hex}/q3.md"
            first = _put(s, path=path, data=b"# Q3 numbers\nrevenue grew")
            assert first
            row = _row(s, first)
            assert row["owner_email"] == ANN and row["made_by"] == AGENT and row["via"] == "chat"
            assert row["session_id"] == "chat-1" and row["run_id"] == "run-1"
            assert row["kind"] == "document" and row["title"] == "Q3 numbers"
            assert row["version"] == 1 and row["scope"] == "personal" and row["status"] == "active"
            assert "agent:pytest-artifacts" in row["labels"] and "via:chat" in row["labels"]
            same = _put(s, path=path, data=b"# Q3 numbers\nrevenue grew")
            assert same == first and _row(s, first)["version"] == 1
            again = _put(s, path=path, data=b"# Renamed heading\nrevenue fell")
            assert again == first
            row = _row(s, first)
            assert row["version"] == 2
            assert row["title"] == "Q3 numbers", "a rewrite keeps the title"
            assert row["sha256"] != first
            s.commit()
        finally:
            s.close()
            eng.dispose()

    def test_no_row_for_agent_data_no_owner_or_flag_off(self, promoted, monkeypatch) -> None:  # noqa: F811
        from acb_common import get_settings

        settings = get_settings()
        eng, s = _session(promoted, promoted.org_a)
        try:
            assert _put(s, path="agent-data/NOTES.md", data=b"x") is None
            assert _put(s, path=f"outputs/{uuid.uuid4().hex}.md", data=b"x", owner="") is None
            monkeypatch.setattr(settings, "artifact_registry_enabled", False)
            assert _put(s, path=f"outputs/{uuid.uuid4().hex}.md", data=b"x") is None
            s.rollback()
        finally:
            s.close()
            eng.dispose()

    def test_trash_marks_the_row_and_a_new_write_reactivates_it(self, promoted) -> None:  # noqa: F811
        eng, s = _session(promoted, promoted.org_a)
        try:
            path = f"outputs/{uuid.uuid4().hex}/t.md"
            aid = _put(s, path=path, data=b"# T")
            assert reg.trash_in_session(s, agent_name=AGENT, instance=f"u:{ANN}", path=path) == 1
            assert _row(s, aid)["status"] == "trashed"
            assert reg.trash_in_session(s, agent_name=AGENT, instance=f"u:{ANN}", path=path) == 0
            assert _put(s, path=path, data=b"# T2") == aid
            row = _row(s, aid)
            assert row["status"] == "active" and row["trashed_at"] is None
            s.commit()
        finally:
            s.close()
            eng.dispose()

    def test_another_organization_reads_nothing(self, promoted) -> None:  # noqa: F811
        path = f"outputs/{uuid.uuid4().hex}/secret.md"
        eng, s = _session(promoted, promoted.org_a)
        try:
            aid = _put(s, path=path, data=b"# secret")
            s.commit()
        finally:
            s.close()
            eng.dispose()
        eng, s = _session(promoted, promoted.org_b)
        try:
            n = s.execute(text("SELECT count(*) FROM artifacts WHERE path = :p"), {"p": path}).scalar()
            assert n == 0
            assert _row(s, aid) == {}
            s.rollback()
        finally:
            s.close()
            eng.dispose()

    async def test_list_and_get_follow_the_member_and_the_scope(self, promoted) -> None:  # noqa: F811
        from acb_common.db import bind_tenant, release_tenant

        from tests.unit._tenant_ladder import tenant_engine_scope

        tag = uuid.uuid4().hex
        eng, s = _session(promoted, promoted.org_a)
        try:
            mine = _put(s, path=f"outputs/{tag}/mine.md", data=b"# Mine\nthe pelican flew")
            shared = _put(s, path=f"outputs/{tag}/shared.md", data=b"# Shared")
            s.execute(text("UPDATE artifacts SET scope = 'org' WHERE id = CAST(:i AS uuid)"), {"i": shared})
            theirs = _put(s, path=f"outputs/{tag}/theirs.md", data=b"# Theirs", owner=BOB, instance=f"u:{BOB}")
            s.commit()
        finally:
            s.close()
            eng.dispose()
        token = bind_tenant(promoted.org_a)
        try:
            async with tenant_engine_scope(promoted.app_url.render_as_string(hide_password=False)):
                ann_rows = await reg.list_artifacts(ANN, session_id="chat-1", limit=200)
                ids = {r["id"] for r in ann_rows}
                assert mine in ids and shared in ids and theirs not in ids
                bob_rows = await reg.list_artifacts(BOB, session_id="chat-1", limit=200)
                ids = {r["id"] for r in bob_rows}
                assert theirs in ids and shared in ids and mine not in ids
                assert (await reg.get_artifact(mine, BOB)) is None
                assert (await reg.get_artifact(shared, BOB))["id"] == shared
                assert (await reg.get_artifact(mine, ANN))["title"] == "Mine"
                found = await reg.list_artifacts(ANN, q="pelican", limit=200)
                assert [r["id"] for r in found] == [mine]
                assert await reg.list_artifacts(BOB, q="pelican", limit=200) == []
                assert await reg.list_artifacts(ANN, kind="image", session_id="chat-1") == []
        finally:
            release_tenant(token)
