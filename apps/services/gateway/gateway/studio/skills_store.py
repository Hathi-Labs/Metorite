"""The one writer of ``studio_skills`` and ``studio_skill_versions`` (WS-STU F1).

Spec: ``project-docs/specs/ai_studio.md`` §3.5 and the §14 F1 row. Table:
migration ``studio_skills`` (320 at build). ``tests/unit/test_studio_skills_r8.py``
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
DEFAULT is ``private``. Only the share of WS-STU J2c changes it, below:
:func:`approve_share` writes ``org`` and :func:`unshare` writes ``private``.
No function writes ``group`` or ``system``.

**The share with the org (WS-STU J2c, ``ai_studio_rows/J2c.md``).** The
owner asks for ONE version (:func:`request_share`). A holder of
``studio:publish`` who is not the owner approves it (:func:`approve_share`)
or rejects it (:func:`reject_share`). An approve sets ``visibility = 'org'``
and pins ``shared_version``. Each member who is not the owner loads that
pinned version, and the owner loads the newest one (``_LOADABLE_WHERE``).
So a later :func:`add_version` reaches no other member until a holder
approves a new ask. :func:`unshare` returns the skill to ``private``. The
caller passes ``can_publish``, the check of the right that the route made,
because a store does not read rights. A version stays immutable: the share
writes columns of ``studio_skills`` only.

**A version is immutable.** No function here updates a version row. The
trigger ``studio_skill_versions_immutable`` refuses each UPDATE on the
database too, so a later writer cannot change a stored version either.

**Two writers.** :func:`add_version` raises ``current_version`` on the skill
row and writes the version in ONE statement. The UPDATE locks the skill row,
so a second writer waits, then reads the raised number and takes the next
one. Two calls at once give two numbers. The primary key
``(organization_id, skill_id, version)`` refuses a second row of one number,
if some other path tries one.

**The slug.** A slug is unique for each owner in an org, not for each org.
Two members may each hold the slug ``layoff-plan``, so
:class:`SkillSlugTaken` tells a member nothing about a skill of another
member. A tool pack id (F3) uses the skill ``id``, never the slug.

**The body.** :func:`add_version` checks a light shape only: an object with a
non-empty ``description`` and ``instructions``, and ``actions`` as a list of
strings. ``tests`` has the shape of ``skill_tests.parse_tests`` (F4a). The
other parts of §3.5 (``cards``, ``knowledge``) pass as given. The full manifest check is A2, and this module does not import it.
The CHECK ``studio_skill_versions_body_size`` caps the jsonb text at
:data:`MAX_BODY_BYTES`, and the store refuses a bigger body first. The store
measures its own JSON text, and that text has the same length as the jsonb
text, because :func:`check_body` refuses each value that Postgres writes
longer (a float with an exponent) or refuses (a NUL, a lone surrogate).
``test_studio_skills_r8.py`` stores a body of exactly the limit, so the two
limits stay in step.

**Hidden characters (WS-STU F1b).** A model reads a skill as instructions,
so the store refuses a character that no person sees: in the name, in the
description, and in each string and each key of the body, at any depth. The
one rule is ``acb_skills.hidden_text``, and the store keeps no list of code
points. The refusal is one sentence that names the path and the position,
never the text: "body.instructions: This text holds a hidden character at
position 12 (U+200B). Remove it." The store refuses, and it never strips,
because a strip would change what the owner saved. ``check_body`` runs the
walk after the size check, so the walk reads at most :data:`MAX_BODY_BYTES`.

**The text columns.** The store refuses a name or a description with a NUL,
a lone surrogate or a hidden character, and it matches the whole slug
(``fullmatch``). So no value reaches a statement that Postgres refuses, and
no refusal echoes it.

**State.** :func:`set_state` writes one of :data:`STATES`. F1 holds no
transition table. F2 and J1 own the rules of a move. A caller that decides a
move from the state reads the row with ``get_skill(..., for_update=True)``
first, so two moves at once cannot both pass the rule.
"""

from __future__ import annotations

import hashlib
import json
import math
import re
import uuid
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import datetime
from typing import Any

from acb_skills.hidden_text import body_hidden_problem, hidden_problem
from acb_skills.skill_slug import MAX_SLUG_CHARS  # defined once, in acb_skills
from sqlalchemy import text

from gateway.studio.skill_tests import SkillTestError, parse_tests

#: The four states, as the CHECK ``studio_skills_state`` holds them.
STATES: tuple[str, ...] = ("draft", "published", "paused", "archived")
#: The three visibilities, as the CHECK ``studio_skills_visibility`` holds
#: them. F1 writes only the DEFAULT, ``private``. J2c writes ``org`` on an
#: approve and ``private`` on an unshare. ``system`` is not here: only a
#: reviewed migration can add it.
VISIBILITIES: tuple[str, ...] = ("private", "group", "org")
#: The states of a share ask, as the CHECK ``studio_skills_share_state``
#: holds them (J2c). NULL in the row means no ask.
SHARE_STATES: tuple[str, ...] = ("pending", "approved", "rejected")
#: The bound of the CHECK ``studio_skill_versions_body_size``: the JSON text
#: of a body, in bytes (256 KiB).
MAX_BODY_BYTES = 262144
#: A float of this size or more is refused. See :func:`_check_values`.
MAX_FLOAT = 1e15
#: The bounds of the CHECKs ``studio_skills_name``, ``studio_skills_slug`` and
#: ``studio_skills_description``.
MAX_NAME_CHARS = 120
MAX_DESCRIPTION_CHARS = 1024
#: The slug shape, as the CHECK ``studio_skills_slug`` holds it.
_SLUG_RE = re.compile(r"^[a-z0-9]+(-[a-z0-9]+)*$")

_BOUND_ORG = "CAST(NULLIF(current_setting('app.tenant_id', true), '') AS uuid)"

_SKILL_COLUMNS = (
    "id::text, name, slug, description, owner_email, visibility, state, "
    "current_version, created_at, updated_at"
)
_VERSION_COLUMNS = "skill_id::text, version, body, content_hash, created_by, created_at"

#: A slug that the member holds already makes no row. The store then refuses.
_CREATE_SQL = (
    "INSERT INTO studio_skills (name, slug, description, owner_email) "
    "VALUES (:name, :slug, :desc, :m) "
    "ON CONFLICT (organization_id, owner_email, slug) DO NOTHING "
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

#: The history of a skill of the member, newest first, with no body (F7a).
_LIST_VERSIONS_SQL = (
    "SELECT v.skill_id::text, v.version, v.content_hash, v.created_by, "
    "v.created_at FROM studio_skill_versions v "
    "JOIN studio_skills s ON s.organization_id = v.organization_id AND s.id = v.skill_id "
    f"WHERE v.organization_id = {_BOUND_ORG} AND s.owner_email = :m "
    "AND v.skill_id = CAST(:id AS uuid) "
    "ORDER BY v.version DESC"
)


class SkillStoreError(ValueError):
    """A write or a read that the store refuses before it reaches the database."""


class SkillSlugTaken(SkillStoreError):
    """The member holds a skill with this slug already, in this org.

    The text names no slug and no other member: a slug is unique for each
    owner, so this error tells nothing about a skill of a colleague.
    """


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


@dataclass(frozen=True)
class VersionInfo:
    """One row of the history of a skill: a version with no body."""

    skill_id: str
    version: int
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
    """A text column as Postgres takes it. Postgres refuses a NUL, and a lone
    surrogate has no UTF-8 form. A hidden character can carry a second text
    into a prompt (``acb_skills.hidden_text``). Each refusal names the field,
    never the value."""
    if not isinstance(value, str):
        raise SkillStoreError(f"{field} must be a string")
    if "\x00" in value:
        raise SkillStoreError(f"{field} must not hold a NUL character")
    try:
        value.encode("utf-8")
    except UnicodeEncodeError:
        raise SkillStoreError(f"{field} must not hold a lone surrogate") from None
    hidden = hidden_problem(value, field)
    if hidden is not None:
        raise SkillStoreError(hidden)
    if required and not value.strip():
        raise SkillStoreError(f"{field} must not be empty")
    if len(value) > limit:
        raise SkillStoreError(f"{field} holds at most {limit} characters")
    return value


def _check_values(value: object) -> None:
    """Refuse each value, a key too, that jsonb refuses or writes longer.

    jsonb refuses the NUL character. A lone surrogate has no UTF-8 form,
    and :func:`check_body` refuses it when it encodes the text.
    """
    if isinstance(value, str):
        if "\x00" in value:
            raise SkillStoreError("body must not hold a NUL character")
    elif isinstance(value, float):
        # Postgres writes each jsonb number with all its digits, so a float
        # in exponent form (1e308 becomes 309 digits) grows past the
        # measured size.
        if (not math.isfinite(value) or abs(value) >= MAX_FLOAT
                or "e" in float.__repr__(value)):
            raise SkillStoreError(
                "a body number must be finite and below 10^15, with no exponent")
    elif isinstance(value, Mapping):
        for k, v in value.items():
            _check_values(k)
            _check_values(v)
    elif isinstance(value, list | tuple):
        for v in value:
            _check_values(v)


def check_body(body: object) -> str:
    """The light shape check of a version body. Answers its JSON text.

    The body must be an object with a non-empty ``description`` and
    ``instructions``. ``actions``, when present, is a list of non-empty
    strings. The JSON text holds at most :data:`MAX_BODY_BYTES` bytes.
    No string holds a NUL or a lone surrogate. A float is finite, below
    :data:`MAX_FLOAT`, and JSON writes it with no exponent. Then the text
    has the length of the jsonb text that the CHECK measures. Last, no
    string and no key holds a hidden character (``body_hidden_problem``).
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
        # The separators of the jsonb text form. With the value checks
        # below, the count is the count that the CHECK makes.
        encoded = json.dumps(dict(body), ensure_ascii=False, allow_nan=False)
    except (TypeError, ValueError) as exc:
        raise SkillStoreError("body must be plain JSON") from exc
    # After the dump, which refuses a circular body, so the walk ends.
    _check_values(body)
    try:
        size = len(encoded.encode("utf-8"))
    except UnicodeEncodeError as exc:
        raise SkillStoreError("body must not hold a lone surrogate") from exc
    if size > MAX_BODY_BYTES:
        raise SkillStoreError(f"body holds at most {MAX_BODY_BYTES} bytes of JSON")
    # After the size check, so the walk reads at most MAX_BODY_BYTES of text.
    hidden = body_hidden_problem(body)
    if hidden is not None:
        raise SkillStoreError(hidden)
    # After the hidden walk, so a hidden character keeps its own refusal.
    if "tests" in body:
        try:
            parse_tests(body)
        except SkillTestError as exc:
            raise SkillStoreError(str(exc)) from None
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

    Raises :class:`SkillSlugTaken` when *member* holds the slug already in
    this org, and then writes no row. Another member may hold the same slug.
    The slug is lower-case letters and digits, with one hyphen between parts,
    at most 64 characters. The name and the description hold no NUL and no
    lone surrogate. Each refusal raises :class:`SkillStoreError` before the
    database.
    """
    m = _member(member)
    clean_name = _text(name, "name", limit=MAX_NAME_CHARS, required=True).strip()
    # fullmatch, because ``$`` of ``match`` also matches before a final newline.
    if not isinstance(slug, str) or len(slug) > MAX_SLUG_CHARS or not _SLUG_RE.fullmatch(slug):
        raise SkillStoreError(
            f"slug must be lower-case letters, digits and single hyphens, "
            f"at most {MAX_SLUG_CHARS} characters")
    desc = _text(description, "description", limit=MAX_DESCRIPTION_CHARS, required=False)
    row = (await db.execute(text(_CREATE_SQL), {
        "name": clean_name, "slug": slug, "desc": desc, "m": m})).first()
    if row is None:
        raise SkillSlugTaken("You already have a skill with this name. Choose another name.")
    return _skill(row)


async def get_skill(db: Any, member: str, skill_id: str, *,
                    for_update: bool = False) -> Skill | None:
    """The skill *skill_id* of *member*, or None. A skill of another member
    or of another org answers None.

    With *for_update*, the read locks the row until the transaction ends. A
    caller that decides a write from the state that it reads takes the lock.
    Then a second writer waits, and reads the state that the first one wrote.
    """
    sql = _GET_SQL + " FOR UPDATE" if for_update else _GET_SQL
    row = (await db.execute(text(sql), {
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


async def carry_tests(db: Any, member: str, skill_id: str,
                      body: Mapping[str, Any]) -> Mapping[str, Any]:
    """*body* with the ``tests`` of the newest version, when *body* has no
    ``tests`` key and that version holds a list that passes
    ``skill_tests.parse_tests``. Otherwise *body* itself (F4a).

    The route of a new version calls it, because the editor form sends no
    ``tests``. The undo route does not, because it copies an older body
    exactly, and ``add_version`` carries nothing. ``"tests": []`` in *body*
    clears the tests, and a list replaces them.

    F4b: the first read takes the row lock, as the undo route does
    (``get_skill(..., for_update=True)``). The lock holds until the commit.
    So a second save waits, then reads the version that this save wrote, and
    no save can carry a list that a save at the same moment replaced.
    """
    skill = await get_skill(db, member, skill_id, for_update=True)
    if "tests" in body or skill is None:
        return body
    newest = await get_version(db, member, skill_id)
    kept = newest.body.get("tests") if newest is not None else None
    if not kept:
        return body
    try:
        parse_tests({"tests": kept})
    except SkillTestError:
        return body  # an old list of a free shape is dropped, never copied
    return {**body, "tests": kept}


async def add_version(db: Any, member: str, skill_id: str,
                      body: Mapping[str, Any]) -> Version | None:
    """Store *body* as the next version of the skill *skill_id* of *member*.

    It stores *body* as given. :func:`carry_tests` does the carry-forward of
    ``tests``, and the route of a new version calls it first.

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


async def list_versions(db: Any, member: str, skill_id: str) -> list[VersionInfo]:
    """The versions of the skill *skill_id* of *member*, newest first, with
    no body. A skill of another member or of another org gives an empty
    list, as a skill with no version does."""
    rows = (await db.execute(text(_LIST_VERSIONS_SQL), {
        "m": _member(member), "id": _skill_id(skill_id)})).all()
    return [VersionInfo(skill_id=str(r[0]), version=int(r[1]), content_hash=str(r[2]),
                        created_by=str(r[3]), created_at=r[4]) for r in rows]


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


# ── The share with the org (WS-STU J2c) ─────────────────────────────────────
#
# The migration ``studio_skill_share`` (463 at build) adds the columns. Each
# statement names the bound org, and RLS binds it too. The member and the
# right come from the route, which reads the verified session (R5).

_SHARE_COLUMNS = (
    "id::text, owner_email, visibility, shared_version, share_state, share_version, "
    "share_requested_by, share_requested_at, share_decided_by, share_decided_at"
)

#: The owner's row, locked. A skill of another member gives no row.
_SHARE_OWNER_SQL = (
    f"SELECT {_SHARE_COLUMNS} FROM studio_skills "
    f"WHERE organization_id = {_BOUND_ORG} AND owner_email = :m "
    "AND id = CAST(:id AS uuid) FOR UPDATE"
)

#: The row of a holder, locked, with no member filter, because a holder is
#: not the owner. RLS and the bound org scope it.
_SHARE_ANY_SQL = (
    f"SELECT {_SHARE_COLUMNS} FROM studio_skills "
    f"WHERE organization_id = {_BOUND_ORG} AND id = CAST(:id AS uuid) FOR UPDATE"
)

_VERSION_EXISTS_SQL = (
    "SELECT 1 FROM studio_skill_versions "
    f"WHERE organization_id = {_BOUND_ORG} AND skill_id = CAST(:id AS uuid) "
    "AND version = :v"
)

_SHARE_ASK_SQL = (
    "UPDATE studio_skills SET share_state = 'pending', share_version = :v, "
    "share_requested_by = :m, share_requested_at = now(), "
    "share_decided_by = NULL, share_decided_at = NULL "
    f"WHERE organization_id = {_BOUND_ORG} AND owner_email = :m "
    f"AND id = CAST(:id AS uuid) RETURNING {_SHARE_COLUMNS}"
)

#: The approve. The one statement that writes ``visibility = 'org'``. It
#: pins the asked version, and only from ``pending`` of that version.
_SHARE_APPROVE_SQL = (
    "UPDATE studio_skills SET share_state = 'approved', visibility = 'org', "
    "shared_version = share_version, share_decided_by = :a, share_decided_at = now() "
    f"WHERE organization_id = {_BOUND_ORG} AND id = CAST(:id AS uuid) "
    f"AND share_state = 'pending' AND share_version = :v RETURNING {_SHARE_COLUMNS}"
)

#: The reject. It changes no visibility, so an older approved version stays
#: shared.
_SHARE_REJECT_SQL = (
    "UPDATE studio_skills SET share_state = 'rejected', "
    "share_decided_by = :a, share_decided_at = now() "
    f"WHERE organization_id = {_BOUND_ORG} AND id = CAST(:id AS uuid) "
    f"AND share_state = 'pending' AND share_version = :v RETURNING {_SHARE_COLUMNS}"
)

#: The unshare. The one statement that writes ``visibility = 'private'``. It
#: clears a pending or an approved ask, and it keeps a rejected one.
_UNSHARE_SQL = (
    "UPDATE studio_skills SET visibility = 'private', shared_version = NULL, "
    "share_state = CASE WHEN share_state = 'rejected' THEN 'rejected' END "
    f"WHERE organization_id = {_BOUND_ORG} AND id = CAST(:id AS uuid) "
    f"RETURNING {_SHARE_COLUMNS}"
)

#: The most pending asks in one read of :func:`list_pending_shares`.
MAX_PENDING_SHARES = 20

#: The pending asks of the bound org, oldest first, each with the body of the
#: asked version. ⚠️ The one read of a skill that does not filter on the
#: member, because the approver is not the owner. Only ``pending`` shows, so
#: a private skill with no ask never shows. A read only: no lock.
_PENDING_SHARES_SQL = (
    "SELECT s.id::text, s.name, s.slug, s.description, s.owner_email, s.share_version, "
    "s.share_requested_by, s.share_requested_at, v.body, v.content_hash "
    "FROM studio_skills s JOIN studio_skill_versions v "
    "ON v.organization_id = s.organization_id AND v.skill_id = s.id "
    "AND v.version = s.share_version "
    f"WHERE s.organization_id = {_BOUND_ORG} AND v.organization_id = {_BOUND_ORG} "
    "AND s.share_state = 'pending' "
    "ORDER BY s.share_requested_at, s.id LIMIT :lim"
)

#: The refusal of a move without the right ``studio:publish``.
NO_SHARE_RIGHT = "Only a holder of studio:publish can approve, reject or list a skill share."
#: The refusal of an approve by the owner of the skill.
SELF_SHARE = ("The owner of this skill cannot approve its share. Another holder of "
              "studio:publish approves.")


class ShareRefused(Exception):
    """The actor may not make this move. Nothing was written. The route
    answers 403."""


class ShareConflict(Exception):
    """The share is not in the state that the move expects. Nothing was
    written. The route answers 409. ``current`` is the state that the store
    read, or None when the guarded UPDATE changed no row."""

    def __init__(self, message: str, current: str | None = None) -> None:
        super().__init__(message)
        self.current = current


@dataclass(frozen=True)
class Share:
    """The share columns of one skill (J2c)."""

    skill_id: str
    owner_email: str
    visibility: str
    shared_version: int | None
    share_state: str | None
    share_version: int | None
    requested_by: str | None
    requested_at: datetime | None
    decided_by: str | None
    decided_at: datetime | None


@dataclass(frozen=True)
class PendingShare:
    """One ask that waits for a holder, with the body of the asked version."""

    skill_id: str
    name: str
    slug: str
    description: str
    owner_email: str
    version: int
    requested_by: str
    requested_at: datetime | None
    body: dict[str, Any]
    content_hash: str


def _int_or_none(value: Any) -> int | None:
    return None if value is None else int(value)


def _str_or_none(value: Any) -> str | None:
    return None if value is None else str(value)


def _share(row: Any) -> Share:
    return Share(
        skill_id=str(row[0]), owner_email=str(row[1]), visibility=str(row[2]),
        shared_version=_int_or_none(row[3]), share_state=_str_or_none(row[4]),
        share_version=_int_or_none(row[5]), requested_by=_str_or_none(row[6]),
        requested_at=row[7], decided_by=_str_or_none(row[8]), decided_at=row[9])


def _share_number(version: object) -> int:
    if isinstance(version, bool) or not isinstance(version, int) or version < 1:
        raise SkillStoreError("version must be a whole number of 1 or more")
    return version


def _share_right(can_publish: object) -> None:
    """Only ``True`` passes. A truthy value that is not a bool is refused."""
    if can_publish is not True:
        raise ShareRefused(NO_SHARE_RIGHT)


async def request_share(db: Any, member: str, skill_id: str, version: int) -> Share | None:
    """The owner asks to share *version* of the skill *skill_id* with the org.

    Answers None when *member* owns no such skill in the bound org, or the
    skill has no such version. Then nothing changes. A ``pending`` ask of the
    same version answers as it is, and writes nothing. A ``pending`` ask of
    another version raises :class:`ShareConflict`: one ask at a time. After
    an ``approved`` or a ``rejected`` ask, only a newer version may be asked,
    and an older or the same version raises :class:`ShareConflict`. The ask
    reads no right: a member without ``studio:publish`` may ask."""
    m = _member(member)
    sid = _skill_id(skill_id)
    v = _share_number(version)
    row = (await db.execute(text(_SHARE_OWNER_SQL), {"m": m, "id": sid})).first()
    if row is None:
        return None
    current = _share(row)
    found = (await db.execute(text(_VERSION_EXISTS_SQL), {"id": sid, "v": v})).first()
    if found is None:
        return None
    if current.share_state == "pending":
        if current.share_version == v:
            return current
        raise ShareConflict(
            f"Version {current.share_version} of this skill waits for an approval. "
            "Wait for the decision, or unshare it first.", "pending")
    if current.share_state in ("approved", "rejected") and v <= (current.share_version or 0):
        raise ShareConflict(
            f"The share of version {current.share_version} is {current.share_state}. "
            "Ask for a newer version.", current.share_state)
    rows = (await db.execute(text(_SHARE_ASK_SQL), {"m": m, "id": sid, "v": v})).all()
    if len(rows) != 1:  # pragma: no cover - the row lock holds the row
        raise ShareConflict("The skill changed under this ask. Nothing was written.")
    return _share(rows[0])


async def _decide_share(db: Any, actor: str, skill_id: str, version: int, *,
                        can_publish: bool, approve: bool) -> Share | None:
    a = _member(actor)
    sid = _skill_id(skill_id)
    v = _share_number(version)
    _share_right(can_publish)
    row = (await db.execute(text(_SHARE_ANY_SQL), {"id": sid})).first()
    if row is None:
        return None
    current = _share(row)
    # A skill that nobody asked to share, or another version, names nothing:
    # a 409 here would tell a holder that a private skill exists.
    if current.share_state is None or current.share_version != v:
        return None
    if approve and current.owner_email == a:
        raise ShareRefused(SELF_SHARE)
    if current.share_state != "pending":
        raise ShareConflict(
            f"The share of this version is {current.share_state}. Only a pending "
            "share can be decided.", current.share_state)
    sql = _SHARE_APPROVE_SQL if approve else _SHARE_REJECT_SQL
    rows = (await db.execute(text(sql), {"a": a, "id": sid, "v": v})).all()
    if len(rows) != 1:  # pragma: no cover - the row lock holds the row
        raise ShareConflict("The share changed under this move. Nothing was written.")
    return _share(rows[0])


async def approve_share(db: Any, approver: str, skill_id: str, version: int, *,
                        can_publish: bool) -> Share | None:
    """A holder approves the pending ask of *version*: the skill becomes
    ``org``, and ``shared_version`` pins *version*.

    *can_publish* is the check of ``studio:publish`` that the caller made.
    Anything but ``True`` raises :class:`ShareRefused` before any statement.
    The owner raises :class:`ShareRefused`. Answers None when the bound org
    holds no ask of this version of this skill. A decided ask raises
    :class:`ShareConflict`."""
    return await _decide_share(db, approver, skill_id, version,
                               can_publish=can_publish, approve=True)


async def reject_share(db: Any, rejecter: str, skill_id: str, version: int, *,
                       can_publish: bool) -> Share | None:
    """A holder rejects the pending ask of *version*. The rule of the right
    is the rule of :func:`approve_share`, but the owner may reject too,
    because a reject only narrows the share. No visibility changes."""
    return await _decide_share(db, rejecter, skill_id, version,
                               can_publish=can_publish, approve=False)


async def unshare(db: Any, actor: str, skill_id: str, *, can_publish: bool) -> Share | None:
    """Return the skill to ``private``. The next turn of each other member
    loads it no more, because the loader reads this row each turn.

    The owner may always unshare. Another member needs *can_publish*, or
    :class:`ShareRefused` raises before the read of a skill of another
    member. A holder unshares only a skill of ``org`` visibility in the
    bound org, and each other id answers None."""
    m = _member(actor)
    sid = _skill_id(skill_id)
    row = (await db.execute(text(_SHARE_OWNER_SQL), {"m": m, "id": sid})).first()
    if row is None:
        _share_right(can_publish)
        row = (await db.execute(text(_SHARE_ANY_SQL), {"id": sid})).first()
        if row is None or _share(row).visibility != "org":
            return None
    rows = (await db.execute(text(_UNSHARE_SQL), {"id": sid})).all()
    if len(rows) != 1:  # pragma: no cover - the row lock holds the row
        raise ShareConflict("The skill changed under this unshare. Nothing was written.")
    return _share(rows[0])


async def list_pending_shares(db: Any, *, can_publish: bool,
                              limit: int = MAX_PENDING_SHARES) -> tuple[list[PendingShare], bool]:
    """The pending asks of the bound org, oldest first, at most *limit* (1
    to :data:`MAX_PENDING_SHARES`). The second value is True when more asks
    wait past the page. Anything but ``True`` in *can_publish* raises
    :class:`ShareRefused` before any statement."""
    _share_right(can_publish)
    if (isinstance(limit, bool) or not isinstance(limit, int)
            or not 1 <= limit <= MAX_PENDING_SHARES):
        raise SkillStoreError(f"limit must be an integer from 1 to {MAX_PENDING_SHARES}")
    rows = (await db.execute(text(_PENDING_SHARES_SQL), {"lim": limit + 1})).all()
    out: list[PendingShare] = []
    for r in rows[:limit]:
        body = r[8]
        if isinstance(body, str | bytes):
            body = json.loads(body)
        out.append(PendingShare(
            skill_id=str(r[0]), name=str(r[1]), slug=str(r[2]), description=str(r[3]),
            owner_email=str(r[4]), version=int(r[5]),
            requested_by=str(r[6]) if r[6] is not None else str(r[4]),
            requested_at=r[7], body=dict(body or {}), content_hash=str(r[9])))
    return out, len(rows) > limit


# ── The reads of the tool packs (WS-STU F3) ─────────────────────────────────
#
# ``orchestrator._tool_injection._turn_with_skills`` is the one caller. It
# opens ``tenant_session()`` on the bound tenant of the run, so RLS binds each
# statement, and each statement filters on ``_BOUND_ORG`` too. The member is
# the verified member of the run, never request input (R5).
#
# **The rule of a loadable skill.** The state is ``published``. The member
# owns it and it has a version, OR its visibility is ``org`` and a holder
# approved a version (``shared_version``, J2c). A skill of visibility
# ``group`` loads for its owner only: J2 adds the group and its members, so
# until then a group share reaches no other member (it fails closed). A
# draft, a paused and an archived skill never load. The fences are
# ``tests/unit/test_skill_packs_r8.py`` and
# ``tests/unit/test_studio_skill_share_r8.py``.
#
# **The pinned version (J2c).** The owner loads ``current_version``. Each
# other member loads ``shared_version``, the version that a holder approved.
# So a later version of the owner reaches no other member until a holder
# approves a new ask. ``_LOADABLE_VERSION`` is the one expression of it, and
# each loadable read reports it as ``current_version``.

#: The most skill heads that one turn reads. The match runs on the name, and
#: TOF-1c also on the head description, so the head read takes no body.
MAX_LOADABLE = 200

#: The version that the member loads: the newest for the owner, the pinned
#: one for each other member (J2c).
_LOADABLE_VERSION = (
    "(CASE WHEN s.owner_email = :m THEN s.current_version ELSE s.shared_version END)"
)

_LOADABLE_WHERE = (
    f"s.organization_id = {_BOUND_ORG} AND s.state = 'published' "
    f"AND {_LOADABLE_VERSION} IS NOT NULL "
    "AND (s.owner_email = :m OR s.visibility = 'org')"
)

_LOADABLE_SQL = (
    "SELECT s.id::text, s.name, s.slug, s.description, s.owner_email, s.visibility, "
    f"s.state, {_LOADABLE_VERSION}, s.created_at, s.updated_at FROM studio_skills s "
    f"WHERE {_LOADABLE_WHERE} ORDER BY s.id LIMIT :lim"
)

#: The loadable version of each loadable skill in *ids*: the newest for the
#: owner, the pinned one for each other member. The statement checks the
#: rule again, so a skill that left ``published`` or ``org`` after the head
#: read gives no row.
_LOADABLE_VERSIONS_SQL = (
    "SELECT s.id::text, s.name, s.slug, s.description, s.owner_email, s.visibility, "
    f"s.state, {_LOADABLE_VERSION}, s.created_at, s.updated_at, "
    "v.skill_id::text, v.version, v.body, v.content_hash, v.created_by, v.created_at "
    "FROM studio_skills s JOIN studio_skill_versions v "
    "ON v.organization_id = s.organization_id AND v.skill_id = s.id "
    f"AND v.version = {_LOADABLE_VERSION} "
    f"WHERE {_LOADABLE_WHERE} AND v.organization_id = {_BOUND_ORG} "
    "AND s.id = ANY(CAST(:ids AS uuid[])) ORDER BY s.id"
)


async def list_loadable(db: Any, member: str) -> list[Skill]:
    """The skills that a turn of *member* may load, in the order of their id.

    A published skill with a version, that *member* owns or that the org
    shares (``visibility = 'org'`` with an approved ``shared_version``). At
    most :data:`MAX_LOADABLE`. No body. ``current_version`` of each head is
    the version that *member* loads: the pinned one for a member who is not
    the owner (J2c)."""
    rows = (await db.execute(text(_LOADABLE_SQL), {
        "m": _member(member), "lim": MAX_LOADABLE})).all()
    return [_skill(r) for r in rows]


async def loadable_versions(db: Any, member: str,
                            skill_ids: list[str]) -> list[tuple[Skill, Version]]:
    """The current version of each skill of *skill_ids* that *member* may
    load, with its skill, in the order of the id. A skill that is no longer
    loadable gives no pair. An id that is no UUID raises before the database."""
    ids = [_skill_id(i) for i in skill_ids]
    if not ids:
        return []
    rows = (await db.execute(text(_LOADABLE_VERSIONS_SQL), {
        "m": _member(member), "ids": ids})).all()
    return [(_skill(r[:10]), _version(r[10:])) for r in rows]
