"""Agent manifest — the single declaration an agent is derived from.

Spec: ``project-docs/specs/agent_architecture.md``.

Today an agent's behaviour is spread across ``config.json`` (tool scope, skills,
integrations), ``agents.py`` (runtime, model, permission handler) and hardcoded
platform defaults — with no validation that they agree.  ``agent_architecture.md``
§3.1 documents the consequence: every first-party agent declares ``"runtime":
"maf"`` and three of them build a Copilot agent anyway, because nothing checks.

This module is the *parallel representation* that fixes that, introduced ahead of
any behaviour change:

* :class:`AgentManifest` is the whole declaration — sharing, capabilities,
  knowledge, memory and permissions.
* :meth:`AgentManifest.from_config` derives one from today's ``config.json``,
  preserving current behaviour exactly for anything not declared.
* The derived accessors (:meth:`memory_scope`, :meth:`blob_instance`,
  :meth:`isolation_tier`, :meth:`resolve_tool_surface`) are the values the
  platform should compute from the manifest instead of from hardcoded names.

**Partly wired: only** :meth:`AgentManifest.from_config` **and**
:meth:`AgentManifest.instance_key` **are on the run path** (``executor.py``'s
``_resolve_agent_instance`` and ``gateway/routes/workspace.py``'s
``_agent_instance_for``); every other derived accessor still has zero production
callers — see ``agent_architecture.md`` §12.1.  It is deliberately
side-effect-free so ``tests/unit/test_agent_manifest.py`` can assert that the
derived values match what the platform does today — which is what makes the
later flip a provable no-op rather than a hopeful one.

Usage::

    from acb_skills.manifest import AgentManifest

    m = AgentManifest.from_config(config_dict, name="sales")
    problems = m.validate()          # [] when the manifest is coherent
    m.memory_scope("vijay@x.com")    # 'agent:sales#t:sales'
    m.isolation_tier()               # 'T1'
"""
from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass, field
from typing import Any

__all__ = [
    "INSTANCING_VALUES",
    "MANIFEST_SCHEMA_VERSION",
    "SHELL_TOOLS",
    "VISIBILITY_VALUES",
    "WRITE_TOOLS",
    "AgentManifest",
    "CapabilitySpec",
    "MemorySpec",
    "SharingSpec",
]

MANIFEST_SCHEMA_VERSION = 1

INSTANCING_VALUES = ("personal", "team", "shared")
VISIBILITY_VALUES = ("private", "team", "organization")
KIND_VALUES = ("declarative", "code")
MEMORY_MODE_VALUES = ("instance", "none")
OUTPUTS_VISIBILITY_VALUES = ("instance", "room", "org")

#: Tools that execute arbitrary code.  Holding any of these puts the agent in the
#: container tier regardless of how it was authored — see
#: ``agent_platform_hardening_2026-07.md`` Part 1.  The isolation boundary is the
#: resolved tool surface, NOT declarative-vs-code.  ``run_command`` (WS-43d,
#: ``maf_coding_engine.md`` §7.4) runs in the sandbox broker, and it is still a
#: shell tool: an agent that holds it derives T2.
SHELL_TOOLS = frozenset({"code_task", "run_script", "install_dependency", "run_command"})

#: Tools that write files or reach declared integrations.  Confined in-process
#: (T1) rather than merely observational (T0).
WRITE_TOOLS = frozenset({
    "write_artifact", "share_artifact", "save_note", "save_memory",
    "save_episode", "remember", "emit_generative_ui", "call_agent",
})


# ---------------------------------------------------------------------------
# Blocks
# ---------------------------------------------------------------------------

@dataclass(slots=True)
class SharingSpec:
    """Who an agent is for, and whose memory it keeps.

    ``instancing`` is the axis that decides how the agent's own memory and file
    store are partitioned (``docs/multiplayer/agent-kinds.md``):

    * ``personal`` — one instance per user; nothing crosses between them.
    * ``team``     — one instance per team.
    * ``shared``   — a single instance for everyone (today's implicit behaviour).

    ``visibility`` is the orthogonal axis: who may *invoke* it.  The email agent
    is the case that proves they are independent — organization visibility with
    personal instancing.
    """

    instancing: str = "shared"
    visibility: str = "organization"
    team: str | None = None
    shareable: bool = False
    outputs_visibility: str = "instance"


@dataclass(slots=True)
class CapabilitySpec:
    """What the agent may reach.

    ``tool_scope`` mirrors ``config.json``'s field exactly, including its
    fail-open semantics: ``None`` means *inject every platform tool*
    (``_tool_injection._resolve_injected_scope`` returns ``None`` for an absent
    scope).  That default is defensible for engineer-authored in-repo agents and
    is a privilege grant nobody made for creator-authored ones, which is why
    :meth:`AgentManifest.validate` reports it for declarative agents.
    """

    skills: list[str] = field(default_factory=list)
    integrations: list[str] = field(default_factory=list)
    optional_integrations: list[str] = field(default_factory=list)
    mcp_servers: list[str] = field(default_factory=list)
    tool_scope: list[str] | None = None          # None == inject everything
    own_tool_scope: list[str] | None = None      # filters repo-baked tools
    agents: list[dict[str, Any]] = field(default_factory=list)  # delegation edges


@dataclass(slots=True)
class MemorySpec:
    """Which compartments the agent may touch, and how it writes."""

    compartments: list[str] = field(default_factory=lambda: ["user", "agent", "org"])
    always_on_budget_tokens: int = 2000
    mode: str = "instance"          # instance | none
    write_gate: str = "all"         # all | decisions_only
    distill: bool = False


# ---------------------------------------------------------------------------
# The manifest
# ---------------------------------------------------------------------------

@dataclass(slots=True)
class AgentManifest:
    """One declaration an agent's runtime behaviour is derived from."""

    slug: str
    name: str = ""
    description: str = ""
    kind: str = "code"                  # declarative | code
    runtime: str = "maf"
    model_tier: str = "tier-balanced"
    schema_version: int = MANIFEST_SCHEMA_VERSION

    sharing: SharingSpec = field(default_factory=SharingSpec)
    capabilities: CapabilitySpec = field(default_factory=CapabilitySpec)
    memory: MemorySpec = field(default_factory=MemorySpec)

    #: True when this manifest was derived from a legacy ``config.json`` that
    #: declared no ``sharing`` block.  Consumers use it to keep today's
    #: behaviour rather than silently re-partitioning an agent's memory.
    legacy: bool = False

    # -- construction ------------------------------------------------------

    @classmethod
    def from_config(
        cls,
        config: dict[str, Any] | None,
        *,
        name: str | None = None,
    ) -> AgentManifest:
        """Derive a manifest from a parsed ``config.json``.

        Preserves current behaviour exactly for anything the config doesn't
        declare.  In particular an agent with no ``sharing`` block is derived as
        ``instancing="shared"`` — today's implicit model, one memory bucket per
        agent name — and flagged :attr:`legacy`.  The *schema* default for a new
        (creator-authored) agent is ``personal``; the *derived* default for an
        existing one must not change behaviour under it.
        """
        cfg = dict(config or {})
        slug = str(name or cfg.get("name") or "").strip()

        raw_sharing = cfg.get("sharing")
        is_legacy = not isinstance(raw_sharing, dict)
        sharing_dict: dict[str, Any] = raw_sharing if isinstance(raw_sharing, dict) else {}

        sharing = SharingSpec(
            instancing=str(sharing_dict.get("instancing", "shared")),
            visibility=str(sharing_dict.get("visibility", "organization")),
            team=(sharing_dict.get("team") or None),
            shareable=bool(sharing_dict.get("shareable", False)),
            outputs_visibility=str(sharing_dict.get("outputs_visibility", "instance")),
        )

        mcp = cfg.get("mcp_servers") or {}
        capabilities = CapabilitySpec(
            skills=list(cfg.get("skill_repos") or []),
            integrations=list(cfg.get("integrations") or []),
            optional_integrations=list(cfg.get("optional_integrations") or []),
            mcp_servers=sorted(mcp.keys()) if isinstance(mcp, dict) else list(mcp),
            tool_scope=(list(cfg["tool_scope"]) if cfg.get("tool_scope") else None),
            own_tool_scope=(
                list(cfg["own_tool_scope"]) if cfg.get("own_tool_scope") else None
            ),
            agents=list(cfg.get("agents") or []),
        )

        mem_dict = cfg.get("memory") if isinstance(cfg.get("memory"), dict) else {}
        memory = MemorySpec(
            compartments=list(mem_dict.get("compartments") or ["user", "agent", "org"]),
            always_on_budget_tokens=int(mem_dict.get("always_on_budget_tokens", 2000)),
            mode=str(mem_dict.get("mode", "instance")),
            write_gate=str(mem_dict.get("write_gate", "all")),
            distill=bool(mem_dict.get("distill", False)),
        )

        return cls(
            slug=slug,
            name=str(cfg.get("display_name") or cfg.get("name") or slug),
            description=str(cfg.get("description") or ""),
            kind=str(cfg.get("kind") or "code"),
            runtime=str(cfg.get("runtime") or "maf"),
            model_tier=str(cfg.get("model_tier") or "tier-balanced"),
            schema_version=int(cfg.get("schema_version") or MANIFEST_SCHEMA_VERSION),
            sharing=sharing,
            capabilities=capabilities,
            memory=memory,
            legacy=is_legacy,
        )

    # -- derived values ----------------------------------------------------

    def instance_key(self, actor: str | None = None) -> str:
        """The partition suffix for this agent's memory and file store.

        ``''`` (shared) · ``u:<email>`` (personal) · ``t:<team>`` (team).
        Mirrors ``app_data.user_scope``, where ``''`` already means "shared row,
        else a per-user partition" (migration 114).
        """
        if self.sharing.instancing == "personal":
            return f"u:{actor}" if actor else ""
        if self.sharing.instancing == "team":
            return f"t:{self.sharing.team}" if self.sharing.team else ""
        return ""

    def memory_scope(self, actor: str | None = None) -> str | None:
        """This agent's Mem0 compartment key, or ``None`` when it keeps none.

        A bare ``agent:<slug>`` (no suffix) is the existing key, so a `shared`
        agent's partition is unchanged by adopting the manifest.
        """
        if self.memory.mode == "none":
            return None
        suffix = self.instance_key(actor)
        return f"agent:{self.slug}#{suffix}" if suffix else f"agent:{self.slug}"

    def blob_instance(self, actor: str | None = None) -> str:
        """The ``agent_blob.instance`` value for this run (see migration 136)."""
        return self.instance_key(actor)

    def resolve_tool_surface(
        self, core_floor: Iterable[str] = (),
    ) -> frozenset[str] | None:
        """The injected tool names, or ``None`` for "everything".

        Deliberately identical to ``_tool_injection._resolve_injected_scope``:
        an absent or empty ``tool_scope`` returns ``None``.  The equivalence is
        asserted by ``tests/unit/test_agent_manifest.py`` so the manifest can
        take over this computation without changing what any agent receives.
        """
        if not self.capabilities.tool_scope:
            return None
        return frozenset(self.capabilities.tool_scope) | frozenset(core_floor)

    def isolation_tier(self, core_floor: Iterable[str] = ()) -> str:
        """``'T0'`` | ``'T1'`` | ``'T2'`` — derived from the resolved surface.

        ``agent_platform_hardening_2026-07.md`` Part 1: the isolation boundary
        is what the agent can *do*, not how it was authored.  An open tool scope
        resolves to T2 because it includes the shell tools.
        """
        surface = self.resolve_tool_surface(core_floor)
        if surface is None:
            return "T2"                      # open scope ⇒ includes code_task
        if surface & SHELL_TOOLS:
            return "T2"
        if surface & WRITE_TOOLS or self.capabilities.integrations:
            return "T1"
        return "T0"

    def is_shareable(self) -> bool:
        """May one of this agent's sessions become a multiplayer room?

        A `personal` agent is never shareable unless it explicitly opts in — its
        whole point is that its context is one person's.
        """
        if self.sharing.instancing == "personal":
            return bool(self.sharing.shareable)
        return bool(self.sharing.shareable)

    # -- validation --------------------------------------------------------

    def validate(self) -> list[str]:
        """Return a list of problems; empty means the manifest is coherent.

        Reports, rather than raises, so a caller can log-and-continue during the
        backfill and fail hard once the manifest is authoritative.
        """
        problems: list[str] = []

        if not self.slug:
            problems.append("slug is required")
        if self.kind not in KIND_VALUES:
            problems.append(f"kind must be one of {KIND_VALUES}, got {self.kind!r}")
        if self.sharing.instancing not in INSTANCING_VALUES:
            problems.append(
                f"sharing.instancing must be one of {INSTANCING_VALUES}, "
                f"got {self.sharing.instancing!r}"
            )
        if self.sharing.visibility not in VISIBILITY_VALUES:
            problems.append(
                f"sharing.visibility must be one of {VISIBILITY_VALUES}, "
                f"got {self.sharing.visibility!r}"
            )
        if self.sharing.outputs_visibility not in OUTPUTS_VISIBILITY_VALUES:
            problems.append(
                "sharing.outputs_visibility must be one of "
                f"{OUTPUTS_VISIBILITY_VALUES}, got {self.sharing.outputs_visibility!r}"
            )
        if self.memory.mode not in MEMORY_MODE_VALUES:
            problems.append(
                f"memory.mode must be one of {MEMORY_MODE_VALUES}, got {self.memory.mode!r}"
            )

        # A team instance without a team has nowhere to partition to.
        if self.sharing.instancing == "team" and not self.sharing.team:
            problems.append("sharing.instancing='team' requires sharing.team")
        if self.sharing.visibility == "team" and not self.sharing.team:
            problems.append("sharing.visibility='team' requires sharing.team")

        # Delegation edges must name a target and a mode.
        for i, edge in enumerate(self.capabilities.agents):
            if not isinstance(edge, dict) or not edge.get("slug"):
                problems.append(f"capabilities.agents[{i}] needs a 'slug'")
                continue
            mode = edge.get("mode", "call")
            if mode not in ("call", "handoff", "background"):
                problems.append(
                    f"capabilities.agents[{i}].mode must be call|handoff|background, "
                    f"got {mode!r}"
                )
            if edge.get("slug") == self.slug:
                problems.append(f"capabilities.agents[{i}] delegates to itself")

        # Fail-open tool scope: tolerated for in-repo code agents, never for a
        # declarative one (hardening C1).
        if self.kind == "declarative" and self.capabilities.tool_scope is None:
            problems.append(
                "declarative agents must declare capabilities.tool_scope — an "
                "absent scope injects every platform tool, including shell"
            )

        # A personal agent has no cross-user instance to share memory with.
        if self.sharing.instancing == "personal" and self.sharing.team:
            problems.append("sharing.team is meaningless when instancing='personal'")

        return problems

    def warnings(self) -> list[str]:
        """Non-blocking observations worth surfacing at registration time."""
        out: list[str] = []
        if self.capabilities.tool_scope is None:
            out.append(
                f"{self.slug}: no tool_scope — every platform tool is injected "
                f"(isolation tier {self.isolation_tier()})"
            )
        if self.legacy:
            out.append(
                f"{self.slug}: no sharing block; derived as instancing='shared' "
                "to preserve current behaviour"
            )
        if self.sharing.instancing == "personal" and self.sharing.shareable:
            out.append(
                f"{self.slug}: personal agent marked shareable — its sessions can "
                "become rooms, so its instance memory must stay out of room context"
            )
        return out
