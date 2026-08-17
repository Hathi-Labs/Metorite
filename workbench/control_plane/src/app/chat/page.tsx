"use client";

import Button from "@/components/ui/Button";
import Icon from "@/components/Icon";
import { useState, useEffect, useCallback, useMemo, useRef, Suspense } from "react";
import { useSearchParams } from "next/navigation";
import { useSession } from "next-auth/react";
import BreathingCharacter, { characterForAgent } from "@/components/BreathingCharacter";
import {
  getSessions,
  upsertSession,
  deleteSession,
  createSession,
  enrichSession,
  fetchAndMergeSessionsFromDb,
  isUnresolvedAgent,
  type ChatSession,
} from "@/lib/sessions";
import {
  buildEmailAssistantPersona,
  type PersonaAccountSettings,
} from "@/app/email/lib/emailAssistantPersona";
import { getAssistantSettings } from "@/app/email/lib/api";
import AgentChat from "@/components/AgentChat";
import { AgentAvatar, useAgentAvatars } from "@/components/AgentAvatar";
import type { ArtifactEntry } from "@/hooks/useAgentChat";
import ArtifactSidebar, { type FileEntry } from "@/components/ArtifactSidebar";
import ArtifactViewerModal from "@/components/ArtifactViewerModal";
import SidePanelEditor from "@/components/SidePanelEditor";
import { openDoc, pruneToSession, setDocLive } from "@/lib/sidePanelStore";
import FileUploadButton from "@/components/FileUploadButton";
import { useViewMode } from "@/components/ViewModeProvider";
import { useMobileDrawer } from "@/components/AppShell";
import { useActiveSessions } from "@/hooks/useActiveSessions";
import { useChatMemories } from "@/hooks/useChatMemories";
import type { AgentEntry } from "@/app/api/agent/list/route";
import type { IntegrationStatus } from "@/app/api/integrations/status/route";

// Agent names that receive the Metorite persona (general-purpose brain).
// All agents get persistent Mem0 memory — conversations are saved to Mem0
// regardless of agent type; memories are managed at /memory.
const PERSONA_AGENTS = new Set(["orchestrator", "default"]);

// Metorite persona injected as system context for the default agent.
const METORITE_PERSONA =
  "You are Metorite, the AI operations brain for Fracktal Works. You help the team " +
  "with tasks, project tracking, the sales pipeline, and company intelligence. You have " +
  "access to memories from past conversations — use them for continuity. When you take " +
  "actions that modify company data, confirm before proceeding. Be concise and direct.";

// ---------------------------------------------------------------------------
// Agent picker modal — shown on "+ New session"
// ---------------------------------------------------------------------------

/**
 * One agent card in the picker: two columns — the agent's CHARACTER on the
 * left at full card height (breathing, so the persona reads as alive), and
 * everything about the agent on the right with the description clamped to a
 * couple of lines so long personas never blow the card up.
 */
function AgentPickerCard({
  name,
  displayName,
  description,
  avatarId,
  badge,
  needsSetup,
  tags,
  onClick,
}: {
  name: string;
  displayName: string;
  description?: string;
  avatarId?: string | null;
  badge?: { label: string; className: string };
  needsSetup?: boolean;
  tags?: string[];
  onClick: () => void;
}) {
  const char = characterForAgent(name, avatarId);
  return (
    <button
      onClick={onClick}
      className="group flex w-full items-stretch overflow-hidden rounded-lg border border-border bg-secondary/40 text-left hover:border-primary/40 hover:bg-secondary tech-transition"
    >
      {/* Character column — full height of the card */}
      <div className="flex w-16 shrink-0 items-center justify-center self-stretch border-r border-border/60 bg-background/40 group-hover:bg-primary/5 tech-transition">
        <BreathingCharacter
          char={char}
          box={60}
          fallback={
            <AgentAvatar
              libraryId={avatarId}
              size={34}
              fallback={<Icon name="Bot" size={22} className="text-muted-foreground/70" />}
            />
          }
        />
      </div>
      {/* Info column */}
      <div className="min-w-0 flex-1 px-3.5 py-2.5">
        <div className="flex items-center justify-between gap-2">
          <div className="text-sm font-medium text-foreground truncate">{displayName}</div>
          <div className="flex items-center gap-1.5 shrink-0">
            {badge && (
              <span className={`text-[10px] px-1.5 py-0.5 rounded-full whitespace-nowrap ${badge.className}`}>
                {badge.label}
              </span>
            )}
            {needsSetup && (
              <span className="text-[10px] px-1.5 py-0.5 rounded-full bg-destructive/10 text-destructive border border-destructive/30 whitespace-nowrap">
                ⚙ Setup needed
              </span>
            )}
          </div>
        </div>
        {(tags?.length ?? 0) > 0 && (
          <div className="mt-1 flex items-center gap-1.5 flex-wrap">
            {tags!.slice(0, 3).map((t) => (
              <span key={t} className="text-[10px] px-1.5 py-0.5 rounded bg-muted text-muted-foreground whitespace-nowrap">
                {t}
              </span>
            ))}
          </div>
        )}
        {description && (
          <div className="text-xs text-muted-foreground mt-1 line-clamp-2 leading-relaxed">
            {description}
          </div>
        )}
      </div>
    </button>
  );
}

function AgentPickerModal({
  onSelect,
  onClose,
}: {
  onSelect: (agentName: string, description?: string, agentEntry?: AgentEntry) => void;
  onClose: () => void;
}) {
  const [agents, setAgents] = useState<AgentEntry[]>([]);
  const [loading, setLoading] = useState(true);
  const agentAvatars = useAgentAvatars();
  // Integration statuses fetched in background to show "needs setup" badges
  const [allStatuses, setAllStatuses] = useState<IntegrationStatus[]>([]);

  useEffect(() => {
    fetch("/api/agent/list")
      .then((r) => r.json())
      .then((data: AgentEntry[]) => setAgents(data))
      .catch(() => {})
      .finally(() => setLoading(false));
    // Fetch integration statuses in background for badges
    fetch("/api/integrations/status")
      .then((r) => r.json())
      .then((data: IntegrationStatus[]) => setAllStatuses(data))
      .catch(() => {});
  }, []);

  /** True when all mandatory integrations for this agent are already configured. */
  function isReady(agent: AgentEntry): boolean {
    if (!agent.integrations?.length) return true;
    if (!allStatuses.length) return true; // not loaded yet — optimistic
    return agent.integrations.every((svc) => {
      const s = allStatuses.find((st) => st.service === svc);
      return !s || s.configured;
    });
  }

  const handleAgentClick = (agent: AgentEntry) => {
    // Always allow — missing integrations are surfaced as a banner inside the
    // chat window so users can configure them conversationally.
    onSelect(agent.name, agent.description, agent);
  };

  const needsSetupBadge = (agent: AgentEntry) =>
    allStatuses.length > 0 && !isReady(agent);

  return (
    <div
      className="fixed inset-0 z-[60] flex items-center justify-center bg-black/60 backdrop-blur-sm"
      onClick={onClose}
    >
      <div
        className="w-full max-w-lg rounded-xl border border-border bg-card p-5 shadow-2xl tech-glass-subtle flex flex-col max-h-[85vh]"
        onClick={(e) => e.stopPropagation()}
      >
        <div className="mb-4 flex items-center justify-between shrink-0">
          <div>
            <div className="text-base font-semibold text-foreground">New session</div>
            <div className="text-xs text-muted-foreground mt-0.5">Choose an agent to chat with</div>
          </div>
          <Button variant="text" size="none" layout="" onClick={onClose} className="text-sm">
            ✕
          </Button>
        </div>

        <div className="overflow-y-auto flex-1 min-h-0 -mr-2 pr-2">
        {/* Default — Metorite */}
        <div className="mb-2">
          <div className="text-xs font-medium uppercase tracking-wide text-muted-foreground/70 mb-1.5">
            Default
          </div>
          <AgentPickerCard
            name="orchestrator"
            displayName="Metorite"
            description="General-purpose AI company brain — tasks, projects, sales pipeline, and company intelligence."
            avatarId={agentAvatars["orchestrator"]}
            onClick={() => onSelect("orchestrator", "Metorite — AI company brain")}
          />
        </div>

        {/* Copilot SDK Agents — talk directly to GitHub Copilot SDK */} 
        <div className="mt-4">
          <div className="text-xs font-medium uppercase tracking-wide text-muted-foreground/70 mb-1.5">
            Copilot SDK agents
          </div>
          {loading ? (
            <div className="text-xs text-muted-foreground py-2 text-center">Loading agents…</div>
          ) : (
            <div className="flex flex-col gap-1.5">
              {agents.filter(a => a.agent_runtime === "github-copilot").map((a) => (
                <AgentPickerCard
                  key={a.name}
                  name={a.name}
                  displayName={a.display_name || a.name}
                  description={a.description}
                  avatarId={agentAvatars[a.name]}
                  badge={{
                    label: "Copilot SDK",
                    className: "border border-primary/40 bg-primary/10 text-primary",
                  }}
                  needsSetup={needsSetupBadge(a)}
                  tags={a.tags}
                  onClick={() => handleAgentClick(a)}
                />
              ))}
              {agents.filter(a => a.agent_runtime === "github-copilot").length === 0 && (
                <div className="text-xs text-muted-foreground py-1">No Copilot SDK agents registered</div>
              )}
            </div>
          )}
        </div>

        {/* MAF Agents — run through Microsoft Agent Framework */}
        <div className="mt-4">
          <div className="text-xs font-medium uppercase tracking-wide text-muted-foreground/70 mb-1.5">
            MAF agents
          </div>
          {loading ? (
            <div className="text-xs text-muted-foreground py-2 text-center">Loading agents…</div>
          ) : (
            <div className="flex flex-col gap-1.5">
              {agents.filter(a => a.agent_runtime !== "github-copilot").map((a) => (
                <AgentPickerCard
                  key={a.name}
                  name={a.name}
                  displayName={a.display_name || a.name}
                  description={a.description}
                  avatarId={agentAvatars[a.name]}
                  badge={{
                    label: "MAF",
                    className: "border border-accent/40 bg-accent/10 text-accent",
                  }}
                  needsSetup={needsSetupBadge(a)}
                  tags={a.tags}
                  onClick={() => handleAgentClick(a)}
                />
              ))}
            </div>
          )}
        </div>
        </div>
      </div>
    </div>
  );
}

// ---------------------------------------------------------------------------
// Session list — grouped by agent with accordion sections
// ---------------------------------------------------------------------------

/** Compact "2h" / "3d" relative timestamp for the conversation list. */
function relTime(iso?: string): string {
  if (!iso) return "";
  const t = Date.parse(iso);
  if (!Number.isFinite(t)) return "";
  const s = Math.max(0, (Date.now() - t) / 1000);
  if (s < 60) return "now";
  if (s < 3600) return `${Math.floor(s / 60)}m`;
  if (s < 86400) return `${Math.floor(s / 3600)}h`;
  if (s < 7 * 86400) return `${Math.floor(s / 86400)}d`;
  return new Date(t).toLocaleDateString(undefined, { month: "short", day: "numeric" });
}

function SessionList({
  sessions,
  activeId,
  activeRunIds,
  onSelect,
  onNew,
  onDelete,
  agentAliases,
}: {
  sessions: ChatSession[];
  activeId: string;
  activeRunIds: Set<string>;
  onSelect: (id: string) => void;
  onNew: () => void;
  onDelete: (id: string) => void;
  /** canonical agent name → friendly display name, for group headers. */
  agentAliases?: Record<string, string>;
}) {
  const agentAvatars = useAgentAvatars();
  // Client-side filter over title/preview/agent — no backend involved.
  const [query, setQuery] = useState("");
  const visibleSessions = useMemo(() => {
    const q = query.trim().toLowerCase();
    if (!q) return sessions;
    return sessions.filter((s) =>
      [s.title ?? s.name, s.lastPreview ?? "", s.agentName]
        .join(" ").toLowerCase().includes(q),
    );
  }, [sessions, query]);
  // Group sessions by agentName
  const groups = useMemo(() => {
    const map = new Map<string, ChatSession[]>();
    for (const s of visibleSessions) {
      const list = map.get(s.agentName) ?? [];
      list.push(s);
      map.set(s.agentName, list);
    }
    // Sort groups: active agent first, then alphabetically
    const entries = Array.from(map.entries());
    const activeAgent = sessions.find((s) => s.id === activeId)?.agentName;
    entries.sort(([a], [b]) => {
      if (a === activeAgent) return -1;
      if (b === activeAgent) return 1;
      return a.localeCompare(b);
    });
    return entries;
  }, [visibleSessions, sessions, activeId]);

  // Track which accordion sections are expanded (empty = all collapsed by default).
  const [expanded, setExpanded] = useState<Set<string>>(new Set());
  // Auto-expand the active agent's group when activeId changes.
  /* eslint-disable react-hooks/set-state-in-effect */
  useEffect(() => {
    const activeAgent = sessions.find((s) => s.id === activeId)?.agentName;
    if (activeAgent) {
      setExpanded((prev) => {
        if (prev.has(activeAgent)) return prev;
        const next = new Set(prev);
        next.add(activeAgent);
        return next;
      });
    }
  }, [activeId, sessions]);
  /* eslint-enable react-hooks/set-state-in-effect */

  const toggleAgent = (agent: string) => {
    setExpanded((prev) => {
      const next = new Set(prev);
      if (next.has(agent)) next.delete(agent);
      else next.add(agent);
      return next;
    });
  };

  if (sessions.length === 0) {
    return (
      <div className="flex flex-col gap-1">
        <button
          onClick={onNew}
          className="mb-2 w-full rounded-md bg-primary/10 border border-primary/30 px-3 py-2 text-left text-sm font-medium text-primary hover:bg-primary/15 tech-transition"
        >
          + New conversation
        </button>
        <p className="px-1 text-xs text-muted-foreground">No conversations yet.</p>
      </div>
    );
  }

  const searching = query.trim().length > 0;

  return (
    <div className="flex flex-col gap-1">
      <button
        onClick={onNew}
        className="mb-1.5 w-full rounded-md bg-primary/10 border border-primary/30 px-3 py-2 text-left text-sm font-medium text-primary hover:bg-primary/15 tech-transition"
      >
        + New conversation
      </button>

      {/* Search — client-side filter over titles, previews, and agent names. */}
      <div className="relative mb-1.5">
        <Icon name="Search" className="pointer-events-none absolute left-2.5 top-1/2 h-3.5 w-3.5 -translate-y-1/2 text-muted-foreground/60" />
        <input
          value={query}
          onChange={(e) => setQuery(e.target.value)}
          placeholder="Search conversations…"
          className="w-full rounded-md border border-border bg-background/60 py-1.5 pl-8 pr-2 text-xs text-foreground placeholder:text-muted-foreground/60 focus:border-primary/50 focus:outline-none tech-transition"
        />
      </div>
      {searching && groups.length === 0 && (
        <p className="px-1 py-2 text-xs text-muted-foreground">No matches.</p>
      )}

      {groups.map(([agentName, agentSessions]) => {
        const isExpanded = searching || expanded.has(agentName);
        const isActiveAgent = agentSessions.some((s) => s.id === activeId);
        const count = agentSessions.length;
        const groupRunning = agentSessions.some((s) => activeRunIds.has(s.id));

        return (
          <div key={agentName} className="mb-1">
            {/* Accordion header — the agent's character fronts its group */}
            <button
              onClick={() => toggleAgent(agentName)}
              className={`w-full flex items-center gap-2 rounded-md px-2 py-1.5 text-left transition-colors ${
                isActiveAgent
                  ? "bg-secondary/60 text-foreground"
                  : "text-muted-foreground hover:bg-secondary/40 hover:text-foreground"
              }`}
            >
              <span className="text-[10px] transition-transform duration-150 shrink-0" style={{ transform: isExpanded ? "rotate(0deg)" : "rotate(-90deg)" }}>
                ▼
              </span>
              <span className={`shrink-0 rounded-full ${groupRunning ? "ring-2 ring-emerald-400/60" : ""}`}>
                <AgentAvatar
                  libraryId={agentAvatars[agentName] ?? agentName}
                  size={18}
                  fallback={<Icon name="Bot" size={14} className="text-muted-foreground/60" />}
                />
              </span>
              <span className="flex-1 text-xs font-medium truncate">
                {agentAliases?.[agentName] || agentName}
              </span>
              {/* Active run count badge */}
              {groupRunning && (
                <span className="flex items-center gap-1 text-[10px] text-emerald-400 shrink-0" title="Agent is running">
                  <span className="inline-block w-1.5 h-1.5 rounded-full bg-emerald-400 animate-pulse" />
                  {agentSessions.filter((s) => activeRunIds.has(s.id)).length}
                </span>
              )}
              <span className="text-[10px] text-muted-foreground tabular-nums shrink-0">
                {count}
              </span>
            </button>

            {/* Sessions for this agent */}
            {isExpanded && (
              <div className="mt-1 ml-2.5 flex flex-col gap-0.5 border-l border-border/60 pl-1.5">
                {agentSessions.map((s) => {
                  const isActive = s.id === activeId;
                  return (
                    <div
                      key={s.id}
                      className={`group relative flex items-start justify-between rounded-md py-2 pl-3 pr-1.5 cursor-pointer tech-transition ${
                        isActive
                          ? "bg-primary/10 text-sidebar-foreground"
                          : "text-muted-foreground hover:bg-secondary/60 hover:text-foreground"
                      }`}
                      onClick={() => onSelect(s.id)}
                    >
                      {/* Active accent bar */}
                      {isActive && (
                        <span className="absolute left-0 top-1.5 bottom-1.5 w-0.5 rounded-full bg-primary" />
                      )}
                      <div className="min-w-0 flex-1 space-y-0.5">
                        <div className="flex items-center gap-1.5 text-xs leading-snug">
                          {activeRunIds.has(s.id) && (
                            <span className="inline-block w-1.5 h-1.5 rounded-full bg-emerald-400 animate-pulse shrink-0" title="Agent is generating a response" />
                          )}
                          <span className={`truncate flex-1 ${isActive ? "font-semibold text-foreground" : "font-medium"}`}>
                            {s.title ?? s.name}
                          </span>
                          {/* Shared rooms look different at a glance. A count
                              rather than a generic icon, because "who else is
                              in here" is the question the badge answers. */}
                          {(s.participantCount ?? 0) > 1 && (
                            <span
                              className="inline-flex shrink-0 items-center gap-0.5 text-[10px] text-primary/80"
                              title={
                                s.isOwner === false
                                  ? "Shared with you"
                                  : `Shared · ${s.participantCount} in the room`
                              }
                            >
                              <Icon name="Users" className="h-2.5 w-2.5" />
                              {s.participantCount}
                            </span>
                          )}
                          <span className="shrink-0 text-[10px] text-muted-foreground/70 tabular-nums">
                            {relTime(s.updatedAt)}
                          </span>
                        </div>
                        {s.lastPreview ? (
                          <div className="truncate text-[10px] leading-snug text-muted-foreground">
                            {s.lastPreview}
                          </div>
                        ) : (
                          <div className="text-[10px] leading-snug text-muted-foreground/70">
                            {s.messageCount > 0 ? `${s.messageCount} msgs` : "New"}
                          </div>
                        )}
                      </div>
                      {/* Delete — hover-revealed on pointer devices to keep rows
                          clean, but ALWAYS visible on touch (no hover exists, so
                          the button was unreachable on mobile). */}
                      <button
                        onClick={(e) => {
                          e.preventDefault();
                          e.stopPropagation();
                          onDelete(s.id);
                        }}
                        className="ml-1 shrink-0 rounded-md p-1.5 text-muted-foreground/60 opacity-0 group-hover:opacity-100 focus-visible:opacity-100 [@media(hover:none)]:opacity-100 hover:text-destructive hover:bg-destructive/10 transition-all"
                        title="Delete conversation"
                        aria-label="Delete conversation"
                      >
                        <Icon name="Trash2" className="w-3.5 h-3.5" />
                      </button>
                    </div>
                  );
                })}
              </div>
            )}
          </div>
        );
      })}
    </div>
  );
}

// ---------------------------------------------------------------------------
// Page root
// ---------------------------------------------------------------------------

function ChatPageInner() {
  const searchParams = useSearchParams();
  const { data: nextAuthSession } = useSession();
  const userId: string = nextAuthSession?.user?.email ?? "dev@fracktal.in";

  const { isMobile } = useViewMode();
  const {
    open: openDrawer, close: closeDrawer, isOpen: drawerIsOpen,
  } = useMobileDrawer();
  const activeRunIds = useActiveSessions();

  // Refs to hold drawer content (defined later in render).
  const conversationsRef = useRef<React.ReactNode>(null);
  const filesRef = useRef<React.ReactNode>(null);
  // Which tab's content the mobile drawer is currently showing — so we can
  // re-push fresh content into it when state changes (the drawer holds a frozen
  // snapshot, so e.g. deleting a session wouldn't otherwise update it).
  const drawerTabRef = useRef<"chats" | "files" | null>(null);

  // Listen for bottom-nav tab events from AppShell MobileBottomNav.
  useEffect(() => {
    const handler = (e: Event) => {
      const tab = (e as CustomEvent<string>).detail;
      if (tab === "chats" && conversationsRef.current) {
        drawerTabRef.current = "chats";
        openDrawer(conversationsRef.current);
      } else if (tab === "files" && filesRef.current) {
        drawerTabRef.current = "files";
        openDrawer(filesRef.current);
      }
    };
    window.addEventListener("cc-mobile-nav", handler);
    return () => window.removeEventListener("cc-mobile-nav", handler);
  }, [openDrawer]);

  const [sessions, setSessions] = useState<ChatSession[]>([]);
  const [activeSessionId, setActiveSessionId] = useState<string>("");
  // Cross-conversation memory (load + 30s poll) — injected into AgentChat for
  // continuity; managed in the full memory manager at /memory, not here.
  const { memories } = useChatMemories(userId);

  // ── Email-assistant parity ────────────────────────────────────────────────
  // Give the email-assistant the SAME account-aware context in the chat app
  // that the email app provides (the open-email context is inherently
  // email-app-only).  Fetch the user's accounts when the active session is the
  // email-assistant and build the SHARED persona the email app also uses.
  const [emailAccounts, setEmailAccounts] = useState<
    Array<{ id: string; label?: string | null; email_address?: string | null }>
  >([]);
  const activeAgentName = sessions.find((s) => s.id === activeSessionId)?.agentName;
  useEffect(() => {
    if (activeAgentName !== "email-assistant") return;
    let cancelled = false;
    fetch("/api/email/accounts")
      .then((r) => (r.ok ? r.json() : []))
      .then((d) => { if (!cancelled && Array.isArray(d)) setEmailAccounts(d); })
      .catch(() => {});
    return () => { cancelled = true; };
  }, [activeAgentName]);
  // Which mailbox the email assistant is working on. The chat app has no inbox
  // sidebar, so this used to resolve only when the user had exactly ONE account
  // — with several, the assistant ran unconfigured and had to ask. The
  // composer's mailbox picker supplies the choice instead; we default to the
  // first account so a multi-account user still starts somewhere concrete.
  const [emailMailboxId, setEmailMailboxId] = useState<string | null>(null);
  const emailChatAccountId = emailMailboxId ?? emailAccounts[0]?.id ?? null;
  const emailMailboxOptions = useMemo(
    () =>
      emailAccounts.map((a) => ({
        id: a.id,
        label: a.email_address || a.label || a.id,
      })),
    [emailAccounts],
  );
  // Default to the documented email-chat default (tier-powerful) so a send during
  // the brief settings-fetch window uses a sensible model instead of "auto"
  // (which the backend would coerce to a different tier). Refined to the
  // account's saved chat_model once the fetch resolves; kept on lookup failure.
  const [emailChatModel, setEmailChatModel] = useState<string | undefined>("tier-powerful");
  // The account's standing configuration, fed into the persona below — same
  // fetch, so the assistant behaves the same here as in the email app.
  const [emailAcctSettings, setEmailAcctSettings] =
    useState<PersonaAccountSettings | null>(null);
  useEffect(() => {
    if (activeAgentName !== "email-assistant" || !emailChatAccountId) return;
    let cancelled = false;
    getAssistantSettings(emailChatAccountId)
      .then((s) => {
        if (cancelled) return;
        setEmailChatModel(s.chat_model || "tier-powerful");
        setEmailAcctSettings({
          about: s.about,
          personal_instructions: s.personal_instructions,
          writing_style: s.writing_style,
          learned_writing_style: s.learned_writing_style,
        });
      })
      .catch(() => { /* keep the tier-powerful default on lookup failure */ });
    return () => { cancelled = true; };
  }, [activeAgentName, emailChatAccountId]);
  // Declared after the settings it reads (the persona carries the active
  // account's standing configuration, not just the account list).
  const emailAssistantPersona = useMemo(
    () =>
      activeAgentName === "email-assistant"
        ? buildEmailAssistantPersona({
            accounts: emailAccounts,
            selectedAccountId: emailChatAccountId,
            settings: emailAcctSettings,
          })
        : undefined,
    [activeAgentName, emailAccounts, emailChatAccountId, emailAcctSettings],
  );
  const [showPicker, setShowPicker] = useState(false);
  // Desktop: collapsible side panel.  Mobile: drawer-based (never a sidebar).
  const [sessionPanelOpen, setSessionPanelOpen] = useState(true);
  const [artifactPanelOpen, setArtifactPanelOpen] = useState(false);
  const [viewerEntry, setViewerEntry] = useState<FileEntry | null>(null);
  const [artifactUpdates, setArtifactUpdates] = useState<FileEntry[]>([]);

  // On mobile the side panels start collapsed so the chat fills the screen;
  // they open as overlay drawers on demand.
  /* eslint-disable react-hooks/set-state-in-effect */
  useEffect(() => {
    if (isMobile) {
      setSessionPanelOpen(false);
      setArtifactPanelOpen(false);
    } else {
      setSessionPanelOpen(true);
    }
  }, [isMobile]);
  /* eslint-enable react-hooks/set-state-in-effect */

  // Auto-reveal the artifact sidebar the first time an agent writes a file
  // (desktop only — on mobile it's a drawer the user opens explicitly).
  const artifactsRevealedRef = useRef(false);
  useEffect(() => {
    if (artifactUpdates.length > 0 && !artifactsRevealedRef.current && !isMobile) {
      artifactsRevealedRef.current = true;
      // eslint-disable-next-line react-hooks/set-state-in-effect
      setArtifactPanelOpen(true);
    }
  }, [artifactUpdates.length, isMobile]);

  // Fetch agents once at page level so AgentChat knows agent_runtime before first render.
  const [agentList, setAgentList] = useState<AgentEntry[]>([]);
  useEffect(() => {
    fetch("/api/agent/list")
      .then((r) => r.json())
      .then((data: AgentEntry[]) => { if (Array.isArray(data)) setAgentList(data); })
      .catch(() => {});
  }, []);
  // canonical name → friendly display name, for the session-list group headers.
  const agentAliasMap = useMemo(
    () => Object.fromEntries(
      agentList
        .filter((a) => a.display_name)
        .map((a) => [a.name, a.display_name as string]),
    ),
    [agentList],
  );

  // Load sessions from localStorage on mount.
  // If ?agent=<name> is in the URL, immediately open a new session for that agent.
  // If no sessions exist, show the agent picker — never default to any agent.
  /* eslint-disable react-hooks/set-state-in-effect */
  useEffect(() => {
    const agentParam = searchParams?.get("agent");
    const existing = getSessions();
    if (agentParam) {
      const existing2 = getSessions();
      const match = existing2.find((s) => s.agentName === agentParam);
      if (match) {
        setSessions(getSessions());
        setActiveSessionId(match.id);
      } else {
        const fresh = createSession(agentParam);
        upsertSession(fresh);
        setSessions(getSessions());
        setActiveSessionId(fresh.id);
      }
    } else if (existing.length === 0) {
      // No sessions yet — show the agent picker so the user explicitly
      // chooses which agent to talk to instead of defaulting blindly.
      setShowPicker(true);
    } else {
      setSessions(existing);
      setActiveSessionId(existing[0].id);
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps -- one-time init
  }, []);
  /* eslint-enable react-hooks/set-state-in-effect */

  // After initial localStorage render, fetch from Postgres and merge any sessions
  // that exist there but not in the browser (e.g. after cache clear or new device).
  // Also poll every 30s so sessions created on other devices appear in the sidebar
  // without requiring a page refresh.
  useEffect(() => {
    let cancelled = false;
    let timer: ReturnType<typeof setTimeout>;

    const sync = async () => {
      if (cancelled) return;
      try {
        const merged = await fetchAndMergeSessionsFromDb();
        if (!cancelled) setSessions(merged);
      } catch { /* best-effort */ }
      if (!cancelled) timer = setTimeout(sync, 30000);
    };

    // Initial sync
    sync();

    return () => { cancelled = true; clearTimeout(timer); };
  }, []);

  const handleNewSession = useCallback(() => {
    setShowPicker(true);
  }, []);

  const handleSelectAgent = useCallback(
    (agentName: string) => {
      setShowPicker(false);
      // If the active session is unresolved ("unknown"), REPAIR it in place
      // rather than spawning a duplicate — the user picked the agent for THIS
      // conversation, so adopt it (updates localStorage + Postgres via upsert).
      const current = getSessions().find((s) => s.id === activeSessionId);
      if (current && isUnresolvedAgent(current.agentName)) {
        const repaired = { ...current, agentName, updatedAt: new Date().toISOString() };
        upsertSession(repaired);
        setSessions(getSessions());
        setActiveSessionId(repaired.id);
        return;
      }
      const s = createSession(agentName);
      upsertSession(s);
      setSessions(getSessions());
      setActiveSessionId(s.id);
    },
    [activeSessionId]
  );

  const handleSelectSession = useCallback((id: string) => {
    setActiveSessionId(id);
  }, []);

  const handleDeleteSession = useCallback(
    (id: string) => {
      const deleted = getSessions().find((s) => s.id === id);
      // The delete control is now always tappable on touch, and deletion is
      // irreversible (it wipes the conversation's history) — confirm first so a
      // stray tap can't destroy a thread.
      const label = deleted?.title ?? deleted?.name ?? "this conversation";
      if (typeof window !== "undefined" &&
          !window.confirm(`Delete "${label}"? This can't be undone.`)) {
        return;
      }
      deleteSession(id);
      const remaining = getSessions();
      setSessions(remaining);
      if (id === activeSessionId) {
        if (remaining.length > 0) {
          // Prefer a sibling of the SAME agent so deleting a session doesn't
          // bounce the user into an unrelated agent's conversation (sessions are
          // sorted across all agents by recency).
          const sameAgent = deleted
            ? remaining.find((s) => s.agentName === deleted.agentName)
            : undefined;
          setActiveSessionId((sameAgent ?? remaining[0]).id);
        } else {
          // All sessions gone — show the agent picker instead of
          // silently defaulting to the orchestrator.
          setActiveSessionId("");
          setShowPicker(true);
        }
      }
    },
    [activeSessionId]
  );

  // Enrich the active session (auto-title + last-turn preview) as the chat runs.
  const handleActivity = useCallback(
    (
      id: string,
      info: { firstUserMessage?: string; lastPreview?: string; messageCount: number },
    ) => {
      enrichSession(id, info);
      setSessions(getSessions());
    },
    [],
  );

  const activeSession = sessions.find((s) => s.id === activeSessionId);

  // Side panel shows the ACTIVE session's documents only — drop other sessions'
  // tabs when switching so we never render a file from the wrong workspace.
  useEffect(() => {
    if (activeSessionId) pruneToSession(activeSessionId);
  }, [activeSessionId]);

  // Open a workspace file as a tab in the side-panel editor (right-click action
  // and the auto-open-on-write path both funnel through here).
  const handleOpenInSidePanel = useCallback(
    (entry: FileEntry, opts?: { live?: boolean }) => {
      if (!activeSessionId) return;
      openDoc({
        path: entry.path,
        name: entry.name,
        sessionId: activeSessionId,
        live: opts?.live,
      });
    },
    [activeSessionId],
  );

  // ── Unresolved-agent guard ("infer, else prompt") ──────────────────────
  // A session persisted with agentName="unknown" (the /chat/active-sessions
  // placeholder that leaked into chat_session rows) must NOT be dispatched — it
  // 422s "Unknown agent 'unknown'". The gateway now infers the real agent from
  // the run trace on dispatch, and the data migration repairs stored rows; this
  // is the client "else prompt" fallback for a still-unresolved active session:
  // open the picker so the user resolves it, which repairs the row in place
  // (handleSelectAgent). Only fires when a session is actually selected.
  useEffect(() => {
    if (activeSession && isUnresolvedAgent(activeSession.agentName)) {
      setShowPicker(true);
    }
  }, [activeSession]);

  // ── Mobile: drawer content builders ────────────────────────────────────
  const conversationsContent = (
    <>
      <div className="flex items-center justify-between border-b border-border px-4 py-3">
        <div className="text-sm font-semibold text-foreground">Conversations</div>
        <Button variant="ghost" size="icon-sm" layout="" onClick={closeDrawer} aria-label="Close">
          <svg width="16" height="16" viewBox="0 0 16 16" fill="none" stroke="currentColor" strokeWidth="1.5">
            <path d="M4 4l8 8M12 4l-8 8" />
          </svg>
        </Button>
      </div>
      <div className="flex flex-col flex-1 overflow-y-auto p-3">
        <SessionList
          sessions={sessions}
          activeId={activeSessionId}
          activeRunIds={activeRunIds}
          onSelect={(id) => { handleSelectSession(id); closeDrawer(); }}
          onNew={() => { handleNewSession(); closeDrawer(); }}
          onDelete={handleDeleteSession}
          agentAliases={agentAliasMap}
        />
      </div>
    </>
  );

  const filesContent = (
    <>
      <div className="flex items-center justify-between border-b border-border px-4 py-3">
        <div className="text-sm font-semibold text-foreground">Files</div>
        <Button variant="ghost" size="icon-sm" layout="" onClick={closeDrawer} aria-label="Close">
          <svg width="16" height="16" viewBox="0 0 16 16" fill="none" stroke="currentColor" strokeWidth="1.5">
            <path d="M4 4l8 8M12 4l-8 8" />
          </svg>
        </Button>
      </div>
      <div className="flex-1 overflow-y-auto">
        {/* Upload drop zone at top of files drawer */}
        <div className="px-3 pt-3 pb-2">
          <FileUploadButton
            sessionId={activeSessionId}
            dropZone
            onUploadComplete={(files) => {
              setArtifactUpdates((prev) => [
                ...prev,
                ...files.map((f) => ({ ...f, is_dir: false })),
              ]);
            }}
          />
        </div>
        <ArtifactSidebar
          sessionId={activeSessionId}
          open
          fullWidth
          onToggle={closeDrawer}
          onFileOpen={(entry) => {
            setViewerEntry(entry);
            closeDrawer();
          }}
          artifactUpdates={artifactUpdates}
        />
      </div>
    </>
  );

  // Sync refs after render (avoid refs-during-render lint error).
  useEffect(() => {
    conversationsRef.current = conversationsContent;
    filesRef.current = filesContent;
  });

  // The mobile drawer holds a one-time snapshot of its content, so state changes
  // after it opens (e.g. deleting a session) don't show until it's reopened.
  // Re-push the live content into the open drawer when the underlying data
  // changes. Runs after the ref-sync effect above, so the refs are current.
  useEffect(() => {
    if (!drawerIsOpen) {
      drawerTabRef.current = null;
      return;
    }
    if (drawerTabRef.current === "chats" && conversationsRef.current) {
      openDrawer(conversationsRef.current);
    } else if (drawerTabRef.current === "files" && filesRef.current) {
      openDrawer(filesRef.current);
    }
    // Keyed on the data that rebuilds the drawer content; openDrawer only
    // updates AppShell state (won't loop — our own deps don't change from it).
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [sessions, artifactUpdates, activeSessionId, drawerIsOpen]);

  // ── Render ─────────────────────────────────────────────────────────────
  return (
    <div className="relative flex h-full overflow-hidden">
      {/* Agent picker modal */}
      {showPicker && (
        <AgentPickerModal
          onSelect={handleSelectAgent}
          onClose={() => setShowPicker(false)}
        />
      )}

      {/* ── Desktop: conversations sidebar ────────────────────────────── */}
      {/* Collapsed → the same rail treatment as the Files / Documents
          columns: reopen icon, conversation count, vertical label. */}
      {!isMobile && !sessionPanelOpen && (
        <aside className="flex w-10 shrink-0 flex-col border-r border-border bg-sidebar">
          {/* The whole rail is the click target — a thin strip is fiddly to
              hit an icon inside. */}
          <button
            onClick={() => setSessionPanelOpen(true)}
            className="flex w-full flex-1 cursor-pointer flex-col items-center py-2.5 text-muted-foreground hover:bg-sidebar-accent hover:text-sidebar-foreground transition-colors"
            title="Open conversations"
          >
            <Icon name="MessagesSquare" size={15} />
            {sessions.length > 0 && (
              <span className="mt-1 rounded-full bg-secondary px-1 text-[10px]">
                {sessions.length}
              </span>
            )}
            {sessions.some((s) => activeRunIds.has(s.id)) && (
              <span
                className="mt-1.5 inline-block h-1.5 w-1.5 rounded-full bg-emerald-400 animate-pulse"
                title="An agent is running"
              />
            )}
            <span className="mt-3 flex flex-1 items-center justify-center">
              <span
                className="text-[10px] font-semibold tracking-widest"
                style={{ writingMode: "vertical-rl", transform: "rotate(180deg)" }}
              >
                CONVERSATIONS
              </span>
            </span>
          </button>
        </aside>
      )}
      {!isMobile && sessionPanelOpen && (
        <aside className="w-72 shrink-0 border-r border-border bg-sidebar flex flex-col overflow-hidden">
          <div className="flex items-center justify-between border-b border-border px-4 py-3">
            <div className="text-sm font-semibold text-sidebar-foreground">Conversations</div>
            <button
              onClick={() => setSessionPanelOpen(false)}
              className="shrink-0 rounded-lg p-1.5 text-muted-foreground hover:bg-sidebar-accent hover:text-sidebar-foreground tech-transition"
              title="Collapse conversations"
            >
              <svg width="14" height="14" viewBox="0 0 16 16" fill="none" stroke="currentColor" strokeWidth="1.5">
                <path d="M10 3L5 8l5 5" />
              </svg>
            </button>
          </div>

          <div className="flex flex-col flex-1 p-4 overflow-y-auto">
            <SessionList
              sessions={sessions}
              activeId={activeSessionId}
              activeRunIds={activeRunIds}
              onSelect={handleSelectSession}
              onNew={handleNewSession}
              onDelete={handleDeleteSession}
              agentAliases={agentAliasMap}
            />

            <div className="mt-auto pt-6 text-[10px] text-muted-foreground/50">
              Memory persists to Mem0 · Sessions in localStorage
            </div>
          </div>
        </aside>
      )}

      {/* ── Desktop: files column (left of documents) ──────────────────── */}
      {!isMobile && (
        <ArtifactSidebar
          side="left"
          sessionId={activeSessionId}
          open={artifactPanelOpen}
          onToggle={() => setArtifactPanelOpen((o) => !o)}
          onFileOpen={(entry) => {
            setViewerEntry(entry);
            setArtifactPanelOpen(true);
          }}
          onOpenInSidePanel={(entry) => handleOpenInSidePanel(entry)}
          artifactUpdates={artifactUpdates}
        />
      )}

      {/* ── Desktop: VS Code-style document side panel ─────────────────── */}
      {!isMobile && <SidePanelEditor />}

      {/* ── Chat area ──────────────────────────────────────────────────── */}
      <div className="flex flex-1 overflow-hidden min-w-0">
        <div className="flex flex-1 flex-col overflow-hidden min-w-0">

          {activeSession ? (
              <AgentChat
                key={activeSession.id}
                agentName={activeSession.agentName}
                sessionId={activeSession.id}
                agentDescription={
                  PERSONA_AGENTS.has(activeSession.agentName)
                    ? "General-purpose AI company brain"
                    : undefined
                }
                persona={
                  PERSONA_AGENTS.has(activeSession.agentName)
                    ? METORITE_PERSONA
                    : activeSession.agentName === "email-assistant"
                      ? emailAssistantPersona
                      : undefined
                }
                emailContext={
                  activeSession.agentName === "email-assistant"
                    ? { accountId: emailChatAccountId }
                    : undefined
                }
                mailboxes={
                  activeSession.agentName === "email-assistant"
                    ? emailMailboxOptions
                    : undefined
                }
                activeMailboxId={emailChatAccountId}
                onMailboxChange={setEmailMailboxId}
                // Email-assistant locks to the account chat_model (parity with
                // the email app); all other agents keep the generic picker.
                model={
                  activeSession.agentName === "email-assistant"
                    ? emailChatModel
                    : undefined
                }
                lockModel={activeSession.agentName === "email-assistant"}
                memories={memories.map((m) => m.memory)}
                memoryUserId={userId}
                availableAgents={agentList.length > 0 ? agentList : undefined}
                expectedMessageCount={activeSession.messageCount}
                onActivity={(info) => handleActivity(activeSession.id, info)}
                onArtifact={(entry: ArtifactEntry) => {
                  const name = entry.path.split("/").pop() ?? entry.path;
                  setArtifactUpdates((prev) => {
                    // Merge by path (artifact_created -> artifact_updated for the
                    // same file is one entry, not two) and keep a prior size /
                    // mime when a later event omits them, so the file tree doesn't
                    // regress to 0 B / a generic icon.
                    const prior = prev.find((f) => f.path === entry.path);
                    const fe: FileEntry = {
                      path: entry.path,
                      name,
                      size: entry.size ?? prior?.size ?? 0,
                      modified_at: new Date().toISOString(),
                      mime_type: entry.mimeType ?? prior?.mime_type ?? "",
                    };
                    return [...prev.filter((f) => f.path !== entry.path), fe];
                  });

                  // Auto-open documents the agent writes so the user watches
                  // them build in real time (Markdown/HTML → live preview in the
                  // side panel). Other file types surface in the Files tree only.
                  const ext = name.split(".").pop()?.toLowerCase() ?? "";
                  // A .jsx/.tsx under outputs/ is a full-page React artifact, so
                  // it opens like any other document. Elsewhere those extensions
                  // are ordinary source files an agent may be editing — opening
                  // every one of those would hijack the panel (matches
                  // DocumentPane's isArtifactPath rule).
                  const isReactArtifact =
                    (ext === "jsx" || ext === "tsx") &&
                    entry.path.replace(/^\/+/, "").startsWith("outputs/");
                  const isDoc =
                    ["md", "mdx", "html", "htm"].includes(ext) || isReactArtifact;
                  if (isDoc && activeSession && !isMobile) {
                    openDoc({
                      path: entry.path,
                      name,
                      sessionId: activeSession.id,
                      live: true,
                    });
                    // Clear the "writing" badge shortly after the last write —
                    // a subsequent write to the same path re-sets it to live.
                    const sid = activeSession.id;
                    window.setTimeout(() => setDocLive(sid, entry.path, false), 2500);
                  }
                }}
              />
          ) : (
            <div className="flex flex-1 flex-col items-center justify-center gap-4 text-muted-foreground">
              <div className="text-sm">Choose an agent to start chatting</div>
              <Button size="none" layout="" onClick={handleNewSession} className="px-5 py-2.5 text-sm">
                + New session
              </Button>
            </div>
          )}
        </div>
      </div>

      {/* File viewer modal (shared) */}
      {viewerEntry && (
        <ArtifactViewerModal
          sessionId={activeSessionId}
          entry={viewerEntry}
          onClose={() => setViewerEntry(null)}
          onDelete={(entry) => {
            setViewerEntry(null);
            setArtifactUpdates((prev) => prev.filter((f) => f.path !== entry.path));
          }}
        />
      )}
    </div>
  );
}

export default function ChatPage() {
  return (
    <Suspense fallback={null}>
      <ChatPageInner />
    </Suspense>
  );
}
