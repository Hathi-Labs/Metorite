"""HRD-13 (task 2992): the claim check of the orchestrator's answer, in shadow.

Spec: ``project-docs/specs/chat_failure_modes.md`` §4.4 rows D1 and D2, §6 row
HRD-13 and §8 D-HRD-6. Module: ``acb_skills.answer_claims``. Seam:
``orchestrator._tool_injection._apply_claim_check``.

These tests hold each rule of the slice:

1. ``CHAT_CLAIM_CHECK_ORGS`` is empty by default. Off, the seam attaches no
   provider, and the agent view does not change.
2. On, only the view of the agent ``orchestrator`` carries ONE provider. It
   sits between the call id step and the progress notice. It adds no
   middleware and changes no answer.
3. MAF 1.19 calls ``after_run`` ONCE for each run, with the whole run. So a
   promise that a later call of the same run keeps never flags.
4. The verdicts, with no model: a table of sentences for each rule and each
   exclusion.
5. The fixture of run a8ad07f3 (task 3594) gives the exact set, and its two
   controls give none.
6. ONE log line for each run, of counts and closed names only. A planted
   secret never reaches it.
7. ``after_run`` never raises. A fault logs ``answer_claims.failed``, and the
   session save and each other provider still run.
8. A delegated run and a resume run log their abstain reasons.
9. A fence pins each act tool to a real pack tool and to its not-acted lead.
"""
from __future__ import annotations

import ast
import functools
import importlib.util
import inspect
import json
from collections.abc import AsyncIterable
from pathlib import Path
from typing import Any

import pytest
from acb_skills import answer_claims as ac
from acb_skills.answer_claims import RunCall, call_outcome, check_run
from agent_framework import (
    Agent,
    AgentSession,
    BaseChatClient,
    ChatMiddlewareLayer,
    ChatResponse,
    ChatResponseUpdate,
    Content,
    ContextProvider,
    FunctionInvocationLayer,
    Message,
)
from orchestrator import _tool_injection, executor

ROOT = Path(__file__).resolve().parents[2]
ORG = "org-hrd13"
SECRET = "zq7SECRETx"

A = ac.ACT_WITHOUT_OUTCOME
B = ac.DOUBT_WITHOUT_OUTCOME
C = ac.PROMISE_WITHOUT_FOLLOWUP
E = ac.EARLIER_TURN_DOUBT


@pytest.fixture
def flag_on(monkeypatch):
    from acb_common import get_settings
    from acb_common.db import bind_tenant, release_tenant

    monkeypatch.setattr(get_settings(), "chat_claim_check_orgs", ORG)
    token = bind_tenant(ORG)
    try:
        yield
    finally:
        release_tenant(token)


@pytest.fixture
def logged(monkeypatch) -> list[dict[str, Any]]:
    """Each line of the module's logger, at every level."""
    out: list[dict[str, Any]] = []
    for level in ("debug", "info", "warning", "error", "exception", "critical"):
        monkeypatch.setattr(
            ac._log, level,
            lambda event, _level=level, **kw: out.append({"event": event, "level": _level, **kw}),
        )
    return out


def _checked(logged: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return [e for e in logged if e["event"] == ac.CHECKED_EVENT]


# ── A scripted model ────────────────────────────────────────────────────────


class ScriptedClient(FunctionInvocationLayer, ChatMiddlewareLayer, BaseChatClient):
    """Answers each request from one step: a list of ``("text", words)`` and
    ``("call", name, args)`` parts. A text streams as two deltas."""

    def __init__(self, script: list[list[tuple[Any, ...]]]) -> None:
        super().__init__()
        self.script = list(script)
        self.requests = 0
        #: What each request sent: the instructions, the tools and each message.
        self.seen: list[Any] = []

    def _inner_get_response(self, *, messages, stream, options, **kwargs):  # type: ignore[override]
        self.requests += 1
        n = self.requests
        self.seen.append((
            str(options.get("instructions") or ""),
            sorted(str(getattr(t, "name", t)) for t in options.get("tools") or ()),
            [(str(getattr(m.role, "value", m.role)),
              [(c.type, getattr(c, "text", None), getattr(c, "name", None),
                str(getattr(c, "result", None)) if c.type == "function_result" else None)
               for c in m.contents])
             for m in messages],
        ))
        contents: list[Content] = []
        for part in self.script.pop(0):
            if part[0] == "text":
                contents.append(Content.from_text(part[1]))
            else:
                contents.append(Content.from_function_call(
                    f"c{n}-{len(contents)}", part[1], arguments=json.dumps(part[2])))
        if stream:
            async def _gen() -> AsyncIterable[ChatResponseUpdate]:
                for content in contents:
                    pieces = ([Content.from_text(content.text[:len(content.text) // 2]),
                               Content.from_text(content.text[len(content.text) // 2:])]
                              if content.type == "text" else [content])
                    for piece in pieces:
                        yield ChatResponseUpdate(contents=[piece], role="assistant",
                                                 response_id=f"r{n}", message_id="m")
            return self._build_response_stream(_gen())

        async def _one() -> ChatResponse:
            return ChatResponse(messages=[Message("assistant", contents)], response_id=f"r{n}")
        return _one()


STREAMS = pytest.mark.parametrize("stream", [True, False], ids=["stream", "batch"])


async def read_thread(email_id: str) -> str:
    """Read one mail thread."""
    return "The thread says that the invoice is paid."


def _send_tool(results: list[str]) -> Any:
    async def send_email(to: str, subject: str = "", attachments: str = "") -> str:
        """Send one email."""
        return results.pop(0)
    return send_email


def _orchestrator(script: list[list[tuple[Any, ...]]], tools: list[Any]) -> Agent:
    return Agent(client=ScriptedClient(script), name="orchestrator", tools=tools)


def _seam(agent: Any, name: str = "orchestrator") -> Any:
    agents = [agent]
    _tool_injection._apply_claim_check(agents, name)
    return agents[0]


async def _run(view: Any, *, stream: bool, session: Any = None) -> str:
    if stream:
        return "".join([u.text or "" async for u in view.run("hi", stream=True, session=session)])
    return (await view.run("hi", session=session)).text


def _providers(view: Any) -> list[Any]:
    return [p for p in view.context_providers if isinstance(p, ac.ClaimCheckProvider)]


# ── 1. The flag ─────────────────────────────────────────────────────────────


def test_the_flag_is_off_by_default(monkeypatch):
    from acb_common import get_settings
    from acb_common.settings import Settings

    assert Settings.model_fields["chat_claim_check_orgs"].default == ""
    monkeypatch.setattr(get_settings(), "chat_claim_check_orgs", "")
    assert not ac.claim_check_on(ORG)


def test_the_flag_names_an_org_or_every_org(monkeypatch):
    from acb_common import get_settings

    monkeypatch.setattr(get_settings(), "chat_claim_check_orgs", f" {ORG.upper()} , org-b")
    assert ac.claim_check_on(ORG)
    assert not ac.claim_check_on("org-other")
    monkeypatch.setattr(get_settings(), "chat_claim_check_orgs", "*")
    assert ac.claim_check_on("org-other")


def test_the_flag_fails_closed(monkeypatch):
    import acb_common

    def _broken() -> Any:
        raise RuntimeError("no settings")

    monkeypatch.setattr(acb_common, "get_settings", _broken)
    assert not ac.claim_check_on(ORG)


def test_the_flag_is_named_in_the_three_places_of_a_flag():
    from acb_common import env_guard
    from gateway import flag_reload

    env = (ROOT / ".env.example").read_text(encoding="utf-8")
    assert "\nCHAT_CLAIM_CHECK_ORGS=\n" in env
    # Only the operator writes it: no tenant route may set it.
    assert env_guard.is_platform_env("CHAT_CLAIM_CHECK_ORGS")
    assert flag_reload.reloadable("CHAT_CLAIM_CHECK_ORGS")
    doc = (ROOT / "docs" / "flag_reload.md").read_text(encoding="utf-8")
    assert "`CHAT_CLAIM_CHECK_ORGS` turns on the claim check" in doc


# ── 2. The seam ─────────────────────────────────────────────────────────────


def test_flag_off_attaches_nothing_and_the_view_does_not_change(monkeypatch):
    from acb_common import get_settings

    monkeypatch.setattr(get_settings(), "chat_claim_check_orgs", "")
    agent = _orchestrator([], [])
    assert _seam(agent) is agent
    assert not _providers(agent)


def test_flag_on_gives_the_orchestrator_one_provider(flag_on):
    agent = _orchestrator([], [read_thread])
    view = _seam(agent)
    assert view is not agent
    assert len(_providers(view)) == 1
    assert not _providers(agent), "the shared agent object never changes"


def test_an_org_off_the_list_gets_none(monkeypatch, flag_on):
    from acb_common import get_settings

    monkeypatch.setattr(get_settings(), "chat_claim_check_orgs", "org-other")
    agent = _orchestrator([], [])
    assert _seam(agent) is agent


@pytest.mark.parametrize("name", ["crm-assistant", "email-assistant", "worker", "", None])
def test_another_agent_gets_none(flag_on, name):
    agent = Agent(client=ScriptedClient([]), name=str(name or "x"))
    assert _seam(agent, name) is agent


def test_a_copilot_agent_and_a_plain_object_are_left_alone(flag_on):
    plain = object()
    assert _seam(plain) is plain


def test_the_seam_sits_between_the_call_id_and_the_progress_notice():
    """``test_progress_notice.py`` keeps the notice and the number check last,
    and ``test_resume_stored_call.py`` keeps the call id after the steer step.
    The claim check sits between the call id and the notice."""
    tree = ast.parse((ROOT / "apps/services/orchestrator/orchestrator/_tool_injection.py")
                     .read_text(encoding="utf-8-sig"))
    inject = next(n for n in ast.walk(tree)
                  if isinstance(n, ast.FunctionDef) and n.name == "_inject_agent_tools")
    names = [getattr(getattr(getattr(stmt, "value", None), "func", None), "id", None)
             for stmt in inject.body]
    assert names.count("_apply_claim_check") == 1
    assert (names.index("_apply_resume_call_id") < names.index("_apply_claim_check")
            < names.index("_apply_progress_notice"))
    assert names[-3:-1] == ["_apply_progress_notice", "_apply_answer_check"]


def test_no_other_module_attaches_the_provider():
    users = set()
    for base in ("apps", "packages"):
        for path in (ROOT / base).rglob("*.py"):
            if ".venv" in path.parts:
                continue
            text = path.read_text(encoding="utf-8-sig", errors="replace")
            if "ClaimCheckProvider(" in text and path.name != "answer_claims.py":
                users.add(path.relative_to(ROOT).as_posix())
    assert users == {"apps/services/orchestrator/orchestrator/_tool_injection.py"}


def test_the_provider_adds_no_middleware():
    """The check reads the run at its end. It holds no model step and wraps no
    tool call, so it cannot change an answer (D-HRD-6)."""
    assert ac.ClaimCheckProvider.before_run is ContextProvider.before_run


@STREAMS
async def test_the_answer_and_the_requests_do_not_change(stream, flag_on, logged):
    def _script() -> list[list[tuple[Any, ...]]]:
        return [[("text", "I'll check the thread."), ("call", "read_thread", {"email_id": "e1"})],
                [("text", "I sent the email. I'll follow up tomorrow.")]]

    plain = _orchestrator(_script(), [read_thread])
    checked = _orchestrator(_script(), [read_thread])
    assert await _run(plain, stream=stream) == await _run(_seam(checked), stream=stream)
    assert plain.client.requests == checked.client.requests == 2
    # Fix round 1 (P3): each request holds the same instructions, the same
    # tools and the same messages, not only the same count.
    assert plain.client.seen == checked.client.seen
    assert len(_checked(logged)) == 1


def test_the_delegation_names_and_the_seam_templates_reach_the_provider(flag_on):
    async def ask_email_assistant(task: str) -> str:
        """Hand a task to the email assistant."""
        return "Done."

    ask_email_assistant.__delegates_to__ = "email-assistant"  # type: ignore[attr-defined]
    view = _seam(_orchestrator([], [ask_email_assistant, read_thread]))
    [provider] = _providers(view)
    assert "ask_email_assistant" in provider.delegations
    assert "read_thread" not in provider.delegations
    assert "call_agent" in provider.delegations
    assert provider.not_acted == (_tool_injection.PACK_CALL_REFUSED,)


# ── 3. MAF gives the whole run to after_run, once ───────────────────────────


class _Probe(ContextProvider):
    def __init__(self) -> None:
        super().__init__("hrd13-probe")
        self.runs: list[Any] = []

    async def after_run(self, *, agent, session, context, state) -> None:  # type: ignore[override]
        self.runs.append(context.response)


@STREAMS
async def test_maf_calls_after_run_once_with_the_whole_run(stream):
    """The design rests on this. If MAF stops it, the check needs a recorder
    middleware, so this test names the assumption."""

    async def send_email(to: str) -> str:
        """Send one email."""
        raise RuntimeError("the send broke")

    probe = _Probe()
    agent = Agent(client=ScriptedClient([
        [("text", "I'll check the thread."), ("call", "read_thread", {"email_id": "e1"})],
        [("call", "send_email", {"to": "a"}), ("call", "nope_tool", {})],
        [("text", "It was not sent.")],
    ]), name="orchestrator", tools=[read_thread, send_email], context_providers=[probe])
    await _run(agent, stream=stream)
    assert len(probe.runs) == 1, "once for each run, never for each model step"
    blocks, calls = ac.read_run(probe.runs[0])
    assert [b for _at, b in blocks] == ["I'll check the thread.", "It was not sent."]
    assert [(c.name, c.outcome) for c in calls] == [
        ("read_thread", ac.ACTED), ("send_email", ac.NOT_ACTED), ("nope_tool", ac.NOT_ACTED)]
    assert blocks[0][0] < calls[0].at < calls[1].at < blocks[1][0]


@STREAMS
async def test_a_promise_kept_later_in_the_same_run_does_not_flag(stream, flag_on, logged):
    agent = _orchestrator([
        [("text", "I'll check the thread."), ("call", "read_thread", {"email_id": "e1"})],
        [("text", "The thread says that the invoice is paid.")],
    ], [read_thread])
    await _run(_seam(agent), stream=stream)
    [line] = _checked(logged)
    assert line[C] == 0 and line["passed"] == 1 and line["claims"] == 1


@STREAMS
async def test_a_promise_with_no_later_call_flags(stream, flag_on, logged):
    agent = _orchestrator([[("text", "I'll check the thread and get back to you.")]], [read_thread])
    await _run(_seam(agent), stream=stream)
    [line] = _checked(logged)
    assert line[C] == 1 and line["rules"] == [C]


# ── 4. The verdicts, with no model ──────────────────────────────────────────

ACTED, NOT_ACTED, UNKNOWN = ac.ACTED, ac.NOT_ACTED, ac.UNKNOWN

#: (id, answer, calls as (name, place, outcome), the expected counts, the
#: place of the answer). A missing count is 0. An ``abstain`` entry names the
#: exact reasons.
CASES: list[tuple[str, str, list[tuple[str, int, str]], dict[str, Any], int]] = [
    # A: an act with no outcome.
    ("a_sent_backed", "I sent the email to Ravi.", [("send_email", 1, ACTED)], {"passed": 1}, 0),
    ("a_sent_alone", "I sent the email to Ravi.", [], {A: 1}, 0),
    ("a_created_declined", "I have created the task for Friday.",
     [("create_task", 1, NOT_ACTED)], {A: 1}, 0),
    ("a_we_added", "We added two tasks to the project.", [], {A: 1}, 0),
    ("a_new_task", "I created a new task for the invoice.", [], {A: 1}, 0),
    ("a_marked_done", "I've marked it as done.", [("complete", 1, ACTED)], {"passed": 1}, 0),
    ("a_two_acts", "I created the task and set a reminder for Friday.",
     [("create_task", 1, ACTED)], {"passed": 1, A: 1}, 0),
    ("a_forwarded", "I forwarded the invoice to Sharad.", [("forward_email", 1, ACTED)],
     {"passed": 1}, 0),
    ("a_unknown_abstains", "I sent the email.", [("send_email", 1, UNKNOWN)],
     {"abstained": 1, "abstain": [ac.UNKNOWN_OUTCOME]}, 0),
    ("a_earlier_abstains", "As I said earlier, I sent the email.", [],
     {"abstained": 1, "abstain": [ac.EARLIER_TURN]}, 0),
    # Fix round 1 (P2): a time word of an earlier turn abstains, and an acted
    # call of the kind still passes first.
    ("a_yesterday", "I sent the email yesterday.", [],
     {"abstained": 1, "abstain": [ac.EARLIER_TURN]}, 0),
    ("a_this_morning", "This morning I created the task for the audit.", [],
     {"abstained": 1, "abstain": [ac.EARLIER_TURN]}, 0),
    ("a_on_monday", "I set a reminder on Monday.", [],
     {"abstained": 1, "abstain": [ac.EARLIER_TURN]}, 0),
    ("a_already_backed", "I've already sent the email.", [("send_email", 1, ACTED)],
     {"passed": 1}, 0),
    # Fix round 1 (P1-b): a make word gives task.create only when its object
    # IS a new task. Each case follows an acted call of its real tool.
    ("k_comment", "I added a comment to the task.", [("comment", 1, ACTED)],
     {"abstained": 1, "abstain": [ac.UNKNOWN_KIND]}, 1),
    ("k_cycle", "I added the task to the Q3 cycle.", [("plan_cycle", 1, ACTED)],
     {"abstained": 1, "abstain": [ac.UNKNOWN_KIND]}, 1),
    ("k_changes", "I made the changes to the task.", [("update_task", 1, ACTED)],
     {"abstained": 1, "abstain": [ac.UNKNOWN_KIND]}, 1),
    ("k_tag", "I added the urgent tag to the task.", [("update_task", 1, ACTED)],
     {"abstained": 1, "abstain": [ac.UNKNOWN_KIND]}, 1),
    ("k_watcher", "I added Priya as a watcher on the task.", [("watch", 1, ACTED)],
     {"abstained": 1, "abstain": [ac.UNKNOWN_KIND]}, 1),
    # More probes of my own: a note, a file, a label, a link and a move.
    ("k_note", "I added a note to the reminder.", [], {"abstained": 1}, 1),
    ("k_file", "I added the PDF to the task.", [("attach_to_task", 1, ACTED)],
     {"abstained": 1}, 1),
    ("k_link", "I made a link between the two tasks.", [("link_tasks", 1, ACTED)],
     {"abstained": 1}, 1),
    ("k_move", "I added the task to your personal list.", [("move_task", 1, ACTED)],
     {"abstained": 1}, 1),
    ("k_no_object", "I made the call to the vendor.", [], {}, 0),
    # B: a doubt fires only against an acted call of its kind (fix round 1,
    # PROPOSED to the owner). With no call of its kind it abstains.
    ("b_not_sent_backed", "The email was not sent.", [("send_email", 1, NOT_ACTED)],
     {"passed": 1}, 0),
    ("b_not_sent_but_sent", "It was not sent.", [("send_email", 1, ACTED)], {B: 1}, 0),
    ("b_neither_alone", "Neither email went out.", [],
     {"abstained": 1, "abstain": [ac.NO_CALL]}, 0),
    ("b_could_not_create", "I could not create the task.", [("create_task", 1, NOT_ACTED)],
     {"passed": 1}, 0),
    ("b_nothing_sent", "Nothing was sent.", [("send_draft", 1, NOT_ACTED)], {"passed": 1}, 0),
    ("b_reminder_not_set", "The reminder was not set.", [("set_reminder", 1, ACTED)], {B: 1}, 0),
    ("b_draft_saved", "I've drafted the reply and saved it in Drafts. Nothing was sent.",
     [("draft_reply", 1, ACTED)], {"abstained": 1, "abstain": [ac.NO_CALL]}, 1),
    ("b_after_a_read", "Your email to Ravi was not sent.", [("read_email", 1, ACTED)],
     {"abstained": 1, "abstain": [ac.NO_CALL]}, 1),
    ("b_not_yet", "I have not sent it yet.", [],
     {"abstained": 1, "abstain": [ac.NO_CALL]}, 0),
    # C: a promise with no follow-up.
    ("c_follow_up", "I'll follow up with Ravi tomorrow.", [], {C: 1}, 0),
    ("c_reminder_made", "I'll get back to you on Friday.", [("set_reminder", 1, ACTED)],
     {"passed": 1}, 0),
    ("c_task_made", "I'll let you know when the report is ready.",
     [("create_task", 1, ACTED)], {"passed": 1}, 0),
    ("c_read_after", "I'll check the thread.", [("read_thread", 1, ACTED)], {"passed": 1}, 0),
    ("c_read_before", "I'll check the thread.", [("read_thread", 1, ACTED)], {C: 1}, 1),
    ("c_act_kind_ran", "I'll send it later.", [("send_email", 1, NOT_ACTED)], {"passed": 1}, 1),
    ("c_reminder_declined", "I'll remind you on Monday.", [("set_reminder", 1, NOT_ACTED)],
     {"passed": 1}, 1),
    # The doubt of an earlier turn: it always fires.
    ("e_retraction", "My earlier message said the task was created, but that was wrong.",
     [("create_task", 1, ACTED)], {E: 1}, 0),
    ("e_true_retraction", "I said it was sent earlier, but it was not sent.",
     [("send_email", 1, NOT_ACTED)], {E: 1}, 0),
    # The exclusions give no claim.
    ("x_question_shall", "Shall I send the email now?", [], {}, 0),
    ("x_question_want", "Do you want me to create a task for this", [], {}, 0),
    ("x_condition_once", "Once you approve the card, I'll send it.", [], {}, 0),
    ("x_condition_if", "If you like, I'll set a reminder.", [], {}, 0),
    ("x_quote_line", "> I sent the invoice yesterday.", [], {}, 0),
    ("x_quote_fence", "```\nI sent the email.\n```", [], {}, 0),
    ("x_quote_wrote", "Ravi wrote: I sent the invoice yesterday.", [], {}, 0),
    ("x_quote_says", "Priya says: we added the task.", [], {}, 0),
    ("x_quote_span", 'Here is his note: "I sent the invoice."', [], {}, 0),
    ("x_bare_passive_report", "Your earlier message said it was sent.", [], {}, 0),
    ("x_bare_passive", "The email was sent at 10 am.", [], {}, 0),
    ("x_modal_past", "I would have sent it, but the card closed.", [], {}, 0),
    ("x_modal_can", "I can send it later.", [], {}, 0),
    ("x_ask_to_tell", "Let me know if you need anything else.", [], {}, 0),
    ("x_will_not", "I will not send it.", [], {}, 0),
    ("x_card", "I've sent you a card to approve.", [], {}, 0),
    ("x_no_act", "I read the thread and found two replies.", [], {}, 0),
]


@pytest.mark.parametrize(("answer", "calls", "expected", "at"),
                         [c[1:] for c in CASES], ids=[c[0] for c in CASES])
def test_the_verdict_of_each_sentence(answer, calls, expected, at):
    line = check_run([(at, answer)], [RunCall(*c) for c in calls]).fields("orchestrator")
    got = {key: line[key] for key in (*ac.RULES, "passed", "abstained")}
    assert got == {key: expected.get(key, 0) for key in got}, answer
    if "abstain" in expected:
        assert line["abstain"] == expected["abstain"], answer


def test_the_table_covers_each_class_and_each_exclusion():
    ids = [c[0] for c in CASES]
    assert len(ids) >= 20 and len(set(ids)) == len(ids)
    for head in ("a_", "b_", "c_", "e_", "k_", "x_"):
        assert sum(i.startswith(head) for i in ids) >= 2, head


@pytest.mark.parametrize(("calls", "reason"), [
    ([("send_email", 1, UNKNOWN)], ac.UNKNOWN_OUTCOME),
    ([], ac.EARLIER_TURN),
])
def test_an_abstain_names_its_reason(calls, reason):
    text = "As I said earlier, I sent the email." if reason == ac.EARLIER_TURN else "I sent it."
    line = check_run([(0, text)], [RunCall(*c) for c in calls]).fields("orchestrator")
    assert line["abstain"] == [reason] and line["abstained"] == 1


def test_curly_quotes_read_as_plain_quotes():
    """A model writes curly quotes. The apostrophe of "I've" still reads, and
    a span in curly double quotes is quoted text."""
    right, left_d, right_d = chr(0x2019), chr(0x201C), chr(0x201D)
    line = check_run([(0, f"I{right}ve sent the email.")], []).fields("orchestrator")
    assert line[A] == 1
    line = check_run([(0, f"He said {left_d}I sent the email{right_d}.")], []).fields("orchestrator")
    assert line["claims"] == 0


def test_a_text_over_the_cap_is_skipped_with_its_length_bucket():
    text = "I sent the email. " * 500
    assert len(text) > ac.MAX_CHARS
    line = check_run([(0, text)], []).fields("orchestrator")
    assert line["claims"] == 0 and line["abstain"] == [ac.TOO_LONG]
    assert line["length"] == "8001+"


def test_a_text_of_mostly_other_letters_is_skipped():
    line = check_run([(0, "मैंने ईमेल भेज दिया है। I sent it.")], []).fields("orchestrator")
    assert line["claims"] == 0 and line["abstain"] == [ac.LANG_SKIPPED]


def test_a_run_with_no_text_gives_no_claim():
    line = check_run([], [RunCall("send_email", 1, ACTED)]).fields("orchestrator")
    assert line["claims"] == 0 and line["abstain"] == [ac.NO_TEXT] and line["acted"] == 1


# ── 5. The fixture of run a8ad07f3 (task 3594) ──────────────────────────────

RETRACTION = " Your earlier message said it was sent, and that was wrong."
FIXTURE_ANSWER = (
    "Neither email went out.\n\n"
    "- Email to Payal Dave and Nikita Kadam: you declined the card, so it was not sent."
    + RETRACTION + "\n"
    "- Report to Vijay and Sharad: the PDF is ready, but the mail could not attach it..."
)
#: The three failed sends of turn 2: a duplicate card that the member
#: declined, and two sends of a file that is not in the member's workspace.
DECLINED = ("Declined: the member said no on the card, so nothing was done. "
            "The email was not sent.")
NOT_A_FILE = "Not sent. The attachment Report.pdf is not a file in your own workspace."


def _turn_two(answer: str) -> Agent:
    return _orchestrator([
        [("call", "send_email", {"to": "payal@example.com"}),
         ("call", "send_email", {"to": "vijay@example.com", "attachments": "Report.pdf"}),
         ("call", "send_email", {"to": "sharad@example.com", "attachments": "Report.pdf"})],
        [("text", answer)],
    ], [_send_tool([DECLINED, NOT_A_FILE, NOT_A_FILE])])


@STREAMS
async def test_the_fixture_gives_the_exact_set(stream, flag_on, logged):
    """Zero A. "Neither email went out" is NOT flagged, because three failed
    sends are in the run. ONE ``earlier_turn_doubt``: the retraction of turn 1.
    It is a TRUE retraction, and it still fires, so the shadow count is a
    count of retractions, not of false claims."""
    assert await _run(_seam(_turn_two(FIXTURE_ANSWER)), stream=stream) == FIXTURE_ANSWER
    [line] = _checked(logged)
    assert {rule: line[rule] for rule in ac.RULES} == {A: 0, B: 0, C: 0, E: 1}
    assert line["rules"] == [E]
    assert line["passed"] == 2 and line["claims"] == 3 and line["abstained"] == 0
    assert line["not_acted"] == 3 and line["acted"] == 0 and line["tools"] == ["send_email"]


@STREAMS
async def test_control_1_the_answer_without_the_retraction_gives_no_flag(stream, flag_on, logged):
    answer = FIXTURE_ANSWER.replace(RETRACTION, "")
    assert RETRACTION not in answer
    await _run(_seam(_turn_two(answer)), stream=stream)
    [line] = _checked(logged)
    assert {rule: line[rule] for rule in ac.RULES} == {A: 0, B: 0, C: 0, E: 0}
    assert line["rules"] == [] and line["passed"] == 2


@STREAMS
async def test_control_2_turn_one_with_an_acted_send_passes(stream, flag_on, logged):
    agent = _orchestrator([
        [("call", "send_email", {"to": "payal@example.com"})],
        [("text", "I sent the email to Payal Dave and Nikita Kadam.")],
    ], [_send_tool(["Sent to Payal Dave and Nikita Kadam."])])
    await _run(_seam(agent), stream=stream)
    [line] = _checked(logged)
    assert line["passed"] == 1 and line["rules"] == [] and line["acted"] == 1


# ── 6. One log line, and no text of the run in it ───────────────────────────

FIELDS = {
    "event", "level", "mode", "agent", "claims", "passed", "abstained", *ac.RULES,
    "calls", "delegations", *ac.OUTCOMES, "kinds", "rules", "tools", "abstain", "length",
}
BUCKETS = {"0", "1-200", "201-1000", "1001-4000", "4001-8000", "8001+"}


async def test_one_line_for_each_run_holds_no_text_of_the_run(flag_on, logged):
    agent = _orchestrator([
        [("call", "send_email", {"to": f"{SECRET}@example.com", "subject": SECRET}),
         ("call", f"lookup_{SECRET}", {"q": SECRET})],
        [("text", f"I sent the email to {SECRET}@example.com. I'll follow up tomorrow.")],
    ], [_send_tool([f"Sent to {SECRET}@example.com, id {SECRET}."])])
    await _run(_seam(agent), stream=True)
    assert len(logged) == 1, "ONE line for each run"
    [line] = logged
    assert line["event"] == ac.CHECKED_EVENT and line["level"] == "info"
    assert SECRET not in repr(logged)
    assert set(line) == FIELDS
    assert line["mode"] == "shadow" and line["agent"] == "orchestrator"
    assert set(line["kinds"]) <= set(ac.KINDS) and set(line["rules"]) <= set(ac.RULES)
    assert set(line["tools"]) <= set(ac.ACT_TOOLS) and set(line["abstain"]) <= set(ac.ABSTAIN_REASONS)
    assert line["length"] in BUCKETS
    for key in FIELDS - {"event", "level", "mode", "agent", "kinds", "rules", "tools",
                         "abstain", "length"}:
        assert isinstance(line[key], int), key
    assert line["calls"] == 2 and line["tools"] == ["send_email"]


def test_the_event_name_is_not_the_number_check_name():
    """``scripts/crm_answer_check_replay.py`` reads the LAST
    ``answer_check.checked`` line of a run. A second writer of that name would
    break the replay."""
    assert ac.CHECKED_EVENT == "answer_claims.checked"
    src = (ROOT / "packages/acb_skills/acb_skills/answer_claims.py").read_text(encoding="utf-8")
    assert '"answer_check.checked"' not in src


# ── 7. after_run never raises ───────────────────────────────────────────────


class _Witness(ContextProvider):
    """A provider before the check in the list, so MAF runs its ``after_run``
    AFTER the check's (reverse order)."""

    def __init__(self) -> None:
        super().__init__("hrd13-witness")
        self.after = 0

    async def after_run(self, *, agent, session, context, state) -> None:  # type: ignore[override]
        self.after += 1


@STREAMS
async def test_a_raise_in_the_check_never_reaches_the_run(stream, flag_on, logged, monkeypatch):
    """The REAL provider order: the injection views, then the history and the
    run context of ``executor._agent_for_run``. Mutation: drop the guard of
    ``after_run`` -> the run raises, the witness never runs -> red."""
    from orchestrator._native_run_context import RunContextProvider, agent_with_providers
    from orchestrator.native_session_store import HISTORY_SOURCE_ID, SessionHistoryProvider

    def _boom(*_args: Any, **_kwargs: Any) -> Any:
        raise RuntimeError(SECRET)

    monkeypatch.setattr(ac, "check_run", _boom)
    witness = _Witness()
    agents = [agent_with_providers(_orchestrator([[("text", "I sent the email.")]], []), [witness])]
    _tool_injection._apply_claim_check(agents, "orchestrator")
    view = executor._agent_for_run(agents[0], RunContextProvider("ctx"),
                                   history=SessionHistoryProvider())
    assert [type(p).__name__ for p in view.context_providers] == [
        "_Witness", "ClaimCheckProvider", "SessionHistoryProvider", "RunContextProvider"]
    session = AgentSession()
    assert await _run(view, stream=stream, session=session) == "I sent the email."
    assert session.state[HISTORY_SOURCE_ID]["messages"], "the session save still ran"
    assert witness.after == 1, "each provider after the check still ran"
    assert [(e["event"], e["error"]) for e in logged] == [(ac.FAILED_EVENT, "RuntimeError")]
    assert SECRET not in repr(logged)


# ── 8. A delegated run and a resume run abstain ─────────────────────────────


@STREAMS
async def test_a_delegated_run_abstains(stream, flag_on, logged):
    """The calls of a sub-run stay inside the delegation tool
    (``executor._run_sub_agent_streaming``), so the run cannot see the send."""

    async def ask_email_assistant(task: str) -> str:
        """Hand a task to the email assistant."""
        return "The email assistant finished."

    ask_email_assistant.__delegates_to__ = "email-assistant"  # type: ignore[attr-defined]
    agent = _orchestrator([[("call", "ask_email_assistant", {"task": "send it"})],
                           [("text", "I sent the email.")]], [ask_email_assistant])
    await _run(_seam(agent), stream=stream)
    [line] = _checked(logged)
    assert line["abstain"] == [ac.DELEGATED] and line["abstained"] == 1 and line[A] == 0
    assert line["delegations"] == 1


async def test_a_resume_run_abstains_and_a_plain_run_flags(flag_on, logged):
    """A continuation run calls the stored tool with no middleware
    (orchestrator contract 22b), so its act is not in the response."""
    from acb_skills.write_artifact import artifact_context_scope, bind_artifact_context

    with artifact_context_scope():
        bind_artifact_context(**{ac.RESUMED_KEY: True})
        await _run(_seam(_orchestrator([[("text", "I created the task for Friday.")]], [])),
                   stream=True)
    await _run(_seam(_orchestrator([[("text", "I created the task for Friday.")]], [])),
               stream=True)
    resumed, plain = _checked(logged)
    assert resumed["abstain"] == [ac.RESUME] and resumed[A] == 0
    assert plain["abstain"] == [] and plain[A] == 1


def test_the_executor_marks_a_resume_run_right_after_the_prologue():
    run = inspect.getsource(executor.run_agent_stream)
    gate = run.index("if resume_call is not None:\n                async for _resume_line")
    mark = run.index(f"_derive_ctx({ac.RESUMED_KEY}=True)")
    assert gate < mark < run.index("_session_turn = await _begin_native_session(")
    assert run.count(f"{ac.RESUMED_KEY}=True") == 1


def _is_resume_gate(node: ast.AST) -> bool:
    test = getattr(node, "test", None)
    return (isinstance(node, ast.If) and isinstance(test, ast.Compare)
            and getattr(test.left, "id", None) == "resume_call"
            and isinstance(test.ops[0], ast.IsNot)
            and getattr(test.comparators[0], "value", 0) is None)


def test_the_resume_mark_sits_inside_the_resume_gate():
    """Fix round 1 (P3): the mark is a statement of the body of
    ``if resume_call is not None:``, right after the prologue loop, so a run
    that is no continuation never binds it. Mutation: move the mark out of
    the ``if`` -> red."""
    tree = ast.parse(inspect.getsource(executor).replace("\r\n", "\n"))
    run = next(n for n in ast.walk(tree)
               if isinstance(n, ast.AsyncFunctionDef) and n.name == "run_agent_stream")

    def _marks(nodes: Any) -> list[ast.Call]:
        return [n for n in nodes if isinstance(n, ast.Call)
                and getattr(n.func, "id", None) == "_derive_ctx"
                and any(k.arg == ac.RESUMED_KEY for k in n.keywords)]

    [gate] = [n for n in ast.walk(run) if _is_resume_gate(n)]
    body = gate.body
    assert isinstance(body[0], ast.AsyncFor), "the prologue loop comes first"
    assert getattr(body[0].iter.func, "id", None) == "_resume_prologue"
    [mark] = _marks(ast.walk(body[1]))
    assert isinstance(body[1], ast.Expr) and body[1].value is mark
    assert getattr(mark.keywords[0].value, "value", None) is True
    assert len(_marks(ast.walk(run))) == 1, "no second mark outside the gate"


# ── 9. The fence of the act table ───────────────────────────────────────────

#: Where each kind's tools live, and the mark of its not-acted answer.
HOMES: dict[str, tuple[str, tuple[str, ...]]] = {
    ac.TASK_CREATE: ("apps/skills/skill-projects/skill_projects",
                     ("_not_confirmed(", "card_not_done_text(")),
    ac.TASK_UPDATE: ("apps/skills/skill-projects/skill_projects",
                     ("_not_confirmed(", "card_not_done_text(")),
    ac.REMINDER_SET: ("apps/skills/skill-projects/skill_projects",
                      ("_not_confirmed(", "card_not_done_text(")),
    ac.EMAIL_SEND: ("apps/agents/agent-email-assistant", ("_not_done(", '"Not sent.')),
}
EMAIL_AGENT = ROOT / "apps" / "agents" / "agent-email-assistant" / "agents.py"


def test_the_email_sends_are_the_five_card_sends():
    from acb_skills import egress

    expected = {"send_email", "forward_email", "send_draft", "compose_reply", "schedule_send"}
    assert expected == ac.EMAIL_SEND_TOOLS
    for tool in ac.EMAIL_SEND_TOOLS:
        assert tool in egress.CARD_BOUND_SEND_TOOLS or tool in egress.HELD_CARD_SEND_TOOLS


def test_the_projects_acts_are_the_write_kinds_of_slice_one():
    from acb_skills.tool_packs import WRITE_KINDS

    projects = {t: k for t, k in ac.ACT_TOOLS.items() if k != ac.EMAIL_SEND}
    assert projects == {t: k for t, k in WRITE_KINDS.items() if k in ac.PROJECTS_ACT_KINDS}
    assert projects["complete"] == ac.TASK_UPDATE
    assert projects["set_reminder"] == ac.REMINDER_SET
    assert projects["create_task"] == ac.TASK_CREATE
    assert set(ac.ACT_TOOLS.values()) == set(ac.ACT_KINDS)


@pytest.mark.parametrize("tool", sorted(ac.ACT_TOOLS))
def test_each_act_tool_is_a_pack_tool_with_its_not_acted_lead(tool):
    from acb_skills import skill_families as sf

    assert any(tool in sf.SKILL_FAMILIES[slug]["tools"] for slug in sf.pack_slugs()), tool
    root, marks = HOMES[ac.ACT_TOOLS[tool]]
    homes = [p for p in (ROOT / root).rglob("*.py")
             if f"async def {tool}(" in p.read_text(encoding="utf-8")]
    assert len(homes) == 1, (tool, homes)
    source = homes[0].read_text(encoding="utf-8")
    assert any(mark in source for mark in marks), (tool, homes[0])


@functools.cache
def _email_agent() -> Any:
    spec = importlib.util.spec_from_file_location("email_hrd13_agent", EMAIL_AGENT)
    assert spec and spec.loader
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def test_each_not_acted_text_of_the_families_reads_as_not_acted():
    """The real text builders of each family, through the classifier."""
    from acb_common.tool_refusals import FAILED, REFUSED
    from acb_skills import ask_tools as at
    from acb_skills.whatsapp_acts import EMAIL_WAITING_TEXT, PARKED_TEXT
    from skill_projects import writes

    email = _email_agent()
    for outcome in at.CARD_OUTCOMES:
        if outcome == at.CARD_APPROVED:
            continue
        assert call_outcome(at.card_outcome_text(outcome, "The email was not sent.")) == NOT_ACTED
        token = at._LAST_OUTCOME.set(outcome)
        try:
            assert call_outcome(writes._not_confirmed()) == NOT_ACTED, outcome
            assert call_outcome(email._not_done("The email was not sent.")) == NOT_ACTED, outcome
        finally:
            at._LAST_OUTCOME.reset(token)
    for text in (email._NO_CARD_CHANNEL, "Not sent. No recipient.", f"{REFUSED} 403",
                 f"{FAILED}RuntimeError).", PARKED_TEXT, EMAIL_WAITING_TEXT,
                 "Nothing was sent or saved.", "Error: Function failed."):
        assert call_outcome(text) == NOT_ACTED, text
    assert call_outcome("Sent.", "KeyError") == NOT_ACTED, "a raise"
    assert call_outcome("") == UNKNOWN
    assert call_outcome("Sent.", found=False) == UNKNOWN
    # Fix round 1: "Not forwarded." and "Not scheduled." are the real leads
    # of two act tools. Each is a literal of the email agent.
    source = EMAIL_AGENT.read_text(encoding="utf-8")
    for lead, tool in (("Not forwarded.", "forward_email"), ("Not scheduled.", "schedule_send")):
        assert f'"{lead}' in source or f'f"{lead}' in source, lead
        assert call_outcome(f"{lead} No recipient.", tool=tool) == NOT_ACTED, lead


# ── 9a. Fix round 1 (P1-a): the held and refused templates, and acted ───────


def _seam_templates() -> tuple[str, ...]:
    return (_tool_injection.PACK_CALL_REFUSED,)


@pytest.mark.parametrize("template", [*ac.NOT_ACTED_TEMPLATES, *_seam_templates()],
                         ids=lambda t: t[:32])
@pytest.mark.parametrize("tool", ["send_email", "create_task"])
def test_each_held_or_refused_template_is_not_acted(template, tool):
    """Each REAL constant, filled as the platform fills it. Several templates
    share one head ("{name} is off now."). Mutation: drop each template of a
    head -> its result rates ``unknown`` -> red. Drop one constant only ->
    ``test_the_templates_are_the_real_constants`` is red."""
    text = template.format(name=tool, tool=tool)
    heads = ac.template_heads(_seam_templates())
    assert call_outcome(text, tool=tool, extra=heads) == NOT_ACTED, text[:60]


def test_the_templates_are_the_real_constants():
    from acb_skills import egress, tool_packs

    held = {name for name in dir(egress)
            if name.startswith(("EGRESS_HELD_", "EGRESS_WITHHELD_")) and name.endswith(
                ("_ANSWER", "_ANSWER_CARDS", "_NO_WRITES", "_NO_WRITES_CONTINUE", "_OTHER"))}
    assert held, "non-vacuity"
    for name in held:
        assert getattr(egress, name) in ac.NOT_ACTED_TEMPLATES, name
    for name in ("HANDED_WRITE_REFUSED", "HANDED_FAILED_WRITE_REFUSED", "PACK_ONLY_REFUSED"):
        assert getattr(tool_packs, name) in ac.NOT_ACTED_TEMPLATES, name


def test_a_held_send_does_not_back_a_claim_of_a_send():
    """The case of the review: the platform held ``send_email``, and the
    answer says that it sent. Rule A fires."""
    from acb_skills.egress import EGRESS_HELD_PACK_ANSWER

    outcome = call_outcome(EGRESS_HELD_PACK_ANSWER.format(name="send_email"), tool="send_email")
    assert outcome == NOT_ACTED
    line = check_run([(0, "I sent the email to Ravi.")],
                     [RunCall("send_email", 1, outcome)]).fields("orchestrator")
    assert line[A] == 1 and line["rules"] == [A]


@STREAMS
async def test_a_pack_call_outside_its_run_is_not_acted_end_to_end(stream, flag_on, logged):
    """The seam passes ``PACK_CALL_REFUSED`` to the provider."""
    refused = _tool_injection.PACK_CALL_REFUSED.format(tool="send_email")
    agent = _orchestrator([[("call", "send_email", {"to": "ravi@example.com"})],
                           [("text", "I sent the email to Ravi.")]], [_send_tool([refused])])
    await _run(_seam(agent), stream=stream)
    [line] = _checked(logged)
    assert line["not_acted"] == 1 and line[A] == 1


def test_the_guard_text_of_a_failed_write_is_unknown():
    """HRD-3: a write that raised "may have changed part of it", so the act
    may have run. A read that raised changed nothing."""
    from acb_skills.pack_tool_guard import failure_text

    assert ac.PARTIAL_WRITE == "It may have changed part of it."
    write = failure_text(RuntimeError(), read_only=False)
    assert call_outcome(write, tool="send_email") == UNKNOWN
    assert call_outcome(failure_text(RuntimeError(), read_only=True), tool="send_email") == NOT_ACTED


#: The home module of each act tool that has an acted head.
HEAD_HOMES = {"send_email": EMAIL_AGENT, "compose_reply": EMAIL_AGENT,
              "forward_email": EMAIL_AGENT, "send_draft": EMAIL_AGENT,
              "schedule_send": EMAIL_AGENT}


@pytest.mark.parametrize("tool", sorted(ac.ACTED_HEADS))
def test_each_acted_head_is_a_literal_of_its_tool(tool):
    """``acted`` needs a head that the tool's own source writes. Mutation:
    change "Created:" in ``writes.py`` -> red."""
    home = HEAD_HOMES.get(tool, ROOT / "apps/skills/skill-projects/skill_projects/writes.py")
    source = home.read_text(encoding="utf-8")
    assert f"async def {tool}(" in source
    for head in ac.ACTED_HEADS[tool]:
        assert f'"{head}' in source or f'f"{head}' in source, (tool, head)
        assert call_outcome(f"{head} the work.", tool=tool) == ACTED, (tool, head)


def test_an_act_tool_with_no_known_head_rates_unknown():
    """It FAILS CLOSED: a result that matches nothing known is ``unknown``,
    never ``acted``. These are real answers of Projects tools."""
    assert call_outcome("A task needs a title.", tool="create_task") == UNKNOWN
    assert call_outcome("kind is action or notice.", tool="set_reminder") == UNKNOWN
    assert call_outcome("#12 is already done.", tool="complete") == UNKNOWN
    assert call_outcome("Reminder already set: Pay rent.", tool="set_reminder") == UNKNOWN
    assert call_outcome("Created task #12.", tool="create_task") == UNKNOWN
    assert call_outcome("Created:\n- #12 Pay the invoice", tool="create_task") == ACTED
    assert call_outcome("Moved #12 to Done.", tool="move_task") == UNKNOWN, "no head"
    # A tool outside the act table keeps the plain rule.
    assert call_outcome("The thread says hi.", tool="read_thread") == ACTED
    covered = set(ac.ACTED_HEADS)
    assert covered <= set(ac.ACT_TOOLS)
    assert {"send_email", "create_task", "update_task", "complete", "set_reminder"} <= covered


# ── 10. Fix round 1 (P2): the cost on the event loop ────────────────────────

ADVERSARIAL = {
    "i_then_sent": "i " * 3996 + "sent it",
    "dots": "." * 7999,
    "dot_space": ". " * 3999,
    "no_punctuation": "word " * 1599,
    "i_dot": "i. " * 2666,
    "i_sent_sentences": "I sent it. " * 727,
    "we_and": ("we created the task and " * 340)[:7999],
}


@pytest.mark.parametrize("name", sorted(ADVERSARIAL))
def test_an_adversarial_answer_reads_in_under_100_ms(name):
    import time

    text = ADVERSARIAL[name][:7999]
    assert len(text) <= ac.MAX_CHARS
    check_run([(0, "I sent it.")], [])  # warm the patterns
    start = time.perf_counter()
    check_run([(0, text)], [])
    assert time.perf_counter() - start < 0.1, name


def test_a_long_sentence_is_skipped_with_its_reason():
    line = check_run([(0, "i " * 400 + "sent it.")], []).fields("orchestrator")
    assert line["claims"] == 0 and line["abstain"] == [ac.LONG_SENTENCE]


def test_the_not_acted_leads_reuse_the_two_tables():
    from acb_skills import email_chat_context as ecc
    from acb_skills.ask_tools import CARD_NOT_DONE_LEADS

    assert ecc.NO_ACT_LEADS is ecc._NO_ACT, "the old name stays: open work imports it"
    assert set(ecc.NO_ACT_LEADS) <= set(ac.NOT_ACTED_LEADS)
    assert {f"{lead}:" for lead in CARD_NOT_DONE_LEADS} <= set(ac.NOT_ACTED_LEADS)
