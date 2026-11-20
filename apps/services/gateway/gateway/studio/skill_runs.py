"""The one writer and reader of ``studio_skill_runs`` (WS-TOF TOF-3a, F12a).

Spec: ``project-docs/specs/ai_studio_rows/F12.md``, "The table
``studio_skill_runs`` (F12a)". Migration: the file that creates
``studio_skill_runs`` (465 at build). ``tests/unit/test_studio_skill_runs_r8.py``
runs this SQL on a real Postgres, and ``tests/unit/test_skill_runs.py`` holds
the refusals.

**One row for each run of a skill.** A ``live`` chat turn, a ``test`` of F4,
or a repair ``proposal``. A row never changes after its INSERT. The store
holds no UPDATE, and the trigger ``studio_skill_runs_immutable`` refuses
each UPDATE on the database too.

**No caller in F12a.** F12b calls :func:`record_live` and
:func:`record_test`. F12c calls :func:`record_proposal` and the readers.
F12b and F12c read the flag ``STUDIO_SKILL_REPAIR_ORGS`` before they call.
This module reads no flag.

**No input and no value.** No function stores the words of a member. A live
row keeps :func:`input_hash` of the input, never the input. ``writes`` keeps
action names and key names only, and :func:`check_writes` refuses an item
that holds a value. ``detail`` must name a tool, an action, a key or an error
class. The caller builds it, and the store caps it at 300 characters.

**Tenancy (R5).** Each function takes a session that the caller opened with
``tenant_session()``, so RLS binds each statement, and each statement names
the bound org too. No function takes an org. The org of a new row comes from
the column DEFAULT, the bound tenant. The org of the input hash comes from
the bound session, never from the caller.

**The skill.** A foreign key check skips RLS. So each INSERT selects the
skill, or the failed row, under the bound org, and a skill of another org
gives :class:`UnknownSkill`. A proposal takes its skill, its version and its
input hash from the failed live row that it repairs.

**The refusals.** Each writer checks the whole shape before the database
and raises :class:`SkillRunError`. Its ``code`` names the fault, and its
text names a field, never a value. A refusal writes no row and sends no
statement.

**The member.** ``created_by`` is the member of the run. The caller takes
the member from the session, never from request input. The member purge
(``routes/admin/members.py``) deletes the rows of a member on
``created_by``. No reader ranks or scores a person.
"""

from __future__ import annotations

import hashlib
import json
import re
import uuid
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal, InvalidOperation
from types import MappingProxyType
from typing import Any

from sqlalchemy import text

from gateway.studio import skill_tests

__all__ = [
    "HISTORY_ROWS",
    "KINDS",
    "LIVE_CODES",
    "MAX_DETAIL_CHARS",
    "MAX_WRITES",
    "MAX_WRITES_BYTES",
    "REASONS",
    "REPEAT_REASONS",
    "SKILL_FAULTS",
    "AlreadyRepaired",
    "DuplicateFailure",
    "History",
    "NotRepairable",
    "SkillRun",
    "SkillRunError",
    "UnknownSkill",
    "bound_input_hash",
    "check_writes",
    "fold_space",
    "get_run",
    "history",
    "input_hash",
    "newest_passed_live",
    "proposal_for",
    "record_live",
    "record_proposal",
    "record_test",
    "repeats_a_failed_body",
    "writes_bytes",
]

#: The three kinds, as the CHECK ``studio_skill_runs_kind`` holds them.
KINDS: tuple[str, ...] = ("live", "test", "proposal")

#: The seven faults of the skill itself. A test row takes only these, and
#: the repeat check counts them (F12.md "The test writer").
SKILL_FAULTS: frozenset[str] = frozenset({
    "other_action", "missing_write", "extra_write", "field_mismatch",
    "invalid_call", "run_failed", "call_cap",
})

#: The three codes of a failed live turn (F12.md "The live writer").
LIVE_CODES: frozenset[str] = frozenset({"tool_error", "card_declined", "member_said_wrong"})

#: The closed set of reasons for each kind. A live row takes ``passed`` or a
#: live code. A test row takes a skill fault only. A proposal takes each code
#: of ``skill_tests.REASONS``, ``passed`` too. The union with the skill
#: faults keeps ``invalid_call``, which F4b adds to ``skill_tests.REASONS``.
REASONS: Mapping[str, frozenset[str]] = MappingProxyType({
    "live": frozenset({"passed"}) | LIVE_CODES,
    "test": SKILL_FAULTS,
    "proposal": frozenset(skill_tests.REASONS) | SKILL_FAULTS,
})

#: The reasons of a failure of the skill. Only these make a body a repeat
#: (F12.md check 8). A stop for a cap, a meter fault or the time limit is
#: no failure of the skill (D-F12-13).
REPEAT_REASONS: frozenset[str] = LIVE_CODES | SKILL_FAULTS

#: The bound of the CHECK ``studio_skill_runs_detail``.
MAX_DETAIL_CHARS = 300
#: The bounds of the CHECKs ``studio_skill_runs_writes_items`` and
#: ``studio_skill_runs_writes_size``.
MAX_WRITES = 10
MAX_WRITES_BYTES = 4096
#: The most key names in one item of ``writes``.
MAX_KEYS = 32
#: The most failed rows of the member in :func:`history` (D-F12-3).
HISTORY_ROWS = 10
#: The longest id of an F4 test, as ``skill_tests`` holds it.
MAX_TEST_ID = 64
#: The longest pointer to an agent run or a chat thread.
MAX_POINTER = 200
#: The window of :func:`newest_passed_live`, in hours (F12.md "It was wrong").
WRONG_WINDOW_HOURS = 24

_HEX64 = re.compile(r"[0-9a-f]{64}")
_REASON = re.compile(r"[a-z_]{1,32}")
#: An action name: ``tool:create_task`` or ``crm.read_deal``. No space.
_ACTION = re.compile(r"[A-Za-z0-9_][A-Za-z0-9_.:-]{0,79}")
#: A key name: ``company`` or ``due_date``. No space.
_KEY = re.compile(r"[A-Za-z0-9_][A-Za-z0-9_.-]{0,63}")
_LIMIT_CREDITS = Decimal(10) ** 9

_BOUND_ORG = "CAST(NULLIF(current_setting('app.tenant_id', true), '') AS uuid)"

_COLUMNS = (
    "id::text, skill_id::text, version, kind, passed, reason, detail, body_key, "
    "input_hash, writes, repair_of::text, test_id, run_id, thread_id, credits, "
    "created_by, created_at"
)

_BOUND_ORG_SQL = "SELECT current_setting('app.tenant_id', true)"

#: A live or a test row. The SELECT names the skill under the bound org, so
#: a skill of another org gives no row. A second failed live row of one run
#: gives no row too (the index ``studio_skill_runs_one_failed_live``).
_INSERT_SQL = (
    "INSERT INTO studio_skill_runs (skill_id, version, kind, passed, reason, detail, "
    "body_key, input_hash, writes, test_id, run_id, thread_id, credits, created_by) "
    "SELECT s.id, CAST(:version AS integer), CAST(:kind AS text), "
    "CAST(:passed AS boolean), CAST(:reason AS text), CAST(:detail AS text), "
    "CAST(:body_key AS text), CAST(:input_hash AS text), CAST(:writes AS jsonb), "
    "CAST(:test_id AS text), CAST(:run_id AS text), CAST(:thread_id AS text), "
    "CAST(:credits AS numeric), CAST(:by AS text) "
    f"FROM studio_skills s WHERE s.organization_id = {_BOUND_ORG} "
    "AND s.id = CAST(:sid AS uuid) "
    "ON CONFLICT (organization_id, skill_id, run_id) "
    "WHERE kind = 'live' AND NOT passed DO NOTHING "
    f"RETURNING {_COLUMNS}"
)

#: A proposal. It takes the skill, the version and the input hash from the
#: failed live row that it repairs. A second proposal for one failed row
#: gives no row (the index ``studio_skill_runs_one_repair``).
_INSERT_PROPOSAL_SQL = (
    "INSERT INTO studio_skill_runs (skill_id, version, kind, passed, reason, detail, "
    "body_key, input_hash, writes, repair_of, run_id, thread_id, credits, created_by) "
    "SELECT r.skill_id, r.version, 'proposal', CAST(:passed AS boolean), "
    "CAST(:reason AS text), CAST(:detail AS text), CAST(:body_key AS text), "
    "r.input_hash, CAST(:writes AS jsonb), r.id, CAST(:run_id AS text), "
    "CAST(:thread_id AS text), CAST(:credits AS numeric), CAST(:by AS text) "
    f"FROM studio_skill_runs r WHERE r.organization_id = {_BOUND_ORG} "
    "AND r.id = CAST(:repair_of AS uuid) AND r.skill_id = CAST(:sid AS uuid) "
    "AND r.kind = 'live' AND NOT r.passed "
    "ON CONFLICT (organization_id, repair_of) WHERE kind = 'proposal' DO NOTHING "
    f"RETURNING {_COLUMNS}"
)

_SKILL_EXISTS_SQL = (
    f"SELECT 1 FROM studio_skills WHERE organization_id = {_BOUND_ORG} "
    "AND id = CAST(:sid AS uuid)"
)

_GET_SQL = (
    f"SELECT {_COLUMNS} FROM studio_skill_runs "
    f"WHERE organization_id = {_BOUND_ORG} AND id = CAST(:id AS uuid)"
)

_PROPOSAL_SQL = (
    f"SELECT {_COLUMNS} FROM studio_skill_runs "
    f"WHERE organization_id = {_BOUND_ORG} AND kind = 'proposal' "
    "AND repair_of = CAST(:repair_of AS uuid)"
)

#: The failed rows of the member, newest first.
_HISTORY_OWN_SQL = (
    f"SELECT {_COLUMNS} FROM studio_skill_runs "
    f"WHERE organization_id = {_BOUND_ORG} AND skill_id = CAST(:sid AS uuid) "
    "AND NOT passed AND created_by = :m "
    "ORDER BY created_at DESC, id DESC LIMIT CAST(:n AS integer)"
)

#: The failed rows of each other member, as a count for each reason only.
_HISTORY_OTHERS_SQL = (
    "SELECT reason, count(*) FROM studio_skill_runs "
    f"WHERE organization_id = {_BOUND_ORG} AND skill_id = CAST(:sid AS uuid) "
    "AND NOT passed AND created_by <> :m "
    "GROUP BY reason ORDER BY reason"
)

#: A failure of the skill with this body key, by any member (D-F12-3).
_REPEAT_SQL = (
    "SELECT EXISTS (SELECT 1 FROM studio_skill_runs "
    f"WHERE organization_id = {_BOUND_ORG} AND skill_id = CAST(:sid AS uuid) "
    "AND body_key = CAST(:body_key AS text) AND NOT passed "
    "AND reason = ANY(CAST(:reasons AS text[])))"
)

_NEWEST_PASSED_LIVE_SQL = (
    f"SELECT {_COLUMNS} FROM studio_skill_runs "
    f"WHERE organization_id = {_BOUND_ORG} AND skill_id = CAST(:sid AS uuid) "
    "AND kind = 'live' AND passed AND created_by = :m "
    f"AND created_at > now() - interval '{WRONG_WINDOW_HOURS} hours' "
    "ORDER BY created_at DESC, id DESC LIMIT 1"
)


class SkillRunError(ValueError):
    """A row that the store refuses. ``code`` names the fault. The text names
    a field, never a value."""

    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code


class UnknownSkill(SkillRunError):
    """No skill of the bound org has the id. A skill of another org gives
    this refusal too, so it tells nothing about another org."""

    def __init__(self) -> None:
        super().__init__("skill", "No skill of this organization has this id.")


class DuplicateFailure(SkillRunError):
    """The run holds a failed live row of this skill already. The unique
    index ``studio_skill_runs_one_failed_live`` allows one."""

    def __init__(self) -> None:
        super().__init__("duplicate_failure", "This run has a failed row for this skill already.")


class AlreadyRepaired(SkillRunError):
    """A proposal for this failed row exists already. One repair for each
    failed run (F12.md check 5)."""

    def __init__(self) -> None:
        super().__init__("already_repaired", "This failed run has a proposal already.")


class NotRepairable(SkillRunError):
    """``repair_of`` names no failed live row of this skill in the bound org."""

    def __init__(self) -> None:
        super().__init__("not_repairable", "repair_of names no failed live row of this skill.")


@dataclass(frozen=True)
class SkillRun:
    """One row of ``studio_skill_runs``."""

    id: str
    skill_id: str
    version: int
    kind: str
    passed: bool
    reason: str
    detail: str
    body_key: str
    input_hash: str | None
    writes: tuple[Mapping[str, Any], ...]
    repair_of: str | None
    test_id: str | None
    run_id: str | None
    thread_id: str | None
    credits: Decimal | None
    created_by: str
    created_at: datetime


@dataclass(frozen=True)
class History:
    """The failures of one skill for one member: the member's own failed
    rows, newest first, and a count for each reason of the other members."""

    rows: tuple[SkillRun, ...]
    others: Mapping[str, int]


# ── The pure rules ─────────────────────────────────────────────────────────


def fold_space(value: str) -> str:
    """*value* with each run of white space as one space, and no white space
    at each end. The ONE white-space rule of F12: the input hash and the
    repair key of F12c both use it."""
    return " ".join(value.split())


def input_hash(org_id: object, input_text: str) -> str:
    """The SHA-256 of the org id, one NUL byte, and *input_text* after
    :func:`fold_space`, in 64 lower-case hex digits (F12.md "The input
    hash"). A change of white space alone gives the same hash."""
    try:
        org = str(uuid.UUID(str(org_id)))
    except (ValueError, TypeError, AttributeError) as exc:
        raise SkillRunError("org", "The org id must be a UUID.") from exc
    if not isinstance(input_text, str):
        raise SkillRunError("input", "The input must be a string.")
    data = org.encode("ascii") + b"\x00" + fold_space(input_text).encode("utf-8", "surrogatepass")
    return hashlib.sha256(data).hexdigest()


def writes_bytes(items: Sequence[Mapping[str, Any]]) -> int:
    """The length of *items* in bytes, as the jsonb text of Postgres. jsonb
    writes ``", "`` and ``": "``, and each name is ASCII, so the two texts
    have one length."""
    return len(json.dumps(list(items), separators=(", ", ": "), ensure_ascii=False).encode("utf-8"))


def check_writes(writes: object) -> list[dict[str, Any]]:
    """The ``writes`` of a row, checked: at most :data:`MAX_WRITES` items
    ``{"action": <name>, "keys": [<name>, ...]}``, and at most
    :data:`MAX_WRITES_BYTES` bytes. Each key list comes back sorted, with no
    repeat. An item with any other key, or a key list with a value in it,
    is refused, because a row holds names only."""
    if isinstance(writes, str | bytes | Mapping) or not isinstance(writes, Iterable):
        raise SkillRunError("writes", "writes must be a list.")
    items = list(writes)
    if len(items) > MAX_WRITES:
        raise SkillRunError("writes", f"writes holds at most {MAX_WRITES} items.")
    out: list[dict[str, Any]] = []
    for n, item in enumerate(items):
        if not isinstance(item, Mapping) or set(item) != {"action", "keys"}:
            raise SkillRunError(
                "writes_value", f"writes[{n}] must hold only an action and its key names.")
        action, keys = item["action"], item["keys"]
        if not isinstance(action, str) or not _ACTION.fullmatch(action):
            raise SkillRunError("writes_value", f"writes[{n}].action must be an action name.")
        if isinstance(keys, str | bytes | Mapping) or not isinstance(keys, Iterable):
            raise SkillRunError("writes_value", f"writes[{n}].keys must be a list of key names.")
        names = list(keys)
        if len(names) > MAX_KEYS:
            raise SkillRunError("writes", f"writes[{n}].keys holds at most {MAX_KEYS} names.")
        for key in names:
            if not isinstance(key, str) or not _KEY.fullmatch(key):
                raise SkillRunError(
                    "writes_value", f"writes[{n}].keys must hold key names only.")
        out.append({"action": action, "keys": sorted(set(names))})
    if writes_bytes(out) > MAX_WRITES_BYTES:
        raise SkillRunError("writes", f"writes holds at most {MAX_WRITES_BYTES} bytes.")
    return out


def _plain(value: object, field: str, *, limit: int, required: bool) -> str | None:
    """A short text column: no control character and no lone surrogate."""
    if value is None:
        if required:
            raise SkillRunError(field, f"{field} is required.")
        return None
    if not isinstance(value, str):
        raise SkillRunError(field, f"{field} must be a string.")
    if required and not value.strip():
        raise SkillRunError(field, f"{field} must not be empty.")
    if len(value) > limit:
        raise SkillRunError(field, f"{field} holds at most {limit} characters.")
    if any(ord(c) < 0x20 or ord(c) == 0x7F or 0xD800 <= ord(c) <= 0xDFFF for c in value):
        raise SkillRunError(field, f"{field} holds a control character.")
    return value


def _member(member: object) -> str:
    if not isinstance(member, str) or not member.strip():
        raise SkillRunError("member", "member must be a non-empty string.")
    m = member.strip().lower()
    _plain(m, "member", limit=320, required=True)
    return m


def _uuid(value: object, field: str) -> str:
    try:
        return str(uuid.UUID(str(value)))
    except (ValueError, TypeError, AttributeError) as exc:
        raise SkillRunError(field, f"{field} must be a UUID.") from exc


def _version(version: object) -> int:
    if isinstance(version, bool) or not isinstance(version, int) or not 1 <= version < 2**31:
        raise SkillRunError("version", "version must be a whole number of 1 or more.")
    return version


def _reason(kind: str, reason: object, passed: object) -> str:
    if not isinstance(passed, bool):
        raise SkillRunError("passed", "passed must be true or false.")
    if not isinstance(reason, str) or not _REASON.fullmatch(reason):
        raise SkillRunError("reason", "reason must be a code.")
    if reason not in REASONS[kind]:
        raise SkillRunError("reason", f"reason is no code of a {kind} row.")
    if passed != (reason == "passed"):
        raise SkillRunError("reason", "A row passed exactly when its reason is passed.")
    return reason


def _body_key(body_key: object) -> str:
    if not isinstance(body_key, str) or not _HEX64.fullmatch(body_key):
        raise SkillRunError("body_key", "body_key must be 64 lower-case hex digits.")
    return body_key


def _credits(credits: object) -> str | None:
    if credits is None:
        return None
    if isinstance(credits, bool) or not isinstance(credits, int | Decimal):
        raise SkillRunError("credits", "credits must be a Decimal or a whole number.")
    try:
        value = Decimal(credits)
        good = value.is_finite() and Decimal(0) <= value < _LIMIT_CREDITS
    except InvalidOperation:
        good = False
    if not good:
        raise SkillRunError("credits", "credits must be 0 or more, and finite.")
    return str(value)


def _row(row: Any) -> SkillRun:
    writes = row[9]
    if isinstance(writes, str | bytes):
        writes = json.loads(writes)
    credits = row[14]
    return SkillRun(
        id=str(row[0]), skill_id=str(row[1]), version=int(row[2]), kind=str(row[3]),
        passed=bool(row[4]), reason=str(row[5]), detail=str(row[6]), body_key=str(row[7]),
        input_hash=None if row[8] is None else str(row[8]),
        writes=tuple(dict(w) for w in writes or ()),
        repair_of=None if row[10] is None else str(row[10]),
        test_id=None if row[11] is None else str(row[11]),
        run_id=None if row[12] is None else str(row[12]),
        thread_id=None if row[13] is None else str(row[13]),
        credits=None if credits is None else Decimal(str(credits)),
        created_by=str(row[15]), created_at=row[16])


# ── The bound org ──────────────────────────────────────────────────────────


async def _bound_org(db: Any) -> str:
    org = (await db.execute(text(_BOUND_ORG_SQL))).scalar()
    if not org:
        raise SkillRunError("org", "The session has no bound tenant.")
    return str(org)


async def bound_input_hash(db: Any, input_text: str) -> str:
    """:func:`input_hash` of *input_text* with the org of the bound session.
    The org never comes from the caller."""
    if not isinstance(input_text, str):
        raise SkillRunError("input", "The input must be a string.")
    return input_hash(await _bound_org(db), input_text)


# ── The writers ────────────────────────────────────────────────────────────


async def _insert(db: Any, sid: str, params: dict[str, Any]) -> SkillRun | None:
    row = (await db.execute(text(_INSERT_SQL), {"sid": sid, **params})).first()
    if row is not None:
        return _row(row)
    if (await db.execute(text(_SKILL_EXISTS_SQL), {"sid": sid})).first() is None:
        raise UnknownSkill()
    return None


async def record_live(db: Any, member: str, skill_id: str, *, version: int,
                      passed: bool, reason: str, body_key: str, input_text: str,
                      run_id: str, thread_id: str | None = None, detail: str = "",
                      writes: Iterable[Mapping[str, Any]] = ()) -> SkillRun:
    """Write one ``live`` row: one chat turn that loaded the skill (F12b).

    *input_text* is the words of the turn. The row keeps only
    :func:`input_hash` of them, with the org of the bound session. *run_id*
    is required, so the unique index holds one failed row for each run.

    Raises :class:`SkillRunError` on a bad shape, before any statement.
    Raises :class:`UnknownSkill` when no skill of the bound org has the id,
    and :class:`DuplicateFailure` when the run has a failed row of the skill
    already. Both write no row.
    """
    m = _member(member)
    sid = _uuid(skill_id, "skill_id")
    params = {
        "version": _version(version), "kind": "live", "passed": passed,
        "reason": _reason("live", reason, passed),
        "detail": _plain(detail, "detail", limit=MAX_DETAIL_CHARS, required=False) or "",
        "body_key": _body_key(body_key),
        "writes": json.dumps(check_writes(writes)), "test_id": None,
        "run_id": _plain(run_id, "run_id", limit=MAX_POINTER, required=True),
        "thread_id": _plain(thread_id, "thread_id", limit=MAX_POINTER, required=False),
        "credits": None, "by": m,
    }
    if not isinstance(input_text, str) or not fold_space(input_text):
        raise SkillRunError("input", "A live row needs the input of the turn.")
    params["input_hash"] = await bound_input_hash(db, input_text)
    made = await _insert(db, sid, params)
    if made is None:
        raise DuplicateFailure()
    return made


async def record_test(db: Any, member: str, skill_id: str, *, version: int,
                      reason: str, body_key: str, test_id: str, detail: str = "",
                      writes: Iterable[Mapping[str, Any]] = (),
                      run_id: str | None = None,
                      credits: Decimal | int | None = None) -> SkillRun:
    """Write one ``test`` row: one F4 test that failed with a fault of the
    skill (F12b). *reason* is one of :data:`SKILL_FAULTS`, so the row never
    passed. A test row has no input hash: its input is the test's own.

    Raises :class:`SkillRunError` on a bad shape, before any statement, and
    :class:`UnknownSkill` when no skill of the bound org has the id.
    """
    m = _member(member)
    sid = _uuid(skill_id, "skill_id")
    params = {
        "version": _version(version), "kind": "test", "passed": False,
        "reason": _reason("test", reason, False),
        "detail": _plain(detail, "detail", limit=MAX_DETAIL_CHARS, required=False) or "",
        "body_key": _body_key(body_key), "input_hash": None,
        "writes": json.dumps(check_writes(writes)),
        "test_id": _plain(test_id, "test_id", limit=MAX_TEST_ID, required=True),
        "run_id": _plain(run_id, "run_id", limit=MAX_POINTER, required=False),
        "thread_id": None, "credits": _credits(credits), "by": m,
    }
    made = await _insert(db, sid, params)
    if made is None:  # pragma: no cover - only a failed live row can collide
        raise SkillRunError("insert", "The test row was not written.")
    return made


async def record_proposal(db: Any, member: str, skill_id: str, repair_of: str, *,
                          passed: bool, reason: str, body_key: str, detail: str = "",
                          writes: Iterable[Mapping[str, Any]] = (),
                          credits: Decimal | int | None = None,
                          run_id: str | None = None,
                          thread_id: str | None = None) -> SkillRun:
    """Write one ``proposal`` row: the result of one repair (F12c).

    *repair_of* is the id of a failed ``live`` row of the skill. The row
    takes its version (the base version) and its input hash from that row.
    *writes* names the held writes of the input run.

    Raises :class:`SkillRunError` on a bad shape, before any statement.
    Raises :class:`AlreadyRepaired` when a proposal for the failed row
    exists, and :class:`NotRepairable` when *repair_of* names no failed live
    row of this skill in the bound org. Both write no row.
    """
    params = {
        "by": _member(member), "sid": _uuid(skill_id, "skill_id"),
        "repair_of": _uuid(repair_of, "repair_of"), "passed": passed,
        "reason": _reason("proposal", reason, passed),
        "detail": _plain(detail, "detail", limit=MAX_DETAIL_CHARS, required=False) or "",
        "body_key": _body_key(body_key),
        "writes": json.dumps(check_writes(writes)), "credits": _credits(credits),
        "run_id": _plain(run_id, "run_id", limit=MAX_POINTER, required=False),
        "thread_id": _plain(thread_id, "thread_id", limit=MAX_POINTER, required=False),
    }
    row = (await db.execute(text(_INSERT_PROPOSAL_SQL), params)).first()
    if row is not None:
        return _row(row)
    if await proposal_for(db, params["repair_of"]) is not None:
        raise AlreadyRepaired()
    raise NotRepairable()


# ── The readers ────────────────────────────────────────────────────────────


async def get_run(db: Any, run_row_id: str) -> SkillRun | None:
    """The row *run_row_id* of the bound org, or None."""
    row = (await db.execute(text(_GET_SQL), {"id": _uuid(run_row_id, "id")})).first()
    return None if row is None else _row(row)


async def proposal_for(db: Any, repair_of: str) -> SkillRun | None:
    """The proposal that repairs the failed row *repair_of*, or None."""
    row = (await db.execute(text(_PROPOSAL_SQL), {
        "repair_of": _uuid(repair_of, "repair_of")})).first()
    return None if row is None else _row(row)


async def history(db: Any, member: str, skill_id: str, *,
                  limit: int = HISTORY_ROWS) -> History:
    """The failures of the skill for *member* (F12.md "The history read").

    ``rows``: the failed rows of *member*, of each kind, newest first, at
    most *limit* and never more than :data:`HISTORY_ROWS`. ``others``: a
    count for each reason of the failed rows of the other members, and
    nothing more of them. No row holds an input or a value.
    """
    m = _member(member)
    sid = _uuid(skill_id, "skill_id")
    if isinstance(limit, bool) or not isinstance(limit, int) or limit < 1:
        raise SkillRunError("limit", "limit must be a whole number of 1 or more.")
    rows = (await db.execute(text(_HISTORY_OWN_SQL), {
        "sid": sid, "m": m, "n": min(limit, HISTORY_ROWS)})).all()
    counts = (await db.execute(text(_HISTORY_OTHERS_SQL), {"sid": sid, "m": m})).all()
    return History(rows=tuple(_row(r) for r in rows),
                   others=MappingProxyType({str(r[0]): int(r[1]) for r in counts}))


async def repeats_a_failed_body(db: Any, skill_id: str, body_key: str) -> bool:
    """True when a row of the skill, by any member, failed with a fault of
    the skill and ran the body with *body_key* (F12.md check 8). Only
    :data:`REPEAT_REASONS` count."""
    found = (await db.execute(text(_REPEAT_SQL), {
        "sid": _uuid(skill_id, "skill_id"), "body_key": _body_key(body_key),
        "reasons": sorted(REPEAT_REASONS)})).scalar()
    return bool(found)


async def newest_passed_live(db: Any, member: str, skill_id: str) -> SkillRun | None:
    """The newest ``live`` row of the skill by *member* that passed, in the
    last :data:`WRONG_WINDOW_HOURS` hours, or None (F12.md "It was wrong")."""
    row = (await db.execute(text(_NEWEST_PASSED_LIVE_SQL), {
        "sid": _uuid(skill_id, "skill_id"), "m": _member(member)})).first()
    return None if row is None else _row(row)
