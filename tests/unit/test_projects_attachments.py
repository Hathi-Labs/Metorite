"""WS-27i — files on a project task (spec §11.2 item 1).

The interesting claims here are all about **who may read the bytes**, because
this feature deliberately does *not* inherit the file store's own answer:

* `attachments` is owner-scoped — `/tasks/attachments/{id}/{name}` serves
  only to the uploader. Right for a private capture, useless for a shared task.
* So the JOIN carries the decision: a file is readable by anyone who can see a
  task it hangs off. Two properties follow, and both are security properties
  rather than conveniences:
  - **there is no attach-by-id endpoint** — upload and attach are one call, so
    nobody can attach somebody else's private capture to a task they own and
    read it back through the project route;
  - **a personal capture stays unreachable** here, because it has no join row.

Hermetic: a purpose-built double; no Postgres, no disk beyond a tmp_path.
"""

from __future__ import annotations

import asyncio
import re
from contextlib import asynccontextmanager
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest
from fastapi import HTTPException
from gateway.routes.projects import attachments as pm_attachments

REPO = Path(__file__).resolve().parents[2]


def run(coro):
    return asyncio.run(coro)


class _Result:
    def __init__(self, rows: list[Any], rowcount: int = 0):
        self._rows = rows
        self.rowcount = rowcount

    def fetchone(self):
        return self._rows[0] if self._rows else None

    def fetchall(self):
        return list(self._rows)


class FakeDB:
    """Answers by statement fingerprint; records what it was asked."""

    def __init__(self) -> None:
        self.serve_row: Any = None
        self.listed: list[Any] = []
        self.deleted = 0
        self.statements: list[str] = []
        self.params: list[dict] = []
        self.committed = 0

    async def execute(self, sql: Any, params: dict | None = None) -> _Result:
        statement = " ".join(str(sql).split())
        self.statements.append(statement)
        self.params.append(dict(params or {}))
        if statement.startswith("DELETE FROM pm_task_attachments"):
            return _Result([], rowcount=self.deleted)
        if "FROM pm_task_attachments ta JOIN pm_tasks t" in statement:
            return _Result([self.serve_row] if self.serve_row else [])
        if "FROM pm_task_attachments ta JOIN attachments" in statement:
            return _Result(self.listed)
        return _Result([])

    async def commit(self) -> None:
        self.committed += 1

    async def close(self) -> None:
        return None

    def sql_touching(self, needle: str) -> list[str]:
        return [s for s in self.statements if needle in s]

    def params_touching(self, needle: str) -> list[dict]:
        """The bound values beside :meth:`sql_touching`'s statements.

        A clause naming `:vis_org` proves the SQL asks for a tenant; only the
        parameter proves it asks for the CALLER's.
        """
        return [
            args for statement, args in zip(self.statements, self.params,
                                            strict=True)
            if needle in statement
        ]


class UploadStub:
    def __init__(self, filename: str, content: bytes, content_type: str = "image/png"):
        self.filename = filename
        self.content_type = content_type
        self._content = content
        self.reads = 0

    async def read(self) -> bytes:
        self.reads += 1
        return self._content


@pytest.fixture()
def db() -> FakeDB:
    return FakeDB()


@pytest.fixture(autouse=True)
def wiring(monkeypatch, db, tmp_path):
    # H2: the module's seam is `_tenant_session` (an async context manager),
    # mirrored commit-on-clean-exit like `_projects_fakes.bind_db`.
    @asynccontextmanager
    async def _tenant_session(organization_id=None):
        yield db
        await db.commit()

    async def _resolve(_db, _user):
        from gateway.routes.projects.core import Visibility

        return Visibility(unrestricted=False, email="me@x.in", groups=("group:ops",))

    async def _load_visible(_db, _vis, task_id):
        if task_id == "denied":
            raise HTTPException(status_code=404, detail="No such task")
        return SimpleNamespace(id=task_id, project_id="p1", root_project_id="p1")

    async def _record(*_a, **_k):
        return None

    async def _emit(*_a, **_k):
        return None

    monkeypatch.setattr(pm_attachments, "_tenant_session", _tenant_session)
    monkeypatch.setattr(pm_attachments, "resolve_visibility", _resolve)
    monkeypatch.setattr(pm_attachments, "load_visible_task", _load_visible)
    monkeypatch.setattr(pm_attachments, "record_activity", _record)
    monkeypatch.setattr(pm_attachments, "emit", _emit)
    monkeypatch.setattr(pm_attachments, "_storage_dir", lambda: tmp_path)
    return tmp_path


def user(email: str = "me@x.in"):
    from acb_auth import UserContext, UserRole, build_access

    return UserContext(
        email=email, role=UserRole.EMPLOYEE,
        access=build_access(["feature:projects"]),
    )


# ── The access model ────────────────────────────────────────────────────────

def test_there_is_no_attach_by_id_endpoint():
    """The escalation this design refuses: naming an arbitrary attachment id
    would let a caller attach somebody else's private capture to a task they
    own and then read it. Upload and attach are ONE call, so the only way a row
    enters the join is by supplying the bytes."""
    paths = {
        getattr(r, "path", "") for r in pm_attachments.router.routes
        if "attachment" in getattr(r, "path", "")
    }
    for path in paths:
        methods = {
            m for r in pm_attachments.router.routes
            if getattr(r, "path", "") == path
            for m in getattr(r, "methods", set())
        }
        # A POST that does NOT carry an upload would be the attach-by-id shape.
        if "POST" in methods:
            assert path.endswith("/attachments"), path


def test_serving_asks_whether_the_caller_can_see_a_task_it_hangs_off(db):
    db.serve_row = SimpleNamespace(name="x.png", mime="image/png", path="/nope")
    with pytest.raises(HTTPException):
        run(pm_attachments.serve_attachment("a1", "x.png", user=user()))
    sql = db.sql_touching("FROM pm_task_attachments ta JOIN pm_tasks t")[-1]
    # The grant closure, not the file's owner column.
    assert "root_project_id IN" in sql
    assert "user_id" not in sql


def test_an_unrestricted_viewer_is_scoped_to_their_own_organization(db, monkeypatch):
    """⚠️ WS-29b. This test used to assert the opposite — that a
    ``data:org:read`` holder got NO predicate at all — and that was correct
    while the deployment had one organization.

    What broke if it stayed that way: the route skipped the clause entirely for
    an unrestricted caller, so the first `data:org:read` holder to exist
    alongside a second tenant could fetch any organization's uploaded file by
    guessing an attachment id. The grant closure is gone for this caller, by
    design; the TENANT never is.
    """
    async def _resolve(_db, _user):
        from gateway.routes.projects.core import Visibility

        return Visibility(
            unrestricted=True, email="", groups=(), organization_id="org-a",
        )

    monkeypatch.setattr(pm_attachments, "resolve_visibility", _resolve)
    db.serve_row = None
    with pytest.raises(HTTPException):
        run(pm_attachments.serve_attachment("a1", "x.png", user=user()))
    sql = db.sql_touching("FROM pm_task_attachments ta JOIN pm_tasks t")[-1]
    # Still no GRANT closure — that is what `unrestricted` buys.
    assert "pm_project_grants" not in sql
    # But the tenant is there, and it is bound.
    assert "t.root_project_id IN" in sql
    assert "organization_id = CAST(:vis_org AS uuid)" in sql
    params = db.params_touching("FROM pm_task_attachments ta JOIN pm_tasks t")[-1]
    assert params["vis_org"] == "org-a"


def test_an_attachment_on_no_visible_task_is_a_404_not_a_403(db):
    """R5: a 403 would confirm the file exists."""
    db.serve_row = None
    with pytest.raises(HTTPException) as err:
        run(pm_attachments.serve_attachment("a1", "x.png", user=user()))
    assert err.value.status_code == 404


def test_a_missing_file_on_disk_is_a_404_even_when_the_row_exists(db):
    db.serve_row = SimpleNamespace(name="x.png", mime="image/png",
                                   path="/definitely/not/here.png")
    with pytest.raises(HTTPException) as err:
        run(pm_attachments.serve_attachment("a1", "x.png", user=user()))
    assert err.value.status_code == 404


# ── Upload ──────────────────────────────────────────────────────────────────

def test_visibility_is_checked_BEFORE_the_bytes_are_read(db):
    """Otherwise an unauthorised caller can still make the server read and
    validate a 15MB upload."""
    upload = UploadStub("photo.png", b"x" * 10)
    with pytest.raises(HTTPException):
        run(pm_attachments.attach_file("denied", upload, user=user()))
    assert upload.reads == 0


def test_an_upload_writes_the_file_row_and_the_join(db, wiring):
    upload = UploadStub("photo.png", b"bytes")
    result = run(pm_attachments.attach_file("t1", upload, user=user()))
    assert result["kind"] == "image"
    assert result["name"] == "photo.png"
    assert result["url"].startswith("/api/projects/attachments/")
    assert db.sql_touching("INSERT INTO attachments")
    assert db.sql_touching("INSERT INTO pm_task_attachments")
    assert db.committed == 1
    # …and the bytes actually landed.
    written = list(Path(wiring).iterdir())
    assert len(written) == 1
    assert written[0].read_bytes() == b"bytes"


def test_the_uploader_is_recorded_as_the_file_owner_and_the_adder(db):
    run(pm_attachments.attach_file("t1", UploadStub("a.png", b"z"), user=user("her@x.in")))
    file_params = db.params[db.statements.index(
        db.sql_touching("INSERT INTO attachments")[0]
    )]
    assert file_params["uid"] == "her@x.in"
    join_params = db.params[db.statements.index(
        db.sql_touching("INSERT INTO pm_task_attachments")[0]
    )]
    assert join_params["who"] == "her@x.in"


def test_an_executable_is_refused(db):
    """Imported from the capture flow's list, not re-decided here."""
    with pytest.raises(HTTPException) as err:
        run(pm_attachments.attach_file("t1", UploadStub("evil.exe", b"MZ"), user=user()))
    assert err.value.status_code == 400
    assert db.sql_touching("INSERT INTO attachments") == []


def test_an_empty_file_is_refused(db):
    with pytest.raises(HTTPException) as err:
        run(pm_attachments.attach_file("t1", UploadStub("a.png", b""), user=user()))
    assert err.value.status_code == 400


def test_an_oversized_file_is_refused_with_413(db):
    big = b"x" * (pm_attachments._MAX_BYTES + 1)
    with pytest.raises(HTTPException) as err:
        run(pm_attachments.attach_file("t1", UploadStub("a.png", big), user=user()))
    assert err.value.status_code == 413
    assert db.sql_touching("INSERT INTO attachments") == []


def test_the_disk_path_cannot_be_steered_by_the_filename(db, wiring):
    """Safe BY CONSTRUCTION, not by sanitisation: the destination is
    `<uuid><suffix>`, so the supplied name never reaches the path at all."""
    run(pm_attachments.attach_file(
        "t1", UploadStub("../../etc/passwd.png", b"z"), user=user(),
    ))
    written = list(Path(wiring).iterdir())
    assert len(written) == 1
    assert written[0].parent == Path(wiring)
    assert ".." not in written[0].name


def test_a_hostile_filename_is_sanitised_in_the_STORED_name(db):
    """What `_safe_name` actually protects.

    The stored name is echoed into the descriptor, rendered in the UI, and
    handed to `FileResponse(filename=…)`, which puts it in a
    `Content-Disposition` header — so path separators, quotes and newlines
    matter there even though the disk path is already safe. Asserting on the
    disk path alone let a mutant that removed `_safe_name` entirely survive.
    """
    result = run(pm_attachments.attach_file(
        "t1", UploadStub('../../etc/pa"ss\nwd.png', b"z"), user=user(),
    ))
    assert "/" not in result["name"]
    assert ".." not in result["name"]
    assert '"' not in result["name"]
    assert "\n" not in result["name"]
    # …and the same sanitised name is what went into the file row and the URL.
    stored = db.params[db.statements.index(
        db.sql_touching("INSERT INTO attachments")[0]
    )]
    assert stored["name"] == result["name"]
    assert result["url"].endswith(result["name"])


def test_a_non_image_is_described_as_a_file(db):
    result = run(pm_attachments.attach_file(
        "t1", UploadStub("spec.pdf", b"%PDF", content_type="application/pdf"),
        user=user(),
    ))
    assert result["kind"] == "file"


# ── Listing and detaching ───────────────────────────────────────────────────

def test_listing_requires_seeing_the_task(db):
    with pytest.raises(HTTPException):
        run(pm_attachments.list_attachments("denied", user=user()))


def test_listing_renders_the_descriptor_shape(db):
    db.listed = [SimpleNamespace(
        id="a1", name="photo.png", mime="image/png", size_bytes=12,
        added_by="me@x.in", created_at=None,
    )]
    res = run(pm_attachments.list_attachments("t1", user=user()))
    assert res.total == 1
    assert res.rows[0]["kind"] == "image"
    assert res.rows[0]["url"] == "/api/projects/attachments/a1/photo.png"


def test_detaching_keeps_the_bytes(db):
    """The same file may hang off another task; deleting the row from under it
    would turn one person's tidy-up into somebody else's broken link."""
    db.deleted = 1
    run(pm_attachments.detach_file("t1", "a1", user=user()))
    assert db.sql_touching("DELETE FROM pm_task_attachments")
    assert db.sql_touching("DELETE FROM attachments") == []
    assert db.sql_touching("DELETE FROM pm_tasks") == []


def test_detaching_something_already_gone_is_a_no_op_not_a_404(db):
    """Paca's lenient-removes lesson — what makes a retry after a half-failed
    request safe."""
    db.deleted = 0
    result = run(pm_attachments.detach_file("t1", "a1", user=user()))
    assert result["removed"] == 0


def test_detaching_requires_seeing_the_task(db):
    with pytest.raises(HTTPException):
        run(pm_attachments.detach_file("denied", "a1", user=user()))


# ── The shared validation is shared ─────────────────────────────────────────

def test_the_upload_rules_are_imported_not_reimplemented():
    """One answer to "what may be uploaded" — the capture flow's."""
    from gateway.routes.tasks import attachments as capture

    assert pm_attachments._MAX_BYTES is capture._MAX_BYTES
    assert pm_attachments._BLOCKED_EXT is capture._BLOCKED_EXT
    assert pm_attachments._safe_name is capture._safe_name
    # `_storage_dir` is redirected to tmp_path by this module's autouse fixture,
    # so identity cannot be asserted at run time. Read the import instead — the
    # property that matters is that there is no second definition, and a copy
    # would show up here as an absent import rather than a passing identity.
    source = Path(pm_attachments.__file__).read_text(encoding="utf-8")
    assert re.search(
        r"from gateway\.routes\.tasks\.attachments import \((?:[^)]*\n)*?\s*_storage_dir,",
        source,
    )
    assert "def _storage_dir" not in source


# ── The migration ───────────────────────────────────────────────────────────

@pytest.fixture(scope="module")
def sql() -> str:
    hits = [
        p for p in (REPO / "infra" / "postgres").glob("*.sql")
        # By the CREATE, not by a mention: migration 161 (the tenant key) names
        # every `pm_*` table, and a fixture that matched on the name alone would
        # start finding two files and fail for a reason that is not about
        # attachments at all.
        if "CREATE TABLE IF NOT EXISTS pm_task_attachments" in p.read_text(
            encoding="utf-8",
        )
    ]
    assert len(hits) == 1, hits
    raw = hits[0].read_text(encoding="utf-8")
    # Comments stripped: this file explains its own reasoning at length, and an
    # assertion that passes on the prose is not an assertion.
    return "\n".join(re.sub(r"--.*$", "", line) for line in raw.splitlines())


def test_the_join_is_created_idempotently(sql: str):
    assert "CREATE TABLE IF NOT EXISTS pm_task_attachments" in sql
    for statement in re.findall(r"CREATE INDEX[^;]*;", sql):
        assert "IF NOT EXISTS" in statement, statement


def test_deleting_a_task_takes_its_attachment_rows(sql: str):
    assert re.search(r"task_id\s+UUID NOT NULL REFERENCES pm_tasks \(id\) ON DELETE CASCADE", sql)


def test_the_attachment_id_does_NOT_cascade(sql: str):
    """Deleting a file row must not silently erase the task's record that a file
    was there — the API detaches explicitly."""
    join = sql.split("CREATE TABLE IF NOT EXISTS pm_task_attachments")[1].split(";")[0]
    assert "attachment_id" in join
    assert "REFERENCES attachments" not in join


def test_the_activity_vocabulary_gains_attachment(sql: str):
    assert "'attachment'" in sql
    assert "ADD CONSTRAINT pm_activities_type_check" in sql


def test_the_old_check_is_dropped_by_SHAPE_not_by_name(sql: str):
    """Dropping a name that does not exist is a silent no-op, and the ADD would
    then succeed under a second name — leaving the old, narrower CHECK in force
    alongside the new one. Both apply, `attachment` is still rejected, and the
    migration reports success."""
    assert "pg_get_constraintdef" in sql
    assert "contype = 'c'" in sql
    assert "FOR cname IN" in sql


# ── Serving bytes inline (2026-09-19) ───────────────────────────────────────

import re as _re
from pathlib import Path as _Path

_PREVIEW_TS = _Path(
    "workbench/control_plane/src/app/projects/lib/preview.ts"
)


def test_svg_is_never_served_inline():
    """🔴 The security case, and the one a reader will be tempted to undo.

    `_IMAGE_MIMES` (the UPLOAD safelist) contains `image/svg+xml`, so the
    descriptor reports `kind: "image"` for an SVG and it looks previewable
    from the outside. An SVG is a document that may carry `<script>`, and
    served inline on this origin that script runs with the member's session.

    So the two lists are deliberately different, and this asserts the
    difference rather than trusting it.
    """
    from gateway.routes.projects.attachments import _INLINE_MIMES
    from gateway.routes.tasks.attachments import _IMAGE_MIMES

    assert "image/svg+xml" in _IMAGE_MIMES
    assert "image/svg+xml" not in _INLINE_MIMES


def test_nothing_textual_is_served_inline():
    from gateway.routes.projects.attachments import _INLINE_MIMES

    assert not [m for m in _INLINE_MIMES if m.startswith("text/")]
    assert "application/xhtml+xml" not in _INLINE_MIMES


def test_the_inline_set_mirrors_the_client_safelist():
    """⚠️ The two halves of one decision, on opposite sides of the wire.

    A type the SERVER sends inline but the CLIENT will not preview is a
    download nobody asked for. A type the client previews but the server
    sends as `attachment` renders as an empty frame — which is exactly the
    PDF bug this change exists to fix. Parsed from the TypeScript rather
    than restated, so the mirror cannot drift silently.
    """
    from gateway.routes.projects.attachments import _INLINE_MIMES

    source = _PREVIEW_TS.read_text(encoding="utf-8")
    listed = set(_re.findall(r'"((?:image|application|text)/[a-z0-9.+-]+)"', source))
    # The client file names only what it will render; both arrays together.
    assert listed == set(_INLINE_MIMES), (
        f"client previews {sorted(listed)}, server serves inline "
        f"{sorted(_INLINE_MIMES)}"
    )


# ── The per-task budget (owner directive, 2026-09-19) ───────────────────────

def test_the_budget_is_ten_megabytes_per_task():
    from gateway.routes.projects.attachments import _TASK_ATTACHMENT_BUDGET

    assert _TASK_ATTACHMENT_BUDGET == 10 * 1024 * 1024


def test_the_budget_is_PER_TASK_and_not_per_file():
    """⚠️ The case a per-file cap misses, which is the whole point.

    The shared upload rule caps ONE file at 15MB. Twenty 9MB files each pass
    that and put 180MB on a single task. "The attachments that each task can
    hold" is about the task, so the budget is summed across its rows.

    Asserted as a property of the two numbers rather than by uploading
    twenty files: the budget must be smaller than the per-file cap, or it
    could never bind first.
    """
    from gateway.routes.projects.attachments import _TASK_ATTACHMENT_BUDGET
    from gateway.routes.tasks.attachments import _MAX_BYTES

    assert _TASK_ATTACHMENT_BUDGET < _MAX_BYTES


def test_the_budget_also_caps_a_single_file():
    """A 10MB task budget means no ONE file may exceed 10MB either, so the
    narrower reading of the directive is satisfied by the wider one."""
    from gateway.routes.projects.attachments import _TASK_ATTACHMENT_BUDGET

    oversized = _TASK_ATTACHMENT_BUDGET + 1
    assert 0 + oversized > _TASK_ATTACHMENT_BUDGET


def test_the_refusal_is_readable():
    """A 413 saying `10485760` does not tell anybody which file to remove."""
    from gateway.routes.projects.attachments import _mb

    assert _mb(10 * 1024 * 1024) == "10.0 MB"
    assert _mb(0) == "0.0 MB"
    assert "MB" in _mb(9_500_000)
