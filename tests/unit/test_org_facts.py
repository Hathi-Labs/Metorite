"""WS-53 CRM-Q32-5 — the org-facts seam, hermetic.

Spec: ``project-docs/specs/crm_platform.md`` §18.14.4 and the CRM-Q32-5
done-when list of §18.14.10.

* Item 1: the registry, the row shape and ``register``.
* Item 2: the CRM provider builds the facts of §18.14.4. ``set_by`` is a
  role, never an email.
* Item 4: the tool ``org_facts`` loads with the ``crm`` family only.
* Item 5: the CRM facts block holds 12 lines at most, and the run assembly
  adds it only to a run that holds a CRM tool.
* Item 7: a run of org A never carries the CRM facts block of org B.
* Item 8: no module outside ``org_facts.py`` and the providers builds a fact
  row.

``test_org_facts_r8.py`` proves the route, the tool and the tenancy on a
real Postgres.
"""

from __future__ import annotations

import ast
import inspect
from datetime import UTC, datetime
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest
from gateway import org_facts
from gateway.crm_sync import catalogue as cat

REPO = Path(__file__).resolve().parents[2]
GATEWAY = REPO / "apps/services/gateway/gateway"
NOW = datetime(2026, 10, 18, 9, 0, tzinfo=UTC)


# ── Item 1: the registry and the row ────────────────────────────────────────


def test_the_builder_names_a_role_never_an_email() -> None:
    row = org_facts.fact("crm", "module:Invoices", "purpose", "invoice", "M.", "admin", NOW)
    assert (row.set_by, row.source) == ("admin", "admin")
    assert org_facts.fact("crm", "s", "k", None, "M.", "ai").set_by == "Jev"
    assert org_facts.fact("crm", "s", "k", None, "M.", "code").set_by == "Metorite"
    odd = org_facts.fact("crm", "s", "k", None, "M.", "someone@example.test")
    assert (odd.source, odd.set_by) == ("code", "Metorite")
    assert set(row.as_json()) == {"app", "subject", "key", "value", "meaning", "source",
                                  "set_at", "set_by"}
    assert row.as_json()["set_at"] == NOW.isoformat()


async def test_read_asks_each_provider_and_filters_by_app_and_q(monkeypatch) -> None:
    monkeypatch.setattr(org_facts, "_REGISTRY", {})
    seen: list[Any] = []

    async def crm(db, user):
        seen.append((db, user))
        return [org_facts.fact("crm", "term:revenue", "revenue", "invoice",
                               "Revenue means billed.", "code"),
                org_facts.fact("crm", "module:Towers", "purpose", "asset",
                               "Towers is an asset module.", "admin")]

    async def other(db, user):
        return [org_facts.fact("email", "term:inbox", "k", "v", "Inbox.", "code")]

    org_facts.register("crm", crm)
    org_facts.register("email", other)
    monkeypatch.setattr(org_facts, "PROVIDER_MODULES", ())
    db, user = object(), object()
    assert [r.app for r in await org_facts.read(db, user)] == ["crm", "crm", "email"]
    assert seen == [(db, user)], "a provider reads in the caller's session"
    assert [r.subject for r in await org_facts.read(db, user, app="crm", q="TOWER")] == [
        "module:Towers"]
    assert [r.key for r in await org_facts.read(db, user, q="billed")] == ["revenue"]
    assert await org_facts.read(db, user, app="nothing") == []


def test_the_crm_provider_is_registered_on_import() -> None:
    from gateway.routes.crm import facts

    assert org_facts.providers()["crm"] is facts.crm_facts


# ── Item 8: one builder of a fact row ───────────────────────────────────────


def _builders(tree: ast.AST) -> tuple[set[str], set[str]]:
    """The names that call the row builders of ``gateway.org_facts`` in one
    module: the bare names imported from it, and the aliases of the module."""
    names: set[str] = set()
    modules: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom) and node.module == "gateway.org_facts":
            names |= {a.asname or a.name for a in node.names if a.name in ("Fact", "fact")}
        elif isinstance(node, ast.ImportFrom) and node.module == "gateway":
            modules |= {a.asname or a.name for a in node.names if a.name == "org_facts"}
        elif isinstance(node, ast.Import):
            modules |= {a.asname or a.name for a in node.names if a.name == "gateway.org_facts"}
    return names, modules


def _row_builds(path: Path) -> int:
    tree = ast.parse(path.read_text(encoding="utf-8"))
    names, modules = _builders(tree)
    found = 0
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        func = node.func
        if (isinstance(func, ast.Name) and func.id in names) or (isinstance(func, ast.Attribute) and func.attr in ("Fact", "fact")
              and ast.unparse(func.value) in modules | {"gateway.org_facts"}):
            found += 1
    return found


def test_no_module_outside_the_seam_and_the_providers_builds_a_fact_row() -> None:
    allowed = {GATEWAY / "org_facts.py"} | {
        GATEWAY / (name.removeprefix("gateway.").replace(".", "/") + ".py")
        for name in org_facts.PROVIDER_MODULES
    }
    hits = [p.relative_to(GATEWAY).as_posix() for p in GATEWAY.rglob("*.py")
            if p not in allowed and _row_builds(p)]
    assert hits == [], hits
    # The detector is not vacuous: the provider builds rows through it.
    assert _row_builds(GATEWAY / "routes/crm/facts.py") >= 5


# ── Item 2: the facts of the CRM provider ───────────────────────────────────


def _entries() -> list[Any]:
    def f(key: str, kind: str, target: str | None = None) -> cat.Field:
        return cat.Field(key, key.replace("_", " "), kind, target)

    invoices = cat.Source("Invoices", "module:Invoices", "module", "Invoices", 30,
                          custom=False, fields=(f("Total", "currency"), f("Day", "date")),
                          fills={"Total": 0.9, "Day": 0.95})
    collection = cat.Source("Payment_Received", "module:Payment_Received", "module",
                            "Collection", 8, fields=(f("A", "currency"), f("B", "currency")),
                            fills={"A": 0.6, "B": 0.6})
    towers = cat.Source("Towers", "module:Towers", "module", "Towers", 0)
    items = [
        {"kind": "catalogue", "module": "Invoices", "amount": "Total", "date": "Day",
         "by": {"amount": "admin", "date": "ai"}},
        {"kind": "catalogue", "module": "Payment_Received", "amount": None, "status": None,
         "by": {"amount": "admin", "status": "ai"}},
    ]
    return cat.build([invoices, collection, towers], {}, items)


def test_the_crm_facts_of_the_entries_and_the_plan() -> None:
    from gateway.routes.crm import facts

    entries = {e.module: e for e in _entries()}
    rows = facts.entry_facts(entries["Invoices"], NOW)
    by_key = {r.key: r for r in rows}
    assert by_key["purpose"].meaning == "Invoices holds invoice records."
    assert (by_key["purpose"].source, by_key["purpose"].set_at) == ("code", None)
    assert by_key["amount"].subject == "field:Invoices.Total"
    assert by_key["amount"].meaning == "The amount of Invoices is Total (Total)."
    assert (by_key["amount"].set_by, by_key["amount"].set_at) == ("admin", NOW)
    assert by_key["date"].set_by == "Jev"
    rows = {r.key: r for r in facts.entry_facts(entries["Payment_Received"], NOW)}
    # The admin left the amount as none: a fact. The AI left the status as
    # null: an open question, not a fact.
    assert rows["amount"].meaning == "Collection has no single amount."
    assert rows["amount"].value is None and "status" not in rows
    revenue = facts.revenue_fact(_entries(), {}, NOW)
    assert (revenue.value, revenue.source, revenue.meaning) == (
        "invoice", "code", "Revenue means billed: the amount of Invoices.")
    stored = {"@org": {"kind": "catalogue", "module": "@org", "revenue": "payment_in",
                       "by": {"revenue": "admin"}}}
    revenue = facts.revenue_fact(_entries(), stored, NOW)
    assert (revenue.value, revenue.set_by, revenue.meaning) == (
        "payment_in", "admin", "Revenue means collected: the amount of Collection.")
    plan = facts.plan_facts([
        {"kind": "role", "slot": "deal", "module": "Payment_Received"},
        {"kind": "stage", "slot": "deal", "module": "Payment_Received",
         "field_api_name": "Stage", "values": {"Paid": "won", "Due": "open"}},
    ], {"Payment_Received": "Collection"}, NOW)
    assert [r.meaning for r in plan] == [
        "Collection plays Deals: its records show there.",
        "In Collection, the Stage value Due means Open.",
        "In Collection, the Stage value Paid means Won.",
    ]
    assert {r.set_by for r in plan} == {"admin"}


def test_the_fact_of_a_sum_names_each_part() -> None:
    """CRM-Q32-9a item 9: the org-facts row of an amount that is a sum."""
    from gateway.routes.crm import facts

    collection = cat.Source(
        "Payment_Received", "module:Payment_Received", "module", "Collection", 8681,
        fields=(cat.Field("Billing_Amount", "Rent Net Amount", "currency"),
                cat.Field("IP", "IP Basic Amount", "currency"),
                cat.Field("EB_Net_Amount", "EB Net Amount", "currency")),
        fills={"Billing_Amount": 0.52, "IP": 0.52, "EB_Net_Amount": 0.46})
    item = {"kind": "catalogue", "module": "Payment_Received", "purpose": "payment_in",
            "amount": ["Billing_Amount", "IP", "EB_Net_Amount"],
            "by": {"purpose": "admin", "amount": "admin"}}
    (entry,) = cat.build([collection], {}, [item])
    rows = {r.key: r for r in facts.entry_facts(entry, NOW)}
    assert rows["amount"].meaning == (
        "The amount of Collection is the sum of Rent Net Amount, IP Basic Amount and "
        "EB Net Amount.")
    assert rows["amount"].value == "Billing_Amount+IP+EB_Net_Amount"
    assert rows["amount"].set_by == "admin"


async def test_the_provider_gives_nothing_outside_the_list_or_the_access_rule(
    monkeypatch,
) -> None:
    from fastapi import HTTPException
    from gateway.routes.crm import facts

    user = SimpleNamespace(organization_id="o-1", email="m@x.test")
    monkeypatch.setattr(facts, "questions_allowed", lambda org: False)
    assert await facts.crm_facts(object(), user) == []
    monkeypatch.setattr(facts, "questions_allowed", lambda org: True)

    async def refuse(u):
        raise HTTPException(status_code=403, detail="no")

    monkeypatch.setattr(facts, "require_crm_access", refuse)
    assert await facts.crm_facts(object(), user) == []


# ── Item 4: the tool, with the crm family only ──────────────────────────────


def test_the_tool_loads_with_the_crm_family_only() -> None:
    import orchestrator._tool_injection as ti
    from acb_skills import skill_families as sf
    from acb_skills.tool_annotations import TOOL_ANNOTATIONS

    assert "org_facts" in sf.SKILL_FAMILIES["crm"]["tools"]
    assert "org_facts" not in ti._CORE_STANDARD_TOOL_NAMES
    families = [k for k, v in sf.SKILL_FAMILIES.items() if "org_facts" in v["tools"]]
    assert families == ["crm"]
    assert TOOL_ANNOTATIONS["org_facts"]["read_only"] is True
    assert TOOL_ANNOTATIONS["org_facts"]["open_world"] is False


async def test_the_tool_reads_the_route_as_the_member(monkeypatch) -> None:
    from acb_skills import org_facts_tools as tools

    calls: list[tuple[str, dict]] = []

    async def fake_get(path, params=None):
        calls.append((path, dict(params or {})))
        return {"facts": [{"subject": "term:revenue", "key": "revenue",
                           "meaning": "Revenue means billed.", "set_by": "admin"}]}

    monkeypatch.setattr(tools, "_get", fake_get)
    out = await tools.org_facts("crm", "revenue")
    assert calls == [("/org/facts", {"app": "crm", "q": "revenue"})]
    assert out.splitlines() == [
        "Facts of this organization (fixed answers):",
        "- Revenue means billed. (term:revenue; set by admin)",
    ]

    async def none_get(path, params=None):
        return {"facts": []}

    monkeypatch.setattr(tools, "_get", none_get)
    assert "No facts" in await tools.org_facts()

    async def many_get(path, params=None):
        return {"facts": [{"subject": f"s{i}", "key": "amount", "meaning": f"Fact {i}.",
                           "set_by": "Jev"} for i in range(70)]}

    monkeypatch.setattr(tools, "_get", many_get)
    first = await tools.org_facts()
    assert "Fact 59." in first and "Fact 60." not in first
    assert "offset=60" in first.splitlines()[-1]
    rest = await tools.org_facts(offset=60)
    assert "Fact 60." in rest and "Fact 69." in rest and "Fact 59." not in rest


# ── Item 5: the block, 12 lines at most ─────────────────────────────────────


def _row(key: str, meaning: str) -> dict:
    return {"key": key, "meaning": meaning, "subject": "s"}


def test_the_block_holds_twelve_lines_at_most_and_only_money_and_stages() -> None:
    from acb_skills.org_facts_tools import BLOCK_LINES, BLOCK_MARKER, crm_facts_block

    rows = [_row("revenue", "Revenue means billed."), _row("purpose", "A purpose."),
            *[_row("amount", f"Amount {i}.") for i in range(20)],
            _row("stage:Won", "In X, Won means Won.")]
    block = crm_facts_block(rows)
    lines = block.splitlines()
    assert BLOCK_LINES == 12 and len(lines) == 12
    assert lines[0].startswith(BLOCK_MARKER)
    assert lines[1] == "- Revenue means billed."
    assert "A purpose." not in block
    # 22 facts, 10 shown: the last line names the rest, so none is lost.
    assert lines[-1] == "- 12 more facts: call org_facts to read them."
    exact = crm_facts_block([_row("amount", f"Amount {i}.") for i in range(11)])
    assert len(exact.splitlines()) == 12 and "more facts" not in exact
    assert crm_facts_block([_row("purpose", "A purpose.")]) == ""
    assert crm_facts_block([]) == ""


class _MafAgent:
    def __init__(self, text: str = "Base prompt.") -> None:
        self.default_options = {"instructions": text, "tools": []}


class _OldAgent:
    def __init__(self) -> None:
        self.instructions = "Old prompt."


class _CopilotAgent:
    def __init__(self) -> None:
        self._tools: list = []
        self._default_options = {"system_message": {"mode": "append", "content": "Sys."}}


@pytest.fixture
def questions_on(monkeypatch):
    """The box lists every org, and the run belongs to ``org-a``."""
    import orchestrator._tool_injection as ti
    from acb_common import get_settings

    monkeypatch.setattr(get_settings(), "crm_questions_orgs", "*", raising=False)
    monkeypatch.setattr(ti, "_run_org_or_none", lambda: "org-a")


async def test_a_box_with_the_list_empty_sends_no_request(monkeypatch) -> None:
    import orchestrator._tool_injection as ti
    from acb_common import get_settings
    from acb_skills import org_facts_tools as tools

    monkeypatch.setattr(get_settings(), "crm_questions_orgs", "", raising=False)
    reads: list[int] = []

    async def rows():
        reads.append(1)
        return [_row("revenue", "Revenue means billed.")]

    monkeypatch.setattr(tools, "crm_facts_for_run", rows)
    agent = _MafAgent()
    agent.default_options["tools"].append(tools.org_facts)
    held = await ti._add_crm_facts_block([agent], frozenset({"crm_aggregate", "org_facts"}))
    assert reads == [] and agent.default_options["instructions"] == "Base prompt."
    # The tool goes with the facts: a dark box keeps no org_facts.
    assert held == {"crm_aggregate"} and agent.default_options["tools"] == []


async def test_the_run_gets_the_block_only_with_a_crm_tool(monkeypatch, questions_on) -> None:
    import orchestrator._tool_injection as ti
    from acb_skills import org_facts_tools as tools

    reads: list[int] = []

    async def block():
        reads.append(1)
        return [_row("revenue", "Revenue means billed.")]

    monkeypatch.setattr(tools, "crm_facts_for_run", block)
    agents = [_MafAgent(), _OldAgent(), _CopilotAgent()]
    await ti._add_crm_facts_block(agents, frozenset({"web_search"}))
    assert reads == [] and agents[0].default_options["instructions"] == "Base prompt."
    await ti._add_crm_facts_block(agents, frozenset({"crm_aggregate"}), no_egress=True)
    assert reads == []
    await ti._add_crm_facts_block(agents, frozenset({"crm_aggregate", "web_search"}))
    assert reads == [1]
    assert agents[0].default_options["instructions"].endswith("- Revenue means billed.")
    assert agents[0].default_options["instructions"].startswith("Base prompt.")
    assert agents[1].instructions.endswith("- Revenue means billed.")
    assert agents[2]._default_options["system_message"]["content"].endswith(
        "- Revenue means billed.")
    # A second append of one run adds nothing.
    await ti._add_crm_facts_block(agents, frozenset({"crm_aggregate"}))
    assert agents[0].default_options["instructions"].count("## CRM facts") == 1


async def test_a_failed_read_adds_no_block(monkeypatch, questions_on) -> None:
    import orchestrator._tool_injection as ti
    from acb_skills import org_facts_tools as tools

    async def broken(path, params=None):
        raise RuntimeError("gateway down")

    monkeypatch.setattr(tools, "_get", broken)
    agent = _MafAgent()
    agent.default_options["tools"].append(tools.org_facts)
    held = await ti._add_crm_facts_block([agent], frozenset({"crm_aggregate", "org_facts"}))
    assert agent.default_options["instructions"] == "Base prompt."
    assert held == {"crm_aggregate"} and agent.default_options["tools"] == []


# ── Item 7: a run of org A never carries the block of org B ─────────────────


async def test_a_run_of_org_a_never_carries_the_block_of_org_b(
    monkeypatch, questions_on,
) -> None:
    """The block is read as the run's member, with no org argument, and
    nothing keeps it between runs. A gateway that answers by the member
    proves it: each run carries the facts of its own org only."""
    import orchestrator._tool_injection as ti
    from acb_skills import crm_tools
    from acb_skills import org_facts_tools as tools

    facts_by_member = {
        "a@org-a.test": [_row("revenue", "Revenue means billed: the amount of A-Invoices.")],
        "b@org-b.test": [_row("revenue", "Revenue means collected: the amount of B-Receipts.")],
    }
    member = {"now": ""}

    async def gateway(path, params=None):
        assert path == "/org/facts" and params == {"app": "crm"}
        return {"facts": facts_by_member[member["now"]]}

    monkeypatch.setattr(tools, "_get", gateway)
    for email, own, other in (("a@org-a.test", "A-Invoices", "B-Receipts"),
                              ("b@org-b.test", "B-Receipts", "A-Invoices"),
                              ("a@org-a.test", "A-Invoices", "B-Receipts")):
        member["now"] = email
        agent = _MafAgent()
        await ti._add_crm_facts_block([agent], frozenset({"crm_list_modules"}))
        text = agent.default_options["instructions"]
        assert own in text and other not in text, email
    # No org reaches the read from the run, and nothing keeps a block.
    assert "org" not in inspect.signature(ti._add_crm_facts_block).parameters
    assert not inspect.signature(tools.crm_facts_block_for_run).parameters
    assert not inspect.signature(tools.crm_facts_for_run).parameters
    source = inspect.getsource(tools)
    assert "lru_cache" not in source and "_CACHE" not in source
    assert inspect.getsource(crm_tools._headers).count("X-User-Email") == 1


def test_each_run_assembly_adds_the_block_after_the_injection() -> None:
    source = (REPO / "apps/services/orchestrator/orchestrator/executor.py").read_text(
        encoding="utf-8")
    assert source.count("await _add_crm_facts_block(") == 3
    assert source.count("_inject_agent_tools(\n") == 3


@pytest.mark.parametrize("name", ["org_facts.py", "routes/org_facts.py", "routes/crm/facts.py"])
def test_the_seam_files_take_no_org_from_input(name: str) -> None:
    text = (GATEWAY / name).read_text(encoding="utf-8")
    assert "X-Organization" not in text and "organization_id: str = Query" not in text


# ── Fix round P0: the two REAL agents get the tool and the block ────────────


def _config(agent_dir: str) -> dict:
    import json

    return json.loads((REPO / "apps/agents" / agent_dir / "config.json").read_text(
        encoding="utf-8"))


#: The tools that go together (CRM-Q32-8, "Withholding"): org_facts and the
#: reply of a CRM question card.
GATED = ("org_facts", "crm_answer_question")


def test_the_reply_tool_is_withheld_wherever_org_facts_is(monkeypatch) -> None:
    """CRM-Q32-8 item 20: one list of names feeds the three withholding
    sites, and the addendum hides both on a box with the list empty."""
    import orchestrator._tool_injection as ti
    from acb_common import get_settings
    from acb_skills import org_facts_tools
    from acb_skills.addendum import _dark_tools

    assert {fn.__name__ for fn in org_facts_tools.FACTS_GATED_TOOLS} == set(GATED)
    monkeypatch.setattr(get_settings(), "crm_questions_orgs", "other-org", raising=False)
    monkeypatch.setattr(ti, "_run_org_or_none", lambda: "org-a")
    assert ti._facts_withheld() == frozenset(GATED)
    monkeypatch.setattr(ti, "_run_org_or_none", lambda: "other-org")
    assert ti._facts_withheld() == frozenset()
    monkeypatch.setattr(get_settings(), "crm_questions_orgs", "", raising=False)
    assert set(GATED) <= _dark_tools()
    monkeypatch.setattr(get_settings(), "crm_questions_orgs", "*", raising=False)
    assert not set(GATED) & _dark_tools()


def _pool_names(agents: list[Any]) -> set[str]:
    import orchestrator._tool_injection as ti

    return {ti._tool_name(t) for a in agents for _x, pool in ti._own_tool_pools(a)
            for t in pool}


def _instructions(agent: Any) -> str:
    do = getattr(agent, "default_options", None)
    if isinstance(do, dict):
        return do.get("instructions") or ""
    return getattr(agent, "instructions", "") or ""


async def _assemble(agent_dir: str, *, listed: bool, monkeypatch) -> tuple[list, frozenset]:
    """One run of *agent_dir* through the injection and the facts block, with
    its real ``config.json``. The CRM Assistant is built from its own file.
    The facts read answers a row for a listed org and none for another."""
    import orchestrator._tool_injection as ti
    from acb_skills import org_facts_tools as tools

    async def rows():
        return [_row("revenue", "Revenue means billed: the amount of Invoices.")] if listed else []

    monkeypatch.setattr(tools, "crm_facts_for_run", rows)
    cfg = _config(agent_dir)
    if agent_dir == "agent-crm":
        from tests.unit._crm_agent_fakes import load_agent_module

        agents = load_agent_module("crm_assistant_q32_fix").build_agents()
        ti._apply_own_tool_scope(agents, cfg.get("own_tool_scope") or None)
    else:
        agents = [_MafAgent()]
    run_tools = ti._inject_agent_tools(
        agents, tool_scope=cfg.get("tool_scope") or None, agent_name=cfg.get("name"),
        agent_config=cfg, no_egress=False)
    held = await ti._add_crm_facts_block(agents, run_tools)
    return agents, held


@pytest.mark.parametrize("agent_dir", ["agent-orchestrator", "agent-crm"])
async def test_the_real_agents_get_the_tool_and_the_block_for_a_listed_org(
    agent_dir: str, monkeypatch, questions_on,
) -> None:
    pytest.importorskip("agent_framework")
    agents, held = await _assemble(agent_dir, listed=True, monkeypatch=monkeypatch)
    # CRM-Q32-8 item 20: crm_answer_question goes with org_facts.
    for name in GATED:
        assert name in _pool_names(agents) and name in held, name
    assert any("Revenue means billed: the amount of Invoices." in _instructions(a)
               for a in agents)


@pytest.mark.parametrize("agent_dir", ["agent-orchestrator", "agent-crm"])
async def test_the_real_agents_get_neither_for_an_org_off_the_list(
    agent_dir: str, monkeypatch, questions_on,
) -> None:
    pytest.importorskip("agent_framework")
    agents, held = await _assemble(agent_dir, listed=False, monkeypatch=monkeypatch)
    for name in GATED:
        assert name not in _pool_names(agents) and name not in held, name
    assert not any("## CRM facts" in _instructions(a) for a in agents)


def test_both_real_configs_name_the_tool() -> None:
    """The scope gives tool_scope and the core floor, with no family expansion, so each
    agent that reads the CRM names org_facts itself."""
    import orchestrator._tool_injection as ti

    for agent_dir in ("agent-orchestrator", "agent-crm"):
        scope = ti._resolve_injected_scope(_config(agent_dir)["tool_scope"])
        for name in GATED:
            assert scope is not None and name in scope, (agent_dir, name)


# ── Follow-up: org_facts follows the run's org, not only the box ────────────


def _copilot_with_tools() -> Any:
    agent = _CopilotAgent()
    agent._default_options["system_message"] = {"mode": "append", "content": "Sys."}
    return agent


@pytest.mark.parametrize(("run_org", "held"), [("org-a", False), ("other-org", True)])
async def test_an_org_off_a_listed_box_gets_no_org_facts_and_no_prompt_word(
    monkeypatch, run_org: str, held: bool,
) -> None:
    """A box that lists "other-org": a run of org-a holds no ``org_facts``,
    its addendum names no ``org_facts``, and it sends no facts read. A run
    of other-org holds the tool and the section."""
    pytest.importorskip("agent_framework")
    import orchestrator._tool_injection as ti
    from acb_common import get_settings
    from acb_skills import org_facts_tools as tools

    monkeypatch.setattr(get_settings(), "crm_questions_orgs", "other-org", raising=False)
    monkeypatch.setattr(ti, "_run_org_or_none", lambda: run_org)
    ti._build_injected_tools_addendum.cache_clear()
    reads: list[int] = []

    async def rows():
        reads.append(1)
        return [_row("revenue", "Revenue means billed.")]

    monkeypatch.setattr(tools, "crm_facts_for_run", rows)
    cfg = _config("agent-orchestrator")
    agent = _copilot_with_tools()
    run_tools = ti._inject_agent_tools(
        [agent], tool_scope=cfg["tool_scope"], agent_name=cfg["name"], agent_config=cfg,
        no_egress=False)
    run_tools = await ti._add_crm_facts_block([agent], run_tools)
    prompt = agent._default_options["system_message"]["content"]
    for name in GATED:
        assert (name in run_tools) is held, name
        assert (name in _pool_names([agent])) is held, name
        assert (f"{name}(" in prompt) is held, name
    assert ("## CRM facts" in prompt) is held
    assert reads == ([1] if held else [])
    ti._build_injected_tools_addendum.cache_clear()


def test_the_list_grammar_of_the_run_side() -> None:
    from acb_common import get_settings
    from acb_skills.crm_tools import questions_org_on

    settings = get_settings()
    old = settings.crm_questions_orgs
    try:
        for raw, org, on in (("", "a1", False), ("*", "a1", True), ("*", None, False),
                             ("b2, A1", "a1", True), ("b2", "a1", False)):
            settings.crm_questions_orgs = raw
            assert questions_org_on(org) is on, (raw, org)
    finally:
        settings.crm_questions_orgs = old


# ── CRM-Q32-8 fix round: a run that lost the reply tool shows no card ───────


@pytest.mark.parametrize(("rows", "no_egress", "tools", "held"), [
    # The facts read gives rows, and the run holds the reply tool.
    (True, False, {"crm_aggregate", "org_facts", "crm_answer_question"}, True),
    # A failed or empty facts read drops the tool with org_facts.
    (False, False, {"crm_aggregate", "org_facts", "crm_answer_question"}, False),
    # A no_egress run reads nothing, so it drops the tool too.
    (True, True, {"crm_aggregate", "org_facts", "crm_answer_question"}, False),
    # A run whose scope never named the tool.
    (True, False, {"crm_aggregate", "org_facts"}, False),
])
async def test_the_assembly_tells_the_holder_whether_the_run_holds_the_reply(
    monkeypatch, questions_on, rows: bool, no_egress: bool, tools: set, held: bool,
) -> None:
    import acb_skills.crm_tools as crm
    import orchestrator._tool_injection as ti
    from acb_skills import org_facts_tools

    async def read():
        return [_row("revenue", "Revenue means billed.")] if rows else []

    monkeypatch.setattr(org_facts_tools, "crm_facts_for_run", read)
    token = crm.open_asks_for_run()
    try:
        crm.mark_answer_tool(not held)  # the opposite, so the call must set it
        await ti._add_crm_facts_block([_MafAgent()], frozenset(tools), no_egress=no_egress,
                                      mark_run=True)
        assert crm.run_asks().answer_tool is held
        # A sub-agent never marks the holder of its parent.
        await ti._add_crm_facts_block([_MafAgent()], frozenset({"crm_aggregate"}))
        assert crm.run_asks().answer_tool is held
    finally:
        crm.release_asks(token)


def test_only_the_streamed_run_marks_the_holder() -> None:
    source = (REPO / "apps/services/orchestrator/orchestrator/executor.py").read_text(
        encoding="utf-8")
    assert source.count("mark_run=True") == 1
    assert source.index("_asks_token = open_asks_for_run()") < source.index("mark_run=True")
