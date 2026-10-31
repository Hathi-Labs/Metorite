"""The one writer of ``studio_skills`` and ``studio_skill_versions`` (WS-STU F1).

Spec: ``project-docs/specs/ai_studio.md`` §3.5 and the §14 F1 row. Table:
migration ``studio_skills`` (319 at build). ``tests/unit/test_studio_skills_r8.py``
runs this SQL on a real Postgres.

**Tenancy (R5).** Every function takes a session that the caller opened with
``tenant_session(org)``, so RLS binds each statement. No function takes an
org. An INSERT writes no ``organization_id``: the column DEFAULT reads the
bound tenant, and each statement filters on ``_BOUND_ORG`` too.

**The member.** Every function takes the member as a required argument, and
every statement filters on it. In F1 a member reads and writes only the
skills that the member owns. ⚠️ The store does not authenticate. It only
scopes. The caller resolves the member from the session, never from request
input (R5, contract R11). J2 adds the share to a group or to the org, and
the reads of a shared skill come with it.

**Private by default.** :func:`create_skill` takes no visibility. The column
DEFAULT is ``private``, and no function here changes it.

**A version is immutable.** No function here updates a version row. The
trigger ``studio_skill_versions_immutable`` refuses each UPDATE on the
database too, so a later writer cannot change a stored version either.

**Two writers.** :func:`add_version` raises ``current_version`` on the skill
row and writes the version in ONE statement. The UPDATE locks the skill row,
so a second writer waits, then reads the raised number and takes the next
one. Two calls at once give two numbers. The primary key
``(organization_id, skill_id, version)`` refuses a second row of one number,
if some other path tries one.

**The body.** :func:`add_version` checks a light shape only: an object with a
non-empty ``description`` and ``instructions``, and ``actions`` as a list of
strings. The other parts of §3.5 (``cards``, ``knowledge``, ``tests``) pass
as given. The full manifest check is A2, and this module does not import it.
The CHECK ``studio_skill_versions_body_size`` caps the JSON text at
:data:`MAX_BODY_BYTES`, and the store refuses a bigger body first.

**State.** :func:`set_state` writes one of :data:`STATES`. F1 holds no
transition table. F2 and J1 own the rules of a move.
"""

from __future__ import annotations

import hashlib
import json
import re
import uuid
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import datetime
from typing import Any

from sqlalchemy import text

#: The four states, as the CHECK ``studio_skills_state`` holds them.
STATES: tuple[str, ...] = ("draft", "published", "paused", "archived")
#: The three visibilities, as the CHECK ``studio_skills_visibility`` holds
#: them. F1 writes only the DEFAULT, ``private``.
VISIBILITIES: tuple[str, ...] = ("private", "group", "org")
#: The bound of the CHECK ``studio_skill_versions_body_size``: the JSON text
#: of a body, in bytes (256 KiB).
MAX_BODY_BYTES = 262144
#: The bounds of the CHECKs ``studio_skills_name``, ``studio_skills_slug`` and
#: ``studio_skills_description``.
MAX_NAME_CHARS = 120
MAX_SLUG_CHARS = 64
MAX_DESCRIPTION_CHARS = 1024
#: The slug shape, as the CHECK ``studio_skills_slug`` holds it.
_SLUG_RE = re.compile(r"^[a-z0-9]+(-[a-z0-9]+)*$")

_BOUND_ORG = "CAST(NULLIF(current_setting('app.tenant_id', true), '') AS uuid)"

_SKILL_COLUMNS = (
    "id::text, name, slug, description, owner_email, visibility, state, "
    "current_version, created_at, updated_at"
)
_VERSION_COLUMNS = "skill_id::text, version, body, content_hash, created_by, created_at"

#: A slug that the org holds already makes no row. The store then refuses.
_CREATE_SQL = (
    "INSERT INTO studio_skills (name, slug, description, owner_email) "
    "VALUES (:name, :slug, :desc, :m) "
    "ON CONFLICT (organization_id, slug) DO NOTHING "
    f"RETURNING {_SKILL_COLUMNS}"
)

_GET_SQL = (
    f"SELECT {_SKILL_COLUMNS} FROM studio_skills "
    f"WHERE organization_id = {_BOUND_ORG} AND owner_email = :m "
    "AND id = CAST(:id AS uuid)"
)

_LIST_SQL = (
    f"SELECT {_SKILL_COLUMNS} FROM studio_skills "
    f"WHERE organization_id = {_BOUND_ORG} AND owner_email = :m "
    "ORDER BY updated_at DESC, id"
)

_SET_STATE_SQL = (
    "UPDATE studio_skills SET state = :st, updated_at = now() "
    f"WHERE organization_id = {_BOUND_ORG} AND owner_email = :m "
    "AND id = CAST(:id AS uuid)"
)

#: The next number and the version, in one statement. The UPDATE locks the
#: skill row. A second writer waits for the first to commit, then reads the
#: raised ``current_version`` again (READ COMMITTED) and takes the next one.
#: A skill of another member or of another org raises nothing, so no
#: version goes in.
_ADD_VERSION_SQL = (
    "WITH bumped AS ("
    "UPDATE studio_skills SET current_version = COALESCE(current_version, 0) + 1, "
    "updated_at = now() "
    f"WHERE organization_id = {_BOUND_ORG} AND owner_email = :m "
    "AND id = CAST(:id AS uuid) "
    "RETURNING id, current_version) "
    "INSERT INTO studio_skill_versions (skill_id, version, body, content_hash, created_by) "
    "SELECT b.id, b.current_version, CAST(:body AS jsonb), :hash, :m FROM bumped b "
    f"RETURNING {_VERSION_COLUMNS}"
)

#: One version of a skill of the member. With no number, the newest one.
_GET_VERSION_SQL = (
    "SELECT v.skill_id::text, v.version, v.body, v.content_hash, v.created_by, "
    "v.created_at FROM studio_skill_versions v "
    "JOIN studio_skills s ON s.organization_id = v.organization_id AND s.id = v.skill_id "
    f"WHERE v.organization_id = {_BOUND_ORG} AND s.owner_email = :m "
    "AND v.skill_id = CAST(:id AS uuid) "
    "AND v.version = COALESCE(CAST(:v AS integer), s.current_version)"
)


class SkillStoreError(ValueError):
    """A write or a read that the store refuses before it reaches the database."""


class SkillSlugTaken(SkillStoreError):
    """The org holds a skill with this slug already."""


@dataclass(frozen=True)
class Skill:
    """One row of ``studio_skills``."""

    id: str
    name: str
    slug: str
    description: str
    owner_email: str
    visibility: str
    state: str
    current_version: int | None
    created_at: datetime
    updated_at: datetime


@dataclass(frozen=True)
class Version:
    """One row of ``studio_skill_versions``."""

    skill_id: str
    version: int
    body: dict[str, Any]
    content_hash: str
    created_by: str
    created_at: datetime


def _member(member: object) -> str:
    """The member as the CHECK ``studio_skills_owner_email`` holds it."""
    if not isinstance(member, str) or not member.strip():
        raise SkillStoreError("member must be a non-empty string")
    return member.strip().lower()


def _skill_id(skill_id: object) -> str:
    """A skill id must be a UUID. A bad one fails here, not in a statement
    that would end the caller's transaction."""
    try:
        return str(uuid.UUID(str(skill_id)))
    except (ValueError, TypeError, AttributeError) as exc:
        raise SkillStoreError("skill_id must be a UUID") from exc


def _text(value: object, field: str, *, limit: int, required: bool) -> str:
    if not isinstance(value, str):
        raise SkillStoreError(f"{field} must be a string")
    if required and not value.strip():
        raise SkillStoreError(f"{field} must not be empty")
    if len(value) > limit:
        raise SkillStoreError(f"{field} holds at most {limit} characters")
    return value


def _holds_nul(value: object) -> bool:
    """True when a string anywhere in *value*, a key too, holds a NUL."""
    if isinstance(value, str):
        return "\x00" in value
    if isinstance(value, Mapping):
        return any(_holds_nul(k) or _holds_nul(v) for k, v in value.items())
    if isinstance(value, list | tuple):
        return any(_holds_nul(v) for v in value)
    return False


def check_body(body: object) -> str:
    """The light shape check of a version body. Answers its JSON text.

    The body must be an object with a non-empty ``description`` and
    ``instructions``. ``actions``, when present, is a list of non-empty
    strings. The JSON text holds at most :data:`MAX_BODY_BYTES` bytes.
    """
    if not isinstance(body, Mapping):
        raise SkillStoreError("body must be an object")
    for field in ("description", "instructions"):
        value = body.get(field)
        if not isinstance(value, str) or not value.strip():
            raise SkillStoreError(f"body.{field} must be a non-empty string")
    actions = body.get("actions")
    if actions is not None and (
            not isinstance(actions, list)
            or not all(isinstance(a, str) and a.strip() for a in actions)):
        raise SkillStoreError("body.actions must be a list of non-empty strings")
    try:
        # The separators of the jsonb text form, so the count is close to
        # what the CHECK measures.
        encoded = json.dumps(dict(body), ensure_ascii=False, allow_nan=False)
    except (TypeError, ValueError) as exc:
        raise SkillStoreError("body must be plain JSON") from exc
    if _holds_nul(body):
        # jsonb refuses the NUL character, in a key or in a value.
        raise SkillStoreError("body must not hold a NUL character")
    if len(encoded.encode("utf-8")) > MAX_BODY_BYTES:
        raise SkillStoreError(f"body holds at most {MAX_BODY_BYTES} bytes of JSON")
    return encoded


def content_hash(body: Mapping[str, Any]) -> str:
    """The SHA-256 of *body* in canonical JSON: sorted keys, no spaces."""
    canonical = json.dumps(dict(body), sort_keys=True, separators=(",", ":"),
                           ensure_ascii=False, allow_nan=False)
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def _skill(row: Any) -> Skill:
    return Skill(
        id=str(row[0]), name=str(row[1]), slug=str(row[2]), description=str(row[3]),
        owner_email=str(row[4]), visibility=str(row[5]), state=str(row[6]),
        current_version=None if row[7] is None else int(row[7]), created_at=row[8],
        updated_at=row[9])


def _version(row: Any) -> Version:
    body = row[2]
    if isinstance(body, str | bytes):
        body = json.loads(body)
    return Version(
        skill_id=str(row[0]), version=int(row[1]), body=dict(body),
        content_hash=str(row[3]), created_by=str(row[4]), created_at=row[5])


async def create_skill(db: Any, member: str, *, name: str, slug: str,
                       description: str = "") -> Skill:
    """Create a skill of *member*, private and in ``draft``, with no version.

    Raises :class:`SkillSlugTaken` when the org holds the slug already, and
    then writes no row. The slug is lower-case letters and digits, with one
    hyphen between parts, at most 64 characters.
    """
    m = _member(member)
    clean_name = _text(name, "name", limit=MAX_NAME_CHARS, required=True).strip()
    if not isinstance(slug, str) or len(slug) > MAX_SLUG_CHARS or not _SLUG_RE.match(slug):
        raise SkillStoreError(
            f"slug must be lower-case letters, digits and single hyphens, "
            f"at most {MAX_SLUG_CHARS} characters")
    desc = _text(description, "description", limit=MAX_DESCRIPTION_CHARS, required=False)
    row = (await db.execute(text(_CREATE_SQL), {
        "name": clean_name, "slug": slug, "desc": desc, "m": m})).first()
    if row is None:
        raise SkillSlugTaken(f"the slug {slug!r} is taken in this organization")
    return _skill(row)


async def get_skill(db: Any, member: str, skill_id: str) -> Skill | None:
    """The skill *skill_id* of *member*, or None. A skill of another member
    or of another org answers None."""
    row = (await db.execute(text(_GET_SQL), {
        "m": _member(member), "id": _skill_id(skill_id)})).first()
    return None if row is None else _skill(row)


async def list_for(db: Any, member: str) -> list[Skill]:
    """The skills that *member* owns in the bound org, newest change first.
    In F1 that is all a member sees: J2 adds the shared ones."""
    rows = (await db.execute(text(_LIST_SQL), {"m": _member(member)})).all()
    return [_skill(r) for r in rows]


async def set_state(db: Any, member: str, skill_id: str, state: str) -> bool:
    """Move the skill *skill_id* of *member* to *state*. Answers False when
    no skill of the member has the id."""
    if state not in STATES:
        raise SkillStoreError(f"state must be one of {STATES}")
    result: Any = await db.execute(text(_SET_STATE_SQL), {
        "st": state, "m": _member(member), "id": _skill_id(skill_id)})
    return bool(getattr(result, "rowcount", 0))


async def add_version(db: Any, member: str, skill_id: str,
                      body: Mapping[str, Any]) -> Version | None:
    """Store *body* as the next version of the skill *skill_id* of *member*.

    The number is ``current_version + 1``, or 1 for the first version, and
    the skill's ``current_version`` moves to it in the same statement.
    Answers None when no skill of the member has the id: then nothing
    changes. A body that fails :func:`check_body` raises before the database.
    """
    m = _member(member)
    sid = _skill_id(skill_id)
    encoded = check_body(body)
    row = (await db.execute(text(_ADD_VERSION_SQL), {
        # The hash reads the body back from its JSON text, so a key that
        # JSON turns into a string hashes as the stored row holds it.
        "m": m, "id": sid, "body": encoded,
        "hash": content_hash(json.loads(encoded))})).first()
    return None if row is None else _version(row)


async def get_version(db: Any, member: str, skill_id: str,
                      version: int | None = None) -> Version | None:
    """The version *version* of the skill *skill_id* of *member*, or the
    newest one when *version* is None. Answers None when the member has no
    such skill or the skill has no such version."""
    if version is not None and (
            isinstance(version, bool) or not isinstance(version, int) or version < 1):
        raise SkillStoreError("version must be a whole number of 1 or more")
    row = (await db.execute(text(_GET_VERSION_SQL), {
        "m": _member(member), "id": _skill_id(skill_id), "v": version})).first()
    return None if row is None else _version(row)
