"""Every agent reads the house voice, once, on both runtimes (WS-52 S1).

Owning spec: ``project-docs/specs/agent_writing_voice.md``.

``acb_llm.voice`` holds the contract (``CORE``), one overlay for each surface,
and ``voice_lint``, a deterministic checker. ``_tool_injection._apply_voice``
is the one place that puts the agent block into a system prompt. It runs
first in ``_inject_agent_tools``, so it follows the agent's own instructions
and comes before every platform block.

Mutations this file catches (R7):

* ``_inject_agent_tools`` stops calling ``_apply_voice``: every agent loses it;
* the Copilot branch of ``_apply_voice`` drops out: task-manager and
  app-builder lose it;
* the native branch drops out: the six native agents lose it;
* the heading guard goes: a second injection adds a second copy;
* a ``floor_opt_out`` name takes it away;
* the core grows past its ceiling;
* a checker rule (em dash, opener) stops matching.
"""
from __future__ import annotations

import importlib.util
import json
from pathlib import Path
from typing import Any

import orchestrator._tool_injection as ti
import pytest
from acb_llm import voice
from acb_llm.voice import (
    AGENT,
    CORE,
    OVERLAYS,
    VOICE_HEADING,
    core_prompt,
    voice_lint,
    voice_prompt,
)

ROOT = Path(__file__).resolve().parents[2]

#: The eight in-tree agents: (folder, registry name, runtime).
AGENTS: tuple[tuple[str, str, str], ...] = (
    ("agent-apis-config", "apis-config", "native"),
    ("agent-app-builder", "app-builder", "copilot"),
    ("agent-crm", "crm", "native"),
    ("agent-email-assistant", "email-assistant", "native"),
    ("agent-orchestrator", "orchestrator", "native"),
    ("agent-projects", "projects-assistant", "native"),
    ("agent-task-manager", "task-manager", "copilot"),
    ("agent-whatsapp-assistant", "whatsapp-assistant", "native"),
)

#: The ceilings, in the run-context tokenizer that ``test_tool_schema_diet``
#: uses (chars/4 plus the message envelope). Measured 2026-10-10 after review
#: round 1: the core 327, the sub-agent block 333, the agent block 577. In
#: tiktoken ``o200k_base``: 303, 310 and 538.
CORE_TOKEN_CEILING = 340
SUB_AGENT_BLOCK_TOKEN_CEILING = 345
AGENT_BLOCK_TOKEN_CEILING = 600


@pytest.fixture(autouse=True)
def _no_db(monkeypatch: pytest.MonkeyPatch) -> None:
    """No database: the toggles, the app grants and the registry are fixed."""
    import orchestrator.app_tools as app_tools

    monkeypatch.setattr(ti, "_build_registry_block", lambda: "Registered agents: (stub)")
    monkeypatch.setattr(ti, "_load_disabled_skill_families", lambda name: frozenset())
    monkeypatch.setattr(app_tools, "load_app_action_tools", lambda name: [])
    monkeypatch.delenv("SKILLS_FAIL_CLOSED", raising=False)
    ti._build_injected_tools_addendum.cache_clear()
    yield
    ti._build_injected_tools_addendum.cache_clear()


def _config(agent_dir: str) -> dict[str, Any]:
    path = ROOT / "apps/agents" / agent_dir / "config.json"
    return json.loads(path.read_text(encoding="utf-8"))


def _built(agent_dir: str, agent_name: str, monkeypatch: pytest.MonkeyPatch) -> Any:
    """The agent as the executor builds it: its own factory, scope, injection."""
    pytest.importorskip("agent_framework")
    from acb_common.settings import get_settings

    monkeypatch.setenv("OPENAI_API_KEY", "sk-voice-dummy")
    get_settings.cache_clear()
    path = ROOT / "apps/agents" / agent_dir
    monkeypatch.syspath_prepend(str(path))
    spec = importlib.util.spec_from_file_location(
        "voice_" + agent_dir.replace("-", "_"), path / "agents.py",
    )
    assert spec and spec.loader
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    agents = mod.build_agents()
    cfg = _config(agent_dir)
    ti._apply_own_tool_scope(agents, cfg.get("own_tool_scope") or None)
    ti._inject_agent_tools(
        agents, tool_scope=cfg.get("tool_scope") or None,
        agent_name=agent_name, agent_config=cfg, no_egress=False,
    )
    get_settings.cache_clear()
    return agents[0]


def _prompt(agent: Any) -> str:
    opts = getattr(agent, "_default_options", None)
    if isinstance(opts, dict) and opts.get("system_message"):
        msg = opts["system_message"]
        return msg["content"] if isinstance(msg, dict) else str(msg)
    do = getattr(agent, "default_options", None)
    if isinstance(do, dict):
        return do.get("instructions") or ""
    return getattr(agent, "instructions", "") or ""


def _tokens(text: str) -> int:
    from acb_llm.context import count_message_tokens
    return count_message_tokens([{"role": "system", "content": text}])


# ── The text ───────────────────────────────────────────────────────────────

def test_the_core_holds_the_twelve_rules_in_order() -> None:
    lines = CORE.splitlines()
    assert [ln.split(".", 1)[0] for ln in lines] == [str(n) for n in range(1, 13)]


def test_the_spec_copies_the_text_of_record() -> None:
    """``agent_writing_voice.md`` §3 shows the contract. The code holds the
    text of record, and an edit of one without the other fails here."""
    spec = (ROOT / "project-docs/specs/agent_writing_voice.md").read_text(encoding="utf-8")
    section = spec.split("## 3. The contract", 1)[1]
    fence = "`" * 3
    blocks = [b.split("\n" + fence, 1)[0] for b in section.split(fence + "text\n")[1:3]]
    assert blocks[0] == CORE
    assert blocks[1] == "\n".join(OVERLAYS.values())


def test_the_core_stays_under_its_token_ceiling() -> None:
    assert _tokens(CORE) <= CORE_TOKEN_CEILING


def test_the_agent_block_stays_under_its_token_ceiling() -> None:
    assert _tokens(voice_prompt(AGENT)) <= AGENT_BLOCK_TOKEN_CEILING


def test_the_sub_agent_block_is_the_core_alone() -> None:
    text = core_prompt()
    assert text == f"{VOICE_HEADING}\n{CORE}"
    assert _tokens(text) <= SUB_AGENT_BLOCK_TOKEN_CEILING


def test_no_record_id_rule_reaches_an_outbound_draft() -> None:
    """"Name the source" belongs to chat. An email to an outside
    recipient must never be told to cite task #141 (review round 1)."""
    assert "Name the source" not in CORE and "#141" not in CORE
    assert "name the source" in OVERLAYS["chat"]
    for name in ("email", "message"):
        assert "#141" not in voice_prompt(name), name
    assert "Never invent a name, number, date or quote" in CORE


def test_the_reader_sets_the_language() -> None:
    assert "Match the language of the person who will read it" in CORE
    assert "member's language" not in CORE
    for name in ("email", "message"):
        assert "recipient's language" in OVERLAYS[name], name


def test_outbound_overlays_defer_to_the_member() -> None:
    """The house voice is the floor. The member's own instructions, style
    and voice profile win where they differ (review round 1)."""
    assert "instructions, writing style and voice profile come first" in OVERLAYS["email"]
    assert "follow the member" in OVERLAYS["email"]
    assert "instructions and style come first" in OVERLAYS["message"]


def test_a_prompt_limit_wins_over_the_title_overlay() -> None:
    assert "unless the prompt sets a limit" in OVERLAYS["title"]


def test_the_chat_overlay_does_not_repeat_rule_one() -> None:
    assert "first sentence" not in OVERLAYS["chat"]
    assert "Use a list" not in CORE


def test_the_voice_obeys_its_own_dash_rule() -> None:
    for surface in (AGENT, *OVERLAYS):
        assert "—" not in voice_prompt(surface), surface


def test_a_surface_prompt_is_the_core_and_one_overlay() -> None:
    for name, overlay in OVERLAYS.items():
        text = voice_prompt(name)
        assert CORE in text and overlay in text
        others = [o for n, o in OVERLAYS.items() if n != name]
        assert not any(o in text for o in others), name


def test_the_agent_block_carries_every_overlay() -> None:
    text = voice_prompt(AGENT)
    assert text.startswith(VOICE_HEADING)
    assert CORE in text
    assert all(o in text for o in OVERLAYS.values())


def test_an_unknown_surface_fails_loudly() -> None:
    with pytest.raises(KeyError):
        voice_prompt("tittle")


def test_the_voice_names_no_tool() -> None:
    """It reaches every agent, so it must not name a tool an agent may lack.

    The word "decide" in rule 2 is a plain verb, so the test looks for a
    call or a code span, which is how a prompt names a tool."""
    text = voice_prompt(AGENT)
    for name in ti._CORE_STANDARD_TOOL_NAMES:
        assert f"{name}(" not in text and f"`{name}`" not in text, name


# ── Every in-tree agent, built as the executor builds it ───────────────────

@pytest.mark.parametrize(("agent_dir", "agent_name", "runtime"), AGENTS)
def test_every_agent_reads_the_voice_once(
    agent_dir: str, agent_name: str, runtime: str, monkeypatch: pytest.MonkeyPatch,
) -> None:
    agent = _built(agent_dir, agent_name, monkeypatch)
    if runtime == "copilot":
        from agent_framework_github_copilot import GitHubCopilotAgent

        assert isinstance(agent, GitHubCopilotAgent), type(agent)
    prompt = _prompt(agent)
    assert prompt.count(VOICE_HEADING) == 1
    assert prompt.count(CORE) == 1


@pytest.mark.parametrize(("agent_dir", "agent_name", "runtime"), AGENTS)
def test_the_voice_follows_the_agent_and_leads_the_platform_blocks(
    agent_dir: str, agent_name: str, runtime: str, monkeypatch: pytest.MonkeyPatch,
) -> None:
    agent = _built(agent_dir, agent_name, monkeypatch)
    prompt = _prompt(agent)
    at = prompt.index(VOICE_HEADING)
    for marker in ("## Metorite Platform Tools", "## Delegatable agents",
                   "Rich UI by default", "### Output discipline", "FILES: write files"):
        if marker in prompt:
            assert at < prompt.index(marker), marker


def test_every_agent_folder_is_listed() -> None:
    """A new agent joins this fence, or the fence says so."""
    folders = {p.name for p in (ROOT / "apps/agents").iterdir() if (p / "config.json").is_file()}
    assert folders == {a[0] for a in AGENTS}


# ── A new agent, on each runtime shape ─────────────────────────────────────

class _CopilotShaped:
    """The GitHubCopilotAgent shape: ``_tools`` plus ``_default_options``."""

    def __init__(self, system_message: Any = None) -> None:
        self.name = "new-copilot"
        self._tools: list[Any] = []
        self._default_options: dict[str, Any] = {}
        if system_message is not None:
            self._default_options["system_message"] = system_message


class _NativeShaped:
    """The native MAF ``Agent`` shape: ``default_options`` with the tools."""

    def __init__(self, instructions: str = "You are a new agent.") -> None:
        self.name = "new-native"
        self.default_options: dict[str, Any] = {"tools": [], "instructions": instructions}


class _OlderMafShaped:
    """The older MAF shape: a ``tools`` list and a string ``instructions``."""

    def __init__(self) -> None:
        self.name = "new-older"
        self.tools: list[Any] = []
        self.instructions = "You are an older agent."


@pytest.fixture()
def _approve_all(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("AGENT_PERMISSION_MODE", "approve_all")


@pytest.mark.parametrize("system_message", [
    None, "You are new.", {"mode": "append", "content": "You are new."},
])
def test_a_new_copilot_agent_gets_the_voice_before_the_addendum(
    system_message: Any, _approve_all: None,
) -> None:
    agent = _CopilotShaped(system_message)
    ti._inject_agent_tools([agent], agent_name="new-copilot", agent_config={}, no_egress=False)
    text = _prompt(agent)
    assert text.count(CORE) == 1
    assert text.index(VOICE_HEADING) < text.index("## Metorite Platform Tools")
    if system_message is not None:
        assert text.startswith("You are new.")


@pytest.mark.parametrize("is_sub_agent", [False, True])
def test_a_new_native_agent_gets_the_voice_after_its_instructions(
    is_sub_agent: bool, _approve_all: None,
) -> None:
    agent = _NativeShaped()
    ti._inject_agent_tools(
        [agent], is_sub_agent=is_sub_agent, agent_name="new-native",
        agent_config={}, no_egress=False,
    )
    text = _prompt(agent)
    assert text.startswith("You are a new agent.\n\n" + VOICE_HEADING)
    assert text.count(CORE) == 1
    # A sub-agent pays for the core only: no overlay reaches it.
    overlays_in = [n for n, o in OVERLAYS.items() if o in text]
    assert overlays_in == ([] if is_sub_agent else list(OVERLAYS))


def test_an_older_maf_agent_gets_the_voice(_approve_all: None) -> None:
    agent = _OlderMafShaped()
    ti._inject_agent_tools([agent], agent_name="new-older", agent_config={}, no_egress=False)
    assert agent.instructions.count(CORE) == 1


def test_a_no_egress_run_keeps_the_voice(_approve_all: None) -> None:
    agent = _NativeShaped()
    agents = [agent]
    ti._inject_agent_tools(
        agents, tool_scope=["web_search"], agent_name="new-native",
        agent_config={}, no_egress=True,
    )
    assert _prompt(agent).count(CORE) == 1


def test_a_second_injection_adds_nothing(_approve_all: None) -> None:
    native, copilot = _NativeShaped(), _CopilotShaped("You are new.")
    for _ in range(2):
        ti._inject_agent_tools(
            [native, copilot], agent_name="twice", agent_config={}, no_egress=False,
        )
    assert _prompt(native).count(VOICE_HEADING) == 1
    assert _prompt(copilot).count(VOICE_HEADING) == 1


def test_a_box_with_no_platform_tools_still_gives_the_voice(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(ti, "_collect_injectable_platform_tools", lambda name=None: [])
    agent = _NativeShaped()
    ti._inject_agent_tools([agent], agent_name="bare", agent_config={}, no_egress=False)
    assert _prompt(agent).count(CORE) == 1


def test_floor_opt_out_cannot_remove_the_voice(_approve_all: None) -> None:
    """The opt-out names tools. No name removes the voice, and none is allowed."""
    names = sorted(ti.FLOOR_OPT_OUT_ALLOWED) + ["voice", "how_metorite_writes"]
    agent = _NativeShaped()
    ti._inject_agent_tools(
        [agent], agent_name="opted", agent_config={"floor_opt_out": names},
        no_egress=False,
    )
    assert _prompt(agent).count(CORE) == 1
    assert "voice" not in ti.FLOOR_OPT_OUT_ALLOWED


def test_the_index_mode_keeps_the_voice(
    monkeypatch: pytest.MonkeyPatch, _approve_all: None,
) -> None:
    """QM-2 index mode defers the addendum bodies. The voice is not a body."""
    monkeypatch.setenv("SKILLS_INDEX_ONLY", "1")
    ti._build_injected_tools_addendum.cache_clear()
    agent = _CopilotShaped("You are new.")
    ti._inject_agent_tools([agent], agent_name="indexed", agent_config={}, no_egress=False)
    assert _prompt(agent).count(CORE) == 1


# ── The checker ────────────────────────────────────────────────────────────

def _rules(text: str, surface: str | None = None) -> list[str]:
    return [f.rule for f in voice_lint(text, surface)]


@pytest.mark.parametrize(("text", "rule"), [
    ("The build passed — ship it.", "em_dash"),
    ("This is really quick.", "filler"),
    ("We simply moved it.", "filler"),
    ("We leverage the cache.", "corporate_verb"),
    ("The total reflects the refund.", "corporate_verb"),
    ("Utilizing the queue helps.", "corporate_verb"),
    ("Great question! The answer is 4.", "opener"),
    ("Certainly. The answer is 4.", "opener"),
    ("I'd be happy to help. It is 4.", "opener"),
    ("Let me check that. It is 4.", "opener"),
    ("It is 4.\n\nIn summary, it is 4.", "closer"),
    ("It is 4. Overall, the plan works.", "closer"),
    ("It is 4. Hope this helps!", "closer"),
    ("This is not a bug, but a missing setting.", "not_x_but_y"),
    ("It's not about speed, it's about trust.", "not_x_but_y"),
    ("Hi Priya, I hope this email finds you well.", "hope_finds_you_well"),
])
def test_each_rule_fires(text: str, rule: str) -> None:
    assert rule in _rules(text), voice_lint(text)


@pytest.mark.parametrize("text", [
    "Task #141 is due on 3 Oct. Priya owns it.",
    "I could not find the file, but I found a copy in outputs/.",
    "The invoice is overdue by 12 days.",
    "Decide by Friday whether we pay the vendor.",
])
def test_clean_text_has_no_finding(text: str) -> None:
    assert voice_lint(text) == []


def test_code_and_quotes_are_not_the_writers_words() -> None:
    text = (
        "Run this:\n```\nx = 1  # really — just leverage it\n```\n"
        "Use `simply()` here.\n"
        "> Great question, I hope this finds you well — Priya\n"
        'Priya wrote "this is really urgent" on 3 Oct.'
    )
    assert voice_lint(text) == []


def test_the_title_overlay_checks() -> None:
    assert _rules("Fix the login bug", "title") == []
    assert "title_full_stop" in _rules("Fix the login bug.", "title")
    assert "title_subtitle" in _rules("Login: fix the bug", "title")
    assert "title_length" in _rules("one two three four five six seven eight nine", "title")


def test_the_description_overlay_checks() -> None:
    assert _rules("Add SSO. Members ask for it. Done when Google sign-in works.",
                  "description") == []
    assert "description_length" in _rules("A. B. C. D.", "description")


def test_the_checker_never_changes_the_text() -> None:
    text = "Great question — we leverage it."
    before = str(text)
    voice_lint(text)
    assert text == before
    assert not hasattr(voice, "voice_fix") and not hasattr(voice, "rewrite")
