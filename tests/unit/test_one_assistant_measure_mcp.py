"""The fence of the connected-systems chats of the measure (CSY-3, R7).

``evals/one_assistant_measure/scenarios_mcp.py`` holds four chats that ask where a question
goes when an MCP server is attached. Spec: ``project-docs/specs/chat_connected_systems.md``
§8.2, row CSY-3, and §3. The server is a fake (``mcp_fixture.py``) behind the REAL path:
``_inject_mcp_servers``, ``attach_mcp_tools``, ``RunScopedMCPTool`` and ``McpPermissionGate``.

A scripted run plays known-good plans, so it proves the PATH and the CHECKS, never the
model's choice. This file proves four things:

* (a) the four chats pass a scripted run, through the real orchestrator run;
* (b) each check FAILS on a broken path (a mutation for each case);
* (c) the words of each chat match the packs that the chat expects, and no more;
* (d) the set has NO live arm, and the scripted run reads no vendor key.

Run::

    uv run python -m pytest tests/unit/test_one_assistant_measure_mcp.py -q
"""
from __future__ import annotations

import dataclasses
import re
from pathlib import Path
from typing import Any

import pytest

pytest.importorskip("agent_framework", reason="MAF not installed")
pytest.importorskip("mcp", reason="mcp not installed")
pytest.importorskip("uvicorn", reason="uvicorn not installed")

from acb_skills import tool_packs
from acb_skills.tier_policy import SHORT_MESSAGE_WORDS

from evals.one_assistant_measure import checks as ck
from evals.one_assistant_measure import mcp_fixture as mx
from evals.one_assistant_measure import run
from evals.one_assistant_measure.scenarios import Plan, all_scenarios, scenario
from evals.one_assistant_measure.scenarios_mcp import MCP_SCENARIOS
from evals.one_assistant_measure.scenarios_work import WORK_SCENARIOS

IDS = ("csy_only_server", "csy_metorite", "csy_miss", "csy_mail")
EVALS = Path(__file__).resolve().parents[2] / "evals" / "one_assistant_measure"


@pytest.fixture(autouse=True)
def _no_router(monkeypatch: pytest.MonkeyPatch) -> None:
    """(d) Any read of the Router and its key fails the test. A scripted run never needs it."""
    def boom(*_a: Any, **_k: Any) -> Any:
        raise AssertionError("the scripted MCP run read the Router settings or a key")

    monkeypatch.setattr(run, "router_env", boom)


@pytest.fixture(scope="module")
def scripted() -> tuple[list[dict[str, Any]], list[str]]:
    return run.run_scripted(MCP_SCENARIOS)


def _by(rows: list[dict[str, Any]]) -> dict[str, dict[str, Any]]:
    return {r["scenario"]: r for r in rows}


# ── (a) the scripted run ─────────────────────────────────────────────────────


def test_the_set_is_the_four_chats_and_in_no_work_set() -> None:
    assert tuple(s.id for s in MCP_SCENARIOS) == IDS
    assert all(s.set == run.MCP_SET == "mcp" for s in MCP_SCENARIOS)
    assert not {s.id for s in MCP_SCENARIOS} & {s.id for s in WORK_SCENARIOS}
    ids = [s.id for s in all_scenarios()]
    assert len(ids) == len(set(ids)) and set(IDS) <= set(ids)
    assert scenario("csy_miss") is MCP_SCENARIOS[2]


def test_every_chat_is_short() -> None:
    """Under 12 words, so no turn-kind request goes out. Mutation: a 12-word chat → red."""
    for sc in MCP_SCENARIOS:
        for turn in sc.turns:
            assert len(turn.words.split()) < SHORT_MESSAGE_WORDS, (sc.id, turn.words)


def test_the_chats_run_on_the_pack_arm_only() -> None:
    for sc in MCP_SCENARIOS:
        assert run.arms_for(sc, run.ALL_ARMS) == ("pack",)


def test_the_four_chats_pass_a_scripted_run(scripted) -> None:
    """Each known-good plan passes every check, through the real run and the real MCP
    path. Mutation: break one answer in ``scenarios_mcp`` → its check fails → red."""
    rows, problems = scripted
    assert problems == [], problems
    assert [r["scenario"] for r in rows] == list(IDS)
    assert all(r["passed"] for r in rows)


def test_what_the_server_itself_saw(scripted) -> None:
    """The server's own log is the ground truth. Only chats 1 and 3 reach it."""
    rows = _by(scripted[0])
    assert rows["csy_only_server"]["mcp_tools"] == [mx.FULL]
    assert len(rows["csy_only_server"]["mcp_served"]) == 1
    assert rows["csy_metorite"]["mcp_tools"] == [] and rows["csy_metorite"]["mcp_served"] == []
    assert rows["csy_miss"]["work_tools"] == ["find_tasks"]
    assert rows["csy_miss"]["mcp_tools"] == [mx.FULL]
    assert len(rows["csy_miss"]["mcp_served"]) == 1
    assert rows["csy_mail"]["mcp_tools"] == [] and rows["csy_mail"]["mcp_served"] == []


def test_the_run_took_the_real_mcp_path(scripted) -> None:
    """The server's result came back through the real tool, and no card was needed for a
    read-only tool. Mutation: stub ``attach_mcp_tools`` → no tool name in the request → red."""
    rows = _by(scripted[0])
    assert rows["csy_only_server"]["cards"] == 0
    assert "2026-03-02" in rows["csy_only_server"]["answer"]
    assert rows["csy_only_server"]["sent"] == [] and rows["csy_miss"]["sent"] == []


# ── (b) each check fails on a broken path ────────────────────────────────────


def _mutant(sid: str, calls: tuple[tuple[str, dict[str, Any]], ...], answer: str) -> list[dict[str, Any]]:
    """Run chat *sid* with a broken plan: other *calls* and *answer*."""
    sc = scenario(sid)
    turn = dataclasses.replace(sc.turns[0], plan=Plan(calls, answer))
    return run.run_chat(dataclasses.replace(sc, turns=(turn,)), "pack")


def _failed(rows: list[dict[str, Any]]) -> set[str]:
    return {k for k, v in rows[0]["checks"].items() if v is False}


def test_case_1_with_no_server_call_fails() -> None:
    """Mutation: the plan answers from memory and never calls the server."""
    failed = _failed(_mutant("csy_only_server", (), "I do not know."))
    assert {"mcp_called", "gives_the_fact_and_the_server"} <= failed


def test_case_2_that_calls_the_server_fails() -> None:
    """Mutation: a Metorite question where the plan calls the server too."""
    rows = _mutant("csy_metorite", (("my_work", {}), (mx.FULL, {"query": "my plate"})),
                   "Three tasks: Draft the launch brief and Vendor call prep.")
    assert "mcp_not_called" in _failed(rows)
    assert rows[0]["mcp_served"], "the server must have seen the call, or the mutation is void"


def test_case_3_that_stops_at_not_found_fails() -> None:
    """Mutation: Metorite finds nothing, and the plan says "not found" with no server call."""
    failed = _failed(_mutant("csy_miss", (("find_tasks", {"query": "circuit audit"}),),
                             "Not found. No task matches."))
    assert {"server_after_metorite", "mcp_called", "no_dead_end"} <= failed


def test_case_3_that_skips_metorite_fails() -> None:
    """Mutation: the plan goes straight to the server and never tries Metorite. (The scripted
    plan always plays the server call last, so the order check is proved on its own below.)"""
    failed = _failed(_mutant("csy_miss", ((mx.FULL, {"query": "circuit audit C-17"}),),
                             "Circuit C-17 was last serviced on 2026-03-02."))
    assert {"server_after_metorite", "tried_metorite"} <= failed


def test_case_4_that_holds_the_server_and_mail_fails() -> None:
    """Mutation: the mail chat where the plan calls the server. The run holds no server
    tool beside the email pack (OA-7b), so the call finds no function, and the check
    refuses the attempt: the chat must not reach for the server at all."""
    rows = _mutant("csy_mail", (("query_inbox", {"from_email": "priya"}),
                                (mx.FULL, {"query": "board order"})),
                   "Priya wants 250 boards instead of 200.")
    assert "mcp_not_called" in _failed(rows) or "mail_without_server" in _failed(rows)
    assert rows[0]["mcp_served"] == [], "OA-7b: the server must never run beside the mail pack"


def _obs(**kw: Any) -> ck.Observation:
    return ck.Observation(arm="pack", **kw)


MAIL = frozenset({"query_inbox", "read_email"})
SERVER_TOOL = frozenset({mx.FULL})


def test_the_check_of_oa7b_fails_when_a_run_holds_both() -> None:
    """The check on its own: a request with an email pack tool and the server tool fails."""
    both = _obs(tool_names=MAIL | SERVER_TOOL)
    assert ck.KINDS["no_mail_pack_with_mcp"](both, None) is False
    assert ck.KINDS["mail_without_server"](both, None) is False
    assert ck.KINDS["no_mail_pack_with_mcp"](_obs(tool_names=MAIL), None) is True
    assert ck.KINDS["no_mail_pack_with_mcp"](_obs(tool_names=SERVER_TOOL), None) is True
    # A turn with no request read proves nothing, so it fails.
    assert ck.KINDS["no_mail_pack_with_mcp"](_obs(), None) is False


def test_mail_without_server_needs_the_pack_to_be_there() -> None:
    """A mail chat with no email pack tool in the request is vacuous, so it fails."""
    assert ck.KINDS["mail_without_server"](_obs(tool_names=MAIL), None) is True
    assert ck.KINDS["mail_without_server"](_obs(tool_names=frozenset({"my_work"})), None) is False


def test_mcp_checks_on_a_refused_call_and_an_unseen_call() -> None:
    """A call that the run made and the server never saw does not pass ``mcp_called``. A
    call that the run refused ("function not found") does not pass it either."""
    ok = (mx.FULL, {"query": "x"}, mx.FACT)
    unknown = (mx.FULL, {"query": "x"}, f'Error: Requested function "{mx.FULL}" not found')
    assert ck.KINDS["mcp_called"](_obs(mcp_calls=[ok], mcp_served=["x"]), None) is True
    assert ck.KINDS["mcp_called"](_obs(mcp_calls=[ok], mcp_served=[]), None) is False
    assert ck.KINDS["mcp_called"](_obs(mcp_calls=[unknown], mcp_served=["x"]), None) is False
    assert ck.KINDS["mcp_not_called"](_obs(mcp_calls=[unknown]), None) is False
    assert ck.KINDS["mcp_not_called"](_obs(), None) is True
    assert ck.KINDS["mcp_offered"](_obs(tool_names=SERVER_TOOL), None) is True
    assert ck.KINDS["mcp_offered"](_obs(tool_names=MAIL), None) is False


def test_server_after_metorite_reads_the_order() -> None:
    work = "find_tasks"
    assert ck.KINDS["server_after_metorite"](_obs(call_order=[work, mx.FULL]), None) is True
    assert ck.KINDS["server_after_metorite"](_obs(call_order=[mx.FULL, work]), None) is False
    assert ck.KINDS["server_after_metorite"](_obs(call_order=[work]), None) is False
    assert ck.KINDS["server_after_metorite"](_obs(call_order=[mx.FULL]), None) is False


def test_the_fake_server_holds_the_one_fact_that_no_metorite_fixture_holds() -> None:
    """Chat 1 is a question that ONLY the server answers: no fixture of Metorite and no
    mailbox holds the fact. Mutation: put the fact in a fixture → this test is red."""
    text = "\n".join(p.read_text(encoding="utf-8") for p in EVALS.glob("*.py")
                     if p.name not in {"mcp_fixture.py", "scenarios_mcp.py"})
    assert "2026-03-02" not in text and "C-17" not in text and "Vendor Nova" not in text
    assert mx.serves("circuit C-17") == mx.FACT and mx.serves("board order") == mx.NOTHING


# ── (c) the words against the packs ──────────────────────────────────────────

#: The packs that the words of each chat load, by ``tool_packs.matched_layer``. The Metorite
#: chats name the Projects pack on purpose. The server chat and the mail chat name none:
#: the mail chat loads the email pack by its SURFACE, and the server chat holds no pack.
WORDS_LOAD = {
    "csy_only_server": {},
    "csy_metorite": {"projects": "words"},
    "csy_miss": {"projects": "words"},
    "csy_mail": {},
}


def test_the_words_of_each_chat_match_the_packs_they_expect() -> None:
    """Mutation: add "inbox" to the server question → the email pack matches → red."""
    for sc in MCP_SCENARIOS:
        for turn in sc.turns:
            hits = {slug: layer for slug in tool_packs.PACKS
                    if (layer := tool_packs.matched_layer(slug, turn.words))}
            assert hits == WORDS_LOAD[sc.id], (sc.id, turn.words, hits)


def test_no_server_question_matches_an_email_pack_by_words() -> None:
    """OA-7b: a chat that holds an email pack gets no server. So the three server chats
    must not load the email pack by their own words, or case 1 and 3 would lose it."""
    for sid in IDS[:3]:
        for turn in scenario(sid).turns:
            assert not tool_packs.matched_layer("email", turn.words), (sid, turn.words)
    assert scenario("csy_mail").surface == "email"


def test_the_proof_can_fail() -> None:
    """The control of the proof above: a mail word DOES match."""
    assert tool_packs.matched_layer("email", "check my inbox") == "words"


# ── (d) no live arm, no vendor key ───────────────────────────────────────────


def test_a_live_run_that_names_an_mcp_chat_is_refused(capsys: pytest.CaptureFixture[str]) -> None:
    """``--live --scenarios csy_mail`` stops with NO-GO BEFORE the Router or its key is
    read (the autouse fixture makes ``router_env`` raise). Mutation: drop the refusal →
    the run reaches ``router_env`` → the fixture raises → red."""
    assert run.main(["--live", "--scenarios", "csy_mail"]) == run.EXIT_NO_GO
    out = capsys.readouterr().out
    assert "NO-GO" in out and "csy_mail" in out and "no live arm" in out


def test_run_chat_refuses_a_router_for_the_mcp_set() -> None:
    with pytest.raises(ValueError, match="no live arm"):
        run.run_chat(scenario("csy_only_server"), "pack", router=("http://127.0.0.1:4000", "k"))


def test_a_live_run_of_every_chat_leaves_the_mcp_chats_out(monkeypatch: pytest.MonkeyPatch) -> None:
    """With no ``--scenarios``, the live run drops the MCP set and runs the rest."""
    seen: list[tuple[Any, ...]] = []

    def fake_live(_router: Any, scenarios: Any, *_a: Any, **_k: Any) -> dict[str, Any]:
        seen.append(tuple(scenarios))
        return {}

    monkeypatch.setattr(run, "router_env", lambda: ("http://127.0.0.1:4000", "not-a-key"))
    monkeypatch.setattr(run, "_peak_ok", lambda _allow: True)
    monkeypatch.setattr(run, "_preflight", lambda *_a, **_k: (True, "ok"))
    monkeypatch.setattr(run, "run_live", fake_live)
    monkeypatch.setattr(run, "report", lambda _d: "")
    assert run.main(["--live", "--arms", "pack"]) == run.EXIT_PASS
    assert seen and seen[0]
    assert not any(sc.set == run.MCP_SET for sc in seen[0])
    assert run.live_refusal(MCP_SCENARIOS, set()) == ""
    assert "csy_mail" in run.live_refusal(MCP_SCENARIOS, {"csy_mail"})


def test_the_new_files_read_no_key_and_no_environment() -> None:
    """The new modules name no vendor key, no environment read and no ``eval.env``.
    Mutation: add ``os.environ`` to ``mcp_fixture.py`` → red."""
    banned = re.compile(r"environ|getenv|eval\.env|api_key|API_KEY|LITELLM|router_env|"
                        r"anthropic|openai", re.IGNORECASE)
    for name in ("mcp_fixture.py", "scenarios_mcp.py"):
        text = (EVALS / name).read_text(encoding="utf-8")
        # The docstring of each file may say "key" in prose: only the code lines count.
        code = "\n".join(line for line in text.splitlines() if not line.lstrip().startswith("#"))
        assert not banned.search(code.split('"""', 2)[-1]), name
