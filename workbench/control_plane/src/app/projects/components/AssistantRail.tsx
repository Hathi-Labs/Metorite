"use client";

/**
 * AssistantRail — the Projects app's AI chat (WS-27bm).
 *
 * Spec: `project-docs/specs/projects_ai_chat.md` §4.2.
 *
 * A THIN wrapper around the shared <AgentChat> pinned to the
 * `projects-assistant` agent — the exact pattern of the Tasks app's
 * AssistantRail and the email app's EmailAssistantChat: streaming, tool
 * rendering, HITL cards, recovery, persistence and compaction all come from
 * the shared chat infrastructure. This wrapper only
 *   1. manages the projects-assistant session list (shared @/lib/sessions
 *      store, scoped to agentName="projects-assistant" — the SAME
 *      conversations the main chat app sees),
 *   2. feeds the agent the member's PLACE (selected node, view, filters, open
 *      task, selection) via buildProjectsAssistantPersona,
 *   3. offers four quick actions that drop a prompt into the composer.
 *
 * It renders in two places from one component: the `ai-chat` sidebar slot
 * (full width) and, later, a docked rail beside the board. Nothing here knows
 * which.
 */

import Icon from "@/components/Icon";
import { useState, useEffect, useCallback, useMemo } from "react";
import { useSession } from "next-auth/react";
import AgentChat from "@/components/AgentChat";
import {
  getSessions, createSession, upsertSession, deleteSession,
  enrichSession, fetchAndMergeSessionsFromDb, type ChatSession,
} from "@/lib/sessions";
import { useActiveSessions } from "@/hooks/useActiveSessions";
import { useChatMemories } from "@/hooks/useChatMemories";
import { useAccess } from "@/components/AccessProvider";
import { hasCapability } from "@/lib/access";
import {
  buildProjectsAssistantPersona,
  describeFilters,
  type PersonaInput,
} from "../lib/assistantPersona";

export const PROJECTS_AGENT = "projects-assistant";

/** The permission the vocabulary writes need (`routes/projects/core.py`). */
const SETTINGS_WRITE = "projects:settings:write";

/**
 * The four composer suggestions, scoped to the selected node by the persona.
 * Shown only while the conversation is empty, as the Tasks rail does.
 */
export const QUICK_ACTIONS: ReadonlyArray<{ label: string; prompt: string }> = [
  { label: "What is stuck here?", prompt: "What is stuck in this space? Lead with what needs attention." },
  { label: "Summarise this task", prompt: "Summarise this task: what it is, who has it, what is blocking it, and what happened last." },
  { label: "Who is overloaded?", prompt: "Who is overloaded here, and what is unassigned?" },
  { label: "What did we finish?", prompt: "What did we finish here in the last two weeks, by project?" },
];

export interface AssistantRailProps {
  node?: PersonaInput["node"];
  view?: string | null;
  filters?: Parameters<typeof describeFilters>[0];
  openTask?: PersonaInput["openTask"];
  selectedTaskIds?: readonly string[];
  onClose?: () => void;
}

export function AssistantRail({
  node,
  view,
  filters,
  openTask,
  selectedTaskIds,
  onClose,
}: AssistantRailProps) {
  const { data: nextAuthSession } = useSession();
  const userId: string = nextAuthSession?.user?.email ?? "dev@fracktal.in";
  const { access } = useAccess();

  const activeRunIds = useActiveSessions();

  const [sessions, setSessions] = useState<ChatSession[]>([]);
  const [activeId, setActiveId] = useState<string>("");
  const [showSessions, setShowSessions] = useState(false);
  const [pendingInput, setPendingInput] = useState<string | undefined>();

  // Mem0 parity with the chat, email and tasks apps.
  const { memories: memoryObjs } = useChatMemories(userId);
  const memories = useMemo(
    () => memoryObjs.map((m) => m.memory).filter(Boolean),
    [memoryObjs],
  );

  const mySessions = useMemo(
    () => sessions.filter((s) => s.agentName === PROJECTS_AGENT),
    [sessions],
  );

  /* eslint-disable react-hooks/set-state-in-effect */
  useEffect(() => {
    const existing = getSessions().filter((s) => s.agentName === PROJECTS_AGENT);
    if (existing.length > 0) {
      setSessions(getSessions());
      setActiveId(existing[0].id);
    } else {
      const s = createSession(PROJECTS_AGENT);
      upsertSession(s);
      setSessions(getSessions());
      setActiveId(s.id);
    }
  }, []);

  useEffect(() => {
    let cancelled = false;
    fetchAndMergeSessionsFromDb()
      .then((merged) => { if (!cancelled) setSessions(merged); })
      .catch(() => {});
    return () => { cancelled = true; };
  }, []);
  /* eslint-enable react-hooks/set-state-in-effect */

  const newSession = useCallback(() => {
    const s = createSession(PROJECTS_AGENT);
    upsertSession(s);
    setSessions(getSessions());
    setActiveId(s.id);
    setShowSessions(false);
  }, []);

  const switchSession = useCallback((id: string) => {
    setActiveId(id);
    setShowSessions(false);
  }, []);

  const removeSession = useCallback(
    (id: string) => {
      deleteSession(id);
      const remaining = getSessions().filter((s) => s.agentName === PROJECTS_AGENT);
      setSessions(getSessions());
      if (id === activeId) {
        if (remaining.length > 0) {
          setActiveId(remaining[0].id);
        } else {
          const s = createSession(PROJECTS_AGENT);
          upsertSession(s);
          setSessions(getSessions());
          setActiveId(s.id);
        }
      }
    },
    [activeId],
  );

  const handleActivity = useCallback(
    (info: { firstUserMessage?: string; lastPreview?: string; messageCount: number }) => {
      enrichSession(activeId, info);
      setSessions(getSessions());
    },
    [activeId],
  );

  // The member's place, refreshed whenever the page's selection moves.
  const persona = useMemo(() => {
    const today = new Date();
    const yyyy = today.getFullYear();
    const mm = String(today.getMonth() + 1).padStart(2, "0");
    const dd = String(today.getDate()).padStart(2, "0");
    let timezone: string | undefined;
    try {
      timezone = Intl.DateTimeFormat().resolvedOptions().timeZone;
    } catch {
      timezone = undefined;
    }
    return buildProjectsAssistantPersona({
      node: node ?? null,
      view,
      filterSummary: filters ? describeFilters(filters) : "",
      openTask: openTask ?? null,
      selectedTaskIds,
      canManageSettings: hasCapability(access, SETTINGS_WRITE),
      today: `${yyyy}-${mm}-${dd}`,
      timezone,
    });
  }, [node, view, filters, openTask, selectedTaskIds, access]);

  const activeSession = mySessions.find((s) => s.id === activeId);
  const showQuickActions = !activeSession?.messageCount;

  return (
    <div className="flex flex-col h-full bg-sidebar text-sidebar-foreground overflow-hidden">
      {/* Header */}
      <div className="flex items-center justify-between px-4 h-9 border-b border-sidebar-border flex-shrink-0">
        <div className="flex items-center gap-2">
          <div className="w-5 h-5 rounded-full bg-primary/20 text-primary flex items-center justify-center">
            <Icon name="Sparkles" size={11} />
          </div>
          <span className="text-xs font-semibold text-sidebar-foreground">
            AI chat
          </span>
          {node ? (
            <span className="text-[11px] text-muted-foreground truncate max-w-[14rem]" title={node.name}>
              · {node.name}
            </span>
          ) : null}
        </div>
        <div className="flex items-center gap-0.5">
          <button
            type="button"
            onClick={() => setShowSessions((v) => !v)}
            title="Chat history"
            aria-label="Chat history"
            className={`p-1 rounded transition-colors ${
              showSessions
                ? "text-primary bg-primary/10"
                : "text-muted-foreground hover:text-sidebar-foreground hover:bg-sidebar-accent"
            }`}
          >
            <Icon name="MessagesSquare" size={14} />
          </button>
          <button
            type="button"
            onClick={newSession}
            title="New chat"
            aria-label="New chat"
            className="p-1 rounded text-muted-foreground hover:text-sidebar-foreground hover:bg-sidebar-accent transition-colors"
          >
            <Icon name="Plus" size={15} />
          </button>
          {onClose && (
            <button
              type="button"
              onClick={onClose}
              title="Close AI chat"
              aria-label="Close AI chat"
              className="p-1 rounded text-muted-foreground hover:text-sidebar-foreground hover:bg-sidebar-accent transition-colors"
            >
              <Icon name="X" size={15} />
            </button>
          )}
        </div>
      </div>

      {/* Sessions list */}
      {showSessions && (
        <div className="border-b border-sidebar-border bg-secondary/30 max-h-56 overflow-y-auto scrollbar-hide flex-shrink-0">
          <div className="flex items-center justify-between px-3 py-1.5">
            <span className="text-[10px] uppercase tracking-wide text-muted-foreground">
              Conversations
            </span>
            <button
              type="button"
              onClick={() => setShowSessions(false)}
              aria-label="Hide conversations"
              className="text-muted-foreground hover:text-foreground"
            >
              <Icon name="X" size={12} />
            </button>
          </div>
          {mySessions.length === 0 ? (
            <div className="px-3 py-2 text-[11px] text-muted-foreground">
              No conversations yet.
            </div>
          ) : (
            mySessions.map((s) => (
              <div
                key={s.id}
                className={`group flex items-center gap-2 px-3 py-2 cursor-pointer transition-colors ${
                  s.id === activeId ? "bg-primary/10" : "hover:bg-secondary/60"
                }`}
                onClick={() => switchSession(s.id)}
              >
                {activeRunIds.has(s.id) ? (
                  <span
                    className="w-1.5 h-1.5 rounded-full bg-success animate-pulse flex-shrink-0"
                    title="Active — agent is working"
                  />
                ) : (
                  <span className="w-1.5 h-1.5 rounded-full bg-muted-foreground/30 flex-shrink-0" />
                )}
                <div className="flex-1 min-w-0">
                  <div className="text-[11px] text-foreground truncate">
                    {s.title || "New conversation"}
                  </div>
                  {s.lastPreview && (
                    <div className="text-[10px] text-muted-foreground truncate">
                      {s.lastPreview}
                    </div>
                  )}
                </div>
                <button
                  type="button"
                  onClick={(e) => {
                    e.stopPropagation();
                    removeSession(s.id);
                  }}
                  title="Delete conversation"
                  aria-label="Delete conversation"
                  className="reveal-on-hover text-muted-foreground hover:text-destructive flex-shrink-0"
                >
                  <Icon name="Trash2" size={12} />
                </button>
              </div>
            ))
          )}
        </div>
      )}

      {/* Quick actions — drop the prompt into the composer; the member
          reviews and sends. Shown only while the conversation is empty. */}
      {showQuickActions && (
        <div className="flex flex-col gap-1.5 border-b border-sidebar-border p-3 flex-shrink-0">
          {QUICK_ACTIONS.map((qa) => (
            <button
              key={qa.label}
              type="button"
              onClick={() => setPendingInput(qa.prompt)}
              className="tech-transition rounded-lg border border-border bg-background/40 px-3 py-2 text-left text-sm text-foreground hover:border-primary/50 hover:bg-secondary/50"
            >
              {qa.label}
            </button>
          ))}
        </div>
      )}

      {/* Shared chat — the same AgentChat the main chat app renders */}
      <div className="flex-1 min-h-0">
        {activeSession && (
          <AgentChat
            key={activeSession.id}
            agentName={PROJECTS_AGENT}
            sessionId={activeSession.id}
            compact
            persona={persona}
            memories={memories}
            memoryUserId={userId}
            expectedMessageCount={activeSession.messageCount}
            onActivity={handleActivity}
            pendingInput={pendingInput}
            onPendingInputConsumed={() => setPendingInput(undefined)}
          />
        )}
      </div>
    </div>
  );
}

export default AssistantRail;
