"""The skill routes of AI Studio (STU-F2a, ``ai_studio.md`` §7 and §14 F2a).

Eight routes over the F1 store, ``gateway/studio/skills_store.py``. They
register on the router of ``actions.py``, as ``workflows/copilot.py`` does on
the router of ``workflows/core.py``. So ``main.py`` mounts no second router,
and the gate ``require_feature_router("studio")`` (J1) and the prefix
``/studio`` hold here too.

**The switch.** The first line of each route reads ``STUDIO_ORGS``. An org
outside it gets 404, with the body of a missing skill.

**Tenancy and the member (R5).** The org is the bound session
(``_tenant_session()``), and the member is ``user.email``. No route reads an
org or a member from the path, the query or the body. A key such as
``organization_id`` or ``owner_email`` in a body changes nothing.

**One answer for a skill that is not yours.** A skill of another member or
of another org answers 404 ``Not found``, as a skill that does not exist
does. An answer names no owner.

**The body checks.** Each write of a body runs three checks, in this order,
and each refusal is 422:

1. The depth cap :data:`MAX_DEPTH` (the cap of A2). The walk is iterative.
2. ``check_body`` of the store.
3. The catalog check: each action is a catalog ``id``, once, and a skill
   holds at most :data:`MAX_ACTIONS` (the WS-67 pack cap). Class B and C
   actions are legal in a draft.

All three run BEFORE the store session opens (the undo runs them inside its
session, after the row lock, because it must check the version it just read). The catalog read opens its own
session (``actions.catalog_rows``), so a request never holds two.

**The states.** :data:`TRANSITIONS` holds each legal move. A move to the
same state is a no-op. A publish needs a version, and it runs the catalog
check again on the newest version. Then it runs the hidden-character check
(WS-STU F1b, ``acb_skills.hidden_text``) on the newest body, then on the name
and the description columns. So a row that a store before F1b kept cannot
publish or resume. A pause is not gated, so a member can always stop a live
skill. No route renames a skill, so a skill with a hidden character in its
name cannot go live again: the member makes a new skill. A new version of a
published skill
returns the skill to ``draft`` in the same transaction. No route of F2a
changes the visibility.

**The share with the org (WS-STU J2c, ``ai_studio_rows/J2c.md``).** Five
routes over the share functions of the store. Each reads ``STUDIO_ORGS``
first (404, no session). ``PACKAGE_HOST_ORGS`` does not apply, because a
skill share needs no install. The owner asks with ``POST
/skills/{id}/versions/{n}/share`` (202 ``pending``). A second ask of the same
version writes nothing. Another member, another org and no such version get
404. A holder of ``studio:publish`` reads ``GET /skill-shares?state=pending``
(each other caller gets 403, before any session). A holder approves or
rejects one version with ``POST .../share/approve`` or ``.../share/reject``.
The handler passes ``user.has_permission(SHARE_RIGHT)`` to the store as
``can_publish``, and the store refuses the rest (403). The owner cannot
approve (403). A version that nobody asked for, another org and no such
skill get 404. A decided ask is 409 ``share_conflict``. ``DELETE
/skills/{id}/share`` unshares: the owner always, another member only with the
right. No route takes a visibility from a request, so no route can set
``group`` or ``system``. The chat tool that asks (TOF-4b) and the page are
later slices.

**Two moves at once.** A move reads the skill ``FOR UPDATE``. So a second
move waits for the first to commit, then reads the new state, and
:data:`TRANSITIONS` decides on that state.

**The test run (TOF-3b F4b).** ``POST /studio/skills/{id}/tests/run`` runs
the tests of a skill on real turns, each in a dry run
(``gateway.studio.skill_tests.run_suite``). Each test spends credits. The
order is fixed: the switch ``STUDIO_ORGS`` and the switch
``STUDIO_SKILL_TESTS_ORGS`` (404 before any other work), the request
checks, the owner check, then the credit cap. With no cap set, the route
answers 409 ``cap_unset`` and runs nothing. A second request of a member
whose suite runs answers 429 ``suite_running`` and runs nothing. The answer
holds codes and action names only, never an argument of a call.
"""

from __future__ import annotations

import contextlib
import re
import uuid
from collections.abc import Iterator, Sequence
from decimal import Decimal
from typing import Any

from acb_auth import UserContext, get_current_user
from acb_common import get_logger
from acb_common.studio_manifest import MAX_DEPTH
from acb_skills.hidden_text import body_hidden_problem, hidden_problem
from acb_skills.skill_slug import slug_from_name  # the one shared slug (STU-F8a)
from acb_skills.tool_packs import MAX_PACK_TOOLS
from fastapi import Body, Depends, HTTPException, Query
from gateway.db import tenant_session as _tenant_session
from gateway.routes.studio import actions, flags
from gateway.routes.studio.actions import NOT_FOUND, router
from gateway.studio import skill_tests
from gateway.routes.studio.packages import SHARE_RIGHT
from gateway.studio import skills_store as store
from pydantic import BaseModel, StrictInt, ValidationError

__all__ = ["MAX_ACTIONS", "TRANSITIONS", "router", "slug_from_name"]

_log = get_logger(__name__)

#: The most actions a skill holds: one WS-67 tool pack.
MAX_ACTIONS = MAX_PACK_TOOLS

#: The legal moves of a skill. A move to the same state is a no-op, and each
#: other move is 409. ``draft`` to ``published`` needs a version too.
TRANSITIONS: dict[str, frozenset[str]] = {
    "draft": frozenset({"published", "archived"}),
    "published": frozenset({"paused", "archived"}),
    "paused": frozenset({"published", "archived"}),
    "archived": frozenset({"draft"}),
}

ADD_A_VERSION = "Add a version first"
CHANGED = "The skill changed while it moved. Load it again, then try again."

#: A state as the 409 of an illegal move says it.
_STATE_WORDS = {"draft": "a draft"}


def _cannot_move(state: str, target: str) -> str:
    """The text of an illegal move, for example "The skill is archived. It
    cannot move to published." """
    return f"The skill is {_STATE_WORDS.get(state, state)}. It cannot move to {target}."

_NUMBER = re.compile(r"[0-9]{1,9}")
_SHORT = 80


class _NewSkill(BaseModel):
    name: str
    slug: str | None = None
    description: str = ""
    body: dict[str, Any] | None = None


class _NewVersion(BaseModel):
    body: dict[str, Any]


class _Undo(BaseModel):
    expected_version: int


class _NewState(BaseModel):
    state: str


def _refuse(status: int, detail: str) -> HTTPException:
    return HTTPException(status_code=status, detail=detail)


def _too_deep(value: Any, limit: int) -> bool:
    """True when *value* nests deeper than *limit*. The root is level 0, as
    in A2. Iterative, so no depth can overflow the stack."""
    stack: list[tuple[Any, int]] = [(value, 0)]
    while stack:
        node, depth = stack.pop()
        if depth > limit:
            return True
        if isinstance(node, dict):
            stack.extend((v, depth + 1) for v in node.values())
        elif isinstance(node, list):
            stack.extend((v, depth + 1) for v in node)
    return False


def _deep_body() -> HTTPException:
    return _refuse(422, f"The body nests deeper than {MAX_DEPTH} levels. Flatten it.")


def _parse[M: BaseModel](model: type[M], payload: Any) -> M:
    """Read a request body into *model*. The depth cap runs first. A
    refusal is 422, and it echoes no part of the input."""
    # The envelope holds the skill body one level down.
    if _too_deep(payload, MAX_DEPTH + 1):
        raise _deep_body()
    try:
        return model.model_validate(payload)
    except ValidationError as exc:
        problems = exc.errors(include_url=False, include_context=False, include_input=False)
        text = ", ".join(
            f"{'.'.join(str(p) for p in e['loc']) or 'body'}: {e['msg']}" for e in problems[:5])
        raise _refuse(422, text or "The request body is not valid") from None
    except RecursionError:
        raise _deep_body() from None


def _skill_id(raw: str) -> str:
    try:
        return str(uuid.UUID(raw))
    except (ValueError, TypeError, AttributeError):
        raise _refuse(422, "The skill id must be a UUID") from None


def _number(raw: str) -> int:
    if not _NUMBER.fullmatch(raw) or int(raw) < 1:
        raise _refuse(422, "The version must be a whole number of 1 or more")
    return int(raw)


def _short(value: str) -> str:
    return repr(value if len(value) <= _SHORT else value[:_SHORT] + "...")


def _count_problem(ids: Sequence[str]) -> str | None:
    """The count and the duplicate checks, which need no catalog."""
    if len(ids) > MAX_ACTIONS:
        return f"$.actions: a skill holds at most {MAX_ACTIONS} actions"
    seen: set[str] = set()
    for i, action in enumerate(ids):
        if action in seen:
            return f"$.actions[{i}]: the action {_short(action)} is in the list twice"
        seen.add(action)
    return None


def _catalog_problem(ids: Sequence[str], known: frozenset[str]) -> str | None:
    """The first action of *ids* that the catalog *known* does not serve."""
    for i, action in enumerate(ids):
        if action not in known:
            return f"$.actions[{i}]: the catalog serves no action {_short(action)}"
    return None


async def _catalog_ids() -> frozenset[str]:
    """The ids of the catalog for the bound org. It opens its own session."""
    return frozenset(str(row["id"]) for row in await actions.catalog_rows())


def _actions_of(body: dict[str, Any]) -> list[str]:
    found = body.get("actions")
    return [str(a) for a in found] if isinstance(found, list) else []


def _precheck(body: dict[str, Any]) -> list[str]:
    """The depth, store and count checks of a version body, in order. Each
    refusal is 422. They need no catalog. Answers the action ids."""
    if _too_deep(body, MAX_DEPTH):
        raise _deep_body()
    try:
        store.check_body(body)
    except store.SkillStoreError as exc:
        raise _refuse(422, str(exc)) from None
    except RecursionError:
        raise _deep_body() from None
    ids = _actions_of(body)
    problem = _count_problem(ids)
    if problem is not None:
        raise _refuse(422, problem)
    return ids


def _catalog_check(ids: list[str], known: frozenset[str]) -> None:
    problem = _catalog_problem(ids, known) if ids else None
    if problem is not None:
        raise _refuse(422, problem)


async def _check_body(body: dict[str, Any]) -> None:
    """The three checks of a version body, in order. Each refusal is 422.
    Run it before the store session opens."""
    ids = _precheck(body)
    if ids:
        _catalog_check(ids, await _catalog_ids())


@contextlib.contextmanager
def _store_errors() -> Iterator[None]:
    """The store's refusals as HTTP answers. A slug that the member holds
    is 409 with the store's text, which names no slug."""
    try:
        yield
    except store.SkillSlugTaken as exc:
        raise _refuse(409, str(exc)) from None
    except store.SkillStoreError as exc:
        raise _refuse(422, str(exc)) from None


def _skill_out(skill: store.Skill) -> dict[str, Any]:
    """A skill as a route answers it. The caller owns each skill that a
    route reads in F2a, so the answer names no owner."""
    return {
        "id": skill.id,
        "name": skill.name,
        "slug": skill.slug,
        "description": skill.description,
        "visibility": skill.visibility,
        "state": skill.state,
        "current_version": skill.current_version,
        "created_at": skill.created_at,
        "updated_at": skill.updated_at,
    }


def _history_out(info: store.VersionInfo) -> dict[str, Any]:
    """One row of the history: no body, and the member who made it."""
    return {
        "version": info.version,
        "created_by": info.created_by,
        "created_at": info.created_at,
        "content_hash": info.content_hash,
    }


def _version_out(version: store.Version | None) -> dict[str, Any] | None:
    if version is None:
        return None
    return {
        "skill_id": version.skill_id,
        "version": version.version,
        "body": version.body,
        "content_hash": version.content_hash,
        "created_at": version.created_at,
    }


@router.get("/skills")
async def list_skills(user: UserContext = Depends(get_current_user)) -> dict[str, Any]:
    """The skills of the caller in the bound org, newest change first."""
    if not flags.studio_allowed(user.organization_id):
        raise HTTPException(status_code=404, detail=NOT_FOUND)
    async with _tenant_session() as db:
        with _store_errors():
            skills = await store.list_for(db, user.email)
    return {"skills": [_skill_out(s) for s in skills]}


@router.post("/skills", status_code=201)
async def create_skill(
    payload: Any = Body(None),
    user: UserContext = Depends(get_current_user),
) -> dict[str, Any]:
    """A new private draft of the caller. With a ``body``, version 1 too."""
    if not flags.studio_allowed(user.organization_id):
        raise HTTPException(status_code=404, detail=NOT_FOUND)
    new = _parse(_NewSkill, payload)
    slug = new.slug or slug_from_name(new.name)
    if not slug:
        raise _refuse(422, "The name gives no slug. Send a slug of letters and digits.")
    if new.body is not None:
        await _check_body(new.body)
    async with _tenant_session() as db:
        with _store_errors():
            skill = await store.create_skill(
                db, user.email, name=new.name, slug=slug, description=new.description)
            version = None
            if new.body is not None:
                version = await store.add_version(db, user.email, skill.id, new.body)
                skill = await store.get_skill(db, user.email, skill.id) or skill
    return {"skill": _skill_out(skill), "version": _version_out(version)}


@router.get("/skills/{skill_id}")
async def get_skill(
    skill_id: str,
    user: UserContext = Depends(get_current_user),
) -> dict[str, Any]:
    """One skill of the caller, and its newest version (null with none)."""
    if not flags.studio_allowed(user.organization_id):
        raise HTTPException(status_code=404, detail=NOT_FOUND)
    sid = _skill_id(skill_id)
    async with _tenant_session() as db:
        with _store_errors():
            skill = await store.get_skill(db, user.email, sid)
            if skill is None:
                raise HTTPException(status_code=404, detail=NOT_FOUND)
            version = await store.get_version(db, user.email, sid)
    return {"skill": _skill_out(skill), "version": _version_out(version)}


@router.get("/skills/{skill_id}/versions/{version}")
async def get_skill_version(
    skill_id: str,
    version: str,
    user: UserContext = Depends(get_current_user),
) -> dict[str, Any]:
    """One stored version of a skill of the caller."""
    if not flags.studio_allowed(user.organization_id):
        raise HTTPException(status_code=404, detail=NOT_FOUND)
    sid = _skill_id(skill_id)
    number = _number(version)
    async with _tenant_session() as db:
        with _store_errors():
            found = await store.get_version(db, user.email, sid, number)
    if found is None:
        raise HTTPException(status_code=404, detail=NOT_FOUND)
    return {"version": _version_out(found)}


@router.get("/skills/{skill_id}/versions")
async def list_skill_versions(
    skill_id: str,
    user: UserContext = Depends(get_current_user),
) -> dict[str, Any]:
    """The history of a skill of the caller, newest first. A row holds the
    version, who made it, when, and the content hash. It holds no body."""
    if not flags.studio_allowed(user.organization_id):
        raise HTTPException(status_code=404, detail=NOT_FOUND)
    sid = _skill_id(skill_id)
    async with _tenant_session() as db:
        with _store_errors():
            skill = await store.get_skill(db, user.email, sid)
            if skill is None:
                raise HTTPException(status_code=404, detail=NOT_FOUND)
            found = await store.list_versions(db, user.email, sid)
    return {"versions": [_history_out(v) for v in found]}


@router.post("/skills/{skill_id}/versions", status_code=201)
async def add_skill_version(
    skill_id: str,
    payload: Any = Body(None),
    user: UserContext = Depends(get_current_user),
) -> dict[str, Any]:
    """The next version of a skill of the caller. A published skill goes
    back to ``draft`` in the same transaction."""
    if not flags.studio_allowed(user.organization_id):
        raise HTTPException(status_code=404, detail=NOT_FOUND)
    sid = _skill_id(skill_id)
    new = _parse(_NewVersion, payload)
    await _check_body(new.body)
    async with _tenant_session() as db:
        with _store_errors():
            # F4a: a body with no ``tests`` keeps the tests of the newest
            # version. The undo copies a body exactly, so it skips this.
            body = await store.carry_tests(db, user.email, sid, new.body)
            stored = await store.add_version(db, user.email, sid, body)
            if stored is None:
                raise HTTPException(status_code=404, detail=NOT_FOUND)
            # The UPDATE of add_version holds the row lock until the commit,
            # so this read sees the newest state, and no move can come between.
            skill = await store.get_skill(db, user.email, sid)
            if skill is None:
                raise HTTPException(status_code=404, detail=NOT_FOUND)
            if skill.state == "published":
                await store.set_state(db, user.email, sid, "draft")
                skill = await store.get_skill(db, user.email, sid) or skill
    return {"skill": _skill_out(skill), "version": _version_out(stored)}


NOTHING_TO_UNDO = "Nothing to undo"


@router.post("/skills/{skill_id}/undo", status_code=201)
async def undo_skill_version(
    skill_id: str,
    payload: Any = Body(None),
    user: UserContext = Depends(get_current_user),
) -> dict[str, Any]:
    """One step back. The route writes version N+1 as a copy of version N-1,
    so the history stays append-only. The caller sends the version that it
    sees, ``expected_version`` N. A published skill returns to ``draft``."""
    if not flags.studio_allowed(user.organization_id):
        raise HTTPException(status_code=404, detail=NOT_FOUND)
    sid = _skill_id(skill_id)
    expected = _parse(_Undo, payload).expected_version
    # The catalog read opens its own session, so it runs before the store one.
    known = await _catalog_ids()
    async with _tenant_session() as db:
        with _store_errors():
            # The row lock holds until the commit. A second undo waits here,
            # then reads the version that this undo wrote, and gets 409.
            skill = await store.get_skill(db, user.email, sid, for_update=True)
            if skill is None:
                raise HTTPException(status_code=404, detail=NOT_FOUND)
            if (skill.current_version or 0) != expected:
                raise _refuse(409, CHANGED)
            if not skill.current_version or skill.current_version < 2:
                raise _refuse(409, NOTHING_TO_UNDO)
            previous = await store.get_version(db, user.email, sid, skill.current_version - 1)
            if previous is None:
                raise _refuse(409, NOTHING_TO_UNDO)
            # The same checks as a new version. A version that is refused now
            # writes nothing.
            _catalog_check(_precheck(previous.body), known)
            stored = await store.add_version(db, user.email, sid, previous.body)
            if stored is None:
                raise HTTPException(status_code=404, detail=NOT_FOUND)
            after = await store.get_skill(db, user.email, sid)
            if after is None:
                raise HTTPException(status_code=404, detail=NOT_FOUND)
            if after.state == "published":
                await store.set_state(db, user.email, sid, "draft")
                after = await store.get_skill(db, user.email, sid) or after
    return {"skill": _skill_out(after), "version": _version_out(stored)}


@router.patch("/skills/{skill_id}/state")
async def set_skill_state(
    skill_id: str,
    payload: Any = Body(None),
    user: UserContext = Depends(get_current_user),
) -> dict[str, Any]:
    """Move a skill of the caller along :data:`TRANSITIONS`."""
    if not flags.studio_allowed(user.organization_id):
        raise HTTPException(status_code=404, detail=NOT_FOUND)
    sid = _skill_id(skill_id)
    target = _parse(_NewState, payload).state
    if target not in store.STATES:
        raise _refuse(422, f"The state must be one of {', '.join(store.STATES)}")
    # A publish checks the actions against the catalog. Read it first,
    # because the catalog read opens its own session.
    known = await _catalog_ids() if target == "published" else frozenset()
    async with _tenant_session() as db:
        with _store_errors():
            # The row lock holds until the commit. A second move waits here,
            # then reads the state that this move wrote, so two moves at
            # once cannot both pass TRANSITIONS.
            skill = await store.get_skill(db, user.email, sid, for_update=True)
            if skill is None:
                raise HTTPException(status_code=404, detail=NOT_FOUND)
            if skill.state == target:
                return {"skill": _skill_out(skill)}
            if target not in TRANSITIONS.get(skill.state, frozenset()):
                raise _refuse(409, _cannot_move(skill.state, target))
            if target == "published":
                newest = None
                if skill.current_version:
                    newest = await store.get_version(db, user.email, sid)
                if newest is None:
                    raise _refuse(409, ADD_A_VERSION)
                ids = _actions_of(newest.body)
                # The store refuses a hidden character since F1b. A body or a
                # column that an older store kept stops here, so it never
                # goes live.
                problem = (_count_problem(ids) or _catalog_problem(ids, known)
                           or body_hidden_problem(newest.body)
                           or hidden_problem(skill.name, "name")
                           or hidden_problem(skill.description, "description"))
                if problem is not None:
                    raise _refuse(422, problem)
            await store.set_state(db, user.email, sid, target)
            # The first read holds the row lock, so no version can come in
            # before the commit. This check is a second guard: a version that
            # came in after the first read is not the version this move checked.
            after = await store.get_skill(db, user.email, sid)
            if after is None or after.current_version != skill.current_version:
                raise _refuse(409, CHANGED)
    return {"skill": _skill_out(after)}


# ── The test run (TOF-3b F4b) ───────────────────────────────────────────────

#: The most input-only runs of one request.
MAX_TEST_INPUTS = 1
#: A request cap: a decimal string of credits with at most 4 places.
_CAP_TEXT = re.compile(r"[0-9]{1,9}(\.[0-9]{1,4})?")
CAP_UNSET = "Skill tests have no credit cap yet. An owner must set the cap first."
SUITE_RUNNING = "Your skill tests are running. Wait for them to end, then try again."
SLOT_UNAVAILABLE = "Metorite cannot start skill tests now. Try again soon."


class _RunTests(BaseModel):
    version: StrictInt | None = None
    body: dict[str, Any] | None = None
    inputs: list[str] = []
    cap: str | None = None


def _request_cap(raw: str | None) -> Decimal | None:
    """The cap of the request, or None. It can only lower the setting."""
    if raw is None:
        return None
    if not _CAP_TEXT.fullmatch(raw) or Decimal(raw) <= 0:
        raise _refuse(422, "The cap must be a number of credits above 0, with at most 4 places")
    return Decimal(raw)


def _check_inputs(inputs: list[str]) -> tuple[str, ...]:
    if len(inputs) > MAX_TEST_INPUTS:
        raise _refuse(422, f"A run takes at most {MAX_TEST_INPUTS} input")
    for i, text in enumerate(inputs):
        if not text.strip() or len(text) > skill_tests.MAX_INPUT_CHARS:
            raise _refuse(422, f"inputs[{i}] must be a non-empty string of at most "
                               f"{skill_tests.MAX_INPUT_CHARS} characters")
    return tuple(inputs)


def _test_out(result: skill_tests.TestResult) -> dict[str, Any]:
    """One test result as the route answers it: codes and action names."""
    return {
        "test_id": result.test_id,
        "passed": result.passed,
        "reason": result.reason,
        "detail": result.detail,
        "writes": list(result.writes),
        "calls": list(result.calls),
        "credits": str(result.credits),
        "model_calls": result.model_calls,
    }


def _suite_out(result: skill_tests.SuiteResult) -> dict[str, Any]:
    return {
        "skill_id": result.skill_id,
        "version": result.version,
        "content_hash": result.content_hash,
        "passed": result.passed,
        "credits": str(result.credits),
        "cap": None if result.cap is None else str(result.cap),
        "stopped": result.stopped,
        "results": [_test_out(r) for r in result.results],
    }


@router.post("/skills/{skill_id}/tests/run")
async def run_skill_tests(
    skill_id: str,
    payload: Any = Body(None),
    user: UserContext = Depends(get_current_user),
) -> dict[str, Any]:
    """Run the tests of a skill of the caller, each on a real turn in a dry run.

    The body is ``{version?, body?, inputs?, cap?}``. ``body`` is an unsaved
    version, and it runs the three body checks first. ``inputs`` holds at most
    one input-only run. ``cap`` is a decimal string, and it can only lower
    the setting. The org and the member come from the session only."""
    org = user.organization_id
    if not flags.studio_allowed(org) or not flags.skill_tests_allowed(org):
        raise HTTPException(status_code=404, detail=NOT_FOUND)
    sid = _skill_id(skill_id)
    req = _parse(_RunTests, payload)
    if req.version is not None and req.version < 1:
        raise _refuse(422, "The version must be a whole number of 1 or more")
    if req.version is not None and req.body is not None:
        raise _refuse(422, "Send a version or a body, not both")
    inputs = _check_inputs(req.inputs)
    cap = _request_cap(req.cap)
    if req.body is not None:
        await _check_body(req.body)
    async with _tenant_session() as db:
        with _store_errors():
            if await store.get_skill(db, user.email, sid) is None:
                raise HTTPException(status_code=404, detail=NOT_FOUND)
            if req.body is None and await store.get_version(
                    db, user.email, sid, req.version) is None:
                raise HTTPException(status_code=404, detail=NOT_FOUND)
    if flags.skill_tests_cap() is None:
        raise HTTPException(status_code=409, detail={"code": "cap_unset", "message": CAP_UNSET})
    from gateway.jobs.headless_turn import console_meter

    try:
        result = await skill_tests.run_suite(
            org=str(org), member=user.email, skill_id=sid, version=req.version,
            body=req.body, inputs=inputs, cap=cap, runner=skill_tests.agent_runner,
            meter=console_meter)
    except LookupError:
        raise HTTPException(status_code=404, detail=NOT_FOUND) from None
    except skill_tests.SkillTestError as exc:
        raise _refuse(422, str(exc)) from None
    except skill_tests.SuiteRunning:
        raise HTTPException(status_code=429, detail={
            "code": "suite_running", "message": SUITE_RUNNING}) from None
    except skill_tests.SuiteSlotUnavailable:
        raise HTTPException(status_code=503, detail={
            "code": "slot_unavailable", "message": SLOT_UNAVAILABLE}) from None
    return _suite_out(result)


# ── The share with the org (WS-STU J2c) ────────────────────────────────────

#: The 409 of a share move that the state of the ask does not allow (J2c).
SHARE_CONFLICT = "share_conflict"
FORBIDDEN = "forbidden"
#: The one state that ``GET /skill-shares`` reads.
PENDING = "pending"
BAD_SHARE_STATE = ("state is pending, the one state that this list reads. Send it, or "
                   "leave it out.")
NO_SHARE_LIST_RIGHT = ("Only a holder of studio:publish reads the skills that wait for a "
                       "share. Ask the owner of your organization for that right.")


@contextlib.contextmanager
def _share_errors() -> Iterator[None]:
    """The refusals of the share moves as HTTP answers. ``ShareRefused`` is
    403 ``forbidden``. ``ShareConflict`` is 409 :data:`SHARE_CONFLICT`, with
    the state that the store read."""
    try:
        yield
    except store.ShareRefused as exc:
        raise HTTPException(status_code=403, detail={
            "code": FORBIDDEN, "message": str(exc)}) from None
    except store.ShareConflict as exc:
        raise HTTPException(status_code=409, detail={
            "code": SHARE_CONFLICT, "message": str(exc),
            "share_state": exc.current}) from None


def _share_out(share: store.Share) -> dict[str, Any]:
    return {
        "skill_id": share.skill_id,
        "visibility": share.visibility,
        "shared_version": share.shared_version,
        "share_state": share.share_state,
        "share_version": share.share_version,
        "requested_by": share.requested_by,
        "requested_at": share.requested_at,
        "decided_by": share.decided_by,
        "decided_at": share.decided_at,
    }


def _pending_share_out(found: store.PendingShare) -> dict[str, Any]:
    """One row of the holder's list, with the FULL body of the asked version."""
    return {
        "skill_id": found.skill_id,
        "name": found.name,
        "slug": found.slug,
        "description": found.description,
        "owner_email": found.owner_email,
        "version": found.version,
        "requested_by": found.requested_by,
        "requested_at": found.requested_at,
        "body": found.body,
        "content_hash": found.content_hash,
    }


@router.post("/skills/{skill_id}/versions/{version}/share", status_code=202)
async def request_skill_share(
    skill_id: str,
    version: str,
    user: UserContext = Depends(get_current_user),
) -> dict[str, Any]:
    """The owner asks to share one version with the org (J2c). The answer is
    202 ``pending``. A second ask of the same version writes nothing. A
    decided ask of this or a newer version is 409. Another member, another
    org and no such version get 404. The handler takes no body, so a
    visibility in a request changes nothing."""
    if not flags.studio_allowed(user.organization_id):
        raise HTTPException(status_code=404, detail=NOT_FOUND)
    sid = _skill_id(skill_id)
    v = _number(version)
    async with _tenant_session() as db:
        with _store_errors(), _share_errors():
            found = await store.request_share(db, user.email, sid, v)
        if found is None:
            raise HTTPException(status_code=404, detail=NOT_FOUND)
    _log.info("studio.skills.share_asked", skill_id=sid, version=v)
    return _share_out(found)


@router.get("/skill-shares")
async def list_skill_shares(
    user: UserContext = Depends(get_current_user),
    state: str | None = Query(default=None),
) -> dict[str, Any]:
    """The asks of the bound org that wait for a holder (J2c), oldest first,
    each with the full body of the asked version. Only a holder of
    ``studio:publish`` reads it, and each other caller gets 403 before any
    session. ``truncated`` is True when more asks wait past the page."""
    if not flags.studio_allowed(user.organization_id):
        raise HTTPException(status_code=404, detail=NOT_FOUND)
    if state is not None and state != PENDING:
        raise _refuse(422, BAD_SHARE_STATE)
    # D180.3: the holder of the share verb reads what waits for it. Fail closed.
    can = user.has_permission(SHARE_RIGHT)
    if not can:
        raise HTTPException(status_code=403, detail={
            "code": FORBIDDEN, "message": NO_SHARE_LIST_RIGHT})
    async with _tenant_session() as db:
        with _store_errors(), _share_errors():
            rows, more = await store.list_pending_shares(db, can_publish=can)
    return {"state": PENDING, "shares": [_pending_share_out(r) for r in rows],
            "truncated": more}


async def _decide_share(user: UserContext, skill_id: str, version: str, approve: bool,
                        can: bool) -> dict[str, Any]:
    """One approve or reject. *can* is the check of the share verb that the
    handler made. The store refuses each other value first."""
    sid = _skill_id(skill_id)
    v = _number(version)
    decide = store.approve_share if approve else store.reject_share
    async with _tenant_session() as db:
        with _store_errors(), _share_errors():
            found = await decide(db, user.email, sid, v, can_publish=can)
        if found is None:
            raise HTTPException(status_code=404, detail=NOT_FOUND)
    _log.info("studio.skills.share_decided", skill_id=sid, version=v,
              move="approve" if approve else "reject")
    return _share_out(found)


@router.post("/skills/{skill_id}/versions/{version}/share/approve")
async def approve_skill_share(
    skill_id: str,
    version: str,
    user: UserContext = Depends(get_current_user),
) -> dict[str, Any]:
    """A holder of ``studio:publish`` approves one pending ask (J2c). The
    skill becomes ``org``, and other members load this version. The owner
    gets 403. No ask of this version, another org and no such skill get
    404. A decided ask is 409 ``share_conflict``."""
    if not flags.studio_allowed(user.organization_id):
        raise HTTPException(status_code=404, detail=NOT_FOUND)
    return await _decide_share(user, skill_id, version, True,
                               user.has_permission(SHARE_RIGHT))


@router.post("/skills/{skill_id}/versions/{version}/share/reject")
async def reject_skill_share(
    skill_id: str,
    version: str,
    user: UserContext = Depends(get_current_user),
) -> dict[str, Any]:
    """A holder of ``studio:publish`` rejects one pending ask (J2c). The
    owner may reject too. The visibility does not change. The answers are
    those of the approve."""
    if not flags.studio_allowed(user.organization_id):
        raise HTTPException(status_code=404, detail=NOT_FOUND)
    return await _decide_share(user, skill_id, version, False,
                               user.has_permission(SHARE_RIGHT))


@router.delete("/skills/{skill_id}/share")
async def unshare_skill(
    skill_id: str,
    user: UserContext = Depends(get_current_user),
) -> dict[str, Any]:
    """Return a skill to ``private`` (J2c). The owner always may. Another
    member needs ``studio:publish`` (403 without it), and then a skill that
    is not ``org`` in the bound org is 404. The next turn of each other
    member loads the skill no more."""
    if not flags.studio_allowed(user.organization_id):
        raise HTTPException(status_code=404, detail=NOT_FOUND)
    sid = _skill_id(skill_id)
    async with _tenant_session() as db:
        with _store_errors(), _share_errors():
            found = await store.unshare(db, user.email, sid,
                                        can_publish=user.has_permission(SHARE_RIGHT))
        if found is None:
            raise HTTPException(status_code=404, detail=NOT_FOUND)
    _log.info("studio.skills.unshared", skill_id=sid)
    return _share_out(found)
