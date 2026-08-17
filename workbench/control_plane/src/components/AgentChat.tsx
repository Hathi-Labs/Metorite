"use client";

/**
 * AgentChat — chat UI for LangGraph agents routed via the Metorite gateway.
 *
 * Used by chat/page.tsx when session.agentName is set to a named agent.
 * Missing integrations are surfaced inline — users can configure them via chat
 * or dismiss the banner and configure later in the Integrations page.
 */

import Button from "@/components/ui/Button";
import Icon from "@/components/Icon";
import { useState, useRef, useEffect, useCallback, useMemo, useSyncExternalStore } from "react";
import React from "react";
import Link from "next/link";
import { useAgentChat } from "@/hooks/useAgentChat";
import type { ArtifactEntry, ChatMessage } from "@/hooks/useAgentChat";
import type { IntegrationStatus } from "@/app/api/integrations/status/route";
import type { AgentEntry } from "@/app/api/agent/list/route";
import type { UnifiedModel } from "@/app/api/models/all/route";
import ArtifactViewerModal from "@/components/ArtifactViewerModal";
import { subscribe as subscribeSidePanel, getOpenDocsForSession, openGenUI } from "@/lib/sidePanelStore";
import type { FileEntry } from "@/components/ArtifactSidebar";
import FileUploadButton from "@/components/FileUploadButton";
import { AgentAvatar, useAgentAvatars } from "@/components/AgentAvatar";
import SuggestionPills from "@/components/SuggestionPills";
import ConfirmationCard from "@/components/ConfirmationCard";
import ElicitationCard from "@/components/ElicitationCard";
import type { ElicitationQuestion, ElicitationAnswers } from "@/components/ElicitationCard";
import TodoPanel from "@/components/TodoPanel";
import ContextRing from "@/components/ContextRing";
import MessageBubble from "@/components/MessageBubble";
import { RoomHeader } from "@/components/room/RoomHeader";
import { PresenceRail } from "@/components/room/PresenceRail";
import { useRoom } from "@/hooks/useRoom";
import { peopleOf } from "@/lib/rooms";
import { getMessages, saveMessages, fetchMessagesFromDb, getQueue, saveQueue, type PersistedMessage } from "@/lib/sessions";
import { computeContextUsage, activeContextSlice, isCompactionCheckpoint } from "@/lib/tokenCount";
import { serializeReasoning } from "@/lib/chatStream";
import { useAgentEvents } from "@/lib/agentEvents";
import { buildFrontendToolsAddendum } from "@/hooks/useFrontendTool";

// Unified model fallback — shown while /api/models/all is loading.
// Always includes the tiers (always accessible) and Gemini models (default provider).
// Provider-specific models for other providers are added dynamically after fetch.
const MODELS_FALLBACK: UnifiedModel[] = [
  { id: "auto",              label: "auto (SDK picks)",     runtime: "copilot", group: "GitHub Copilot SDK" },
  { id: "tier-fast",     label: "Tier 1 (fast / cheap)", runtime: "litellm", group: "LiteLLM — Tiers" },
  { id: "tier-balanced", label: "Tier 2 (balanced)",     runtime: "litellm", group: "LiteLLM — Tiers" },
  { id: "tier-powerful", label: "Tier 3 (powerful)",     runtime: "litellm", group: "LiteLLM — Tiers" },
  { id: "gemini/gemini-2.5-flash", label: "Gemini 2.5 Flash", runtime: "litellm", group: "LiteLLM — Gemini" },
  { id: "gemini/gemini-2.5-pro",   label: "Gemini 2.5 Pro",   runtime: "litellm", group: "LiteLLM — Gemini" },
];

// Number of messages loaded per window when restoring / paging history.
// Keeps initial restore fast for very long sessions; older messages load on
// scroll-up.
const HISTORY_PAGE_SIZE = 30;

type SendMode = "send" | "queue" | "steer";

const SEND_MODE_LABELS: Record<SendMode, string> = {
  send: "Send",
  queue: "Queue",
  steer: "Steer",
};

const SEND_MODE_DESCRIPTIONS: Record<SendMode, string> = {
  steer: "Interrupt current reply and send now",
  send:  "Send when idle, queue if busy (default)",
  queue: "Wait for current reply, then send",
};

// ── Suggestion pills (CopilotKit-style starter prompts) ─────────────────

const DEFAULT_SUGGESTIONS = [
  "What can you help me with?",
  "Summarize recent activity",
  "Create a new task",
];

const AGENT_SUGGESTIONS: Record<string, string[]> = {
  orchestrator: [
    "What can you help me with?",
    "Summarize recent activity across the company",
    "Create a task in ClickUp",
  ],
  "task-manager": [
    "Show my open tasks",
    "Create a new high-priority task",
    "What's due this week?",
  ],
  "sales-assistant": [
    "Show pipeline summary",
    "Create a new lead",
    "What deals are closing this month?",
  ],
  "email-assistant": [
    "Summarize my unread email",
    "What needs a reply?",
    "Draft a reply to this email",
    "Set up a rule to archive newsletters",
  ],
};

// ── Per-agent model memory ──────────────────────────────────────────────

const MODEL_PREF_KEY = (agent: string) => `cc-model-${agent}`;
const MODEL_USAGE_KEY = "cc-model-usage";

function getLastModel(agentName: string): string | null {
  try {
    return localStorage.getItem(MODEL_PREF_KEY(agentName));
  } catch { return null; }
}

function setLastModel(agentName: string, modelId: string): void {
  try { localStorage.setItem(MODEL_PREF_KEY(agentName), modelId); } catch { /* noop */ }
}

function getModelUsage(): Record<string, number> {
  try {
    const raw = localStorage.getItem(MODEL_USAGE_KEY);
    return raw ? JSON.parse(raw) as Record<string, number> : {};
  } catch { return {}; }
}

function incrementModelUsage(modelId: string): void {
  if (modelId === "auto") return; // don't track auto
  try {
    const usage = getModelUsage();
    usage[modelId] = (usage[modelId] ?? 0) + 1;
    localStorage.setItem(MODEL_USAGE_KEY, JSON.stringify(usage));
  } catch { /* noop */ }
}

interface AgentChatProps {
  agentName: string;
  sessionId: string;
  agentDescription?: string;
  /** Integration statuses passed from the parent. If absent, fetched on mount. */
  integrationStatuses?: IntegrationStatus[];
  /** Full agent list for mid-chat agent switching. If absent, fetched on mount. */
  availableAgents?: AgentEntry[];
  /** Persona / system prompt injected as system context (e.g. Metorite brain). */
  persona?: string;
  /** Persistent memory lines (Mem0) injected as system context. */
  memories?: string[];
  /** When set, the conversation is saved to Mem0 on unmount under this userId. */
  memoryUserId?: string;
  /**
   * Reports conversation activity to the parent so it can enrich the session
   * list (auto-title from the first user message + last-turn preview).
   */
  onActivity?: (info: {
    firstUserMessage?: string;
    lastPreview?: string;
    messageCount: number;
  }) => void;
  /** Called when the agent writes a file via write_artifact tool. */
  onArtifact?: (entry: ArtifactEntry) => void;
  /**
   * Number of messages this session is known to have (from the sidebar list,
   * sourced from Postgres). Drives the history-loading skeleton when the local
   * cache is empty but the server still has rows to fetch.
   */
  expectedMessageCount?: number;
  /**
   * Email-app context: the currently-selected account + open email. Passed down
   * to the rich email tool cards (Save-to-Drafts / rule actions) so they can act
   * on the user's current selection. Harmless to omit — the cards also self-source
   * these ids from the agent's tool-call args.
   */
  emailContext?: { accountId?: string | null; emailId?: string | null };
  /**
   * Mailboxes the email assistant can act on, for the composer's mailbox
   * picker. Only meaningful for the email assistant — every other agent omits
   * it and the control doesn't render.
   *
   * A chat thread is not per-account (sessions are keyed by agent), so the same
   * conversation can span mailboxes. Making the active one an explicit control
   * — rather than something inherited silently from whatever the inbox happened
   * to be showing — is what stops "which inbox is it talking about?" from being
   * unanswerable, and the switch is marked in the transcript when it happens.
   */
  mailboxes?: { id: string; label: string }[];
  /** The mailbox currently in force. Controlled by the parent. */
  activeMailboxId?: string | null;
  /** Raised when the user picks a different mailbox in the composer. */
  onMailboxChange?: (id: string) => void;
  /**
   * Force the model this chat runs on (e.g. the email app's assistant
   * `chat_model` setting). When set it overrides the per-agent localStorage
   * default and stays in sync as the value changes — so the surface conforms to
   * an externally-configured model rather than its own picker. Empty/undefined
   * keeps the normal per-agent picker behaviour.
   */
  model?: string;
  /**
   * Hide the in-chat model picker. Use with `model` when the model is governed
   * elsewhere (e.g. the email Assistant settings) so there's a single source of
   * truth instead of two competing controls.
   */
  lockModel?: boolean;
  /**
   * Compact layout for narrow embeds (e.g. the email app's 288px rail): trims the
   * footer disclaimer + keyboard hints to save vertical space.
   */
  compact?: boolean;
  /**
   * One-shot text to drop into the composer (not auto-sent) — used by the email
   * Assistant's "Fix" flow to hand a correction prompt to the user for review.
   * Changing this value injects it; `onPendingInputConsumed` fires once applied.
   */
  pendingInput?: string;
  onPendingInputConsumed?: () => void;
}

export default function AgentChat({
  agentName,
  sessionId,
  agentDescription,
  integrationStatuses: externalStatuses,
  availableAgents: externalAgents,
  persona,
  memories,
  memoryUserId,
  onActivity,
  onArtifact,
  expectedMessageCount,
  emailContext,
  mailboxes,
  activeMailboxId,
  onMailboxChange,
  model: forcedModel,
  lockModel,
  compact,
  pendingInput,
  onPendingInputConsumed,
}: AgentChatProps) {
  // Active agent / model can change mid-chat (VS Code Copilot style).
  const [currentAgentName, setCurrentAgentName] = useState(agentName);
  const [currentModel, setCurrentModel] = useState(
    () => forcedModel || getLastModel(agentName) || "auto",
  );
  // Conform to an externally-governed model (e.g. the email assistant's
  // `chat_model` setting) and re-sync if it changes.
  useEffect(() => {
    if (forcedModel) setCurrentModel(forcedModel);
  }, [forcedModel]);
  // ── Mailbox switches, marked in the transcript ─────────────────────
  // A thread is keyed by agent, not by account, so one conversation can span
  // mailboxes. Without a marker the assistant's standing instructions swap
  // silently mid-thread and neither the reader nor the model can tell where the
  // boundary is. Each switch is anchored to the message it followed.
  const [showMailboxMenu, setShowMailboxMenu] = useState(false);
  const [mailboxMarks, setMailboxMarks] =
    useState<{ afterId: string; label: string }[]>([]);
  const activeMailbox = mailboxes?.find((m) => m.id === activeMailboxId);
  // ── Thinking mode ──────────────────────────────────────────────────
  type ThinkMode = "auto" | "thinking" | "max";
  const [thinkMode, setThinkMode] = useState<ThinkMode>("auto");
  const [showAgentMenu, setShowAgentMenu] = useState(false);
  const [agents, setAgents] = useState<AgentEntry[]>(externalAgents ?? []);
  const [models, setModels] = useState<UnifiedModel[]>(MODELS_FALLBACK);

  // Fetch the unified model list (Copilot SDK + LiteLLM) on mount.
  useEffect(() => {
    fetch("/api/models/all")
      .then((r) => r.json())
      .then((data: unknown) => {
        const d = data as { models?: UnifiedModel[] };
        if (Array.isArray(d.models) && d.models.length > 0) {
          setModels(d.models);
        }
      })
      .catch(() => {}); // Keep fallback list on error
  }, []);

  // Persist model preference per agent + track usage count.
  // Only count usage on an ACTUAL model change (not on mount or agent switch),
  // otherwise every session open inflates the "Frequently Used" ranking.
  const prevModelRef = useRef<string | null>(null);
  useEffect(() => {
    // Don't persist/track an externally-forced model — it's governed by its own
    // setting and would otherwise overwrite the agent's own picker default.
    if (forcedModel) return;
    setLastModel(currentAgentName, currentModel);
    if (prevModelRef.current !== null && prevModelRef.current !== currentModel) {
      incrementModelUsage(currentModel);
    }
    prevModelRef.current = currentModel;
  }, [currentModel, currentAgentName, forcedModel]);

  // ── Model sorting: frequently used models float to the top ──────────────
  const sortedModels = useMemo(() => {
    const usage = getModelUsage();
    const topIds = Object.entries(usage)
      .sort(([, a], [, b]) => b - a)
      .slice(0, 4)
      .map(([id]) => id);
    const topSet = new Set(topIds);

    // Only promote models that are still in the list (not hidden/removed).
    const frequent = models.filter((m) => topSet.has(m.id) && m.id !== "auto");
    const rest = models.filter((m) => !topSet.has(m.id) || m.id === "auto");

    // Attach a synthetic group so the picker can render the section header.
    const withGroup = (m: UnifiedModel, group: string): UnifiedModel => ({ ...m, group });
    return [
      ...frequent.map((m) => withGroup(m, "Frequently Used")),
      ...rest,
    ];
  }, [models]);
  // ────────────────────────────────────────────────────────────────────────

  // Resolve the selected model's routing runtime (copilot SDK vs gateway BYOK).
  const selectedModel = models.find((m) => m.id === currentModel);
  const currentRuntime = selectedModel?.runtime ?? "copilot";

  // Resolve the active agent's metadata (runtime classification, repo link, etc.)
  // NOTE: computed here (before useAgentChat) so we can override the routing mode.
  const currentAgentEntry = agents.find((a) => a.name === currentAgentName);
  const agentRuntime: string = currentAgentEntry?.agent_runtime ?? "maf";
  // Friendly display name (alias) for UI labels only — dispatch, localStorage
  // and routing keep using the canonical `currentAgentName`.
  const currentAgentLabel = currentAgentEntry?.display_name || currentAgentName;
  // Assigned pixel-art avatars (name → libraryId) for the agent switcher/header.
  const agentAvatars = useAgentAvatars();

  // All named agents route through the Copilot SDK executor (/agent/run/stream)
  // regardless of the model selected. The model is forwarded as a hint for
  // BYOK provider injection in the executor. This ensures every agent gets
  // its tools + instructions even when using a custom model.
  // The orchestrator (Metorite) still uses model-driven routing for
  // fast stateless chat when LiteLLM models are selected.
  const isOrchestrator = currentAgentName === "orchestrator" || currentAgentName === "metorite";
  const effectiveRuntime = isOrchestrator ? currentRuntime : "copilot";

  // Documents the user currently has open in the side-panel editor — folded
  // into the agent's context so it knows what the user is looking at / editing
  // and can reference or update those files. Stable snapshot per session.
  const openDocs = useSyncExternalStore(
    subscribeSidePanel,
    () => getOpenDocsForSession(sessionId),
    () => getOpenDocsForSession(sessionId),
  );

  // System context = persona + persistent memories + frontend tools + open docs
  // (sent as system message).
  const systemContext = useMemo(() => {
    const parts: string[] = [];
    if (persona) parts.push(persona);
    if (memories && memories.length > 0) {
      parts.push(
        "Memories from past conversations with this user — use them for continuity:\n" +
          memories.map((m) => `• ${m}`).join("\n")
      );
    }
    // Inject registered frontend tools into the agent's system prompt
    const toolsAddendum = buildFrontendToolsAddendum();
    if (toolsAddendum) parts.push(toolsAddendum);
    if (openDocs.length > 0) {
      parts.push(
        "The user currently has these workspace files open in the side-panel " +
          "editor and may be viewing or editing them — read them with your file " +
          "tools before referencing, and write changes back to the same path:\n" +
          openDocs.map((d) => `• ${d.path}`).join("\n"),
      );
    }
    return parts.length > 0 ? parts.join("\n\n") : undefined;
  }, [persona, memories, openDocs]);

  // Local cache (localStorage) — instant restore when re-opening a session that
  // was active in this browser. Empty when the session lives only in Postgres
  // (cache cleared, a different device, or the first time we open it here).
  // Drop stale __ERROR__ system messages saved by older builds.
  const cachedFull = useMemo(
    () =>
      (getMessages(sessionId) as ChatMessage[]).filter(
        (m) => !(m.role === "system" && m.content.startsWith("__ERROR__")),
      ),
    [sessionId],
  );
  const { messages, isLoading, error, sendMessage, stopGeneration, setMessages, recovering, runStatus } = useAgentChat({
    agentName: currentAgentName,
    threadId: sessionId,
    model: currentModel,
    mode: effectiveRuntime,
    systemContext,
    thinkMode,
    onArtifact,
    // Load the FULL persisted history into memory so the context sent to the
    // model and the context-usage estimate are both accurate.  We only window
    // the RENDERING (below) for performance — not the data.
    initialMessages: cachedFull,
  });

  // ── Windowed RENDERING (perf) ───────────────────────────────────────────────
  // The full conversation lives in `messages`; we render only the most recent
  // `renderLimit` to keep very long sessions snappy.  Scrolling near the top
  // reveals older messages (no refetch — they're already in memory).
  const [renderLimit, setRenderLimit] = useState(HISTORY_PAGE_SIZE);
  const hasMoreHistory = renderLimit < messages.length;
  // Scroll-position preservation when revealing older messages: holds the
  // pre-expand scrollHeight so a layout effect can keep the viewport anchored.
  const pendingPrependRef = useRef<number | null>(null);
  // Latest reveal-older fn, callable from the (deps:[]) scroll handler.
  const loadOlderHistoryRef = useRef<() => void>(() => {});

  // Reset the render window when switching sessions.
  useEffect(() => {
    setRenderLimit(HISTORY_PAGE_SIZE);
  }, [sessionId]);

  // History-loading state: always true when the sidebar says this session has
  // prior messages.  When localStorage has cached messages we show them right
  // away with a subtle "syncing…" banner; when nothing is cached we show a
  // skeleton until the DB fetch completes.  This avoids both the blank "start
  // a conversation" empty state and the jarring silent update when richer DB
  // messages replace the local cache.
  const [loadingHistory, setLoadingHistory] = useState(
    () => (expectedMessageCount ?? 0) > 0,
  );

  // On mount, fetch the authoritative FULL message history from Postgres and
  // sync into memory if it's richer than the local cache (more messages OR
  // longer content — refresh-recovery where persistAssistantMessage grew the
  // assistant record in-place during streaming).  The cached window paints
  // instantly; this background load keeps the full context + token estimate
  // accurate.  Rendering stays windowed regardless of how many we hold.
  useEffect(() => {
    let cancelled = false;
    fetchMessagesFromDb(sessionId).then((remoteRaw) => {
      if (cancelled || remoteRaw.length === 0) return;
      // Drop stale __ERROR__ system messages persisted by older builds —
      // transient errors must never resurface on reload.
      const remote = (remoteRaw as ChatMessage[]).filter(
        (m) => !(m.role === "system" && m.content?.startsWith("__ERROR__")),
      );
      if (remote.length === 0) return;
      const local = messages;
      // Quick check: more messages → definitely use DB.
      if (remote.length > local.length) {
        setMessages(remote as ChatMessage[]);
        return;
      }
      // Same count but maybe richer content?  Compare total content length,
      // tool-event counts, and CUSTOM-event counts (refresh-recovery: same id,
      // longer content).  Custom events belong in this comparison: the
      // run-boundary fold persists generative_ui / artifact cards the browser
      // may never have received (dropped SSE), and without them here a lean
      // local cache won and the very next save wrote that leanness back.
      const localTotalLen = local.reduce((sum, m) => sum + (m.content?.length ?? 0), 0);
      const remoteTotalLen = remote.reduce((sum: number, m: { content?: string }) => sum + (m.content?.length ?? 0), 0);
      const localToolCount = local.reduce((sum, m) => sum + ((m as { toolEvents?: unknown[] }).toolEvents?.length ?? 0), 0);
      const remoteToolCount = remote.reduce((sum: number, m: { toolEvents?: unknown[] }) => sum + (m.toolEvents?.length ?? 0), 0);
      const localCustomCount = local.reduce((sum, m) => sum + (m.customEvents?.length ?? 0), 0);
      const remoteCustomCount = remote.reduce((sum: number, m: { customEvents?: unknown[] }) => sum + (m.customEvents?.length ?? 0), 0);
      if (
        remoteTotalLen > localTotalLen ||
        remoteToolCount > localToolCount ||
        remoteCustomCount > localCustomCount
      ) {
        setMessages(remote as ChatMessage[]);
      }
    }).catch(() => {}).finally(() => {
      if (!cancelled) setLoadingHistory(false);
    });
    return () => { cancelled = true; };
  // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [sessionId]);

  // Persist messages to localStorage + Postgres whenever they change.
  // We skip mid-stream placeholder messages (streaming=true, content empty)
  // to avoid saving incomplete assistant turns.  We also skip transient
  // __ERROR__ system messages so a one-off disconnect error doesn't get
  // persisted and re-displayed on every future refresh.
  useEffect(() => {
    const settled = messages.filter(
      (m) =>
        (!m.streaming || m.content.trim().length > 0) &&
        !(m.role === "system" && m.content.startsWith("__ERROR__")),
    );
    if (settled.length === 0) return;
    const toSave: PersistedMessage[] = settled.map((m) => ({
      id: m.id,
      role: m.role,
      content: m.content,
      timestamp: m.timestamp,
      streaming: m.streaming,
      toolEvents: m.toolEvents,
      progressLines: m.progressLines,
      reasoningBlocks: m.reasoningBlocks,
      agentState: m.agentState,
      customEvents: m.customEvents,
      todos: m.todos,
      // Carried so a run this browser watched is attributed to the agent that
      // produced it. The server owns human attribution — it stamps the
      // authenticated caller — so a human author sent here would be ignored.
      authorEmail: m.authorEmail,
      authorKind: m.authorKind,
      redacted: m.redacted,
      redactedCaps: m.redactedCaps,
    }));
    saveMessages(sessionId, toSave);
  }, [messages, sessionId]);

  const [input, setInput] = useState("");
  const [sendMode, setSendMode] = useState<SendMode>("send");
  const [showSendMenu, setShowSendMenu] = useState(false);
  // The send-queue is persisted per-session (localStorage) so a queued/steered
  // message survives a page refresh or an agent/session switch.  Initialise from
  // the persisted queue for this session; a session-change effect reloads it.
  const [queuedCount, setQueuedCount] = useState(() => getQueue(sessionId).length);
  const queueRef = useRef<string[]>(getQueue(sessionId));

  // A run is "active" whenever the agent is streaming, recovering after a page
  // reload, or the server run is still going.  The Stop control must appear in
  // ALL of these — gating only on isLoading hid Stop during recovery (isLoading
  // is false then), which is why the Stop button sometimes never showed up and
  // appeared to vanish when the input was cleared (it fell back to the idle
  // Send button, which greys out without text).
  const isRunActive =
    isLoading || recovering || (!!runStatus && runStatus !== "idle");

  // ── Live working message (rotates at the bottom of the chat) ──────────
  // Mirrors VS Code Copilot / Claude: the "thinking" indicator is always
  // visible at the bottom of the output, not buried inside the thinking
  // container where it disappears when the container is collapsed.
  const _WM = [
    "Working on it", "Thinking it through", "Processing",
    "Crunching the details", "Pulling the data", "Putting it together",
  ];
  const _FM = ["Bribing the hamster", "Reticulating splines", "Summoning Clippy"];
  const [liveWorkingMsg, setLiveWorkingMsg] = useState<string>(() =>
    Math.random() < 1 / 12 ? _FM[Math.floor(Math.random() * _FM.length)] : _WM[Math.floor(Math.random() * _WM.length)]
  );
  useEffect(() => {
    if (!isRunActive) return;
    const t = setInterval(() => {
      const pool = Math.random() < 1 / 12 ? _FM : _WM;
      setLiveWorkingMsg(pool[Math.floor(Math.random() * pool.length)]);
    }, 3000);
    return () => clearInterval(t);
  // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [isRunActive]);

  // Current running tool name — shown in the live indicator when a tool is in flight.
  const liveToolName = useMemo(() => {
    for (let i = messages.length - 1; i >= 0; i--) {
      const m = messages[i];
      if (m.role !== "assistant" || !m.streaming) break;
      const running = m.toolEvents?.find((t) => t.status === "running");
      if (running) return running.name.replace(/_/g, " ").replace(/\b\w/g, (c) => c.toUpperCase());
    }
    return null;
  }, [messages]);
  const prevLoadingRef = useRef(false);
  // Guards the queue-drain effect against re-entrancy: a second isLoading→false
  // edge (e.g. a poll clearing a stale flag right as a steer lands) must not
  // shift+send a second queued message while the first drained send is starting.
  const drainingRef = useRef(false);
  const bottomRef = useRef<HTMLDivElement>(null);
  const inputRef = useRef<HTMLTextAreaElement>(null);

  // Inject one-shot external text into the composer (e.g. the email Assistant's
  // "Fix" flow hands a correction prompt here for the user to review & send).
  /* eslint-disable react-hooks/set-state-in-effect */
  useEffect(() => {
    if (pendingInput && pendingInput.trim()) {
      setInput(pendingInput);
      inputRef.current?.focus();
      onPendingInputConsumed?.();
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [pendingInput]);
  /* eslint-enable react-hooks/set-state-in-effect */
  const [bannerDismissed, setBannerDismissed] = useState(false);
  const [statuses, setStatuses] = useState<IntegrationStatus[]>(externalStatuses ?? []);
  const [viewerEntry, setViewerEntry] = useState<FileEntry | null>(null);

  // ── Context-window tracking + auto-compaction ─────────────────────────────
  // Recompute when the count changes (new message added), when the total settled
  // content length changes (streaming completed and content grew), when the model
  // changes (different context limit), or when the system context changes.
  // Using settled length (non-streaming messages only) avoids thrashing on every
  // streaming delta while still updating promptly after each turn completes.
  // Context usage reflects only the ACTIVE window (from the last compaction
  // checkpoint forward) — the same set sent to the model — so the ring drops
  // after a compaction, exactly like Claude Code / Copilot CLI.
  const activeMessages = useMemo(() => activeContextSlice(messages), [messages]);
  const settledCount = activeMessages.filter((m) => !m.streaming).length;
  const settledContentLen = useMemo(
    () => activeMessages
      .filter((m) => !m.streaming)
      .reduce((s, m) => {
        let len = m.content?.length ?? 0;
        for (const t of m.toolEvents ?? []) {
          len += (t.result?.length ?? 0) + (t.args ? JSON.stringify(t.args).length : 0);
        }
        for (const b of m.reasoningBlocks ?? []) len += b.length;
        return s + len;
      }, 0),
    // eslint-disable-next-line react-hooks/exhaustive-deps
    [activeMessages.length, settledCount],
  );
  // Real per-model context window (dynamically loaded from the gateway via
  // /api/models/all).  Falls back to the static estimate when unknown.
  const currentModelContextWindow = selectedModel?.contextWindow;
  const contextUsage = useMemo(
    () => computeContextUsage(activeMessages, currentModel, systemContext, currentModelContextWindow),
    // eslint-disable-next-line react-hooks/exhaustive-deps
    [settledContentLen, activeMessages.length, currentModel, systemContext, currentModelContextWindow],
  );
  const [compacting, setCompacting] = useState(false);
  // Armed = allowed to auto-compact.  Disarmed after a compaction fires and
  // re-armed (via hysteresis) once usage falls back below the lower bound, so
  // a long conversation auto-compacts EACH time it fills up — not just once.
  const compactArmedRef = useRef(true);

  // Re-arm on session or model change so a switch (especially to a smaller
  // context window) can trigger its own auto-compaction immediately.
  useEffect(() => {
    compactArmedRef.current = true;
  }, [sessionId]);

  useEffect(() => {
    compactArmedRef.current = true;
  }, [currentModel]);

  // ── Compaction (Claude Code / Copilot CLI checkpoint model) ────────────────
  // Summarise the ACTIVE window (from the last checkpoint) into a new summary
  // checkpoint, INSERTED before the last few turns.  The full transcript stays
  // visible for scrollback; only [summary + recent turns] are sent to the model
  // and counted toward context usage.  Returns true if a checkpoint was added.
  const KEEP_LAST = 6;
  const applyCompaction = useCallback(async (): Promise<boolean> => {
    const forSession = sessionId;
    const active = activeContextSlice(messagesRef.current);
    if (active.length <= KEEP_LAST + 1) return false; // not enough to compact
    setCompacting(true);
    try {
      const res = await fetch(`/api/chat/sessions/${sessionId}/compact`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        // Summarise only the active window — bounds the summariser's input and
        // subsumes any earlier checkpoint it contains.
        body: JSON.stringify({ messages: active, keepLast: KEEP_LAST }),
      });
      const data = (await res.json()) as { messages?: ChatMessage[]; compacted?: boolean };
      if (forSession !== sessionIdRef.current) return false; // session switched
      if (!data.compacted || !Array.isArray(data.messages)) return false;
      const summaryMsg = data.messages.find((m) => isCompactionCheckpoint(m));
      if (!summaryMsg) return false;
      setMessages((prev) => {
        if (prev.some((m) => m.id === summaryMsg.id)) return prev;
        const keep = Math.min(KEEP_LAST, prev.length);
        // Insert the checkpoint before the kept tail: active window becomes
        // [summary + last KEEP_LAST turns]; everything older stays for scrollback.
        return [...prev.slice(0, prev.length - keep), summaryMsg, ...prev.slice(prev.length - keep)];
      });
      return true;
    } catch {
      return false;
    } finally {
      setCompacting(false);
    }
  }, [sessionId, setMessages]);

  // Auto-compact at 80% of the model's context window (GitHub Copilot CLI
  // parity; Claude Code uses ~83%).  75/80 hysteresis so it fires once per
  // fill-up and re-arms after a compaction drops usage.  Between turns only.
  // (A narrow 75/80 band — rather than 70/80 — re-arms even when a compaction
  // only partially reduced usage, so it can't wedge ≥80% and stop firing.)
  // Because contextUsage uses the model's REAL window, switching mid-chat to a
  // smaller-context model re-triggers compaction if the history now overflows.
  useEffect(() => {
    // GitHub Copilot SDK agents keep server-side session state and run their
    // OWN native compaction (Copilot CLI compacts ~80%); when resuming a
    // session they ignore the frontend-sent history entirely.  Running our
    // compaction there is redundant and would make the ring misleading, so we
    // defer to the SDK.  Our compaction is authoritative for MAF agents and the
    // orchestrator (stateless per request — they use the messages we send).
    if (agentRuntime === "github-copilot") return;
    if (contextUsage.pct < 75) compactArmedRef.current = true;
    if (contextUsage.pct < 80) return;
    if (isLoading || compacting) return;
    if (!compactArmedRef.current) return;
    compactArmedRef.current = false;
    void applyCompaction();
  // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [contextUsage.pct, isLoading, compacting, applyCompaction, agentRuntime]);

  /** Manual /compact trigger — user clicks the context ring when it's high. */
  const handleCompact = useCallback(() => {
    if (compacting || isLoading) return;
    compactArmedRef.current = false; // prevent auto-fire right after manual compact
    void applyCompaction();
  }, [compacting, isLoading, applyCompaction]);

  // ── HITL (Human-in-the-Loop) confirmation state ────────────────────────
  // When requestId is set the tool is blocking (the agent is parked on a
  // Future — e.g. confirm-before-send); Approve/Reject POST to
  // /api/agent/respond-input to resume the SAME stream.  Without requestId the
  // legacy non-blocking path sends APPROVE/REJECT as a new chat message.
  const [confirmation, setConfirmation] = useState<{
    id: string; title: string; detail?: string; context?: string;
    requestId?: string;
  } | null>(null);

  // ── HITL elicitation state (VS Code ask_questions parity) ────────────
  // When requestId is set the tool is blocking (MAF Tier 2 path — the
  // agent is parked on a Future).  The answer must be POSTed to
  // /api/agent/respond-input to resume the SAME stream, identical to the
  // native ask_user flow below.  When requestId is absent (Copilot SDK
  // path), the answer is sent as a new chat message.
  const [elicitation, setElicitation] = useState<{
    questions: ElicitationQuestion[];
    requestId?: string;
  } | null>(null);

  // ── Native ask_user state (Copilot SDK on_user_input_request) ────────
  // This prompt BLOCKS the agent run; the answer is POSTed to
  // /api/agent/respond-input to resume the SAME stream.
  const [userInput, setUserInput] = useState<{
    requestId: string;
    question: string;
    choices: string[];
    allowFreeform: boolean;
  } | null>(null);

  // Subscribe to agent events for HITL detection
  useAgentEvents({
    onCustomEvent: ({ name, value, threadId }) => {
      // The subscriber registry is global — ignore events from any session
      // other than the one this chat is showing, so a background run on a
      // different agent never injects its question card / HITL state here.
      if (threadId && threadId !== sessionId) return;
      if (name === "confirmation_requested" && value && typeof value === "object") {
        const v = value as Record<string, unknown>;
        const reqId = v.request_id ? String(v.request_id) : undefined;
        setConfirmation({
          // Static fallback id — only one confirmation card renders at a time
          // (state replace), and Date.now() here trips react-hooks/purity.
          id: String(v.id ?? v.request_id ?? "confirmation"),
          title: String(v.title ?? "Confirm action"),
          detail: v.detail ? String(v.detail) : undefined,
          context: v.context ? String(v.context) : undefined,
          requestId: reqId,
        });
      }
      if (name === "elicitation_requested" && value && typeof value === "object") {
        const v = value as Record<string, unknown>;
        const qs = v.questions;
        const reqId = v.request_id ? String(v.request_id) : undefined;
        if (Array.isArray(qs) && qs.length > 0) {
          setElicitation({ questions: qs as ElicitationQuestion[], requestId: reqId });
        }
      }
      if (name === "user_input_requested" && value && typeof value === "object") {
        const v = value as Record<string, unknown>;
        const requestId = String(v.request_id ?? "");
        const question = String(v.question ?? "");
        if (requestId && question) {
          setUserInput({
            requestId,
            question,
            choices: Array.isArray(v.choices)
              ? (v.choices as unknown[]).map(String)
              : [],
            allowFreeform: v.allowFreeform !== false,
          });
        }
      }
      // Immersive generative UI (surface:"panel") — auto-open in the side
      // panel as it arrives, Claude-artifacts style. The transcript keeps a
      // compact re-open chip (MessageBubble).
      if (name === "generative_ui" && value && typeof value === "object") {
        const v = value as Record<string, unknown>;
        if (v.surface === "panel") {
          // Stable id (no impure calls in the subscriber): request_id when
          // blocking, else the title — a same-titled re-emit replaces its
          // tab, and the transcript chip reopens any specific instance.
          const id = typeof v.request_id === "string" && v.request_id
            ? v.request_id
            : (typeof v.title === "string" && v.title ? v.title : "latest");
          openGenUI({
            id,
            title: typeof v.title === "string" ? v.title : undefined,
            sessionId,
            spec: value,
          });
        }
      }
      // Interaction from a panel-surface generative UI (SidePanelEditor emits
      // through the global bus — it has no direct line to this component's
      // send/HITL plumbing).
      if (name === "genui_panel_action" && value && typeof value === "object") {
        const v = value as Record<string, unknown>;
        const message = typeof v.message === "string" ? v.message : "";
        if (!message) return;
        const spec = (v.spec && typeof v.spec === "object"
          ? v.spec : {}) as Record<string, unknown>;
        const reqId = typeof spec.request_id === "string" ? spec.request_id : "";
        if (reqId) {
          postRespondInput(
            { request_id: reqId, answer: message, was_freeform: true },
            () => submitText(message),
          );
        } else {
          submitText(message);
        }
      }
    },
    onRunFinalized: ({ threadId }) => {
      if (threadId && threadId !== sessionId) return;
      // Clear any stale HITL state when a run completes — a finished
      // run can never be waiting on input anymore.  Only clear a BLOCKING
      // confirmation (requestId, parked on a Future): a non-blocking one must
      // persist until the user answers (its answer is a new chat message).
      setConfirmation((prev) => prev && prev.requestId ? null : prev);
      // Only clear elicitation when the agent was BLOCKED on a Future
      // (requestId present, MAF Tier 2 path).  For the Copilot SDK
      // non-blocking path (no requestId), the card must persist until
      // the user submits — clearing here makes it vanish mid-interaction.
      setElicitation((prev) => prev && prev.requestId ? null : prev);
      setUserInput((prev) => prev && prev.requestId ? null : prev);
    },
  });

  // Keep a live ref to messages so the unmount handler can save the latest.
  const messagesRef = useRef<ChatMessage[]>(messages);
  useEffect(() => {
    messagesRef.current = messages;
  }, [messages]);

  // Live ref to the current sessionId so async callbacks (e.g. /compact) can
  // bail if the user switched sessions while the request was in flight.
  const sessionIdRef = useRef(sessionId);
  useEffect(() => {
    sessionIdRef.current = sessionId;
  }, [sessionId]);

  // HITL cards are per-session component state — clear them on a session
  // switch so a question raised in session A never renders in (or gets its
  // answer routed from) session B.  The event subscriber above already
  // filters incoming events by threadId; this handles the card that was
  // ALREADY showing when the user switched.  Reset-during-render (not an
  // effect) — React's "adjusting state when a prop changes" pattern.
  const [hitlSession, setHitlSession] = useState(sessionId);
  if (hitlSession !== sessionId) {
    setHitlSession(sessionId);
    setConfirmation(null);
    setElicitation(null);
    setUserInput(null);
  }

  // POST a blocking-HITL answer to /api/agent/respond-input.  On failure the
  // card is RESTORED so the user can retry — the agent is still parked on its
  // Future server-side, and the old fire-and-forget `.catch(() => {})` left a
  // blocked run with no card and no error (a dead conversation).
  const postRespondInput = useCallback(
    (
      payload: { request_id: string; answer: string; was_freeform: boolean },
      restoreCard: () => void,
    ) => {
      const forSession = sessionIdRef.current;
      void fetch("/api/agent/respond-input", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        // thread_id lets the gateway route a HITL answer to whichever worker
        // owns the parked run (P1-2 cross-worker control bus).
        body: JSON.stringify({ ...payload, thread_id: forSession }),
      })
        .then(async (r) => {
          if (!r.ok) {
            throw new Error(await r.text().catch(() => `status ${r.status}`));
          }
        })
        .catch((err: unknown) => {
          console.error("respond-input failed — restoring HITL card", err);
          // Only restore if the user is still on the session that asked.
          if (sessionIdRef.current === forSession) restoreCard();
        });
    },
    [],
  );

  // Reload the persisted send-queue when switching sessions/agents so a message
  // queued on another session doesn't leak in and a queue left on THIS session
  // is restored.  queueRef + queuedCount are the live mirror of the stored queue.
  useEffect(() => {
    const q = getQueue(sessionId);
    queueRef.current = q;
    setQueuedCount(q.length);
  }, [sessionId]);

  // Save messages on beforeunload so partial streams survive browser close.
  useEffect(() => {
    const handleUnload = () => {
      const msgs = messagesRef.current;
      const settled = msgs.filter(
        (m) =>
          (!m.streaming || m.content.trim().length > 0) &&
          !(m.role === "system" && m.content.startsWith("__ERROR__")),
      );
      if (settled.length === 0) return;
      const toSave: PersistedMessage[] = settled.map((m) => ({
        id: m.id,
        role: m.role,
        content: m.content,
        timestamp: m.timestamp,
        toolEvents: m.toolEvents,
        progressLines: m.progressLines,
        reasoningBlocks: m.reasoningBlocks,
        agentState: m.agentState,
        customEvents: m.customEvents,
        todos: m.todos,
      }));
      // Use sendBeacon for reliable delivery during page unload
      const payload = toSave.map((m) => ({
        id: m.id,
        role: m.role,
        content: m.content,
        timestamp: m.timestamp,
        tool_events: m.toolEvents ?? [],
        progress_lines: m.progressLines ?? [],
        reasoning: serializeReasoning(m.reasoningBlocks),
        agent_state: m.agentState ?? null,
        custom_events: m.customEvents ?? [],
        todos: m.todos ?? [],
        ...(m.authorKind === "agent"
          ? { author_kind: "agent", author_email: m.authorEmail }
          : {}),
      }));
      try {
        navigator.sendBeacon(
          `/api/chat/sessions/${sessionId}/messages`,
          JSON.stringify(payload)
        );
      } catch { /* best-effort */ }
      // Also save to localStorage synchronously
      try {
        localStorage.setItem(`cc-msgs-${sessionId}`, JSON.stringify(toSave));
      } catch { /* quota exceeded */ }
    };
    window.addEventListener("beforeunload", handleUnload);
    window.addEventListener("pagehide", handleUnload);
    return () => {
      window.removeEventListener("beforeunload", handleUnload);
      window.removeEventListener("pagehide", handleUnload);
    };
  }, [sessionId]);

  // Report activity to the parent for session-list enrichment (title + preview).
  const onActivityRef = useRef(onActivity);
  useEffect(() => {
    onActivityRef.current = onActivity;
  }, [onActivity]);
  useEffect(() => {
    if (!onActivityRef.current) return;
    // Only enrich on SETTLED turns. This effect runs on every streamed token;
    // firing onActivity (which PATCHes the session row in Postgres) per token
    // spammed dozens of writes per turn. Skip while the last message is still
    // streaming — the title/preview only need to reflect settled content.
    if (messages[messages.length - 1]?.streaming) return;
    const firstUser = messages.find((m) => m.role === "user" && m.content.trim());
    const lastAssistant = [...messages]
      .reverse()
      .find((m) => m.role === "assistant" && m.content.trim());
    const counted = messages.filter(
      (m) => (m.role === "user" || m.role === "assistant") && m.content.trim(),
    ).length;
    if (counted === 0) return;
    onActivityRef.current({
      firstUserMessage: firstUser?.content,
      lastPreview: lastAssistant?.content,
      messageCount: counted,
    });
  }, [messages]);

  // Drain the queue when a generation finishes (Queue / Steer send modes).
  useEffect(() => {
    if (prevLoadingRef.current && !isLoading && queueRef.current.length > 0 && !drainingRef.current) {
      drainingRef.current = true;
      const next = queueRef.current.shift();
      setQueuedCount(queueRef.current.length);
      saveQueue(sessionIdRef.current, queueRef.current);
      // Clear the guard only once this send settles, so a second isLoading edge
      // mid-send can't drain another item; sequential drains still work because
      // the next edge fires after this promise (and the guard) resolves.
      if (next) void sendMessage(next).finally(() => { drainingRef.current = false; });
      else drainingRef.current = false;
    }
    prevLoadingRef.current = isLoading;
  }, [isLoading, sendMessage]);

  // Persist the conversation to Mem0 on unmount (default / Metorite agent).
  useEffect(() => {
    return () => {
      if (!memoryUserId) return;
      const payload = messagesRef.current
        .filter((m) => (m.role === "user" || m.role === "assistant") && m.content.trim())
        .map((m) => ({ role: m.role as "user" | "assistant", content: m.content }));
      if (payload.length === 0) return;
      fetch("/api/chat/memories", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        // No userId: the scope is the signed-in member, resolved server-side.
        // Sending one here would be a claim the route no longer accepts.
        body: JSON.stringify({ messages: payload }),
        keepalive: true,
      }).catch(() => {});
    };
  }, [memoryUserId]);

  // Fetch integration statuses if not passed from parent
  useEffect(() => {
    if (externalStatuses) { setStatuses(externalStatuses); return; }
    fetch(`/api/integrations/status?agent=${encodeURIComponent(currentAgentName)}`)
      .then((r) => r.json())
      .then((data: unknown) => { if (Array.isArray(data)) setStatuses(data as IntegrationStatus[]); })
      .catch(() => {});
  }, [currentAgentName, externalStatuses]);

  // Fetch available agents for the switcher if not provided by parent
  useEffect(() => {
    if (externalAgents) { setAgents(externalAgents); return; }
    fetch("/api/agent/list")
      .then((r) => r.json())
      .then((data: unknown) => { if (Array.isArray(data)) setAgents(data as AgentEntry[]); })
      .catch(() => {});
  }, [externalAgents]);

  // Refresh statuses once a NEW assistant message has SETTLED (credentials may
  // have just been saved).  Gate on the message id + agent so we DON'T refetch
  // on every streaming delta — `messages` changes on every token, and the old
  // unguarded version fired this request dozens of times per response.
  const lastStatusKeyRef = useRef<string>("");
  useEffect(() => {
    const last = messages[messages.length - 1];
    if (last?.role !== "assistant" || last.streaming) return;
    const key = `${last.id}:${currentAgentName}`;
    if (lastStatusKeyRef.current === key) return;
    lastStatusKeyRef.current = key;
    fetch(`/api/integrations/status?agent=${encodeURIComponent(currentAgentName)}`)
      .then((r) => r.json())
      .then((data: unknown) => { if (Array.isArray(data)) setStatuses(data as IntegrationStatus[]); })
      .catch(() => {});
  }, [messages, currentAgentName]);

  const missingMandatory = statuses.filter((s) => s.mandatory && !s.configured);

  // ── Smart follow-up suggestions (LLM-generated, contextual) ───────
  const [smartSuggestions, setSmartSuggestions] = useState<string[]>([]);
  const lastSuggestedForRef = useRef<string>("");
  useEffect(() => {
    if (isLoading) return;
    const last = messages[messages.length - 1];
    if (last?.role !== "assistant" || !last.content.trim() || last.streaming) return;
    if (lastSuggestedForRef.current === last.id) return; // already fetched
    lastSuggestedForRef.current = last.id;
    setSmartSuggestions([]); // clear stale suggestions while fetching
    const lastUser = [...messages].reverse().find((m) => m.role === "user");
    const ctrl = new AbortController();
    fetch("/api/chat/suggestions", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        userMessage: lastUser?.content ?? "",
        assistantMessage: last.content.slice(0, 4000),
        agentName: currentAgentName,
      }),
      signal: ctrl.signal,
    })
      .then((r) => r.json())
      .then((d: { suggestions?: string[] }) => {
        if (Array.isArray(d.suggestions) && d.suggestions.length > 0) {
          setSmartSuggestions(d.suggestions);
        }
      })
      .catch(() => {});
    return () => ctrl.abort();
  }, [messages, isLoading, currentAgentName]);

  // ── Smart auto-scroll + scroll-to-bottom button ─────────────────────
  const threadRef = useRef<HTMLDivElement>(null);
  const [showScrollBtn, setShowScrollBtn] = useState(false);
  const isNearBottomRef = useRef(true);

  // Track whether user has scrolled away from the bottom
  useEffect(() => {
    const el = threadRef.current;
    if (!el) return;
    const onScroll = () => {
      const dist = el.scrollHeight - el.scrollTop - el.clientHeight;
      const nearBottom = dist < 80;
      isNearBottomRef.current = nearBottom;
      setShowScrollBtn(!nearBottom && el.scrollHeight > el.clientHeight + 200);
      // Near the top → lazy-load the previous page of history.
      if (el.scrollTop < 140) loadOlderHistoryRef.current();
    };
    el.addEventListener("scroll", onScroll, { passive: true });
    return () => el.removeEventListener("scroll", onScroll);
  }, []);

  // Auto-scroll only when the user is near the bottom.  Use "auto" (instant),
  // NOT "smooth": this fires on every streamed token, and queuing a smooth-
  // scroll animation per token causes visible stutter during long responses.
  useEffect(() => {
    if (isNearBottomRef.current) {
      bottomRef.current?.scrollIntoView({ behavior: "auto" });
    }
  }, [messages]);

  // When the USER sends a new message, snap to the bottom even if they had
  // scrolled up to read history — otherwise sending looks like it did nothing.
  const userMessageCount = useMemo(
    () => messages.reduce((n, m) => (m.role === "user" ? n + 1 : n), 0),
    [messages],
  );
  useEffect(() => {
    if (userMessageCount === 0) return;
    isNearBottomRef.current = true;
    bottomRef.current?.scrollIntoView({ behavior: "auto" });
  }, [userMessageCount]);

  const scrollToBottom = useCallback(() => {
    bottomRef.current?.scrollIntoView({ behavior: "smooth" });
  }, []);

  // ── Reveal older history (scroll-up) — expand the render window ────────────
  // The full conversation is already in memory; this just renders more of it.
  const loadOlderHistory = useCallback(() => {
    if (renderLimit >= messages.length) return;
    const el = threadRef.current;
    pendingPrependRef.current = el ? el.scrollHeight : null; // anchor viewport
    setRenderLimit((r) => Math.min(r + HISTORY_PAGE_SIZE, messages.length));
  }, [renderLimit, messages.length]);

  // Keep the scroll-handler-callable ref pointing at the latest closure.
  useEffect(() => {
    loadOlderHistoryRef.current = loadOlderHistory;
  }, [loadOlderHistory]);

  // After revealing older messages, keep the viewport anchored where the user
  // was reading (offset by the height that was just added at the top).
  React.useLayoutEffect(() => {
    if (pendingPrependRef.current != null) {
      const el = threadRef.current;
      if (el) el.scrollTop += el.scrollHeight - pendingPrependRef.current;
      pendingPrependRef.current = null;
    }
  }, [renderLimit, messages]);

  // The slice of messages actually rendered (most recent `renderLimit`).
  const visibleMessages = useMemo(
    () => (messages.length > renderLimit ? messages.slice(-renderLimit) : messages),
    [messages, renderLimit],
  );

  const enqueue = useCallback((text: string, front = false) => {
    if (front) queueRef.current.unshift(text);
    else queueRef.current.push(text);
    setQueuedCount(queueRef.current.length);
    saveQueue(sessionIdRef.current, queueRef.current);
  }, []);

  /** Submit honouring the active send mode (Send / Queue / Steer). */
  const submitText = useCallback(
    (text: string) => {
      const trimmed = text.trim();
      // Guard ALL senders (suggestion pills, MCQ choices, HITL card submits,
      // the email "Fix" flow) — not just the textarea form — against sending
      // while history is still hydrating from Postgres, which would clobber the
      // about-to-be-replaced messages array.
      if (!trimmed || loadingHistory) return;
      if (!isLoading) {
        void sendMessage(trimmed);
        return;
      }
      // A generation is in flight — branch on send mode.
      if (sendMode === "steer") {
        // Drop the incomplete assistant message so history is clean when the
        // steer text is sent — mirrors VS Code Copilot Chat behaviour.
        setMessages((prev) => {
          const last = prev[prev.length - 1];
          return last?.streaming ? prev.slice(0, -1) : prev;
        });
        stopGeneration();
        enqueue(trimmed, true); // jump the queue, sent as soon as the stream stops
      } else {
        enqueue(trimmed); // queue (also the fallback for "send" while busy)
      }
    },
    [isLoading, loadingHistory, sendMode, sendMessage, stopGeneration, enqueue, setMessages]
  );

  /** Explicit Stop — also clears the queue so steered/queued messages don't
   *  auto-send when the run halts (the drain effect fires on isLoading→false). */
  const handleStop = useCallback(() => {
    queueRef.current = [];
    setQueuedCount(0);
    saveQueue(sessionIdRef.current, queueRef.current);
    stopGeneration();
  }, [stopGeneration]);

  const handleSubmit = async (e: React.FormEvent) => {
    e.preventDefault();
    if (!input.trim() || loadingHistory) return;
    submitText(input);
    setInput("");
    inputRef.current?.focus();
  };

  // Collapse the auto-grown textarea back to one line whenever the input is
  // cleared. The auto-grow sets the height IMPERATIVELY on the `onInput` event
  // (see the textarea below); clearing `input` via React state does NOT fire
  // `onInput`, so without this the empty composer stays as tall as the
  // just-sent multi-line message — it looks like a giant blank box while the
  // agent is processing. Keyed on `input === ""` so it never fights the
  // grow-on-type path.
  useEffect(() => {
    if (input === "" && inputRef.current) {
      inputRef.current.style.height = "auto";
    }
  }, [input]);

  const handleKeyDown = (e: React.KeyboardEvent<HTMLTextAreaElement>) => {
    // Enter inserts a newline (the textarea default) — sending is the Send
    // button's job, so a mid-thought Enter never fires the prompt early.
    // Ctrl/Cmd+Enter stays as the deliberate keyboard send.
    if (e.key === "Enter" && (e.ctrlKey || e.metaKey)) {
      e.preventDefault();
      handleSubmit(e as unknown as React.FormEvent);
    }
  };

  /** Send an MCQ choice the user clicked. */
  const handleChoice = useCallback(
    (choice: string) => {
      submitText(choice);
    },
    [submitText]
  );

  /** Resolve a BLOCKING generative-UI interaction (emit_generative_ui with
   *  hitl:true — the run is parked on a server Future keyed by request_id).
   *  Resumes the SAME stream via /api/agent/respond-input; on failure falls
   *  back to sending the answer as a normal message so it's never lost. */
  const handleGenUiHitl = useCallback(
    (requestId: string, answer: string) => {
      postRespondInput(
        { request_id: requestId, answer, was_freeform: true },
        () => submitText(answer),
      );
    },
    [postRespondInput, submitText]
  );

  // Stable callbacks for MessageBubble so React.memo can skip re-rendering
  // unchanged messages (per-message closures used to defeat the memoization —
  // every message re-ran ReactMarkdown on every streamed token).
  const handleFileOpen = useCallback((entry: FileEntry) => setViewerEntry(entry), []);
  const handleResend = useCallback((content: string) => { submitText(content); }, [submitText]);
  const handleRetryMessage = useCallback((m: ChatMessage) => {
    const all = messagesRef.current;
    const idx = all.findIndex((x) => x.id === m.id);
    if (idx < 0) return;
    const prevUser = [...all.slice(0, idx)].reverse().find((x) => x.role === "user");
    if (!prevUser) return;
    // Regenerate: drop this assistant turn AND its prompt, then re-send — no
    // duplicate user+assistant pair on top of the old (rejected) answer.
    setMessages((prev) => prev.filter((x) => x.id !== m.id && x.id !== prevUser.id));
    submitText(prevUser.content);
  }, [submitText, setMessages]);

  /** Ask the agent to help configure a specific integration. */
  const handleAskAgentConfigure = (svc: IntegrationStatus) => {
    setBannerDismissed(true);
    sendMessage(
      `I need to configure the ${svc.label ?? svc.service} integration. ` +
      `Please guide me through setting it up.`
    );
  };

  /** Switch the active agent mid-chat. History is retained and passed as context. */
  const handleSwitchAgent = useCallback((entry: AgentEntry) => {
    setCurrentAgentName(entry.name);
    setCurrentModel(getLastModel(entry.name) ?? "auto");
    setShowAgentMenu(false);
  }, []);

  const currentModelLabel =
    sortedModels.find((m) => m.id === currentModel)?.label ?? currentModel;
  const modelGroups = Array.from(new Set(sortedModels.map((m) => m.group)));

  // GitHub Copilot SDK agents support BYOK (Bring Your Own Key): when a
  // LiteLLM model (e.g. openrouter/deepseek/deepseek-v4-pro) is selected,
  // the executor injects a provider block so the SDK routes through the local
  // gateway /v1 (litellm SDK) instead of api.githubcopilot.com.  All models are available.
  const isCopilotSdkAgent = agentRuntime === "github-copilot";
  // True when a Copilot SDK agent is running via BYOK (gateway /v1).
  const isByokActive = isCopilotSdkAgent && currentRuntime === "litellm";

  // Model picker state
  const [showModelMenu, setShowModelMenu] = useState(false);
  const [showThinkMenu, setShowThinkMenu] = useState(false);
  const modelMenuRef = useRef<HTMLDivElement>(null);
  const sendMenuRef = useRef<HTMLDivElement>(null);

  // Close model menu on outside click
  useEffect(() => {
    if (!showModelMenu) return;
    const handleOutside = (e: MouseEvent) => {
      if (modelMenuRef.current && !modelMenuRef.current.contains(e.target as Node)) {
        setShowModelMenu(false);
      }
    };
    document.addEventListener("mousedown", handleOutside);
    return () => document.removeEventListener("mousedown", handleOutside);
  }, [showModelMenu]);

  // Close send-mode menu on outside click (works on touch devices too)
  useEffect(() => {
    if (!showSendMenu) return;
    const handleOutside = (e: MouseEvent) => {
      if (sendMenuRef.current && !sendMenuRef.current.contains(e.target as Node)) {
        setShowSendMenu(false);
      }
    };
    document.addEventListener("mousedown", handleOutside);
    return () => document.removeEventListener("mousedown", handleOutside);
  }, [showSendMenu]);

  /** Display labels + styling for an agent_runtime value. */
  function agentRuntimeMeta(rt: string): { label: string; title: string; cls: string }[] {
    if (rt === "github-copilot") {
      return [
        {
          label: "Copilot SDK",
          title: "GitHub Copilot SDK — native shell, file r/w, MCP servers, BYOK provider support",
          cls: "border-sky-700/50 bg-sky-900/30 text-sky-300",
        },
      ];
    }
    if (rt === "langgraph") {
      return [{
        label: "LangGraph",
        title: "Legacy LangGraph agent runner",
        cls: "border-violet-700/50 bg-violet-900/30 text-violet-300",
      }];
    }
    return [{
      label: "MAF",
      title: "Microsoft Agent Framework agent",
      cls: "border-amber-700/50 bg-amber-900/30 text-amber-300",
    }];
  }

  const THINK_MODES: { mode: ThinkMode; label: string; title: string }[] = [
    { mode: "auto", label: "Auto", title: "Let the model decide" },
    { mode: "thinking", label: "Thinking", title: "Enable chain-of-thought reasoning" },
    { mode: "max", label: "Max", title: "Maximum effort / deeper reasoning" },
  ];

  // HITL cards (confirmation / elicitation / ask_user) — rendered INLINE,
  // anchored to the assistant turn that raised the prompt (VS Code / AG-UI
  // reference behaviour), not detached at the bottom of the message list. The
  // agent is parked mid-turn, so the card belongs with that turn's bubble.
  // Returns null when nothing is pending.
  const renderHitlCards = (): React.ReactNode => {
    if (!confirmation && !elicitation && !userInput) return null;
    return (
      <>
        {confirmation && (
          <ConfirmationCard title={confirmation.title} detail={confirmation.detail} context={confirmation.context}
            onApprove={() => {
              const card = confirmation;
              const reqId = card.requestId;
              setConfirmation(null);
              if (reqId) {
                postRespondInput(
                  { request_id: reqId, answer: "APPROVE", was_freeform: false },
                  () => setConfirmation(card),
                );
              } else {
                submitText(`APPROVE: ${card.id}`);
              }
            }}
            onReject={() => {
              const card = confirmation;
              const reqId = card.requestId;
              setConfirmation(null);
              if (reqId) {
                postRespondInput(
                  { request_id: reqId, answer: "REJECT", was_freeform: false },
                  () => setConfirmation(card),
                );
              } else {
                submitText(`REJECT: ${card.id}`);
              }
            }} />
        )}

        {elicitation && (
          <ElicitationCard
            questions={elicitation.questions}
            onSubmit={(answers: ElicitationAnswers) => {
              if (elicitation.requestId) {
                const formatted = Object.entries(answers)
                  .map(([header, ans]) => {
                    const parts: string[] = [`[${header}]`];
                    if (ans.selected && ans.selected.length > 0) {
                      parts.push(`Selected: ${ans.selected.join(", ")}`);
                    }
                    if (ans.freeform) {
                      parts.push(`Answer: ${ans.freeform}`);
                    }
                    return parts.join("\n");
                  })
                  .join("\n\n");
                const single = elicitation.questions.length === 1;
                const firstHeader = elicitation.questions[0]?.header ?? "Question";
                const a = answers[firstHeader] ?? {};
                const freeform = a.freeform?.trim();
                const selectedJoined = (a.selected ?? []).join(", ");
                const answer = single
                  ? (freeform || selectedJoined || formatted)
                  : formatted;
                const wasFreeform = single && !!freeform && !selectedJoined;
                const card = elicitation;
                const reqId = card.requestId!;
                setElicitation(null);
                postRespondInput(
                  { request_id: reqId, answer, was_freeform: wasFreeform },
                  () => setElicitation(card),
                );
              } else {
                const formatted = Object.entries(answers)
                  .map(([header, ans]) => {
                    const parts: string[] = [`[${header}]`];
                    if (ans.selected && ans.selected.length > 0) {
                      parts.push(`Selected: ${ans.selected.join(", ")}`);
                    }
                    if (ans.freeform) {
                      parts.push(`Answer: ${ans.freeform}`);
                    }
                    return parts.join("\n");
                  })
                  .join("\n\n");
                submitText(formatted);
                setElicitation(null);
              }
            }}
          />
        )}

        {userInput && (
          <ElicitationCard
            questions={[{
              header: "Question",
              question: userInput.question,
              multiSelect: false,
              allowFreeformInput: userInput.allowFreeform,
              options: userInput.choices.length > 0
                ? userInput.choices.map((c) => ({ label: c }))
                : null,
            }]}
            onSubmit={(answers: ElicitationAnswers) => {
              const a = answers["Question"] ?? {};
              const selected = a.selected?.[0];
              const freeform = a.freeform?.trim();
              const answer = freeform || selected || "";
              const wasFreeform = !!freeform && !selected;
              const card = userInput;
              setUserInput(null);
              postRespondInput(
                { request_id: card.requestId, answer, was_freeform: wasFreeform },
                () => setUserInput(card),
              );
            }}
          />
        )}
      </>
    );
  };

  // The assistant turn the HITL card anchors to = the last assistant message
  // (the parked run streams into it). Used to render the card inline there.
  const hitlAnchorId = (() => {
    if (!confirmation && !elicitation && !userInput) return null;
    for (let i = messages.length - 1; i >= 0; i--) {
      if (messages[i].role === "assistant") return messages[i].id;
    }
    return null;
  })();

  // ── The room layer ────────────────────────────────────────────────────
  // A conversation with nobody else in it costs one request and renders
  // nothing: `shared` is false, so no rail, no banner, no stream, no
  // heartbeat. Everything below keys off `shared` rather than off the room
  // object, so solo chat is exactly the surface it has always been.
  const viewerEmail = memoryUserId ?? "";
  const { room, shared: isRoom, presence, timeline, refresh: refreshRoom } =
    useRoom(sessionId, viewerEmail, { enabled: !compact });
  const [railOpen, setRailOpen] = useState(false);
  const roomPeople = useMemo(() => (room ? peopleOf(room) : []), [room]);
  // A watcher reads the room and cannot drive its agents. The composer says so
  // instead of failing the send with a 403 after they have typed a paragraph.
  const canSend = room ? room.you.canSend : true;

  return (
    <div className="flex h-full bg-background">
    <div className="flex flex-col h-full flex-1 min-w-0 bg-background">
      {/* Agent header — VS Code-style minimal bar (hidden in compact embeds,
          where the host provides its own toolbar). */}
      {!compact && (
      <div className="flex items-center gap-2 h-9 px-4 border-b border-border bg-card/40 shrink-0">
        <div className={`w-2 h-2 rounded-full shrink-0 ${isLoading ? "bg-warning animate-pulse" : "bg-success"}`} />
        <AgentAvatar libraryId={agentAvatars[currentAgentName]} size={20} fallback={null} />
        <span className="text-xs font-medium text-foreground truncate">{currentAgentLabel}</span>
        {isLoading && (
          <span className="hidden sm:inline text-[10px] text-warning/70 animate-pulse">thinking…</span>
        )}
        {agentRuntime === "github-copilot" && currentAgentEntry?.repo_url && (
          <a href={currentAgentEntry.repo_url} target="_blank" rel="noopener noreferrer"
            className="shrink-0 text-muted-foreground/40 hover:text-muted-foreground tech-transition ml-auto"
            title={`Source: ${currentAgentEntry.repo_name ?? currentAgentEntry.repo_url}`}>
            <svg width="13" height="13" viewBox="0 0 16 16" fill="currentColor">
              <path d="M8 0C3.58 0 0 3.58 0 8c0 3.54 2.29 6.53 5.47 7.59.4.07.55-.17.55-.38 0-.19-.01-.82-.01-1.49-2.01.37-2.53-.49-2.69-.94-.09-.23-.48-.94-.82-1.13-.28-.15-.68-.52-.01-.53.63-.01 1.08.58 1.23.82.72 1.21 1.87.87 2.33.66.07-.52.28-.87.51-1.07-1.78-.2-3.64-.89-3.64-3.95 0-.87.31-1.59.82-2.15-.08-.2-.36-1.02.08-2.12 0 0 .67-.21 2.2.82.64-.18 1.32-.27 2-.27.68 0 1.36.09 2 .27 1.53-1.04 2.2-.82 2.2-.82.44 1.1.16 1.92.08 2.12.51.56.82 1.27.82 2.15 0 3.07-1.87 3.75-3.65 3.95.29.25.54.73.54 1.48 0 1.07-.01 1.93-.01 2.2 0 .21.15.46.55.38A8.013 8.013 0 0016 8c0-4.42-3.58-8-8-8z" />
            </svg>
          </a>
        )}
        {isRoom && (
          <Button variant="ghost" size="none" radius="keep" layout="" onClick={() => setRailOpen((v) => !v)} className="ml-auto shrink-0 rounded-md px-1.5 py-0.5 text-[11px] lg:hidden">
            {railOpen ? "Hide room" : "Room"}
          </Button>
        )}
      </div>
      )}

      {/* Who is in this conversation, and what it costs the agents. Renders a
          lone Share button while you are alone — see RoomHeader. */}
      {!compact && (
        <div className="shrink-0 border-b border-border bg-card/20 px-4 py-2">
          <RoomHeader room={room} presence={presence} onChanged={refreshRoom} />
        </div>
      )}

      {/* Missing integrations banner (compact) */}
      {!bannerDismissed && missingMandatory.length > 0 && (
        <div className="shrink-0 border-b border-warning/20 bg-warning/5 px-4 py-2 flex items-center gap-2 text-[11px]">
          <span className="text-warning">⚡</span>
          <span className="text-warning/80">{missingMandatory.length} integration{missingMandatory.length > 1 ? "s" : ""} not configured</span>
          <button onClick={() => setBannerDismissed(true)} className="ml-auto w-5 h-5 flex items-center justify-center rounded text-muted-foreground hover:text-foreground hover:bg-secondary tech-transition">✕</button>
        </div>
      )}

      {/* Message thread */}
      <div ref={threadRef} className="flex-1 overflow-y-auto px-3 sm:px-4 py-4 relative scrollbar-thin">
        {/* Scroll-to-bottom floating button */}
        {showScrollBtn && (
          <button onClick={scrollToBottom}
            className="sticky bottom-3 left-1/2 -translate-x-1/2 z-10 w-8 h-8 rounded-full bg-card border border-border text-foreground shadow-lg flex items-center justify-center hover:bg-secondary hover:text-foreground tech-transition"
            aria-label="Scroll to bottom">
            <svg width="12" height="12" viewBox="0 0 16 16" fill="none" stroke="currentColor" strokeWidth="2">
              <path d="M4 6l4 4 4-4" />
            </svg>
          </button>
        )}

        <div className="max-w-3xl mx-auto space-y-5">
          {/* Stream recovery / agent status indicator */}
          {recovering || runStatus === "running" ? (
            <div role="status" aria-live="polite" className="rounded-lg border border-primary/30 bg-primary/5 px-3 py-2 text-[12px] text-primary/90 flex items-center gap-2">
              <span className={[
                "w-2.5 h-2.5 rounded-full shrink-0",
                runStatus === "running" ? "bg-green-500 animate-pulse" : "bg-primary animate-pulse",
              ].join(" ")} />
              <span className="font-medium">
                {runStatus === "running" ? "Agent running…" : "Reconnecting…"}
              </span>
              <span className="text-primary/60">
                {runStatus === "running"
                  ? "The agent is still working. Content will appear as it's produced."
                  : runStatus === "recovering"
                    ? "Recovering your stream from the server"
                    : "Checking agent status…"}
              </span>
            </div>
          ) : !isLoading && messages.length > 0 && (() => {
            const last = messages[messages.length - 1];
            const wasInterrupted = last?.role === "assistant" && last.content && !/[.?!]\s*$/.test(last.content.trim());
            return wasInterrupted ? (
              <div className="rounded-lg border border-warning/20 bg-warning/5 px-3 py-2 text-[11px] text-warning/80">
                ⚡ Stream was interrupted. Messages are saved — you can continue chatting below.
              </div>
            ) : null;
          })()}
          {loadingHistory && messages.length === 0 && (
            <div className="flex flex-col items-center justify-center min-h-[50vh] gap-3 text-center">
              <span className="w-6 h-6 rounded-full border-2 border-muted border-t-primary animate-spin" />
              <div className="text-foreground text-sm font-medium">Loading conversation…</div>
              <div className="text-muted-foreground text-xs">Fetching your history from the server</div>
              <div className="w-full max-w-3xl mx-auto space-y-3 pt-6" aria-hidden>
                <div className="h-12 w-2/3 rounded-2xl bg-muted/40 animate-pulse" />
                <div className="h-16 w-3/4 rounded-2xl bg-muted/30 animate-pulse ml-auto" />
                <div className="h-12 w-1/2 rounded-2xl bg-muted/40 animate-pulse" />
              </div>
            </div>
          )}
          {/* Subtle syncing banner — messages are visible (from localStorage) but
              the richer DB versions are still loading in the background. */}
          {loadingHistory && messages.length > 0 && (
            <div className="flex items-center justify-center gap-2 py-2 px-4 mx-auto mb-1 rounded-lg border border-border/50 bg-card/60 text-[11px] text-muted-foreground animate-fade-in">
              <span className="w-3 h-3 rounded-full border-2 border-muted border-t-primary animate-spin" />
              <span>Syncing your history…</span>
            </div>
          )}
          {!loadingHistory && messages.length === 0 && (
            <div className="flex flex-col items-center justify-center min-h-[50vh] gap-3 text-center">
              <AgentAvatar
                libraryId={agentAvatars[currentAgentName]}
                size={48}
                className="tech-glow"
                fallback={
                  <div className="w-12 h-12 rounded-2xl bg-primary/10 border border-primary/20 flex items-center justify-center tech-glow">
                    <div className="w-2.5 h-2.5 rounded-full bg-success" />
                  </div>
                }
              />
              <div className="text-muted-foreground text-sm font-medium">
                Chat with <span className="text-foreground">{currentAgentLabel}</span>
              </div>
              {agentDescription && currentAgentName === agentName && (
                <div className="text-muted-foreground/60 text-xs max-w-sm leading-relaxed">{agentDescription}</div>
              )}
              <SuggestionPills
                suggestions={AGENT_SUGGESTIONS[currentAgentName] ?? DEFAULT_SUGGESTIONS}
                onPick={handleChoice}
              />
            </div>
          )}

          {/* Older-history affordance — auto-reveals on scroll-up; also clickable.
              Messages are already in memory; this just renders more of them. */}
          {hasMoreHistory && visibleMessages.length > 0 && (
            <div className="flex items-center justify-center py-2">
              <button
                type="button"
                onClick={() => loadOlderHistory()}
                className="text-[11px] text-muted-foreground hover:text-foreground px-3 py-1 rounded-full border border-border/60 hover:border-primary/40 tech-transition"
              >
                ↑ Show earlier messages ({messages.length - renderLimit} more)
              </button>
            </div>
          )}

          {visibleMessages.map((msg, i) => {
            const prevMsg = i > 0 ? visibleMessages[i - 1] : null;
            // Find the preceding user message for retry (walk back to the
            // most recent user message before this assistant message).
            const prevUserMsg =
              msg.role === "assistant"
                ? [...visibleMessages.slice(0, i)].reverse().find((m) => m.role === "user")
                : null;
            const showDateDivider = prevMsg &&
              new Date(msg.timestamp).toDateString() !== new Date(prevMsg.timestamp).toDateString();
            return (
              <div key={msg.id} className="animate-fade-in">
                {showDateDivider && (
                  <div className="flex items-center gap-3 my-4">
                    <div className="flex-1 h-px bg-border" />
                    <span className="text-[10px] text-muted-foreground/40 font-medium shrink-0">
                      {new Date(msg.timestamp).toLocaleDateString(undefined, { weekday: "short", month: "short", day: "numeric" })}
                    </span>
                    <div className="flex-1 h-px bg-secondary" />
                  </div>
                )}
                <MessageBubble message={msg} sessionId={sessionId} onChoice={handleChoice}
                  onHitlRespond={handleGenUiHitl}
                  emailContext={emailContext}
                  onFileOpen={handleFileOpen}
                  onResend={handleResend}
                  viewerEmail={viewerEmail}
                  participants={isRoom ? roomPeople : undefined}
                  onRetryMessage={prevUserMsg ? handleRetryMessage : undefined} />
                {/* Mailbox switch — everything below this line is about a
                    different inbox, and the assistant's per-account
                    instructions changed with it. */}
                {mailboxMarks
                  .filter((mk) => mk.afterId === msg.id)
                  .map((mk, k) => (
                    <div key={`mbx-${msg.id}-${k}`} className="flex items-center gap-2 my-3">
                      <div className="flex-1 h-px bg-primary/25" />
                      <span className="flex items-center gap-1 text-[10px] text-primary/80 font-medium shrink-0">
                        <Icon name="Mail" size={10} /> Now working on {mk.label}
                      </span>
                      <div className="flex-1 h-px bg-primary/25" />
                    </div>
                  ))}
                {/* Inline HITL card — anchored to the asking assistant turn. */}
                {msg.id === hitlAnchorId && renderHitlCards()}
              </div>
            );
          })}

          {/* Fallback: a HITL prompt arrived before any assistant bubble exists
              (rare — e.g. a tool asks before any text streamed). Render at the
              list tail so the card is never lost. */}
          {hitlAnchorId === null && renderHitlCards()}

          {!isLoading && messages.length > 0 && (() => {
            const last = messages[messages.length - 1];
            if (last?.role === "assistant" && last.content.trim() && !last.streaming) {
              return (
                <SuggestionPills
                  suggestions={smartSuggestions.length > 0
                    ? smartSuggestions
                    : (AGENT_SUGGESTIONS[currentAgentName] ?? DEFAULT_SUGGESTIONS)}
                  onPick={handleChoice} label="Follow up" align="start" />
              );
            }
            return null;
          })()}

          {/* Turn errors render once, inline in the thread, via the __ERROR__
              system message (see MessageBubble). The old bottom banner here was
              a duplicate and lingered after recovery, so it was removed. */}
        </div>
        <div ref={bottomRef} />
      </div>

      {/* ── Input area + VS Code-style bottom bar ─────────────────────── */}
      <form onSubmit={handleSubmit} className="shrink-0 border-t border-border bg-card/40 px-3 sm:px-4 pt-2 pb-safe-full">
        {/* VS Code-style Todos panel — latest assistant turn's todo list */}
        {(() => {
          for (let i = messages.length - 1; i >= 0; i--) {
            const m = messages[i];
            if (m.role === "assistant" && m.todos && m.todos.length > 0) {
              return <TodoPanel todos={m.todos} running={!!m.streaming} />;
            }
            if (m.role === "user") break; // only the current turn's todos
          }
          return null;
        })()}
        {queuedCount > 0 && (
          <div className="max-w-3xl mx-auto mb-2 flex items-center gap-2 text-[11px] text-amber-400">
            <span className="w-1.5 h-1.5 rounded-full bg-amber-400 animate-pulse" />
            {queuedCount} message{queuedCount > 1 ? "s" : ""} queued
            <button type="button" onClick={() => { queueRef.current = []; setQueuedCount(0); saveQueue(sessionIdRef.current, queueRef.current); }}
              className="text-muted-foreground hover:text-foreground underline">clear</button>
          </div>
        )}

        {/* Live working indicator — always visible at bottom of output while
            agent is active. Mirrors Claude / GitHub Copilot style: status is
            always in view regardless of scroll position or whether the
            ThinkingContainer is expanded or collapsed. */}
        {isRunActive && (
          <div className="max-w-3xl mx-auto mb-2 flex items-center gap-2 text-[11px] text-muted-foreground chat-fade-in">
            <Icon name="LoaderCircle" className="text-sky-400 animate-spin shrink-0" size={12} strokeWidth={1.5} />
            <span className="italic truncate">
              {liveToolName ? `Running ${liveToolName}…` : `${liveWorkingMsg}…`}
            </span>
            <span className="flex items-center gap-0.5 shrink-0" aria-hidden="true">
              <span className="chat-typing-dot" />
              <span className="chat-typing-dot" />
              <span className="chat-typing-dot" />
            </span>
          </div>
        )}

        <div className="max-w-3xl mx-auto">
          {/* A watcher reads the room and cannot drive its agents. Saying so
              here is the difference between a boundary and a bug: the
              alternative is a 403 after they have typed a paragraph. */}
          {!canSend && (
            <div className="mb-2 rounded-2xl border border-border bg-secondary/30 px-4 py-3 text-[12px] text-muted-foreground">
              You are watching this conversation. Ask an owner to make you a
              contributor if you need to send turns to its agents.
            </div>
          )}
          {/* Input pill — textarea + inline controls, unified container */}
          <div className={[
            "rounded-2xl border border-border bg-secondary/50 focus-within:border-primary/40 tech-transition",
            canSend ? "" : "pointer-events-none opacity-40",
          ].join(" ")}>
            {/* Row 1: upload + textarea + send */}
            <div className="flex items-end gap-2 px-2 pt-2 pb-1">
              <FileUploadButton sessionId={sessionId}
                onUploadComplete={(files) => {
                  const names = files.map((f) => f.name).join(", ");
                  const paths = files.map((f) => `\`${f.path}\``).join(", ");
                  const ctx = `📎 Uploaded ${files.length} file(s): ${names}\n\nThese files are available in the workspace at:\n${paths}\n\nYou can read them with the read_file tool. Refer to them whenever I mention the uploaded content.`;
                  setInput((prev) => prev.trim() ? `${prev}\n\n${ctx}` : ctx);
                  inputRef.current?.focus();
                }}
                className="shrink-0 self-end mb-1" />

              <textarea ref={inputRef} value={input}
                onChange={(e) => setInput(e.target.value)} onKeyDown={handleKeyDown} rows={1}
                disabled={loadingHistory}
                aria-label={`Message ${currentAgentLabel}`}
                placeholder={
                  loadingHistory
                    ? "Loading previous messages…"
                    : isLoading
                      ? `${SEND_MODE_LABELS[sendMode]} a follow-up to ${currentAgentLabel}…`
                      : `Message ${currentAgentLabel}…`
                }
                className="flex-1 resize-none bg-transparent px-1 py-1.5 text-[16px] sm:text-sm text-foreground placeholder-muted-foreground focus:outline-none max-h-40 overflow-y-auto scrollbar-thin disabled:opacity-50 disabled:cursor-not-allowed"
                style={{ minHeight: "32px" }}
                onInput={(e) => { const t = e.currentTarget; t.style.height = "auto"; t.style.height = `${Math.min(t.scrollHeight, 160)}px`; }} />

              {/* Contextual send / stop button */}
              {loadingHistory ? (
                <div className="shrink-0 self-end h-9 w-9 rounded-xl bg-muted flex items-center justify-center">
                  <span className="w-4 h-4 rounded-full border-2 border-muted-foreground/30 border-t-muted-foreground animate-spin" />
                </div>
              ) : isRunActive ? (
                <div className="shrink-0 flex items-stretch self-end" ref={sendMenuRef}>
                  {/* Stop button — always visible while a run is active */}
                  <button type="button" onClick={handleStop}
                    className="h-9 w-9 rounded-xl bg-destructive/10 border border-destructive/30 text-destructive flex items-center justify-center hover:bg-destructive/20 tech-transition"
                    aria-label="Stop generation" title="Stop generation">
                    <Icon name="Square" size={15} strokeWidth={2.5} fill="currentColor" />
                  </button>
                  {/* Send/Queue/Steer — unified pill: button + dropdown toggle merged */}
                  {input.trim() && (
                    <div className="relative ml-1.5">
                      <div className={`flex items-stretch rounded-xl overflow-hidden border ${
                        sendMode === "steer" ? "bg-amber-600 border-amber-500" :
                        sendMode === "queue" ? "bg-sky-600 border-sky-500" :
                        "bg-primary border-primary"
                      }`}>
                        {/* Main send action */}
                        <button type="submit"
                          className={`h-9 pl-3 pr-2 text-white font-semibold text-xs flex items-center gap-1.5 hover:brightness-110 tech-transition ${
                            sendMode === "steer" ? "bg-amber-600" :
                            sendMode === "queue" ? "bg-sky-600" :
                            "bg-primary"
                          }`}
                          aria-label={SEND_MODE_LABELS[sendMode]}
                          title={`${SEND_MODE_LABELS[sendMode]} — Ctrl+Enter`}>
                          {sendMode === "steer" ? <Icon name="CornerDownRight" size={14} strokeWidth={2} /> :
                           sendMode === "queue" ? <Icon name="ListOrdered" size={14} strokeWidth={2} /> :
                           <Icon name="ArrowUp" size={14} strokeWidth={2.5} />}
                          <span className="hidden sm:inline text-[11px]">{SEND_MODE_LABELS[sendMode]}</span>
                        </button>
                        {/* Dropdown toggle — divider + chevron */}
                        <button type="button"
                          onClick={(e) => { e.preventDefault(); setShowSendMenu((v) => !v); }}
                          className={`h-9 w-7 flex items-center justify-center border-l hover:brightness-110 tech-transition ${
                            sendMode === "steer" ? "bg-amber-600 border-amber-500" :
                            sendMode === "queue" ? "bg-sky-600 border-sky-500" :
                            "bg-primary border-primary-foreground/20"
                          }`}
                          aria-label="Change send mode" title="Change how messages are sent">
                          <Icon name="ChevronDown" size={14} strokeWidth={2.5} className="text-white/80" />
                        </button>
                      </div>
                      {/* Dropdown menu */}
                      {showSendMenu && (
                        <div className="absolute bottom-full right-0 mb-1.5 w-56 rounded-xl border border-border bg-popover shadow-2xl z-50 py-1.5 tech-glass-subtle"
                          onMouseLeave={() => setShowSendMenu(false)}>
                          <div className="px-3 pb-1 text-[9px] text-muted-foreground/60 uppercase tracking-wider font-semibold">Send mode</div>
                          {(["steer", "send", "queue"] as SendMode[]).map((m) => (
                            <button key={m} type="button"
                              onClick={() => { setSendMode(m); setShowSendMenu(false); }}
                              className={`w-full text-left px-3 py-2 text-xs hover:bg-secondary tech-transition ${m === sendMode ? "text-foreground bg-secondary/60" : "text-muted-foreground"}`}>
                              <div className="flex items-center justify-between gap-2">
                                <span className="font-medium flex items-center gap-2">
                                  <span className={`w-6 h-6 rounded-lg flex items-center justify-center ${
                                    m === "steer" ? "bg-amber-500/15 text-amber-400" :
                                    m === "queue" ? "bg-sky-500/15 text-sky-400" :
                                    "bg-primary/15 text-primary"
                                  }`}>
                                    {m === "steer" ? <Icon name="CornerDownRight" size={13} strokeWidth={2} /> :
                                     m === "queue" ? <Icon name="ListOrdered" size={13} strokeWidth={2} /> :
                                     <Icon name="ArrowUp" size={13} strokeWidth={2.5} />}
                                  </span>
                                  {m === "steer" ? "Steer" : m === "queue" ? "Queue" : "Send"}
                                </span>
                                {m === sendMode && <Icon name="CheckCircle" size={13} className="text-emerald-400 shrink-0" />}
                              </div>
                              <div className="text-muted-foreground mt-0.5 text-[10px] ml-8">
                                {SEND_MODE_DESCRIPTIONS[m]}
                              </div>
                            </button>
                          ))}
                        </div>
                      )}
                    </div>
                  )}
                </div>
              ) : (
                <button type="submit" disabled={!input.trim()}
                  className="shrink-0 self-end h-9 w-9 rounded-xl bg-primary text-primary-foreground flex items-center justify-center disabled:opacity-25 disabled:cursor-not-allowed hover:opacity-90 tech-transition"
                  aria-label="Send" title="Send message">
                  <Icon name="ArrowUp" size={16} strokeWidth={2.5} />
                </button>
              )}
            </div>

            {/* Row 2: control bar inside the pill — wraps on narrow screens */}
            <div className="flex items-center gap-1 px-2 pb-1.5 text-[11px] text-muted-foreground flex-wrap" ref={modelMenuRef}>
              {/* Agent selector — only the orchestrator can switch agents mid-session.
                  Specialised agents lock you into their session for clean history. */}
              {isOrchestrator && (
                <div className="relative">
                  <button onClick={() => setShowAgentMenu((v) => !v)}
                    className="flex items-center gap-1.5 px-2 py-1 rounded-md hover:bg-secondary hover:text-foreground tech-transition">
                    <span className="w-1.5 h-1.5 rounded-full bg-success shrink-0" />
                    <span className="truncate max-w-[72px] sm:max-w-[90px] font-medium">{currentAgentLabel}</span>
                    <span className="text-muted-foreground/50">▾</span>
                  </button>
                  {showAgentMenu && (
                    <div className="absolute bottom-full left-0 mb-1.5 w-64 rounded-lg border border-border bg-popover shadow-xl z-50 py-1 tech-glass-subtle"
                      onMouseLeave={() => setShowAgentMenu(false)}>
                      {agents.length === 0 ? (
                        <div className="px-3 py-2 text-xs text-muted-foreground">No agents available</div>
                      ) : (
                        agents.map((a) => (
                          <button key={a.name} onClick={() => handleSwitchAgent(a)}
                            className={`w-full text-left px-3 py-1.5 text-xs hover:bg-secondary tech-transition ${a.name === currentAgentName ? "text-foreground bg-secondary/60" : "text-muted-foreground"}`}>
                            <div className="flex items-center justify-between gap-1">
                              <span className="flex items-center gap-1.5 min-w-0">
                                <AgentAvatar libraryId={agentAvatars[a.name]} size={18} fallback={null} />
                                <span className="font-medium truncate">{a.display_name || a.name}</span>
                              </span>
                              {a.name === currentAgentName && <span className="text-emerald-400 text-[10px]">✓</span>}
                            </div>
                          </button>
                        ))
                      )}
                    </div>
                  )}
                </div>
              )}

              {isOrchestrator && <span className="w-px h-3.5 bg-border shrink-0" />}

              {/* Mailbox picker — the email assistant only. Which inbox the
                  assistant acts on is a per-conversation choice, so it belongs
                  next to the model, not buried in whatever the inbox list
                  happens to be showing. Switching mid-thread drops a marker in
                  the transcript (see mailboxMarks). */}
              {(mailboxes?.length ?? 0) > 0 && (
                <>
                  <div className="relative">
                    <button
                      type="button"
                      onClick={() => setShowMailboxMenu((v) => !v)}
                      title={
                        activeMailbox
                          ? `The assistant is working on ${activeMailbox.label}`
                          : "Choose which mailbox the assistant works on"
                      }
                      aria-haspopup="listbox"
                      aria-expanded={showMailboxMenu}
                      className="flex items-center gap-1 px-2 py-1 rounded-md hover:bg-secondary hover:text-foreground tech-transition truncate max-w-[120px] sm:max-w-[170px]"
                    >
                      <Icon name="Mail" size={11} className="shrink-0 text-muted-foreground/70" />
                      <span className="truncate">
                        {activeMailbox?.label ?? "Pick mailbox"}
                      </span>
                      <span className="text-muted-foreground/50 shrink-0">▾</span>
                    </button>
                    {showMailboxMenu && (
                      <div
                        role="listbox"
                        className="absolute bottom-full left-0 mb-1.5 w-64 rounded-lg border border-border bg-popover shadow-2xl z-50 overflow-hidden tech-glass-subtle"
                      >
                        <div className="px-3 pt-2 pb-1 text-[9px] text-muted-foreground/60 uppercase tracking-wider font-semibold">
                          Mailbox
                        </div>
                        <div className="max-h-60 overflow-y-auto py-1 scrollbar-thin">
                          {mailboxes!.map((mb) => (
                            <button
                              key={mb.id}
                              role="option"
                              aria-selected={mb.id === activeMailboxId}
                              onClick={() => {
                                setShowMailboxMenu(false);
                                if (mb.id === activeMailboxId) return;
                                // Anchor the marker to the last message so the
                                // switch reads in the right place; a switch
                                // before the first message needs no marker.
                                const last =
                                  visibleMessages[visibleMessages.length - 1];
                                if (last) {
                                  setMailboxMarks((prev) => [
                                    ...prev,
                                    { afterId: last.id, label: mb.label },
                                  ]);
                                }
                                onMailboxChange?.(mb.id);
                              }}
                              className={`w-full text-left px-3 py-1.5 text-xs tech-transition flex items-center justify-between gap-2 ${
                                mb.id === activeMailboxId
                                  ? "text-foreground bg-secondary/60"
                                  : "text-muted-foreground hover:bg-secondary"
                              }`}
                            >
                              <span className="truncate">{mb.label}</span>
                              {mb.id === activeMailboxId && (
                                <span className="text-emerald-400 text-[10px] shrink-0">✓</span>
                              )}
                            </button>
                          ))}
                        </div>
                      </div>
                    )}
                  </div>
                  <span className="w-px h-3.5 bg-secondary/60 shrink-0" />
                </>
              )}

              {/* Model selector — hidden when the model is governed externally
                  (e.g. the email Assistant's chat_model setting). */}
              {!lockModel && (
              <div className="relative">
                <button onClick={() => setShowModelMenu((v) => !v)}
                  className="flex items-center gap-1 px-2 py-1 rounded-md hover:bg-secondary hover:text-foreground tech-transition truncate max-w-[110px] sm:max-w-[150px]">
                  <span className="truncate">{currentModelLabel}</span>
                  <span className="text-muted-foreground/50 shrink-0">▾</span>
                </button>
                {showModelMenu && (
                  <div className="absolute bottom-full left-0 mb-1.5 w-72 rounded-lg border border-border bg-popover shadow-2xl z-50 overflow-hidden tech-glass-subtle">
                    <div className="max-h-72 overflow-y-auto py-1 scrollbar-thin">
                      {modelGroups.map((group) => (
                        <div key={group}>
                          <div className="px-3 pt-2 pb-1 text-[9px] text-muted-foreground/60 uppercase tracking-wider font-semibold">{group}</div>
                          {sortedModels.filter((m) => m.group === group).map((m) => (
                            <button key={m.id}
                              onClick={() => { setCurrentModel(m.id); setShowModelMenu(false); }}
                              className={`w-full text-left px-3 py-1.5 text-xs tech-transition flex items-center justify-between gap-2 ${m.id === currentModel ? "text-foreground bg-secondary/60" : "text-muted-foreground hover:bg-secondary"}`}>
                              <span className="truncate">{m.label}</span>
                              {m.id === currentModel && <span className="text-emerald-400 text-[10px] shrink-0">✓</span>}
                            </button>
                          ))}
                        </div>
                      ))}
                    </div>
                  </div>
                )}
              </div>
              )}

              <span className="w-px h-3.5 bg-secondary/60 shrink-0" />

              {/* Thinking mode — compact dropdown (saves space, easier tap on mobile) */}
              <div className="relative">
                <Button variant="ghost" size="none" radius="keep" layout="flex items-center" type="button" onClick={() => setShowThinkMenu((v) => !v)} title={THINK_MODES.find((t) => t.mode === thinkMode)?.title} className="gap-1 px-2 py-1 rounded-md">
                  <span>{THINK_MODES.find((t) => t.mode === thinkMode)?.label ?? "Auto"}</span>
                  <span className="text-muted-foreground/50 text-[9px]">▾</span>
                </Button>
                {showThinkMenu && (
                  <div className="absolute bottom-full left-0 mb-1.5 w-44 rounded-lg border border-border bg-popover shadow-xl z-50 py-1 tech-glass-subtle"
                    onMouseLeave={() => setShowThinkMenu(false)}>
                    {THINK_MODES.map((tm) => (
                      <button key={tm.mode} type="button"
                        onClick={() => { setThinkMode(tm.mode); setShowThinkMenu(false); }}
                        className={`w-full text-left px-3 py-2 text-xs hover:bg-secondary tech-transition ${thinkMode === tm.mode ? "text-foreground bg-secondary/60" : "text-muted-foreground"}`}>
                        <div className="flex items-center justify-between gap-1">
                          <span className="font-medium">{tm.label}</span>
                          {thinkMode === tm.mode && <span className="text-emerald-400 text-[10px]">✓</span>}
                        </div>
                        <div className="text-muted-foreground mt-0.5 text-[10px]">{tm.title}</div>
                      </button>
                    ))}
                  </div>
                )}
              </div>

              <span className="w-px h-3.5 bg-secondary/60 shrink-0" />

              {/* Context-window ring — always visible inline */}
              <ContextRing
                pct={contextUsage.pct}
                usedTokens={contextUsage.usedTokens}
                totalTokens={contextUsage.totalTokens}
                compacting={compacting}
                onCompact={handleCompact}
                modelId={currentModel}
                isLoading={isLoading}
              />

              {isRunActive && sendMode !== "send" && (
                <span className="text-amber-400 text-[10px] font-medium">
                  {sendMode === "queue" ? "⏱ Queued" : "⤳ Steering"}
                </span>
              )}

              {!compact && (
                <span className="hidden sm:inline text-muted-foreground text-[10px] ml-auto">
                  <kbd className="text-muted-foreground">⏎</kbd> send · <kbd className="text-muted-foreground">⇧⏎</kbd> newline
                </span>
              )}
            </div>
          </div>

          {/* Disclaimer */}
          {!compact && (
            <p className="text-[9px] text-muted-foreground/50 text-center mt-1.5">
              Metorite can make mistakes. Please verify important information.
            </p>
          )}
        </div>
      </form>

      {/* Artifact viewer modal */}
      {viewerEntry && (
        <ArtifactViewerModal sessionId={sessionId} entry={viewerEntry}
          onClose={() => setViewerEntry(null)} onDelete={() => setViewerEntry(null)} />
      )}
    </div>

    {/* The room's own side — people, agents, what just happened. Present on
        wide screens whenever this is a room; a drawer below that. */}
    {isRoom && room && (
      <>
        <div className="hidden lg:flex lg:shrink-0">
          <PresenceRail room={room} presence={presence} timeline={timeline}
            availableAgents={agents.map((a) => a.name)}
            onChanged={refreshRoom} />
        </div>
        {railOpen && (
          <div className="fixed inset-y-0 right-0 z-50 flex w-72 max-w-[85vw] lg:hidden">
            <PresenceRail room={room} presence={presence} timeline={timeline}
              availableAgents={agents.map((a) => a.name)}
              onChanged={refreshRoom} onClose={() => setRailOpen(false)} />
          </div>
        )}
      </>
    )}
    </div>
  );
}

