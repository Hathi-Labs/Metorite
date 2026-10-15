"""The artifact registry (WS-60 AM-1).

Spec: ``project-docs/specs/artifact_management.md`` §4 (the model), §4.5
(one function registers), §4.6 (the row and the store cannot drift), §8
(the search API) and §11 AM-1.

One row in ``artifacts`` for every file a member or an agent makes under
``inputs/`` or ``outputs/``. The bytes stay in ``agent_blob``. The row carries
what the store does not: who made it, through which door, in which chat and
run, what kind of thing it is, its title, its scope and a text index.

**This module is the only writer of ``artifacts``.** The blob store calls
:func:`register_in_session` inside the same transaction that lands the blob
row, after the history row, in a SAVEPOINT. A failed registration rolls back
the savepoint and the blob write still commits. The fence is
``tests/unit/test_artifact_registry.py``: a grep over the tree for an INSERT
into ``artifacts`` outside this file fails.

**The flag.** ``settings.artifact_registry_enabled`` (``ARTIFACT_REGISTRY_ENABLED``),
default off. With it off, :func:`register_in_session` returns ``None`` and
writes nothing, and the two read functions answer empty.

**Tenancy.** Every function here runs inside a session that a caller bound
with ``tenant_session`` (R5). The INSERT names no ``organization_id``: the
column defaults to ``app.tenant_id``, and FORCE RLS refuses a row for any
other tenant. A read sees only the caller's tenant for the same reason.

**Visibility (AM-1 half).** :func:`can_read` answers rules 1 and 2 of §5.2:
the owner, and ``scope = org``. AM-3 adds the project, share and task rules.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any

from acb_common import get_logger

_log = get_logger("acb_memory.artifacts")

__all__ = [
    "KINDS",
    "Provenance",
    "can_read",
    "classify_kind",
    "enabled",
    "extract_text",
    "get_artifact",
    "list_artifacts",
    "register_in_session",
    "title_of",
    "trash_in_session",
]

# ── The kinds (§4.1) ──────────────────────────────────────────────────────────
#
# One table, read by both halves. The frontend's `lib/artifactKind.ts` keeps
# its own extension sets for the VIEWER (markdown, html, react, code, image,
# pdf, docx, text, binary). The registry's kinds are the LIBRARY's: what a
# member filters on. `tests/unit/test_artifact_registry.py` reads the TS file
# and fails when an extension the viewer knows lands in a kind this table does
# not expect.

KINDS = ("document", "data", "html", "react", "image", "pdf", "code", "archive", "other")

_DOCUMENT_EXTS = frozenset({"md", "markdown", "mdx", "txt", "log", "rst", "docx", "doc", "rtf", "odt"})
_HTML_EXTS = frozenset({"html", "htm"})
_REACT_EXTS = frozenset({"jsx", "tsx"})
_IMAGE_EXTS = frozenset({"png", "jpg", "jpeg", "gif", "webp", "svg", "ico", "bmp", "tiff"})
_PDF_EXTS = frozenset({"pdf"})
_DATA_EXTS = frozenset({"csv", "tsv", "json", "xlsx", "xls", "parquet", "ndjson", "xml", "yaml", "yml", "toml"})
_ARCHIVE_EXTS = frozenset({"zip", "tar", "gz", "tgz", "7z", "rar"})
_CODE_EXTS = frozenset({
    "py", "ts", "js", "sh", "bash", "zsh", "fish", "sql", "rs", "go", "java", "c",
    "cpp", "cs", "rb", "php", "swift", "kt", "scala", "r", "lua", "css", "scss",
    "less", "graphql", "proto", "ini", "cfg", "conf", "env",
})

_TEXT_KINDS = frozenset({"document", "data", "html", "react", "code"})
_INDEX_BYTES = 64 * 1024
_TITLE_MAX = 200


def _ext_of(name: str) -> str:
    return name.rsplit(".", 1)[-1].lower() if "." in name else ""


def classify_kind(path: str, mime_type: str = "") -> str:
    """The library kind of a stored file, from its name and mime (§4.1).

    A ``.jsx`` or ``.tsx`` is ``react`` only under ``outputs/``, where the
    viewer runs it. Elsewhere it is ``code``, as `artifactKind.ts` rules.
    """
    clean = path.replace("\\", "/").lstrip("/")
    name = clean.rsplit("/", 1)[-1]
    ext = _ext_of(name)
    mime = (mime_type or "").lower()
    if ext in _HTML_EXTS:
        return "html"
    if ext in _REACT_EXTS:
        return "react" if clean.startswith("outputs/") else "code"
    if ext in _PDF_EXTS or mime == "application/pdf":
        return "pdf"
    if ext in _IMAGE_EXTS or mime.startswith("image/"):
        return "image"
    if ext in _ARCHIVE_EXTS or mime in ("application/zip", "application/gzip", "application/x-tar"):
        return "archive"
    if ext in _DATA_EXTS:
        return "data"
    if ext in _DOCUMENT_EXTS or mime.startswith("text/plain"):
        return "document"
    if ext in _CODE_EXTS:
        return "code"
    if mime.startswith("text/"):
        return "document"
    return "other"


_MD_HEADING = re.compile(r"^\s{0,3}#{1,6}\s+(.+?)\s*#*\s*$", re.MULTILINE)
_HTML_TITLE = re.compile(r"<title[^>]*>(.*?)</title>", re.IGNORECASE | re.DOTALL)
_HTML_H1 = re.compile(r"<h1[^>]*>(.*?)</h1>", re.IGNORECASE | re.DOTALL)
_TAG = re.compile(r"<[^>]+>")
_WS = re.compile(r"\s+")


def _decode(data: bytes, limit: int = _INDEX_BYTES) -> str:
    """The first *limit* bytes as text. A NUL is dropped: Postgres refuses
    one in a text column, and a UTF-16 export is full of them."""
    return data[:limit].decode("utf-8", errors="ignore").replace("\x00", "")


def title_of(path: str, data: bytes, kind: str) -> str:
    """The title of a new row (§4.1): the first heading, else the file name.

    A markdown document gives its first heading. An html document gives its
    ``<title>``, else its first ``<h1>``. Everything else gives the file name
    without its extension. The title is at most 200 characters, and never
    empty.
    """
    name = path.replace("\\", "/").rstrip("/").rsplit("/", 1)[-1]
    stem = name.rsplit(".", 1)[0] if "." in name else name
    found = ""
    if kind == "document" and _ext_of(name) in ("md", "markdown", "mdx"):
        m = _MD_HEADING.search(_decode(data, 8192))
        found = m.group(1) if m else ""
    elif kind == "html":
        text = _decode(data, 16384)
        m = _HTML_TITLE.search(text) or _HTML_H1.search(text)
        found = _TAG.sub("", m.group(1)) if m else ""
    title = _WS.sub(" ", found).strip() or stem or name or "untitled"
    return title[:_TITLE_MAX]


def extract_text(kind: str, data: bytes) -> str:
    """The text the index reads (§8): the first 64 KB of a text kind.

    An html document loses its tags. A binary kind gives nothing. PDF and
    DOCX extraction is AM-7, not here.
    """
    if kind not in _TEXT_KINDS or not data:
        return ""
    text = _decode(data)
    if kind == "html":
        text = _TAG.sub(" ", text)
    return _WS.sub(" ", text).strip()


# ── Provenance ────────────────────────────────────────────────────────────────


@dataclass(frozen=True)
class Provenance:
    """Who made the file and through which door (§4.1).

    ``owner_email`` is the member the row belongs to. ``made_by`` is
    ``member`` or the agent's name. ``via`` is one of the doors the CHECK in
    migration 268 allows. The rest is nullable context from the run.
    """

    owner_email: str
    made_by: str
    via: str = "chat"
    session_id: str | None = None
    run_id: str | None = None
    task_id: str | None = None
    project_id: str | None = None
    labels: tuple[str, ...] = field(default_factory=tuple)


def enabled() -> bool:
    """True when ``ARTIFACT_REGISTRY_ENABLED`` is on. Fails closed."""
    try:
        from acb_common import get_settings

        return getattr(get_settings(), "artifact_registry_enabled", False) is True
    except Exception:
        return False


def _auto_labels(prov: Provenance, kind: str, agent_name: str) -> list[str]:
    labels = {kind, f"via:{prov.via}"}
    if prov.made_by == "member":
        labels.add("made-by:member")
    else:
        labels.add(f"agent:{agent_name}")
    labels.update(prov.labels)
    return sorted(labels)


_REGISTER_SQL = (
    "INSERT INTO artifacts "
    "(owner_email, agent_name, instance, path, sha256, size, mime_type, kind, "
    " title, made_by, via, session_id, run_id, project_id, task_id, labels, "
    " search_tsv) "
    "VALUES (:owner, :agent, :instance, :path, :sha, :size, :mime, :kind, "
    " :title, :made_by, :via, :session_id, :run_id, "
    " CAST(:project_id AS uuid), CAST(:task_id AS uuid), "
    " CAST(:labels AS text[]), to_tsvector('english', :index_text)) "
    "ON CONFLICT (organization_id, agent_name, instance, path) DO UPDATE SET "
    # A trashed row is a dead name. The new write is a NEW artifact on that
    # name, so every provenance column comes from the write, and the version
    # starts again. A live row keeps its owner, title and door.
    " version = CASE WHEN artifacts.status = 'trashed' THEN 1 "
    "   WHEN artifacts.sha256 = EXCLUDED.sha256 THEN artifacts.version "
    "   ELSE artifacts.version + 1 END, "
    " sha256 = EXCLUDED.sha256, size = EXCLUDED.size, mime_type = EXCLUDED.mime_type, "
    " kind = EXCLUDED.kind, "
    " owner_email = CASE WHEN artifacts.status = 'trashed' THEN EXCLUDED.owner_email ELSE artifacts.owner_email END, "
    " title = CASE WHEN artifacts.status = 'trashed' THEN EXCLUDED.title ELSE artifacts.title END, "
    " made_by = CASE WHEN artifacts.status = 'trashed' THEN EXCLUDED.made_by ELSE artifacts.made_by END, "
    " via = CASE WHEN artifacts.status = 'trashed' THEN EXCLUDED.via ELSE artifacts.via END, "
    " labels = CASE WHEN artifacts.status = 'trashed' THEN EXCLUDED.labels ELSE artifacts.labels END, "
    " scope = CASE WHEN artifacts.status = 'trashed' THEN 'personal' ELSE artifacts.scope END, "
    " keep = CASE WHEN artifacts.status = 'trashed' THEN false ELSE artifacts.keep END, "
    " session_id = CASE WHEN artifacts.status = 'trashed' THEN EXCLUDED.session_id "
    "   ELSE COALESCE(artifacts.session_id, EXCLUDED.session_id) END, "
    " run_id = COALESCE(EXCLUDED.run_id, artifacts.run_id), "
    " project_id = CASE WHEN artifacts.status = 'trashed' THEN EXCLUDED.project_id "
    "   ELSE COALESCE(artifacts.project_id, EXCLUDED.project_id) END, "
    " task_id = CASE WHEN artifacts.status = 'trashed' THEN EXCLUDED.task_id "
    "   ELSE COALESCE(artifacts.task_id, EXCLUDED.task_id) END, "
    " search_tsv = EXCLUDED.search_tsv, "
    " status = 'active', trashed_at = NULL, expires_at = NULL, "
    " updated_at = now(), last_activity_at = now() "
    "RETURNING id, version"
)


def register_in_session(
    session: Any,
    *,
    agent_name: str,
    instance: str,
    path: str,
    data: bytes,
    sha256: str,
    mime_type: str,
    prov: Provenance,
) -> str | None:
    """Register one stored file. THE one writer of ``artifacts`` (§4.5).

    Runs inside the caller's tenant session, in a SAVEPOINT, so a failure
    here never loses the blob write. Returns the row id, or ``None`` when the
    flag is off, the path is outside ``inputs/`` and ``outputs/``, the owner
    is unknown, or the write failed (logged as ``artifact_register_failed``).

    A second write of the same path keeps the row and its title, and moves
    ``version`` when the sha changed. A write onto a TRASHED row makes it a
    new artifact on the old name: the owner, title, door, labels and scope
    come from the write, and the version starts at 1 (review, 2026-10-15).
    """
    if not enabled():
        return None
    clean = path.replace("\\", "/").lstrip("/")
    head = clean.split("/", 1)[0]
    if head not in ("inputs", "outputs") or not prov.owner_email:
        return None
    from sqlalchemy import text

    kind = classify_kind(clean, mime_type)
    title = title_of(clean, data, kind)
    labels = _auto_labels(prov, kind, agent_name)
    index_text = " ".join([title, " ".join(labels), extract_text(kind, data)])[: _INDEX_BYTES + 1024]
    params = {
        "owner": prov.owner_email, "agent": agent_name, "instance": instance,
        "path": clean, "sha": sha256, "size": len(data), "mime": mime_type,
        "kind": kind, "title": title, "made_by": prov.made_by, "via": prov.via,
        "session_id": prov.session_id, "run_id": prov.run_id,
        "project_id": prov.project_id, "task_id": prov.task_id,
        "labels": labels, "index_text": index_text,
    }
    try:
        with session.begin_nested():
            row = session.execute(text(_REGISTER_SQL), params).fetchone()
    except Exception as exc:
        _log.warning(
            "artifact_register_failed", agent=agent_name, path=clean,
            error=str(exc)[:200],
        )
        return None
    return str(row[0]) if row is not None else None


def trash_in_session(session: Any, *, agent_name: str, instance: str, path: str) -> int:
    """Mark the row of a deleted file ``trashed`` (§4.5). Returns rows changed.

    The sweep (AM-5) purges it later. A failure here is logged and never
    reaches the caller, for the same reason as :func:`register_in_session`.
    """
    if not enabled():
        return 0
    from sqlalchemy import text

    clean = path.replace("\\", "/").lstrip("/")
    try:
        with session.begin_nested():
            res = session.execute(
                text(
                    "UPDATE artifacts SET status = 'trashed', trashed_at = now(), "
                    "updated_at = now() WHERE agent_name = :a AND instance = :i "
                    "AND path = :p AND status = 'active'"
                ),
                {"a": agent_name, "i": instance, "p": clean},
            )
            return int(res.rowcount or 0)
    except Exception as exc:
        _log.warning("artifact_trash_failed", agent=agent_name, path=clean, error=str(exc)[:200])
        return 0


# ── Reads (AM-1 half of §5.2 and §8) ─────────────────────────────────────────

_COLUMNS = (
    "id, owner_email, agent_name, instance, path, sha256, size, mime_type, "
    "version, kind, title, made_by, via, session_id, run_id, project_id, "
    "task_id, scope, labels, status, keep, expires_at, trashed_at, created_at, "
    "updated_at, last_activity_at"
)


def can_read(row: dict[str, Any], member_email: str) -> bool:
    """Rules 1 and 2 of §5.2: the owner, and ``scope = org``.

    AM-3 adds the project, the shares and the task link. Until then a
    ``project`` scope reads as private to its owner, which is the safe side.
    """
    if not member_email:
        return False
    if row.get("owner_email") == member_email:
        return True
    return row.get("scope") == "org"


def _row(r: Any) -> dict[str, Any]:
    d = dict(r._mapping) if hasattr(r, "_mapping") else dict(r)
    d["id"] = str(d["id"])
    for key in ("project_id", "task_id"):
        if d.get(key) is not None:
            d[key] = str(d[key])
    return d


_SORTS = {
    "recent": "last_activity_at DESC, id DESC",
    "created": "created_at DESC, id DESC",
    "name": "lower(title) ASC, id ASC",
    "size": "size DESC, id DESC",
}


async def list_artifacts(
    member_email: str,
    *,
    q: str | None = None,
    kind: str | None = None,
    made_by: str | None = None,
    via: str | None = None,
    session_id: str | None = None,
    scope: str | None = None,
    status: str = "active",
    sort: str = "recent",
    limit: int = 50,
    offset: int = 0,
) -> list[dict[str, Any]]:
    """The rows the member can read, newest activity first (§8).

    The SQL narrows to what rules 1 and 2 allow, so a row of another member
    with a personal scope never leaves the database. ``q`` is a websearch
    query over ``search_tsv``, ranked. The page is an offset, as the task
    lists are, and keyset paging is AM-2's.
    """
    if not enabled() or not member_email:
        return []
    from acb_common.db import tenant_session
    from sqlalchemy import text

    where = ["(owner_email = :me OR scope = 'org')", "status = :status"]
    params: dict[str, Any] = {
        "me": member_email, "status": status if status in ("active", "trashed") else "active",
        "limit": max(1, min(int(limit), 200)), "offset": max(0, int(offset)),
    }
    if kind in KINDS:
        where.append("kind = :kind")
        params["kind"] = kind
    if made_by:
        where.append("made_by = :made_by")
        params["made_by"] = made_by[:120]
    if via:
        where.append("via = :via")
        params["via"] = via[:80]
    if session_id:
        where.append("session_id = :sid")
        params["sid"] = session_id[:200]
    if scope in ("personal", "project", "org"):
        where.append("scope = :scope")
        params["scope"] = scope
    if status == "trashed":
        # Only the owner sees their own trash.
        where[0] = "owner_email = :me"
    order = _SORTS.get(sort, _SORTS["recent"])
    rank = ""
    if q and q.strip():
        where.append("search_tsv @@ websearch_to_tsquery('english', :q)")
        params["q"] = q.strip()[:200]
        rank = "ts_rank_cd(search_tsv, websearch_to_tsquery('english', :q)) DESC, "
    sql = (
        f"SELECT {_COLUMNS} FROM artifacts WHERE {' AND '.join(where)} "
        f"ORDER BY {rank}{order} LIMIT :limit OFFSET :offset"
    )
    async with tenant_session() as db:
        res = await db.execute(text(sql), params)
        return [_row(r) for r in res.fetchall()]


async def get_artifact(artifact_id: str, member_email: str) -> dict[str, Any] | None:
    """One row the member can read, else ``None`` (the route answers 404)."""
    if not enabled() or not member_email:
        return None
    from acb_common.db import tenant_session
    from sqlalchemy import text

    async with tenant_session() as db:
        res = await db.execute(
            text(f"SELECT {_COLUMNS} FROM artifacts WHERE id = CAST(:id AS uuid)"),
            {"id": artifact_id},
        )
        r = res.fetchone()
    if r is None:
        return None
    row = _row(r)
    if row.get("status") == "trashed" and row.get("owner_email") != member_email:
        # Only the owner sees their own trash, as the list does.
        return None
    return row if can_read(row, member_email) else None
