"""The repair of a saved skill (WS-TOF TOF-3a, F12).

Spec: ``project-docs/specs/ai_studio_rows/F12.md``.

**F12b holds the repair key.** :func:`body_key` is the key of the body that
ran. Each writer of the store of skill runs (``gateway/studio/skill_runs.py``)
stores it in ``body_key``, so the repeat check can find a body that failed
before.

**F12c adds the rest.** Each part here is pure, except :func:`failed_input`
(one read of the chat of the member) and :func:`try_proposal` (the one call
of ``skill_tests.run_suite``). The routes of ``routes/studio/skills.py`` call
them in the order of F12.md "The checks before the dry run".

* :func:`proposed_body` builds the proposal from the base body. It changes
  only ``instructions``, and it can only remove an action.
* :func:`check_contract` is the contract of a repair. It gives ``widened``
  or ``no_change``, or None.
* :func:`earlier_versions` picks the earlier failed versions of the history.
* :func:`failed_input` finds the failed input by its hash. The text stays in
  memory. No row, no log line and no answer holds it.
* :func:`try_proposal` runs the proposal on the hold-and-record door with
  the tests of the base version and the failed input.
* :func:`outcome` reads the result of the door. The words of the model never
  decide it (F12.md "The end-state rule").

Fence: ``tests/unit/test_skill_repair.py``.
"""

from __future__ import annotations

import copy
from collections.abc import Collection, Mapping, Sequence
from dataclasses import dataclass
from decimal import Decimal
from typing import Any

from sqlalchemy import text

from gateway.studio import skill_tests
from gateway.studio.skill_runs import SkillRun, cut_detail, fold_space, input_hash, safe_writes
from gateway.studio.skills_store import content_hash

__all__ = [
    "CHAT_READ_ROWS",
    "CONTRACT_CODES",
    "EARLIER_VERSIONS",
    "EARLIER_VERSION_CHARS",
    "OTHER_WORDS",
    "WORDS",
    "Outcome",
    "RepairError",
    "body_key",
    "check_contract",
    "class_rank",
    "earlier_versions",
    "failed_input",
    "outcome",
    "outcome_writes",
    "passed_words",
    "proposed_body",
    "same_writes",
    "saved_words",
    "try_proposal",
    "words_of",
    "write_name",
]

#: The codes of :func:`check_contract`.
CONTRACT_CODES = ("widened", "no_change")
#: The most messages of the member that :func:`failed_input` reads (check 9).
CHAT_READ_ROWS = 50
#: The most earlier failed versions in the history read (D-F12-3).
EARLIER_VERSIONS = 3
#: The cut of the instructions of one earlier failed version.
EARLIER_VERSION_CHARS = 400

#: The rank of each class of the catalog. A higher rank is a wider act. An
#: unknown class takes the top rank, so the class check fails closed.
_CLASS_RANK = {"A": 0, "B": 1, "C": 2, "X": 3}
_TOP_RANK = max(_CLASS_RANK.values())

#: The keys that a repair may change (D-F12-5).
_REPAIR_KEYS = frozenset({"instructions", "actions"})


#: The base words of the member for each code (F12.md "The words"). Plain
#: English, with no internal name. The model may reword a line. The routes
#: give them, so the chat tool keeps no copy.
WORDS: Mapping[str, str] = {
    "passed": "It passed.",
    "not_run": "It did not run, because the try stopped first.",
    "cap_reached": "The try reached its credit limit, so it stopped.",
    "tool_error": "A tool that this skill uses stopped with an error.",
    "card_declined": "You declined the change that this skill asked for.",
    "member_said_wrong": "You said that the result was wrong.",
    "other_action": "The skill used a tool that is not in its list.",
    "missing_write": "The skill did not make a change that its test expects.",
    "extra_write": "The skill made a change that its test does not expect.",
    "field_mismatch": "The skill made the right kind of change, with a different value.",
    "invalid_call": "The skill sent a change that the tool refuses.",
    "run_failed": "The run stopped with an error before it finished.",
    "call_cap": "The skill took too many steps.",
    "widened": ("A fix can only change the instructions or remove an action. To add an "
                "action, change the skill in AI Studio."),
    "repeat_change": ("An earlier version already tried this change, and it failed. I need a "
                      "different fix."),
    "no_change": "This fix changes nothing in the skill.",
    "already_repaired": ("I already tried one fix for this failure. Run the skill again, or "
                         "change it in AI Studio."),
    "skill_changed": "The skill changed after this failure. Run it again, then ask me to fix it.",
    "input_gone": ("I cannot find the message that failed, so I cannot try a fix on it. Run "
                   "the skill again."),
    "input_too_long": ("The message that failed is too long to try again here. Run the skill "
                       "with a shorter message."),
    "too_many_tests": ("This skill has more tests than one fix can try. Remove a test in AI "
                       "Studio, then ask again."),
    "cap_unset": ("Trying a fix is not turned on yet, so I changed nothing. You can still "
                  "change the skill in AI Studio."),
    "suite_running": "I am already trying a fix for you. Ask again when it finishes.",
    "not_owner": "Only the owner of this skill can fix it. Ask the owner.",
    "no_failed_run": "I cannot find that failed run. Ask me to list the failures.",
    "unresolved_variable": ("The instructions cannot hold {{ or }}. Remove them, then ask "
                            "again. I tried nothing."),
    "too_long": "The instructions are too long. Make them shorter, then ask again. I tried nothing.",
    "skill_unloaded": ("This skill cannot load now, so I tried nothing. Check its actions in AI "
                       "Studio."),
    "failed": ("Version {next} did not pass, so I did not offer it. I saved nothing. Change "
               "the skill in AI Studio, or run it again and tell me what went wrong."),
    "no_tests": "This skill has no tests, so I tried only the request that failed.",
}

#: The fallback line of a code that :data:`WORDS` does not hold.
OTHER_WORDS = "The try stopped before it finished. I saved nothing."


def words_of(code: str) -> str:
    """The base words of *code*, or :data:`OTHER_WORDS`."""
    return WORDS.get(code, OTHER_WORDS)


def passed_words(next_version: int, tests: int) -> str:
    """The words of a proposal that passed (F12.md "The words")."""
    if tests <= 0:
        return (f"{WORDS['no_tests']} I saved nothing, and it passed. Approve the change to "
                f"save it as version {next_version}.")
    noun = "test" if tests == 1 else "tests"
    return (f"I tried version {next_version} on your {tests} {noun} and on the request that "
            "failed. I saved nothing, and all passed. Approve the change to save it.")


def saved_words(version: int, state: str) -> str:
    """The words after the approve. The approve keeps the state (D-F12-6)."""
    kept = "stays published" if state == "published" else f"stays {_STATE.get(state, state)}"
    return f"I saved version {version}. The skill {kept}. Say undo to go back."


_STATE = {"draft": "a draft"}


class RepairError(ValueError):
    """A proposal that the repair refuses. ``code`` names the fault, and the
    text names a field, never a value."""

    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code


# ── The repair key (F12b) ──────────────────────────────────────────────────


def _folded(value: Any) -> Any:
    """*value* with each string folded (``skill_runs.fold_space``), in each
    list and each mapping. A key of a mapping stays as it is."""
    if isinstance(value, str):
        return fold_space(value)
    if isinstance(value, Mapping):
        return {k: _folded(v) for k, v in value.items()}
    if isinstance(value, list | tuple):
        return [_folded(v) for v in value]
    return value


def _sort_key(item: Any) -> tuple[int, str]:
    """A string sorts by its text. Any other item sorts after, by its type."""
    return (0, item) if isinstance(item, str) else (1, type(item).__name__)


def body_key(body: Mapping[str, Any]) -> str:
    """The repair key of *body*, in 64 lower-case hex digits (F12.md "The
    repair key").

    Each run of white space in each string becomes one space, and each
    string loses the white space at each end (``skill_runs.fold_space``).
    The list ``actions`` is sorted. Then the key is
    ``skills_store.content_hash`` of the result. So two bodies that differ
    only in white space, or in the order of their actions, have one key.
    Raises ``TypeError`` or ``ValueError`` for a body that is no JSON
    mapping, as ``content_hash`` does."""
    if not isinstance(body, Mapping):
        raise TypeError("A body is a mapping.")
    folded = _folded(body)
    actions = folded.get("actions")
    if isinstance(actions, list):
        folded["actions"] = sorted(actions, key=_sort_key)
    return content_hash(folded)


# ── The proposal and its contract (F12c) ───────────────────────────────────


def _actions(body: Mapping[str, Any]) -> list[str]:
    found = body.get("actions")
    return [str(a) for a in found] if isinstance(found, list) else []


def proposed_body(base: Mapping[str, Any], instructions: str,
                  remove_actions: Sequence[str]) -> dict[str, Any]:
    """The proposal: a copy of *base* with new *instructions* and with each
    action of *remove_actions* taken out. A blank *instructions* keeps the
    instructions of *base*. A proposal never holds an action that *base*
    does not hold.

    Raises :class:`RepairError` ``not_an_action`` when *remove_actions*
    names an action that *base* does not hold."""
    if not isinstance(base, Mapping):
        raise RepairError("body", "The base body is no mapping.")
    held = _actions(base)
    gone = [str(a) for a in remove_actions]
    for action in gone:
        if action not in held:
            raise RepairError("not_an_action", "remove_actions names an action that the skill "
                                               "does not hold.")
    body = copy.deepcopy(dict(base))
    if isinstance(instructions, str) and instructions.strip():
        body["instructions"] = instructions
    if gone:
        body["actions"] = [a for a in held if a not in set(gone)]
    return body


def class_rank(cls: object) -> int:
    """The rank of a class of the catalog. An unknown class takes the top."""
    return _CLASS_RANK.get(str(cls or "").strip().upper(), _TOP_RANK)


def _top_class(actions: Collection[str], classes: Mapping[str, str]) -> int:
    return max((class_rank(classes.get(a)) for a in actions), default=-1)


def _same(left: Any, right: Any) -> bool:
    """Equal with the same types at each level, so ``1``, ``True`` and
    ``1.0`` all differ."""
    if type(left) is not type(right):
        return False
    if isinstance(left, Mapping):
        return set(left) == set(right) and all(_same(left[k], right[k]) for k in left)
    if isinstance(left, list | tuple):
        return len(left) == len(right) and all(_same(a, b) for a, b in zip(left, right,
                                                                               strict=True))
    return bool(left == right)


def check_contract(base: Mapping[str, Any], proposed: Mapping[str, Any],
                   classes: Mapping[str, str]) -> str | None:
    """The contract of a repair (F12.md "The contract of a repair"). A pure
    function of two bodies and the classes of the catalog. Answers a code of
    :data:`CONTRACT_CODES`, or None.

    * An action of *proposed* that *base* does not hold gives ``widened``.
    * A key other than ``instructions`` and ``actions`` that differs gives
      ``widened``. That covers ``description``, ``cards``, ``knowledge``,
      ``tests`` and each other key.
    * A top class of *proposed* above the top class of *base* gives
      ``widened``. *classes* is one read of the catalog, and an action that
      it does not name takes the top rank.
    * A *proposed* with the repair key of *base* gives ``no_change``."""
    if not isinstance(base, Mapping) or not isinstance(proposed, Mapping):
        return "widened"
    held = set(_actions(base))
    wanted = _actions(proposed)
    if any(a not in held for a in wanted):
        return "widened"
    # A body with no ``actions`` key is no empty subset: the key stays, or
    # it stays absent.
    if ("actions" in proposed) != ("actions" in base):
        return "widened"
    if "actions" in proposed and not isinstance(proposed.get("actions"), list):
        return "widened"
    for key in set(base) | set(proposed):
        if key in _REPAIR_KEYS:
            continue
        if key not in base or key not in proposed or not _same(base[key], proposed[key]):
            return "widened"
    if _top_class(wanted, classes) > _top_class(held, classes):
        return "widened"
    if body_key(proposed) == body_key(base):
        return "no_change"
    return None


def earlier_versions(rows: Sequence[SkillRun], current_version: int | None,
                     limit: int = EARLIER_VERSIONS) -> list[int]:
    """The versions of the failed *rows* that are not *current_version*,
    newest row first, each once, at most *limit* (D-F12-3)."""
    out: list[int] = []
    for row in rows:
        if row.passed or row.version == current_version or row.version in out:
            continue
        out.append(int(row.version))
        if len(out) >= limit:
            break
    return out


# ── The failed input (check 9) ─────────────────────────────────────────────

_BOUND_ORG = "CAST(NULLIF(current_setting('app.tenant_id', true), '') AS uuid)"

#: The newest messages of the member in one chat. The chat must be a chat of
#: the member, and the row must be a typed row of the member. RLS binds the
#: org, and the statement names it too.
_MEMBER_MESSAGES_SQL = (
    "SELECT m.content FROM chat_message m "
    "JOIN chat_session s ON s.id = m.session_id "
    f"WHERE m.session_id = :thread AND m.organization_id = {_BOUND_ORG} "
    f"AND s.organization_id = {_BOUND_ORG} "
    "AND lower(btrim(s.user_id)) = :member AND m.role = 'user' "
    "AND COALESCE(NULLIF(m.author_kind, ''), 'human') = 'human' "
    "AND COALESCE(NULLIF(lower(btrim(m.author_email)), ''), :member) = :member "
    "ORDER BY m.timestamp_ms DESC, m.id DESC LIMIT CAST(:n AS integer)"
)

_BOUND_ORG_SQL = "SELECT current_setting('app.tenant_id', true)"


async def failed_input(db: Any, member: str, thread_id: str | None,
                       want_hash: str | None) -> str | None:
    """The text of the failed input, or None (F12.md check 9).

    It reads the newest :data:`CHAT_READ_ROWS` messages of *member* in the
    chat *thread_id*, under the bound org of *db*, and gives the first one
    whose ``input_hash`` equals *want_hash*. The text stays in memory. The
    caller passes it to the dry run only, and never stores or logs it."""
    if not thread_id or not want_hash or not isinstance(member, str) or not member.strip():
        return None
    org = (await db.execute(text(_BOUND_ORG_SQL))).scalar()
    if not org:
        return None
    rows = (await db.execute(text(_MEMBER_MESSAGES_SQL), {
        "thread": str(thread_id), "member": member.strip().lower(),
        "n": CHAT_READ_ROWS})).all()
    for row in rows:
        content = row[0]
        if isinstance(content, str) and fold_space(content) and input_hash(
                org, content) == want_hash:
            return content
    return None


# ── The dry run and its outcome ────────────────────────────────────────────


@dataclass(frozen=True)
class Outcome:
    """What the ``proposal`` row keeps, read from the record of the door.

    *writes* holds the held write actions of the input run, as action names
    only. *run_id* is the run of the input."""

    passed: bool
    reason: str
    detail: str
    writes: tuple[str, ...]
    credits: Decimal
    run_id: str | None
    tests: int




async def try_proposal(*, org: str, member: str, skill_id: str,
                       proposed: Mapping[str, Any], base_tests: Sequence[Any],
                       input_text: str, runner: skill_tests.Runner,
                       meter: skill_tests.Meter) -> skill_tests.SuiteResult:
    """Run *proposed* once on the hold-and-record door (F12.md "The
    end-state rule"). The tests are the tests of the base version, never the
    tests of *proposed*, so a repair cannot weaken a test. The failed input
    runs as one input-only run. *org* and *member* come from the session.
    The caller injects *runner* and *meter*."""
    return await skill_tests.run_suite(
        org=org, member=member, skill_id=skill_id, body=dict(proposed),
        tests=list(base_tests), inputs=(input_text,), runner=runner, meter=meter)


def outcome(result: skill_tests.SuiteResult) -> Outcome:
    """The outcome of one dry run, from the result of the door only.

    ``passed`` is ``SuiteResult.passed``: each test of the base passed, and
    the input run passed. The reason is the code of the first result that
    did not pass. The text of the model is no input here."""
    first = next((r for r in result.results if r.passed is not True), None)
    passed = bool(result.passed) and first is None
    reason = "passed" if passed else (first.reason if first is not None else "not_run")
    detail = "" if passed or first is None else cut_detail(first.detail)
    # The input run is the LAST result: ``run_suite`` adds each input run
    # after the tests. A test of the member with the id ``input-1`` stays a test.
    held = result.results[-1] if result.results else None
    writes = tuple(dict.fromkeys(held.writes)) if held is not None else ()
    tests = max(len(result.results) - 1, 0)
    return Outcome(passed=passed, reason=reason, detail=detail, writes=writes,
                   credits=result.credits, run_id=None if held is None else held.run_id,
                   tests=tests)


def outcome_writes(found: Outcome) -> list[dict[str, Any]]:
    """The ``writes`` of the proposal row: the held write actions of the
    input run, with no key names. ``safe_writes`` drops a made-up name."""
    return safe_writes({"action": a, "keys": []} for a in found.writes)


def write_name(action: object) -> str:
    """THE one name of a write action for :func:`same_writes`: its chat tool
    (``skill_packs.action_tool``, the one map). A door op ``<app>.<op>`` of
    the live row and ``tool:<name>`` of the dry run then read alike. A
    ``tool:`` action that no chat tool map holds keeps its bare name."""
    from acb_skills.skill_packs import action_tool

    raw = str(action or "").strip()
    tool = action_tool(raw)
    if tool:
        return tool
    return raw[len("tool:"):] if raw.startswith("tool:") else raw


def same_writes(found: Outcome, failed: SkillRun) -> bool:
    """True when the held writes of the input run name the same actions as
    the writes of the failed run (F12.md "A known limit"). The result of the
    door holds action names only, so the check reads names, not keys. Both
    sides go through :func:`write_name`."""
    before = {write_name(w.get("action")) for w in failed.writes}
    return bool(before) and before == {write_name(a) for a in found.writes}
