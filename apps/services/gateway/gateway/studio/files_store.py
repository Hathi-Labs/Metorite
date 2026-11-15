"""The one store of the files that a record of a Studio app holds (WS-STU D13b).

Spec: ``project-docs/specs/ai_studio.md`` §14 D13b. Migration: the file that
adds ``studio_record_files`` (442 at build). The serve route is
``routes/studio/files.py``. ``tests/unit/test_studio_record_files_r8.py`` runs
this SQL on a real Postgres.

**One module names the table.** This is the one module that runs SQL on
``studio_record_files``. Fence:
``test_studio_record_files.py::test_one_module_runs_sql_on_the_table``.

**Tenancy (R5).** Each function takes a session that the caller opened with
``tenant_session()``, so RLS binds each statement. No function takes an org.
An INSERT writes no ``organization_id``: the column DEFAULT reads the bound
tenant, and each SELECT filters on ``_BOUND_ORG`` too.

**The caller decides who may read.** The store does not authenticate and
runs no row rule. The route runs the door order first, and calls
:func:`read_file` last, with the ids that the visible record named.

:func:`put_file` has no caller yet. D13c (the ``document.render`` action) is
the first writer. The id of the new file goes into the ``file`` field of the
record after the write.
"""

from __future__ import annotations

import re
import uuid
from dataclasses import dataclass
from datetime import datetime
from typing import Any

from sqlalchemy import text

__all__ = ["ALLOWED_MIMES", "MAX_FILE_BYTES", "FileStoreError", "RecordFile", "put_file",
           "read_file"]

#: The most bytes of one file. The CHECK ``studio_record_files_size`` holds the
#: same number, and it is ``pdf_render.MAX_DOCUMENT_PDF_BYTES``.
MAX_FILE_BYTES = 10_000_000
#: The types that the CHECK ``studio_record_files_mime`` allows.
ALLOWED_MIMES = frozenset({"application/pdf", "image/png", "image/jpeg"})
#: The most characters of the name of a file.
MAX_NAME = 255
_KEY = re.compile(r"[a-z][a-z0-9_]{0,47}", re.ASCII)

_BOUND_ORG = "CAST(NULLIF(current_setting('app.tenant_id', true), '') AS uuid)"

_READ_SQL = (
    "SELECT id::text, name, mime, size_bytes, content, created_at "
    "FROM studio_record_files "
    f"WHERE organization_id = {_BOUND_ORG} AND package_id = CAST(:pid AS uuid) "
    "AND object_id = CAST(:oid AS uuid) AND record_id = CAST(:rid AS uuid) "
    "AND field_key = :key AND id = CAST(:fid AS uuid)"
)

_INSERT_SQL = (
    "INSERT INTO studio_record_files "
    "(package_id, object_id, record_id, field_key, name, mime, size_bytes, content, "
    "created_by) "
    "VALUES (CAST(:pid AS uuid), CAST(:oid AS uuid), CAST(:rid AS uuid), :key, :name, "
    ":mime, :size, :content, :me) "
    "RETURNING id::text"
)


class FileStoreError(ValueError):
    """A file that the store refuses before it reaches the database."""


@dataclass(frozen=True)
class RecordFile:
    """One file of a record, with its bytes."""

    id: str
    name: str
    mime: str
    size_bytes: int
    content: bytes
    created_at: datetime


def _id(raw: Any, what: str) -> str:
    try:
        return str(uuid.UUID(str(raw)))
    except (ValueError, TypeError, AttributeError):
        raise FileStoreError(f"{what} is not an id.") from None


def _key(raw: Any) -> str:
    if not isinstance(raw, str) or _KEY.fullmatch(raw) is None:
        raise FileStoreError("field_key is not a field key.")
    return raw


async def read_file(db: Any, *, package_id: str, object_id: str, record_id: str,
                    field_key: str, file_id: str) -> RecordFile | None:
    """The file *file_id* that the field *field_key* of the record *record_id*
    holds, or None. The ids of the package, the object and the record all
    bind, so a file of another record never answers. A bad id raises
    :class:`FileStoreError` before a statement runs."""
    row = (await db.execute(text(_READ_SQL), {
        "pid": _id(package_id, "package_id"), "oid": _id(object_id, "object_id"),
        "rid": _id(record_id, "record_id"), "key": _key(field_key),
        "fid": _id(file_id, "file_id")})).first()
    if row is None:
        return None
    return RecordFile(id=str(row[0]), name=str(row[1]), mime=str(row[2]),
                      size_bytes=int(row[3]), content=bytes(row[4]), created_at=row[5])


async def put_file(db: Any, member: str, *, package_id: str, object_id: str,
                   record_id: str, field_key: str, name: str, mime: str,
                   content: bytes) -> str:
    """Write one file and answer its id. D13c is the first caller.

    The record must exist in the bound org: the composite foreign key refuses
    any other. The caps repeat the CHECKs, so a refusal here is a sentence and
    never a database error."""
    who = (member or "").strip().lower()
    if not who:
        raise FileStoreError("A file needs the member who makes it.")
    if mime not in ALLOWED_MIMES:
        raise FileStoreError("A file is a PDF, a PNG or a JPEG.")
    if not isinstance(content, bytes | bytearray | memoryview):
        raise FileStoreError("The content of a file is bytes.")
    data = bytes(content)
    if len(data) > MAX_FILE_BYTES:
        raise FileStoreError(f"A file holds at most {MAX_FILE_BYTES} bytes.")
    if not isinstance(name, str) or not 1 <= len(name) <= MAX_NAME:
        raise FileStoreError(f"The name of a file holds 1 to {MAX_NAME} characters.")
    row = (await db.execute(text(_INSERT_SQL), {
        "pid": _id(package_id, "package_id"), "oid": _id(object_id, "object_id"),
        "rid": _id(record_id, "record_id"), "key": _key(field_key), "name": name,
        "mime": mime, "size": len(data), "content": data, "me": who})).first()
    return str(row[0])
