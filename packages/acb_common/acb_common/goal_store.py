"""The one writer of ``goal_records``, the goal record of a long job (WS-WEB WEB-10).

Spec: ``project-docs/specs/web_research.md`` §6 and the §11 WEB-10 row.
Table: migration ``goal_records`` (324 at build). ``tests/unit/test_goal_record_r8.py``
runs this SQL on a real Postgres.

**Why here.** The research job of WEB-11 runs in the orchestrator, and the
orchestrator cannot import gateway code. ``acb_common`` is the package that
both services import, so the store lives here. It opens no connection and
builds no engine.

**Tenancy (R5).** Every function takes an async session that the caller
opened with ``acb_common.db.tenant_session(org)``, so RLS binds each
statement. No function takes an org. An INSERT writes no
``organization_id``: the column DEFAULT reads the bound tenant, and each
statement filters on ``_BOUND_ORG`` too.

**The member.** Every function takes the member as a required argument, and
every statement filters on it. A goal is private to its member. ⚠️ The store
does not authenticate. It only scopes. The caller resolves the member from a
trusted source, never from request input (R5, contract R11). A goal of
another member or of another org reads as None.

**Compare-and-set.** A goal has a ``version``, 1 at the INSERT. Each write
names the version that the caller read (``expected_version``) and raises it by
one, in one ``UPDATE ... WHERE version = :expected``. When another write came
first, the store raises :class:`StaleGoal` and changes nothing. The caller
reads the goal again and decides again. The trigger ``goal_records_guard``
refuses an UPDATE that does not raise the version by exactly one, so a
second writer cannot break the token either.

**The objective never changes.** :func:`update_goal` refuses an
``objective``, and the trigger refuses a change of the objective, the member,
the kind, the id, the org and ``created_at``.

**The caps.** A goal has a round cap (:data:`DEFAULT_ROUND_CAP` for
research) and a cost cap in AI credits. :func:`cap_block` is the one cap
rule: a goal reaches its cap when ``round_count >= round_cap`` or
``cost_used_credits >= cost_cap_credits``. Each write of the store applies
it. When a write reaches a cap on an active goal, the same write moves the
goal to ``blocked`` with ``cap_reached``. The CHECK
``goal_records_active_under_caps`` holds the same rule on the database, so an
active goal is always under both caps.

**A round.** :func:`record_round` adds one round and its cost to an active
goal. A caller starts a round only on an active goal, and records the round
when it ends. The cost of a round is known only at its end, so the cost cap
stops the NEXT round, and the last round can go past the cap. A blocked or
final goal takes no round: the store raises :class:`GoalStateError`.

**Block codes.** :data:`BLOCK_CODES` is the closed set, with one sentence
for the member for each code. The CHECK ``goal_records_block_code`` holds the
same set. A goal is ``blocked`` if and only if it has a code.

**States.** ``active`` and ``blocked`` are open. ``done``, ``failed`` and
``cancelled`` are final (:data:`FINAL_STATES`). A final goal never changes
again, and its ``finished_at`` is set.

**Permission to continue is not durable** (web_research.md §6). The store
holds no permission, consent or approval. A restart asks the member again
before a new round. The R8 suite pins the column list.

**Text.** The objective goes through :func:`acb_common.pg_text.storable`,
so a NUL or a lone surrogate becomes U+FFFD and the statement does not fail.
Each refusal names the field, never the value.

**Errors.** A refusal before the database raises :class:`GoalStoreError`. A
read or a write of a goal that the member cannot see answers None. A stale
version raises :class:`StaleGoal`. A write that the state does not allow
raises :class:`GoalStateError`. Both are a :class:`GoalStoreError`.
"""

from __future__ import annotations

import math
import re
import uuid
from dataclasses import dataclass
from datetime import datetime
from decimal import ROUND_HALF_UP, Decimal
from typing import Any

from sqlalchemy import text

from acb_common.pg_text import storable

#: The five states, as the CHECK ``goal_records_state`` holds them.
STATES: tuple[str, ...] = ("active", "blocked", "done", "failed", "cancelled")
#: The states that end a goal. A final goal never changes again.
FINAL_STATES: tuple[str, ...] = ("done", "failed", "cancelled")
#: The closed set of block codes, as the CHECK ``goal_records_block_code``
#: holds them, with one sentence for the member for each.
BLOCK_CODES: dict[str, str] = {
    "cap_reached": "This job used all the rounds or credits that it can use, so it stopped.",
    "needs_member": "This job needs an answer from you before it can go on.",
    "source_unreachable": "This job cannot open a source that it needs, so it stopped.",
}
#: The round cap of a research goal (web_research.md §6).
DEFAULT_ROUND_CAP = 40
#: The bounds of the CHECK ``goal_records_round_cap``.
MAX_ROUND_CAP = 10000
#: The bound of the CHECK ``goal_records_cost_cap``, and of the cost of one
#: round. An active goal is under its cap, so a round adds at most this much,
#: and the sum stays inside NUMERIC(14, 4).
MAX_CREDITS = Decimal("1000000000")
#: The bound of the CHECK ``goal_records_objective``.
MAX_OBJECTIVE_CHARS = 2000
#: The bound of :func:`list_goals`.
MAX_LIST = 200
#: The kind shape, as the CHECK ``goal_records_kind`` holds it.
_KIND_RE = re.compile(r"[a-z][a-z0-9_.]{0,63}")
#: The scale of NUMERIC(14, 4). Postgres rounds a half away from zero, and so
#: does ROUND_HALF_UP.
_CREDIT_STEP = Decimal("0.0001")
#: The fields that :func:`update_goal` may change.
_CHANGEABLE: frozenset[str] = frozenset(
    {"state", "block_code", "round_cap", "cost_cap_credits", "job_id"})

_BOUND_ORG = "CAST(NULLIF(current_setting('app.tenant_id', true), '') AS uuid)"

_COLUMNS = (
    "id::text, member_email, kind, objective, state, version, round_count, "
    "round_cap, cost_used_credits, cost_cap_credits, block_code, job_id::text, "
    "created_at, updated_at, finished_at"
)

_WHERE_GOAL = (
    f"WHERE organization_id = {_BOUND_ORG} AND member_email = :m "
    "AND id = CAST(:id AS uuid)"
)

_CREATE_SQL = (
    "INSERT INTO goal_records (member_email, kind, objective, round_cap, "
    "cost_cap_credits, job_id) "
    "VALUES (:m, :kind, :objective, :round_cap, :cost_cap, CAST(:job AS uuid)) "
    f"RETURNING {_COLUMNS}"
)

_GET_SQL = f"SELECT {_COLUMNS} FROM goal_records {_WHERE_GOAL}"

_LIST_SQL = (
    f"SELECT {_COLUMNS} FROM goal_records "
    f"WHERE organization_id = {_BOUND_ORG} AND member_email = :m "
    "ORDER BY updated_at DESC, id LIMIT :limit"
)

#: The compare-and-set write. The values come from :func:`_after`, which
#: read the row at ``:v``. When another write came first, the version is not
#: ``:v`` any more, and no row changes.
_UPDATE_SQL = (
    "UPDATE goal_records SET state = :state, block_code = :block, "
    "round_cap = :round_cap, cost_cap_credits = :cost_cap, "
    "job_id = CAST(:job AS uuid), "
    "finished_at = CASE WHEN :final THEN now() ELSE NULL END, "
    "version = version + 1, updated_at = now() "
    f"{_WHERE_GOAL} AND version = :v "
    f"RETURNING {_COLUMNS}"
)

#: One round, with the same compare-and-set.
_ROUND_SQL = (
    "UPDATE goal_records SET round_count = :rounds, cost_used_credits = :used, "
    "state = :state, block_code = :block, "
    "version = version + 1, updated_at = now() "
    f"{_WHERE_GOAL} AND version = :v AND state = 'active' "
    f"RETURNING {_COLUMNS}"
)


class GoalStoreError(ValueError):
    """A write or a read that the store refuses."""


class StaleGoal(GoalStoreError):
    """The goal changed after the caller read it. Nothing changed.

    ``current_version`` is the version that the store read, or None when the
    store could not read one. The caller reads the goal again and decides
    again.
    """

    def __init__(self, current_version: int | None) -> None:
        super().__init__("The goal changed after you read it. Read it again.")
        self.current_version = current_version


class GoalStateError(GoalStoreError):
    """The state of the goal does not allow the write. Nothing changed."""


@dataclass(frozen=True)
class Goal:
    """One row of ``goal_records``."""

    id: str
    member_email: str
    kind: str
    objective: str
    state: str
    version: int
    round_count: int
    round_cap: int
    cost_used_credits: Decimal
    cost_cap_credits: Decimal
    block_code: str | None
    job_id: str | None
    created_at: datetime
    updated_at: datetime
    finished_at: datetime | None

    @property
    def block_message(self) -> str | None:
        """The sentence for the member of the block code, or None."""
        return None if self.block_code is None else BLOCK_CODES[self.block_code]


def cap_block(round_count: int, round_cap: int,
              cost_used: Decimal, cost_cap: Decimal) -> str | None:
    """The one cap rule. Answers ``cap_reached`` when a goal with these
    counts has reached its round cap or its cost cap, else None."""
    if round_count >= round_cap or cost_used >= cost_cap:
        return "cap_reached"
    return None


def _member(member: object) -> str:
    """The member as the CHECK ``goal_records_member_email`` holds it."""
    if not isinstance(member, str) or not member.strip():
        raise GoalStoreError("member must be a non-empty string")
    return member.strip().lower()


def _uuid(value: object, field: str) -> str:
    """A UUID. A bad one fails here, not in a statement that would end the
    caller's transaction."""
    try:
        return str(uuid.UUID(str(value)))
    except (ValueError, TypeError, AttributeError) as exc:
        raise GoalStoreError(f"{field} must be a UUID") from exc


def _job_id(value: object) -> str | None:
    return None if value is None else _uuid(value, "job_id")


def _version(value: object) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value < 1:
        raise GoalStoreError("expected_version must be a whole number of 1 or more")
    return value


def _round_cap(value: object) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or not (
            1 <= value <= MAX_ROUND_CAP):
        raise GoalStoreError(
            f"round_cap must be a whole number from 1 to {MAX_ROUND_CAP}")
    return value


def _credits(value: object, field: str, *, allow_zero: bool) -> Decimal:
    """A number of credits as NUMERIC(14, 4) stores it.

    It refuses a bool, NaN, an infinity, a negative number and a number over
    :data:`MAX_CREDITS`. A cap must be more than 0 after the rounding, and a
    cost may be 0. The value is rounded to 4 places here, so the store and
    the database hold the same number.
    """
    if isinstance(value, bool) or not isinstance(value, int | float | Decimal):
        raise GoalStoreError(f"{field} must be a number")
    if isinstance(value, float):
        if not math.isfinite(value):
            raise GoalStoreError(f"{field} must be a finite number")
        amount = Decimal(repr(value))
    elif isinstance(value, int):
        amount = Decimal(value)
    else:
        if not value.is_finite():
            raise GoalStoreError(f"{field} must be a finite number")
        amount = value
    if amount < 0 or amount > MAX_CREDITS:
        raise GoalStoreError(f"{field} must be from 0 to {MAX_CREDITS} credits")
    amount = amount.quantize(_CREDIT_STEP, rounding=ROUND_HALF_UP)
    if not allow_zero and amount == 0:
        raise GoalStoreError(f"{field} must be more than 0 credits")
    return amount


def _objective(value: object) -> str:
    """The objective as the CHECK ``goal_records_objective`` takes it."""
    if not isinstance(value, str):
        raise GoalStoreError("objective must be a string")
    clean = str(storable(value)).strip()
    if not clean:
        raise GoalStoreError("objective must not be empty")
    if len(clean) > MAX_OBJECTIVE_CHARS:
        raise GoalStoreError(f"objective holds at most {MAX_OBJECTIVE_CHARS} characters")
    return clean


def _kind(value: object) -> str:
    # fullmatch, because ``$`` of ``match`` also matches before a final newline.
    if not isinstance(value, str) or not _KIND_RE.fullmatch(value):
        raise GoalStoreError(
            "kind must be lower-case letters, digits, '_' and '.', "
            "with a letter first, at most 64 characters")
    return value


def _goal(row: Any) -> Goal:
    return Goal(
        id=str(row[0]), member_email=str(row[1]), kind=str(row[2]),
        objective=str(row[3]), state=str(row[4]), version=int(row[5]),
        round_count=int(row[6]), round_cap=int(row[7]),
        cost_used_credits=Decimal(row[8]), cost_cap_credits=Decimal(row[9]),
        block_code=None if row[10] is None else str(row[10]),
        job_id=None if row[11] is None else str(row[11]),
        created_at=row[12], updated_at=row[13], finished_at=row[14])


def _check_changes(changes: dict[str, Any]) -> dict[str, Any]:
    """The checked values of an :func:`update_goal` call. Refuses before
    the database."""
    if "objective" in changes:
        raise GoalStoreError("objective cannot change after the goal is made")
    unknown = sorted(set(changes) - _CHANGEABLE)
    if unknown:
        raise GoalStoreError(f"these fields cannot change: {', '.join(unknown)}")
    if not changes:
        raise GoalStoreError("give at least one field to change")
    out: dict[str, Any] = {}
    if "state" in changes:
        if changes["state"] not in STATES:
            raise GoalStoreError(f"state must be one of {STATES}")
        out["state"] = changes["state"]
    if "block_code" in changes:
        code = changes["block_code"]
        if code is not None and code not in BLOCK_CODES:
            raise GoalStoreError(f"block_code must be one of {tuple(BLOCK_CODES)}")
        if out.get("state") != "blocked":
            raise GoalStoreError("block_code goes with the state 'blocked' only")
        out["block_code"] = code
    if out.get("state") == "blocked" and out.get("block_code") is None:
        raise GoalStoreError("the state 'blocked' needs a block_code")
    if "round_cap" in changes:
        out["round_cap"] = _round_cap(changes["round_cap"])
    if "cost_cap_credits" in changes:
        out["cost_cap_credits"] = _credits(
            changes["cost_cap_credits"], "cost_cap_credits", allow_zero=False)
    if "job_id" in changes:
        out["job_id"] = _job_id(changes["job_id"])
    return out


def _after(goal: Goal, changes: dict[str, Any]) -> dict[str, Any]:
    """The row after an :func:`update_goal` with checked *changes*.

    The cap rule binds here too: a goal that would be active at or over a
    cap is ``blocked`` with ``cap_reached``.
    """
    state = changes.get("state", goal.state)
    block = changes.get("block_code") if "state" in changes else goal.block_code
    round_cap = changes.get("round_cap", goal.round_cap)
    cost_cap = changes.get("cost_cap_credits", goal.cost_cap_credits)
    if state == "active":
        block = cap_block(goal.round_count, round_cap, goal.cost_used_credits, cost_cap)
        if block is not None:
            state = "blocked"
    return {
        "state": state, "block": block, "round_cap": round_cap,
        "cost_cap": cost_cap, "job": changes.get("job_id", goal.job_id),
        "final": state in FINAL_STATES,
    }


async def create_goal(db: Any, member: str, *, objective: str,
                      cost_cap_credits: int | float | Decimal,
                      round_cap: int = DEFAULT_ROUND_CAP, kind: str = "research",
                      job_id: str | None = None) -> Goal:
    """Make an active goal of *member*, at version 1, with no round and no cost.

    The objective holds at most :data:`MAX_OBJECTIVE_CHARS` characters, and it
    never changes after this call. The caps are a whole number of rounds and
    a number of credits, both more than 0. Each refusal raises
    :class:`GoalStoreError` before the database.
    """
    params = {
        "m": _member(member), "kind": _kind(kind), "objective": _objective(objective),
        "round_cap": _round_cap(round_cap),
        "cost_cap": _credits(cost_cap_credits, "cost_cap_credits", allow_zero=False),
        "job": _job_id(job_id),
    }
    row = (await db.execute(text(_CREATE_SQL), params)).one()
    return _goal(row)


async def get_goal(db: Any, member: str, goal_id: str) -> Goal | None:
    """The goal *goal_id* of *member*, or None. A goal of another member or
    of another org answers None."""
    params = {"m": _member(member), "id": _uuid(goal_id, "goal_id")}
    row = (await db.execute(text(_GET_SQL), params)).first()
    return None if row is None else _goal(row)


async def list_goals(db: Any, member: str, *, limit: int = 50) -> list[Goal]:
    """The goals of *member* in the bound org, newest change first, at most
    *limit* (1 to :data:`MAX_LIST`)."""
    if isinstance(limit, bool) or not isinstance(limit, int) or not (
            1 <= limit <= MAX_LIST):
        raise GoalStoreError(f"limit must be a whole number from 1 to {MAX_LIST}")
    params = {"m": _member(member), "limit": limit}
    rows = (await db.execute(text(_LIST_SQL), params)).all()
    return [_goal(r) for r in rows]


async def _read_for_write(db: Any, m: str, gid: str, expected: int) -> Goal | None:
    """The goal before a write. None when the member cannot see it. Raises
    :class:`StaleGoal` when its version is not *expected*."""
    goal = await get_goal(db, m, gid)
    if goal is not None and goal.version != expected:
        raise StaleGoal(goal.version)
    return goal


async def update_goal(db: Any, member: str, goal_id: str, expected_version: int,
                      **changes: Any) -> Goal | None:
    """Change the goal *goal_id* of *member*, if it is still at *expected_version*.

    *changes* may name ``state``, ``block_code``, ``round_cap``,
    ``cost_cap_credits`` and ``job_id``. ``objective`` and every other field
    are refused. The state ``blocked`` needs a ``block_code``, and a code
    goes with ``blocked`` only. Another state clears the code. When the goal
    would be active at or over a cap, it is ``blocked`` with ``cap_reached``.
    A final state sets ``finished_at``.

    Answers the new goal, or None when the member cannot see the goal. Raises
    :class:`StaleGoal` when the goal is not at *expected_version*, and
    :class:`GoalStateError` when the goal is final. Then nothing changes.
    """
    m = _member(member)
    gid = _uuid(goal_id, "goal_id")
    expected = _version(expected_version)
    checked = _check_changes(changes)
    goal = await _read_for_write(db, m, gid, expected)
    if goal is None:
        return None
    if goal.state in FINAL_STATES:
        raise GoalStateError("This goal ended, so it cannot change.")
    after = _after(goal, checked)
    row = (await db.execute(text(_UPDATE_SQL), {
        "m": m, "id": gid, "v": expected, **after})).first()
    if row is None:
        # Another write came between the read and this statement.
        raise StaleGoal(None)
    return _goal(row)


async def record_round(db: Any, member: str, goal_id: str, expected_version: int,
                       cost_credits: int | float | Decimal) -> Goal | None:
    """Add one round and its cost to the active goal *goal_id* of *member*.

    When the round reaches the round cap or the cost cap, the same write
    moves the goal to ``blocked`` with ``cap_reached``. The caller reads
    ``block_code`` of the goal that this call answers, and starts no round
    on a goal that is not active.

    Answers the new goal, or None when the member cannot see the goal. Raises
    :class:`StaleGoal` when the goal is not at *expected_version*, and
    :class:`GoalStateError` when the goal is not active. Then nothing changes.
    """
    m = _member(member)
    gid = _uuid(goal_id, "goal_id")
    expected = _version(expected_version)
    cost = _credits(cost_credits, "cost_credits", allow_zero=True)
    goal = await _read_for_write(db, m, gid, expected)
    if goal is None:
        return None
    if goal.state != "active":
        raise GoalStateError("This goal is not active, so it takes no round.")
    rounds = goal.round_count + 1
    used = goal.cost_used_credits + cost
    block = cap_block(rounds, goal.round_cap, used, goal.cost_cap_credits)
    row = (await db.execute(text(_ROUND_SQL), {
        "m": m, "id": gid, "v": expected, "rounds": rounds, "used": used,
        "state": "active" if block is None else "blocked", "block": block})).first()
    if row is None:
        raise StaleGoal(None)
    return _goal(row)
