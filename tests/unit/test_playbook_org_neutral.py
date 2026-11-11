"""The playbook holds no HathiLabs rule (D186.2, PB-1). R7: this file is the fence.

The fixture lists the HathiLabs terms. This test fails on a match in any
playbook file. A second test checks that each entry still matches text in the
repository rules, so the list cannot drift. PB-5 widens the scan to the
product strings and defaults.
"""

from __future__ import annotations

import json
import re
from pathlib import Path

import pytest
from acb_playbook import load_all

_ROOT = Path(__file__).resolve().parents[2]
_FIXTURE = _ROOT / "tests/fixtures/playbook_org_tokens.json"
_RULE_FILES = ("CLAUDE.md", "project-docs/work_plan.md", "docs/style_ste.md")


def _entries() -> list[dict]:
    return json.loads(_FIXTURE.read_text(encoding="utf-8"))["entries"]


def _pattern(entry: dict) -> re.Pattern[str]:
    return re.compile(re.escape(entry["value"]) if entry["kind"] == "token" else entry["value"])


def hits(text: str) -> list[str]:
    """The ids of the fixture entries that match the text."""
    return [e["id"] for e in _entries() if _pattern(e).search(text)]


def test_the_fixture_is_well_formed():
    entries = _entries()
    ids = [e["id"] for e in entries]
    assert len(ids) == len(set(ids))
    assert all(e["kind"] in {"token", "regex"} and e["value"] for e in entries)
    for e in entries:
        _pattern(e)  # a bad regex raises here


def test_no_playbook_file_holds_a_hathilabs_term():
    for topic in load_all():
        text = "\n".join([topic.title, topic.summary, topic.body])
        assert hits(text) == [], f"{topic.slug} holds a HathiLabs term: {hits(text)}"


@pytest.mark.parametrize("entry", _entries(), ids=lambda e: e["id"])
def test_each_entry_still_matches_a_repository_rule_file(entry):
    pattern = _pattern(entry)
    for name in _RULE_FILES:
        if pattern.search((_ROOT / name).read_text(encoding="utf-8")):
            return
    pytest.fail(f"{entry['id']} matches none of {_RULE_FILES}; remove it or fix it")


@pytest.mark.parametrize(
    "injected",
    [
        "Follow Simplified Technical English.",
        "Write it in STE.",
        "This is rule R5.",
        "See D145 for the loop.",
        "A HathiLabs rule.",
        "This is OWNER-GATE work.",
        "Release at 21:30 UTC.",
    ],
)
def test_the_scan_catches_an_injected_term(injected):
    assert hits(injected), injected


# ── PB-5: the scan reaches the product strings and the defaults ─────────────


def _flags_on(monkeypatch) -> None:
    from acb_common import get_settings

    settings = get_settings()
    for name in ("agent_access_writes_enabled", "agent_runs_enabled",
                 "agent_mcp_sessions_enabled", "agent_answer_change_enabled"):
        monkeypatch.setattr(settings, name, True)


def _product_strings(monkeypatch) -> dict[str, str]:
    """Every fixed string that the edge sends to an agent in any organization."""
    from agent_edge import mcp, prompts, tools

    _flags_on(monkeypatch)
    scopes = frozenset({"projects:read", "projects:write", "context:read"})
    found = {
        "the initialize instructions": mcp.instructions(scopes),
        "the work_on_task message": prompts._task_text("42", runs=True),
        "the work_on_task message, runs off": prompts._task_text("42", runs=False),
        "the queued-work message": prompts._queued_text(),
    }
    for name, tool in tools.TOOLS.items():
        found[f"the description of {name}"] = tool.describe()["description"]
    # The protocol block (SW-56) and the tips (PB-4) join the scan when they exist.
    lines = getattr(tools, "PROTOCOL_LINES", ())
    if lines:
        found["the protocol block"] = " ".join(lines)
    rules = getattr(tools, "TIP_RULES", ())
    if rules:
        found["the tips"] = " ".join(rule.text for rule in rules)
    return found


def test_no_product_string_holds_a_hathilabs_term(monkeypatch):
    for where, text in _product_strings(monkeypatch).items():
        assert hits(text) == [], f"{where} holds a HathiLabs term: {hits(text)}"


def test_the_scan_of_the_product_strings_is_not_vacuous(monkeypatch):
    found = _product_strings(monkeypatch)
    assert len(found) > 20
    assert all(len(text) > 20 for text in found.values())


def test_a_term_in_a_product_string_would_fail_the_scan(monkeypatch):
    import dataclasses

    from agent_edge import tools

    tainted = dataclasses.replace(tools.ASK_ON_TASK,
                                  description=tools.ASK_ON_TASK.description + " Follow STE.")
    monkeypatch.setitem(tools.TOOLS, "ask_on_task", tainted)
    bad = [w for w, text in _product_strings(monkeypatch).items() if hits(text)]
    assert bad == ["the description of ask_on_task"]


def test_the_default_guidance_topic_exists_is_neutral_and_short():
    from acb_playbook import load_topic

    topic = load_topic("default-guidance")
    assert topic is not None and topic.audience == "agent"
    assert hits(topic.body) == []
    assert 100 < len(topic.body) < 2000


def test_the_default_block_the_gateway_serves_is_neutral(monkeypatch):
    pytest.importorskip("gateway.routes.agent_guidance")
    from acb_common import get_settings
    from gateway.routes import agent_guidance

    monkeypatch.setattr(get_settings(), "playbook_orgs", "*")
    block = agent_guidance.default_block("00000000-0000-4000-8000-000000000001")
    assert block is not None and hits(block["body"]) == []
    assert block["level"] == "default" and block["source"]["kind"] == "default"


def test_the_default_block_is_dark_while_playbook_orgs_is_empty(monkeypatch):
    pytest.importorskip("gateway.routes.agent_guidance")
    from acb_common import get_settings
    from gateway.routes import agent_guidance

    monkeypatch.setattr(get_settings(), "playbook_orgs", "")
    assert agent_guidance.default_block("00000000-0000-4000-8000-000000000001") is None
    monkeypatch.setattr(get_settings(), "playbook_orgs", "00000000-0000-4000-8000-000000000002")
    assert agent_guidance.default_block("00000000-0000-4000-8000-000000000001") is None
    assert agent_guidance.default_block(None) is None


def test_the_default_block_is_per_org_in_one_process(monkeypatch):
    """R7: one org on the list never gives the block to another, in either order."""
    pytest.importorskip("gateway.routes.agent_guidance")
    from acb_common import get_settings
    from gateway.routes import agent_guidance

    a, b = "00000000-0000-4000-8000-00000000000a", "00000000-0000-4000-8000-00000000000b"
    monkeypatch.setattr(get_settings(), "playbook_orgs", a)
    for first, second in ((a, b), (b, a)):
        got = {first: agent_guidance.default_block(first),
               second: agent_guidance.default_block(second)}
        assert got[a] is not None
        assert got[b] is None
