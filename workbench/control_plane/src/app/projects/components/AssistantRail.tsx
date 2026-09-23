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

import Button from "@/components/ui/Button";
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
import { fetchTaskSettings } from "@/app/tasks/lib/api";
import {
  buildProjectsAssistantPersona,
  describeFilters,
  type PersonaInput,
} from "../lib/assistantPersona";
import { SelectButton } from "@/components/ui/SelectButton";
import {
  EVERYTHING,
  focusedEntry,
  initialFocus,
  nextFocus,
  scopeOptions,
  type ScopeEntry,
} from "../lib/chatScope";

export const PROJECTS_AGENT = "projects-assistant";

/** The permission the vocabulary writes need (`routes/projects/core.py`). */
const SETTINGS_WRITE = "projects:settings:write";

/**
 * The four prompts the empty chat suggests. They render as the shared chat's
 * own "Try asking" pills (`AgentChat`'s `AGENT_SUGGESTIONS`), the pattern the
 * main chat and the email assistant use. A second set of full-width buttons
 * above the chat doubled them (visual review, 2026-09-23). Kept here as the
 * record of intent; `AgentChat` carries the strings.
 */
export const QUICK_ACTIONS: ReadonlyArray<{ label: string; prompt: string }> = [
  { label: "What is stuck here?", prompt: "What is stuck in this space? Lead with what needs attention." },
  { label: "Summarise this task", prompt: "Summarise this task: what it is, who has it, what is blocking it, and what happened last." },
  { label: "Plan a project from a goal", prompt: "Plan a project from a goal. Ask me for the goal and the deadline, then propose the plan as an editable card." },
  { label: "Weekly report for this space", prompt: "Write the weekly status report for this space: flag each project, draw the dashboard, and save the report." },
];

export interface AssistantRailProps {
  node?: PersonaInput["node"];
  view?: string | null;
  filters?: Parameters<typeof describeFilters>[0];
  openTask?: PersonaInput["openTask"];
  selectedTaskIds?: readonly string[];
  /**
   * Every node the member can see, for the header's focus picker
   * (`lib/chatScope.ts`). Absent, the header names `node` and offers no pick.
   */
  scopes?: readonly ScopeEntry[];
  onClose?: () => void;
}

export function AssistantRail({
  node,
  view,
  filters,
  openTask,
  selectedTaskIds,
  scopes,
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
  // The member's chat model is ONE row of preference (`user_settings.chat_model`),
  // set in the Tasks app's settings. The Projects chat reads the same row, so
  // there is no second setting to keep in step. Unset until it arrives.
  const [chatModel, setChatModel] = useState<string | undefined>();
  useEffect(() => {
    let cancelled = false;
    fetchTaskSettings()
      .then((s) => { if (!cancelled && s.chatModel) setChatModel(s.chatModel); })
      .catch(() => {});
    return () => { cancelled = true; };
  }, []);

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

  // The focus: a hint, never a boundary (`lib/chatScope.ts`). It follows the
  // tree until the member picks in the header, then it holds.
  const [focus, setFocus] = useState(() => initialFocus(node?.id));
  const treeId = node?.id ?? null;
  useEffect(() => {
    // eslint-disable-next-line react-hooks/set-state-in-effect
    setFocus((f) => nextFocus(f, { type: "tree", id: treeId }));
  }, [treeId]);
  const focusNode: PersonaInput["node"] = useMemo(() => {
    if (!scopes) return node ?? null;
    const e = focusedEntry(focus, scopes);
    return e ? { id: e.id, name: e.name, level: e.level, archived: e.archived } : null;
  }, [scopes, focus, node]);
  const options = useMemo(() => (scopes ? scopeOptions(scopes) : []), [scopes]);

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
      node: focusNode,
      view,
      filterSummary: filters ? describeFilters(filters) : "",
      openTask: openTask ?? null,
      selectedTaskIds,
      canManageSettings: hasCapability(access, SETTINGS_WRITE),
      today: `${yyyy}-${mm}-${dd}`,
      timezone,
    });
  }, [focusNode, view, filters, openTask, selectedTaskIds, access]);

  const activeSession = mySessions.find((s) => s.id === activeId);

  return (
    <div className="flex flex-col h-full bg-sidebar text-sidebar-foreground overflow-hidden">
      {/* Header */}
      <div className="flex items-center justify-between px-4 h-9 border-b border-sidebar-border flex-shrink-0">
        {/* The page's title row already says "AI chat". This row says what
            the chat answers ABOUT, which is the one thing the member cannot
            see elsewhere once the tree scrolls. */}
        {scopes ? (
          <div className="flex min-w-0 items-center gap-1.5">
            <span className="shrink-0 text-xs text-muted-foreground">Chat about</span>
            <SelectButton
              label="What the chat is focused on"
              value={focusNode ? focus.focusId : EVERYTHING}
              defaultValue={EVERYTHING}
              options={options}
              widthClass="max-w-[14rem]"
              onChange={(id) => setFocus((f) => nextFocus(f, { type: "pick", id }))}
            />
          </div>
        ) : (
          <p className="min-w-0 truncate text-xs text-muted-foreground" title={node?.name ?? undefined}>
            {node ? (
              <>
                Asking about <span className="text-sidebar-foreground">{node.name}</span>
              </>
            ) : (
              "Asking about every space you can see"
            )}
          </p>
        )}
        <div className="flex items-center gap-0.5">
          {/* DESIGN_SYSTEM §3: a control is a <Button>. `selected` carries
              the history toggle's state and its aria-pressed together. */}
          <Button
            variant="ghost"
            size="icon-xs"
            icon="MessagesSquare"
            selected={showSessions}
            onClick={() => setShowSessions((v) => !v)}
            title="Chat history"
            aria-label="Chat history"
          />
          <Button
            variant="ghost"
            size="icon-xs"
            icon="Plus"
            onClick={newSession}
            title="New chat"
            aria-label="New chat"
          />
          {onClose && (
            <Button
              variant="ghost"
              size="icon-xs"
              icon="X"
              onClick={onClose}
              title="Close AI chat"
              aria-label="Close AI chat"
            />
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
            <Button
              variant="ghost"
              size="icon-xs"
              icon="X"
              onClick={() => setShowSessions(false)}
              aria-label="Hide conversations"
            />
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
                <Button
                  variant="ghost"
                  size="icon-xs"
                  icon="Trash2"
                  onClick={(e) => {
                    e.stopPropagation();
                    removeSession(s.id);
                  }}
                  title="Delete conversation"
                  aria-label="Delete conversation"
                  className="reveal-on-hover flex-shrink-0"
                />
              </div>
            ))
          )}
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
            model={chatModel}
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
