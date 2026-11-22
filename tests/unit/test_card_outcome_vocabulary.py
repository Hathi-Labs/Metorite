"""HRD-7: ONE vocabulary of card outcomes (``chat_failure_modes.md`` C1, §7 rule 5).

Before HRD-7, Projects gave ONE string for a reject, a timeout and a cancel,
and email and CRM had their own texts. On 2026-11-11 the model said that a
card the member had discarded was "up for your decision". Now each outcome
has one word and one text, in ``ask_tools.CARD_OUTCOMES``. Each card family
takes its words from that table. ``request_confirmation`` keeps its bool and
its sentinels, and ``ask_tools.last_card_outcome`` says WHY a card did nothing.

Each rule names the mutation that turns it red (R7):

1. **The table**: eight outcomes, each lead and each text its own, and each
   text names what happened and a next step. Mutation: give TIMEOUT the text
   of REJECT -> ``test_no_two_outcomes_share_a_text``.
2. **The answer of the card records its outcome**: APPROVE, REJECT, TIMEOUT,
   REVISED, no card, no person and another member. Mutation: record
   ``declined`` for a timeout -> ``test_each_answer_records_its_outcome``.
3. **Each family maps each outcome to its own text from the table**:
   Projects, CRM and email. Mutation: make CRM ``_not_written`` answer one
   fixed text -> ``test_each_family_takes_each_text_from_the_table``.
4. **No module writes an old cancel text.** Mutation: put
   ``CANCELLED = "Cancelled — nothing was changed."`` back in ``writes.py`` ->
   ``test_no_module_writes_a_cancel_text_of_its_own``.
5. **The chat cards read the same leads** (``src/lib/cardOutcomes.ts``).

What rule 1 proves, and what it does not: each non-approved text holds "nothing
was done", which the measure of ``evals/one_assistant_measure`` reads as an
act that did not happen. Each next step is its own part that starts with an
imperative verb for the model (``NEXT_VERBS``). The check proves that every
text has a next step. It does not prove that the step is the right one.
"""
from __future__ import annotations

import ast
import asyncio
import contextvars
import functools
import importlib.util
import re
from pathlib import Path
from typing import Any

import pytest
from acb_skills import ask_tools as at
from orchestrator import executor

ROOT = Path(__file__).resolve().parents[2]
CARD_OUTCOMES_TS = ROOT / "workbench" / "control_plane" / "src" / "lib" / "cardOutcomes.ts"
STEER_TEST = ROOT / "tests" / "unit" / "test_steer_user_turn.py"
EMAIL_AGENT = ROOT / "apps" / "agents" / "agent-email-assistant" / "agents.py"
CRM_AGENT = ROOT / "apps" / "agents" / "agent-crm" / "agents.py"
PROJECTS = ROOT / "apps" / "skills" / "skill-projects" / "skill_projects"

#: The first word of each next step: an imperative for the model.
NEXT_VERBS = ("Do", "Tell", "Ask", "Read", "Show")

#: Each outcome of a card that did nothing.
NOT_DONE = tuple(k for k in at.CARD_OUTCOMES if k != at.CARD_APPROVED)

#: The outcomes that a tool reads after its card: each answer but a park and
#: a change since, which no caller reads yet (RESUME-4 and HRD-10).
READ_BY_TOOLS = (at.CARD_DECLINED, at.CARD_TIMED_OUT, at.CLOSED_BY_YOUR_MESSAGE,
                 at.CARD_NOT_SHOWN, at.CARD_OTHER_MEMBER)


def _decline_pattern() -> re.Pattern[str]:
    """The DECLINE words of ``test_steer_user_turn.py``, read from its source,
    so this file never holds a second copy."""
    m = re.search(r'^DECLINE = re\.compile\(r"([^"]+)", re\.IGNORECASE\)$',
                  STEER_TEST.read_text(encoding="utf-8"), re.MULTILINE)
    assert m, "test_steer_user_turn.py lost its DECLINE pattern"
    return re.compile(m.group(1), re.IGNORECASE)


def _nothing_happened() -> re.Pattern[str]:
    from evals.one_assistant_measure.checks import _NOTHING_HAPPENED

    return _NOTHING_HAPPENED


def _sentences(text: str) -> list[str]:
    return [s for s in re.split(r"(?<=[.!?])\s+", text.strip()) if s]


# ── 1. The table ─────────────────────────────────────────────────────────────


def test_the_table_holds_each_outcome_once() -> None:
    assert list(at.CARD_OUTCOMES) == [
        "approved", "declined", "timed out", "closed by your message", "no card",
        "another member answered", "parked", "changed since"]
    leads = [o.lead for o in at.CARD_OUTCOMES.values()]
    assert len(set(leads)) == len(leads)
    assert at.CLOSED_BY_YOUR_MESSAGE == "closed by your message"


def test_no_two_outcomes_share_a_text() -> None:
    texts = [at.card_outcome_text(k) for k in at.CARD_OUTCOMES]
    assert len(set(texts)) == len(texts)
    for k in at.CARD_OUTCOMES:
        others = [at.card_outcome_text(o) for o in at.CARD_OUTCOMES if o != k]
        assert at.CARD_OUTCOMES[k].happened not in " ".join(others), k


@pytest.mark.parametrize("outcome", list(at.CARD_OUTCOMES))
def test_each_text_names_what_happened_and_a_next_step(outcome: str) -> None:
    o = at.CARD_OUTCOMES[outcome]
    text = at.card_outcome_text(outcome)
    assert text.startswith(f"{o.lead}: {o.happened}")
    assert o.happened.strip() and o.next.strip()
    assert o.next.split()[0] in NEXT_VERBS, o.next
    # Simplified Technical English: no semicolon, and at most 20 words in a
    # sentence (the next step is a procedure).
    assert ";" not in text
    for sentence in _sentences(text):
        assert len(sentence.split()) <= 20, sentence


@pytest.mark.parametrize("outcome", NOT_DONE)
def test_each_text_of_a_card_that_did_nothing_says_so(outcome: str) -> None:
    """``evals/one_assistant_measure/checks.py`` reads it as no act."""
    text = at.card_outcome_text(outcome, "No lead was created for Veena.")
    assert _nothing_happened().search(at.CARD_OUTCOMES[outcome].happened), outcome
    assert _nothing_happened().search(text), outcome


def test_the_approved_text_says_no_nothing() -> None:
    assert not _nothing_happened().search(at.card_outcome_text(at.CARD_APPROVED))


def test_a_card_that_a_message_closed_never_reads_as_a_decline() -> None:
    """Task 2494: the member did not answer, so the text never says no."""
    text = at.card_outcome_text(at.CLOSED_BY_YOUR_MESSAGE, "No lead was created for Veena.")
    assert not _decline_pattern().search(text), text
    assert _decline_pattern().search(at.card_outcome_text(at.CARD_DECLINED))


def test_an_act_goes_between_what_happened_and_the_next_step() -> None:
    text = at.card_outcome_text(at.CARD_TIMED_OUT, "  The rule\nwas not deleted. ")
    o = at.CARD_OUTCOMES[at.CARD_TIMED_OUT]
    assert text == f"{o.lead}: {o.happened} The rule was not deleted. {o.next}"
    assert at.card_outcome_head(at.CARD_TIMED_OUT) == f"{o.lead}: {o.happened}"


def test_a_tool_that_did_not_act_never_reads_approved() -> None:
    """A card with rows can approve and still leave out the row that the act
    needs. The text of a tool that did nothing then reads as declined."""
    def read(outcome: str | None) -> str:
        ctx = contextvars.copy_context()

        def _go() -> str:
            at._LAST_OUTCOME.set(outcome)
            return at.card_not_done_text()

        return ctx.run(_go)

    declined = at.card_outcome_text(at.CARD_DECLINED)
    assert read(at.CARD_APPROVED) == declined
    assert read(None) == declined
    assert read(at.CARD_TIMED_OUT) == at.card_outcome_text(at.CARD_TIMED_OUT)


# ── 2. The answer of the card records its outcome ────────────────────────────


def _ask(answer: str | None, *, responder: str | None = None, member: str = "",
         timeout: bool = False, monkeypatch: pytest.MonkeyPatch | None = None,
         ) -> tuple[Any, str | None]:
    """One plain card on the native queue path, and the outcome it recorded.

    *answer* resolves the card. ``REVISED`` closes it as a chat message does.
    *timeout* makes the wait time out. The card and the read of the outcome
    run in ONE task, as a tool does."""
    if timeout:
        async def _late(_fut: Any, _timeout: float, **_kw: Any) -> dict[str, Any]:
            raise TimeoutError
        assert monkeypatch is not None
        monkeypatch.setattr(executor, "wait_user_future", _late)

    async def _tool() -> tuple[Any, str | None]:
        result = await at.request_confirmation(
            "Archive «Ops»?", "one project", "project: «Ops»",
            responder=member or None)
        return result, at.last_card_outcome()

    async def _run() -> tuple[Any, str | None]:
        q: asyncio.Queue = asyncio.Queue()
        executor._active_run_queue.set(q)
        executor._stream_relay_thread_id.set("t-hrd7")
        task = asyncio.ensure_future(_tool())
        if timeout:
            return await asyncio.wait_for(task, timeout=5)
        card = await asyncio.wait_for(q.get(), timeout=5)
        rid = card["value"]["request_id"]
        if answer == "REVISED":
            executor._pending_user_input[rid].set_result(
                {"answer": at.REVISED_ANSWER, "revised": True})
        else:
            assert executor.resolve_user_input(
                rid, str(answer), was_freeform=False,
                thread_id=executor._pending_user_input.owner_of(rid), responder=responder)
        return await asyncio.wait_for(task, timeout=5)

    return asyncio.run(_run())


def test_each_answer_records_its_outcome(monkeypatch: pytest.MonkeyPatch) -> None:
    assert _ask("APPROVE") == (True, at.CARD_APPROVED)
    assert _ask("REJECT") == (False, at.CARD_DECLINED)
    revised, outcome = _ask("REVISED")
    assert revised is at.REVISED and outcome == at.CLOSED_BY_YOUR_MESSAGE
    # PZ-32: the card asked one member, and another member answered.
    assert _ask("APPROVE", member="ana@acme.test", responder="bo@acme.test") == (
        False, at.CARD_OTHER_MEMBER)
    # The member who asked says no: a decline, not another member.
    assert _ask("REJECT", member="ana@acme.test", responder="ana@acme.test") == (
        False, at.CARD_DECLINED)
    assert _ask(None, timeout=True, monkeypatch=monkeypatch) == (False, at.CARD_TIMED_OUT)


def test_no_card_records_its_outcome() -> None:
    from acb_common.no_person import no_person_scope

    async def _tool() -> tuple[Any, str | None]:
        return (await at.request_confirmation("Archive «Ops»?")), at.last_card_outcome()

    async def _refused() -> tuple[Any, str | None]:
        with at.refuse_cards():
            return await _tool()

    async def _no_person() -> tuple[Any, str | None]:
        with no_person_scope():
            return await _tool()

    async def _no_channel() -> tuple[Any, str | None]:
        executor._active_run_queue.set(None)
        executor._stream_relay_thread_id.set("")
        return await _tool()

    for run in (_refused, _no_person, _no_channel):
        assert asyncio.run(run()) == (False, at.CARD_NOT_SHOWN), run.__name__


def test_two_cards_at_once_each_read_their_own_outcome() -> None:
    """The outcome is a ContextVar: a parallel tool call never reads the
    outcome of another call, and the run around them reads neither."""
    async def _approved() -> str | None:
        q: asyncio.Queue = asyncio.Queue()
        executor._active_run_queue.set(q)
        executor._stream_relay_thread_id.set("t-hrd7-a")

        async def _answer() -> None:
            card = await asyncio.wait_for(q.get(), timeout=5)
            rid = card["value"]["request_id"]
            await asyncio.sleep(0.02)  # the refused card below records first
            executor.resolve_user_input(rid, "APPROVE", was_freeform=False,
                                        thread_id=executor._pending_user_input.owner_of(rid))

        helper = asyncio.ensure_future(_answer())
        await at.request_confirmation("Archive «Ops»?")
        await helper
        return at.last_card_outcome()

    async def _no_card() -> str | None:
        await asyncio.sleep(0)  # the approved card opens first
        with at.refuse_cards():
            await at.request_confirmation("Delete «Ops»?")
        await asyncio.sleep(0.1)  # the approved card records in this time
        return at.last_card_outcome()

    async def _run() -> tuple[list[Any], str | None]:
        got = await asyncio.gather(_approved(), _no_card())
        return got, at.last_card_outcome()

    got, outer = asyncio.run(_run())
    assert got == [at.CARD_APPROVED, at.CARD_NOT_SHOWN]
    assert outer is None


# ── 3. Each family takes each text from the table ───────────────────────────


@functools.cache
def _load(path: Path, name: str) -> Any:
    """The agent module at *path*, loaded once. Each tool imports the card
    gate inside, so a patch of ``ask_tools`` reaches it."""
    spec = importlib.util.spec_from_file_location(name, path)
    assert spec and spec.loader
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


#: The answer that a card gives for each outcome.
ANSWERS: dict[str, Any] = {
    at.CARD_APPROVED: True,
    at.CARD_DECLINED: False,
    at.CARD_TIMED_OUT: False,
    at.CLOSED_BY_YOUR_MESSAGE: at.REVISED,
    at.CARD_NOT_SHOWN: False,
    at.CARD_OTHER_MEMBER: False,
}


def _card(monkeypatch: pytest.MonkeyPatch, outcome: str, *, channel: bool = True) -> None:
    """A card that answers as the real one does for *outcome*, and records it."""
    async def _fake(*_a: Any, **_kw: Any) -> Any:
        return at._noted(outcome, ANSWERS[outcome])

    monkeypatch.setattr(at, "request_confirmation", _fake)
    # Email tells "no card" from a card in a live chat by the channel.
    monkeypatch.setattr(at, "confirmation_channel_open",
                        lambda: channel and outcome != at.CARD_NOT_SHOWN)


def _projects(monkeypatch: pytest.MonkeyPatch, outcome: str) -> str | None:
    from skill_projects import writes
    from skill_projects.refusals import CardClosedByChat

    _card(monkeypatch, outcome)

    async def _go() -> str | None:
        try:
            ok = await writes._confirm("Archive «Ops»?", "one project", "project: «Ops»")
        except CardClosedByChat as closed:
            return str(closed)
        return None if ok else writes._not_confirmed()

    return asyncio.run(_go())


CRM_ACT = "No lead was created for Veena."


def _crm(monkeypatch: pytest.MonkeyPatch, outcome: str) -> str | None:
    crm = _load(CRM_AGENT, "crm_hrd7_agent")
    _card(monkeypatch, outcome)

    async def _go() -> str | None:
        answer = await at.request_confirmation("Create lead Veena?")
        return None if answer else crm._not_written(answer, unchanged=CRM_ACT)

    return asyncio.run(_go())


EMAIL_ACT = "The rule was not deleted."


def _email(monkeypatch: pytest.MonkeyPatch, outcome: str) -> str | None:
    email = _load(EMAIL_AGENT, "email_hrd7_agent")
    _card(monkeypatch, outcome)

    async def _go() -> str | None:
        if outcome == at.CLOSED_BY_YOUR_MESSAGE:
            # The reply card: the steps of its act follow the table's head.
            # Each act has its own steps (HRD-8a, ``test_redraft_keeps_act.py``).
            return email._CARD_REVISED
        return await email._card_refusal("Delete the rule?", "one rule", EMAIL_ACT)

    return asyncio.run(_go())


def _expected(family: str, outcome: str) -> str | None:
    if outcome == at.CARD_APPROVED:
        return None
    if family == "projects":
        return at.card_outcome_text(outcome)
    if family == "crm":
        return at.card_outcome_text(outcome, CRM_ACT)
    if outcome == at.CARD_NOT_SHOWN:
        return at.card_outcome_text(at.CARD_NOT_SHOWN)
    if outcome == at.CLOSED_BY_YOUR_MESSAGE:
        return None  # the head only: see below
    return at.card_outcome_text(outcome, EMAIL_ACT)


FAMILIES = {"projects": _projects, "crm": _crm, "email": _email}


@pytest.mark.parametrize("family", list(FAMILIES))
@pytest.mark.parametrize("outcome", list(ANSWERS))
def test_each_family_takes_each_text_from_the_table(
    monkeypatch: pytest.MonkeyPatch, family: str, outcome: str,
) -> None:
    got = FAMILIES[family](monkeypatch, outcome)
    if family == "email" and outcome == at.CLOSED_BY_YOUR_MESSAGE:
        head = at.card_outcome_head(at.CLOSED_BY_YOUR_MESSAGE, "Nothing was sent.")
        assert got is not None and got.startswith(head), got
        return
    assert got == _expected(family, outcome)


@pytest.mark.parametrize("family", list(FAMILIES))
def test_no_two_outcomes_of_a_family_share_a_text(
    monkeypatch: pytest.MonkeyPatch, family: str,
) -> None:
    texts = [FAMILIES[family](monkeypatch, o) for o in READ_BY_TOOLS]
    assert all(texts) and len(set(texts)) == len(texts), texts


def test_the_email_cards_name_another_member_in_the_table_words() -> None:
    """Email checks the member who answered a reply card, a held card and a
    new-mail card itself. Each says so in the table's words."""
    src = EMAIL_AGENT.read_text(encoding="utf-8")
    assert src.count("card_outcome_text(CARD_OTHER_MEMBER") == 3
    assert "Another member answered this card" not in src


# ── 4. No module writes an old cancel text ───────────────────────────────────


#: A text of a done act, not of a card that did nothing: the act of
#: ``cancel_scheduled_send`` IS a cancel.
DONE_TEXTS = frozenset({"Cancelled. The draft stays in Drafts and is not sent."})

#: The first words of the old texts of a card that did nothing.
OLD_LEADS = re.compile(r"^(?:Send |Forward )?[Cc]ancelled(?: —|\.\s)|^Reply discarded\b")


def _literals(path: Path) -> list[tuple[int, str]]:
    """Each string literal of *path*, and the text before the first value of
    each f-string, with its line."""
    tree = ast.parse(path.read_text(encoding="utf-8-sig"))
    docs = {id(n.body[0].value) for n in ast.walk(tree)
            if isinstance(n, (ast.Module, ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef))
            and n.body and isinstance(n.body[0], ast.Expr)
            and isinstance(n.body[0].value, ast.Constant)}
    out: list[tuple[int, str]] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.JoinedStr) and node.values:
            first = node.values[0]
            if isinstance(first, ast.Constant) and isinstance(first.value, str):
                out.append((node.lineno, first.value))
        elif (isinstance(node, ast.Constant) and isinstance(node.value, str)
              and id(node) not in docs):
            out.append((node.lineno, node.value))
    return out


def _family_files() -> list[Path]:
    return [*sorted(PROJECTS.glob("*.py")), CRM_AGENT, EMAIL_AGENT]


def test_no_module_writes_a_cancel_text_of_its_own() -> None:
    """The three families answer from the table, never with an old text."""
    found = [f"{p.relative_to(ROOT)}:{line}: {text[:60]!r}"
             for p in _family_files() for line, text in _literals(p)
             if OLD_LEADS.match(text) and text not in DONE_TEXTS]
    assert not found, "\n".join(found)


def test_no_module_outside_ask_tools_writes_a_cancel_outcome() -> None:
    """No Python module of the product starts a text with "Cancelled — "."""
    found = []
    for base in (ROOT / "apps", ROOT / "packages"):
        for p in base.rglob("*.py"):
            if "node_modules" in p.parts or p.name == "ask_tools.py":
                continue
            found += [f"{p.relative_to(ROOT)}:{line}" for line, text in _literals(p)
                      if text.startswith("Cancelled — ")]
    assert not found, found


def test_the_projects_cancel_constant_is_gone() -> None:
    from skill_projects import writes

    assert not hasattr(writes, "CANCELLED")
    assert not hasattr(writes, "CARD_REVISED")


# ── 5. The chat cards read the same rows ────────────────────────────────────


def test_the_chat_cards_hold_the_same_rows() -> None:
    """``src/lib/cardOutcomes.ts`` holds each lead, what happened and the next
    step, so its ``memberLineOf`` cuts the next step off a receipt. Its own
    test reads this side."""
    ts = CARD_OUTCOMES_TS.read_text(encoding="utf-8")
    body = ts[ts.index("export const CARD_OUTCOME_TEXTS"):]
    body = body[:body.index("};")]
    rows = re.findall(
        r'^\s+"?([a-z ]+?)"?: \{ lead: "([^"]+)", happened: "([^"]+)", next: "([^"]+)" \},$',
        body, re.MULTILINE)
    assert rows == [(k, o.lead, o.happened, o.next) for k, o in at.CARD_OUTCOMES.items()]


@pytest.mark.parametrize("outcome", list(at.CARD_OUTCOMES))
def test_the_act_sits_between_the_head_and_the_next_step(outcome: str) -> None:
    """``memberLineOf`` takes the act from between what happened and the next
    step of the row. The server's text holds it exactly there
    (``card_outcome_head``), so the member reads the act and never the step."""
    act = "No lead was created for Veena."
    text = at.card_outcome_text(outcome, act)
    o = at.CARD_OUTCOMES[outcome]
    assert text.endswith(" " + o.next)
    head = f"{o.lead}: {o.happened}"
    assert text[len(head): text.index(o.next)].strip() == act
    assert text[: text.index(o.next)].strip() == at.card_outcome_head(outcome, act)


def _member_lines() -> list[tuple[str, str]]:
    ts = CARD_OUTCOMES_TS.read_text(encoding="utf-8")
    body = ts[ts.index("export const CARD_OUTCOME_MEMBER_LINES"):]
    body = body[:body.index("};")]
    return re.findall(r'^\s+"?([a-z ]+?)"?: "([^"]+)",$', body, re.MULTILINE)


def test_the_chat_cards_hold_a_member_line_for_each_outcome() -> None:
    """The member reads a line in the second person, and never the text for
    the model. ``CARD_OUTCOME_MEMBER_LINES`` holds one for each outcome of the
    table, and no line holds a word or a step for the model."""
    lines = _member_lines()
    assert [k for k, _ in lines] == list(at.CARD_OUTCOMES)
    for key, line in lines:
        o = at.CARD_OUTCOMES[key]
        assert not re.search(r"the member|\bTell\b|\bRead\b|Do the act", line, re.IGNORECASE), line
        assert o.next not in line and o.happened not in line, line
        assert ";" not in line and len(line.split()) <= 20, line
