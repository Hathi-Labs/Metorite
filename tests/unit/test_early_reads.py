"""Chat speed SPD-1 part 2: three setup reads start beside the agent load.

Production, 2026-10-30: 53% of chat turns came more than 5 minutes after the
one before, so the knowledge index, the mailbox list and the CRM facts were
read cold, one after another, before the first model request. Part 2 starts
them together beside the load (``executor._EarlyReads``) and awaits each one
before its first reader. The rule of each read does not change.

Mutations this file catches (R7):

* ``_EarlyReads.start`` reads the mailboxes or the CRM facts for an agent
  other than the orchestrator, for a run with ``no_egress``, for an org off
  ``CRM_QUESTIONS_ORGS``, or with no verified solo member.
* A task reads outside the run's context (the member or org of another run).
* ``settle`` skips a cache fill, or lets a fault of one end the run.
* ``close`` leaves a read running after the run ends, or leaves a fault
  unread, so asyncio logs it.
* A failed import of the email context ends the turn (it skips only the
  mailbox read, as ``_prime_email_context`` does).
* ``_add_crm_facts_block`` reads the facts a second time when an early read
  is there, uses an early read where its own rule says no read, or ends the
  run when the early read failed.
* ``run_agent_stream`` starts the reads before the access check, awaits the
  caches after their first reader, or no longer cancels them in ``finally``.
"""
from __future__ import annotations

import asyncio
import contextvars
import inspect
from typing import Any

import pytest
from orchestrator import _tool_injection as ti
from orchestrator import executor as ex

_WHO: contextvars.ContextVar[str] = contextvars.ContextVar("_test_who", default="")


class _MafAgent:
    def __init__(self, text: str = "Base prompt.") -> None:
        self.default_options = {"instructions": text, "tools": []}


@pytest.fixture
def reads(monkeypatch: pytest.MonkeyPatch) -> dict[str, list[Any]]:
    """Each early read records what it saw, and the run is a verified solo
    member of ``org-a`` whose org is on ``CRM_QUESTIONS_ORGS``."""
    seen: dict[str, list[Any]] = {"knowledge": [], "mailboxes": [], "facts": []}

    async def knowledge(agent_name: str) -> None:
        seen["knowledge"].append((agent_name, _WHO.get()))

    async def mailboxes(org: str, member: str) -> None:
        seen["mailboxes"].append((org, member, _WHO.get()))

    async def facts() -> list[Any]:
        seen["facts"].append(_WHO.get())
        return [{"key": "revenue", "meaning": "Revenue means billed.", "subject": "s"}]

    from acb_skills import email_chat_context as ecc

    monkeypatch.setattr(ex, "_prime_knowledge_index", knowledge)
    monkeypatch.setattr(ecc, "prime_mailboxes", mailboxes)
    monkeypatch.setattr(ex, "_early_crm_facts", facts)
    monkeypatch.setattr(ex, "_email_ctx_member", lambda: "a@org-a.test")
    monkeypatch.setattr(ex, "_current_run_org", lambda: "org-a")
    monkeypatch.setattr(ex, "_crm_questions_on", lambda org: org == "org-a")
    return seen


async def test_the_orchestrator_starts_all_three_reads(reads) -> None:
    early = ex._EarlyReads.start("orchestrator", no_egress=False)
    await early.settle()
    assert await early.crm_facts
    assert reads["knowledge"] == [("orchestrator", "")]
    assert reads["mailboxes"] == [("org-a", "a@org-a.test", "")]
    assert reads["facts"] == [""]


async def test_another_agent_starts_only_the_knowledge_read(reads) -> None:
    early = ex._EarlyReads.start("email-assistant", no_egress=False)
    await early.settle()
    assert early.mailboxes is None and early.crm_facts is None
    assert reads["knowledge"] == [("email-assistant", "")]


async def test_a_no_egress_run_reads_no_crm_facts(reads) -> None:
    early = ex._EarlyReads.start("orchestrator", no_egress=True)
    await early.settle()
    assert early.crm_facts is None and reads["facts"] == []
    assert early.mailboxes is not None, "the mailbox list keeps the rule of _prime_email_context"


async def test_an_org_off_the_list_reads_no_crm_facts(reads, monkeypatch) -> None:
    monkeypatch.setattr(ex, "_current_run_org", lambda: "org-b")
    early = ex._EarlyReads.start("orchestrator", no_egress=False)
    await early.settle()
    assert early.crm_facts is None and reads["facts"] == []
    assert reads["mailboxes"] == [("org-b", "a@org-a.test", "")]


async def test_no_verified_solo_member_reads_no_mailboxes(reads, monkeypatch) -> None:
    monkeypatch.setattr(ex, "_email_ctx_member", lambda: "")
    early = ex._EarlyReads.start("orchestrator", no_egress=False)
    await early.settle()
    assert early.mailboxes is None and reads["mailboxes"] == []


async def test_each_read_runs_in_the_context_of_its_own_run(reads) -> None:
    async def one_run(who: str) -> None:
        _WHO.set(who)
        early = ex._EarlyReads.start("orchestrator", no_egress=False)
        await early.settle()
        await early.crm_facts

    await asyncio.gather(
        asyncio.create_task(one_run("run-a")), asyncio.create_task(one_run("run-b")))
    assert sorted(w for _, w in reads["knowledge"]) == ["run-a", "run-b"]
    assert sorted(m[2] for m in reads["mailboxes"]) == ["run-a", "run-b"]
    assert sorted(reads["facts"]) == ["run-a", "run-b"]


async def test_settle_waits_for_both_caches() -> None:
    done: list[str] = []

    async def slow(name: str, delay: float) -> None:
        await asyncio.sleep(delay)
        done.append(name)

    early = ex._EarlyReads(asyncio.ensure_future(slow("knowledge", 0.01)),
                           asyncio.ensure_future(slow("mailboxes", 0.03)))
    await early.settle()
    assert sorted(done) == ["knowledge", "mailboxes"], "a cache fill was not awaited"


async def test_settle_never_raises() -> None:
    async def broken() -> None:
        raise RuntimeError("gateway down")

    early = ex._EarlyReads(asyncio.ensure_future(broken()), asyncio.ensure_future(broken()))
    await early.settle()  # must not raise


async def test_close_cancels_what_is_still_running_and_reads_each_fault() -> None:
    gate = asyncio.Event()

    async def never() -> list[Any]:
        await gate.wait()
        return []

    async def failed() -> None:
        raise RuntimeError("read failed")

    hung = asyncio.ensure_future(never())
    bad = asyncio.ensure_future(failed())
    await asyncio.sleep(0)
    assert bad._log_traceback, "asyncio would log this fault as never retrieved"
    early = ex._EarlyReads(knowledge=bad, crm_facts=hung)
    early.close()
    await asyncio.sleep(0)
    assert hung.cancelled()
    assert not bad._log_traceback, "close() reads the fault, so asyncio logs none"


async def test_a_failed_import_skips_the_mailbox_read_and_never_ends_the_turn(
    reads, monkeypatch,
) -> None:
    import sys

    import acb_skills

    monkeypatch.delattr(acb_skills, "email_chat_context", raising=False)
    monkeypatch.setitem(sys.modules, "acb_skills.email_chat_context", None)
    early = ex._EarlyReads.start("orchestrator", no_egress=False)
    await early.settle()
    assert early.mailboxes is None and reads["mailboxes"] == []
    assert early.knowledge is not None and early.crm_facts is not None
    early.close()


async def test_the_block_uses_the_early_rows_and_reads_no_second_time(monkeypatch) -> None:
    from acb_common import get_settings
    from acb_skills import org_facts_tools as tools

    monkeypatch.setattr(get_settings(), "crm_questions_orgs", "*", raising=False)
    monkeypatch.setattr(ti, "_run_org_or_none", lambda: "org-a")
    second: list[int] = []

    async def read_again() -> list[Any]:
        second.append(1)
        return []

    monkeypatch.setattr(tools, "crm_facts_for_run", read_again)

    async def early() -> list[Any]:
        return [{"key": "revenue", "meaning": "Revenue means billed.", "subject": "s"}]

    agent = _MafAgent()
    await ti._add_crm_facts_block(
        [agent], frozenset({"crm_aggregate"}), facts=asyncio.ensure_future(early()))
    assert second == []
    assert "Revenue means billed." in agent.default_options["instructions"]


async def test_the_rule_still_decides_and_an_unused_read_stays_unread(monkeypatch) -> None:
    from acb_common import get_settings

    monkeypatch.setattr(get_settings(), "crm_questions_orgs", "*", raising=False)
    monkeypatch.setattr(ti, "_run_org_or_none", lambda: "org-a")
    async def early() -> list[Any]:
        return [{"key": "revenue", "meaning": "Revenue means billed.", "subject": "s"}]

    task = asyncio.ensure_future(early())
    for tools_held, no_egress in ((frozenset({"web_search"}), False),
                                  (frozenset({"crm_aggregate"}), True)):
        agent = _MafAgent()
        await ti._add_crm_facts_block([agent], tools_held, no_egress=no_egress, facts=task)
        assert agent.default_options["instructions"] == "Base prompt."
    task.cancel()


async def test_a_failed_early_read_gives_no_rows_and_a_cancel_still_ends_the_run() -> None:
    async def read() -> list[Any]:
        raise AssertionError("no second read")

    async def broken() -> list[Any]:
        raise RuntimeError("gateway down")

    assert await ti._crm_facts_rows(asyncio.ensure_future(broken()), read) == []
    gone = asyncio.ensure_future(asyncio.sleep(10))
    gone.cancel()
    with pytest.raises(asyncio.CancelledError):
        await ti._crm_facts_rows(gone, read)

    async def rows() -> list[Any]:
        return [{"key": "k"}]

    assert await ti._crm_facts_rows(None, rows) == [{"key": "k"}]


def test_the_stream_starts_after_access_settles_before_readers_and_closes() -> None:
    src = inspect.getsource(ex.run_agent_stream)
    assert src.count("_EarlyReads.start(") == 1
    start = src.index("_EarlyReads.start(")
    assert src.index("await _assert_may_run_agent(agent_name)") < start, \
        "no read runs before the access check"
    assert start < src.index("_load_agent_off_loop("), "the reads start beside the load"
    assert "await _prime_knowledge_index(" not in src, "the knowledge read moved, not doubled"
    assert "facts=_early.crm_facts" in src
    settle = src.index("await _early.settle()")
    assert settle < src.index("await _prime_email_context(agent_name)")
    assert settle < src.index("_compose_maf_run("), "the caches fill before the first reader"
    tail = src[src.index("if _pack_task is not None and not _pack_task.done():"):]
    assert "_early.close()" in tail[:600], "the finally cancels the early reads"
