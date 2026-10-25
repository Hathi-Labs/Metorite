"""The assistant profile per organization (WS-63 PZ-25, personalization.md §14.2).

D113. ``ASSISTANT_PROFILE_ENABLED`` is the master switch, and
``ASSISTANT_PROFILE_ORGS`` is the list. ``assistant_profile.enabled_for(org)``
is the one reader. The truth table of the rule is in
``tests/unit/test_assistant_profile.py`` (``test_enabled_for_follows_the_rule``),
with the two GET routes (``TestTheRoutesPerOrg``).

R7 fences named here:

* ``pz25-no-bare-enabled``: no code in ``apps/`` or ``packages/`` calls a
  bare ``enabled()`` of ``acb_common.assistant_profile``, and the module has
  no ``enabled``. A site that reads the switch passes the organization.
* ``pz25-block``: the executor block is empty for an org outside the list,
  and that run reads nothing.
* ``pz25-tools``: the orchestrator holds ``resolve_person``, the profile
  writes and the persona line only for an org on the list.
* ``pz25-whatsapp``: the WhatsApp scope rule has the profile sentences only
  for an org on the list.
"""

from __future__ import annotations

import ast
from pathlib import Path
from typing import Any

import pytest
from acb_common import assistant_profile as ap
from acb_common import get_settings
from acb_skills import profile_tools as pt
from gateway.routes.whatsapp_channel import bot_run
from orchestrator import executor as ex

from tests.unit import test_executor_profile_block as epb
from tests.unit.test_executor_profile_block import run  # noqa: F401

ROOT = Path(__file__).resolve().parents[2]
IN = epb.ORG
OUT = "bbbbbbbb-2222-4222-8222-222222222222"
MEMBER = "ana@alpha.example"


def _switch(monkeypatch: pytest.MonkeyPatch, master: bool, orgs: str) -> None:
    monkeypatch.setattr(get_settings(), "assistant_profile_enabled", master, raising=False)
    monkeypatch.setattr(get_settings(), "assistant_profile_orgs", orgs, raising=False)


# ── pz25-no-bare-enabled ───────────────────────────────────────────────────


def _module_names(tree: ast.AST) -> set[str]:
    """The local names that a file binds to ``acb_common.assistant_profile``."""
    names: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom) and node.module == "acb_common":
            names |= {a.asname or a.name for a in node.names
                      if a.name == "assistant_profile"}
        elif isinstance(node, ast.Import):
            names |= {a.asname for a in node.names
                      if a.name == "acb_common.assistant_profile" and a.asname}
    return names


def _bare_enabled_sites(path: Path) -> list[str]:
    text = path.read_text(encoding="utf-8-sig")
    try:
        tree = ast.parse(text, filename=str(path))
    except SyntaxError:
        # A template that is not Python. It fails closed on the plain text.
        if "assistant_profile" in text and ".enabled()" in text:
            return [f"{path}: unparsed, and it names assistant_profile and .enabled()"]
        return []
    names = _module_names(tree)
    found: list[str] = []
    for node in ast.walk(tree):
        if (isinstance(node, ast.ImportFrom)
                and node.module == "acb_common.assistant_profile"
                and any(a.name == "enabled" for a in node.names)):
            found.append(f"{path}:{node.lineno} imports enabled")
        if isinstance(node, ast.Attribute) and node.attr == "enabled":
            base = node.value
            if isinstance(base, ast.Name) and base.id in names:
                found.append(f"{path}:{node.lineno} {base.id}.enabled")
            if (isinstance(base, ast.Attribute) and base.attr == "assistant_profile"
                    and isinstance(base.value, ast.Name) and base.value.id == "acb_common"):
                found.append(f"{path}:{node.lineno} acb_common.assistant_profile.enabled")
    return found


def _sources() -> list[Path]:
    out: list[Path] = []
    for top in ("apps", "packages"):
        out += [p for p in (ROOT / top).rglob("*.py")
                if "node_modules" not in p.parts and ".venv" not in p.parts]
    return out


def test_no_code_reads_the_switch_without_the_org() -> None:
    files = _sources()
    assert len(files) > 500, "non-vacuity: the walk reads the tree"
    found = [hit for p in files for hit in _bare_enabled_sites(p)]
    assert found == [], found


def test_the_fence_sees_each_form(tmp_path: Path) -> None:
    """Non-vacuity: each form of the old call is a hit."""
    cases = {
        "a.py": "from acb_common import assistant_profile\nassistant_profile.enabled()\n",
        "b.py": "from acb_common import assistant_profile as x\nx.enabled()\n",
        "c.py": "from acb_common.assistant_profile import enabled\n",
        "d.py": "import acb_common\nacb_common.assistant_profile.enabled()\n",
        "e.py": "from acb_common import assistant_profile\nassistant_profile.enabled_for(o)\n",
    }
    hits = {}
    for name, body in cases.items():
        (tmp_path / name).write_text(body, encoding="utf-8")
        hits[name] = bool(_bare_enabled_sites(tmp_path / name))
    assert hits == {"a.py": True, "b.py": True, "c.py": True, "d.py": True, "e.py": False}


def test_the_module_has_no_bare_enabled() -> None:
    assert not hasattr(ap, "enabled")
    assert callable(ap.enabled_for)


# ── pz25-block ─────────────────────────────────────────────────────────────


def test_an_org_outside_the_list_gets_no_block_and_no_read(run, monkeypatch) -> None:  # noqa: F811
    _switch(monkeypatch, True, OUT)
    assert ex._member_profile_entry("orchestrator") == ("", "", "")
    assert run["reads"] == []


def test_an_org_on_the_list_gets_the_block(run, monkeypatch) -> None:  # noqa: F811
    _switch(monkeypatch, True, f"{OUT}, {IN.upper()}")
    assert ex._member_profile_block("orchestrator") == epb.GOLDEN
    assert run["reads"] == [(IN, epb.MEMBER)]


def test_a_run_with_no_org_gets_no_block(run, monkeypatch) -> None:  # noqa: F811
    _switch(monkeypatch, True, "")
    run["org"] = None
    assert ex._member_profile_entry("orchestrator") == ("", "", "")
    assert run["reads"] == []


def test_the_master_off_wins_over_the_list(run, monkeypatch) -> None:  # noqa: F811
    _switch(monkeypatch, False, IN)
    assert ex._member_profile_entry("orchestrator") == ("", "", "")
    assert run["reads"] == []


def test_the_cache_does_not_carry_one_org_to_another(run, monkeypatch) -> None:  # noqa: F811
    """A block cached for an org on the list never reaches an org off it."""
    _switch(monkeypatch, True, IN)
    assert ex._member_profile_block("orchestrator") == epb.GOLDEN
    run["org"] = OUT
    assert ex._member_profile_block("orchestrator") == ""


# ── pz25-tools ─────────────────────────────────────────────────────────────


class _FakeAgent:
    def __init__(self, **kwargs: Any) -> None:
        self.kwargs = kwargs


def _mod() -> Any:
    import orchestrator.agents as pkg

    return pkg._mod


def _build(monkeypatch: pytest.MonkeyPatch, organization_id: str | None = None) -> Any:
    mod = _mod()
    monkeypatch.setattr(mod, "Agent", _FakeAgent)
    monkeypatch.setattr(mod, "_make_openai_client", lambda *a, **k: object())
    monkeypatch.setattr(mod, "_load_specialist_agents_as_tools", lambda: [])
    return mod.build_orchestrator_agent(with_history=False,
                                        organization_id=organization_id)


def _has_profile(agent: Any) -> bool:
    tools = agent.kwargs["tools"]
    _old, new = _mod()._PROFILE_SWAP
    has_tools = pt.resolve_person in tools and all(t in tools for t in pt.WRITE_TOOLS)
    has_line = new in agent.kwargs["instructions"]
    assert has_tools is has_line, "the tools and the persona line agree"
    if not has_tools:
        assert not any(t in tools for t in pt.WRITE_TOOLS)
        assert pt.resolve_person not in tools
        assert agent.kwargs["instructions"] is _mod()._PULL_INSTRUCTIONS
    return has_tools


def test_the_tools_follow_the_caller_s_org(monkeypatch) -> None:
    _switch(monkeypatch, True, IN)
    assert _has_profile(_build(monkeypatch, IN)) is True
    assert _has_profile(_build(monkeypatch, OUT)) is False


def test_the_tools_follow_the_bound_tenant(monkeypatch) -> None:
    from acb_common.db import bind_tenant, release_tenant

    _switch(monkeypatch, True, IN)
    for org, want in ((IN, True), (OUT, False)):
        token = bind_tenant(org)
        try:
            assert _has_profile(_build(monkeypatch)) is want, org
        finally:
            release_tenant(token)


def test_the_tools_follow_the_run_org(monkeypatch) -> None:
    """A run of the executor binds ``_RUN_ORG`` before it builds the agent."""
    _switch(monkeypatch, True, IN)
    monkeypatch.setattr(ex, "_current_run_org", lambda: IN)
    assert _has_profile(_build(monkeypatch)) is True
    monkeypatch.setattr(ex, "_current_run_org", lambda: OUT)
    assert _has_profile(_build(monkeypatch)) is False


def test_an_unbound_build_has_no_profile_tools(monkeypatch) -> None:
    """A build at startup or import has no org, so it fails closed."""
    from acb_common.db import clear_tenant, release_tenant

    _switch(monkeypatch, True, "")
    token = clear_tenant()
    try:
        assert ex._current_run_org() is None
        assert _has_profile(_build(monkeypatch)) is False
    finally:
        release_tenant(token)


def test_the_master_off_gives_no_tools(monkeypatch) -> None:
    _switch(monkeypatch, False, IN)
    assert _has_profile(_build(monkeypatch, IN)) is False


def test_the_chat_routes_pass_the_session_org() -> None:
    """``/copilot/chat`` and ``/pull`` build the agent before the run binds.
    So each passes the org of its ``UserContext``."""
    tree = ast.parse((ROOT / "apps/services/gateway/gateway/main.py")
                     .read_text(encoding="utf-8"))
    by_route: dict[str, list[ast.Call]] = {}
    for fn in ast.walk(tree):
        if isinstance(fn, ast.AsyncFunctionDef) and fn.name in ("copilot_chat", "pull"):
            by_route[fn.name] = [
                c for c in ast.walk(fn) if isinstance(c, ast.Call)
                and getattr(c.func, "id", "") == "build_orchestrator_agent"]
    assert set(by_route) == {"copilot_chat", "pull"}
    for name, calls in by_route.items():
        assert calls, name
        for call in calls:
            assert "organization_id" in {k.arg for k in call.keywords}, name


# ── pz25-whatsapp ──────────────────────────────────────────────────────────


def _rule(org: str | None) -> str:
    return bot_run.build_payload("hi", [], MEMBER, native=True, writes=True,
                                 org=org)["system_context"]


def test_whatsapp_has_the_profile_sentences_only_on_the_list(monkeypatch) -> None:
    _switch(monkeypatch, True, IN)
    on = _rule(IN)
    assert bot_run.PROFILE_EXCEPTION_RULE in on
    assert bot_run.PROFILE_CHANNEL_RULE in on
    for off in (_rule(OUT), _rule(None), _rule("")):
        assert off == bot_run.SCOPE_RULE_WRITES


def test_whatsapp_master_off_wins(monkeypatch) -> None:
    _switch(monkeypatch, False, IN)
    assert _rule(IN) == bot_run.SCOPE_RULE_WRITES
