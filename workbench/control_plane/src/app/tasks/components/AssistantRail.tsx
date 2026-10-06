"use client";

/**
 * AssistantRail — the tasks app's AI chat rail (F9).
 *
 * A THIN wrapper around the shared <AgentChat> pinned to the `task-manager`
 * agent — the exact pattern of the email app's EmailAssistantChat: streaming,
 * tool rendering, recovery, persistence, and compaction all come from the
 * shared chat infrastructure; this wrapper only
 *   1. manages the task-manager session list (`useAgentSessions`, scoped to
 *      agentName="task-manager" — the SAME conversations the main chat app
 *      sees — and to the signed-in member and org),
 *   2. feeds the agent the live My Tasks context (connected workspaces, current
 *      view, open item, inbox pressure) via buildTaskAssistantPersona,
 *   3. wires the My Tasks quick actions into the composer (user reviews & sends).
 */

import Icon from "@/components/Icon";
import { useState, useCallback, useMemo } from "react";
import { useSession } from "next-auth/react";
import AgentChat from "@/components/AgentChat";
import { useAgentSessions } from "@/hooks/useChatSessions";
import { useActiveSessions } from "@/hooks/useActiveSessions";
import { useChatMemories } from "@/hooks/useChatMemories";
import { useTierRouted } from "@/hooks/useTierRouted";
import { governedModelProps } from "@/lib/tierRouting";
import { useTaskStore } from "../lib/taskStore";
import { buildTaskAssistantPersona } from "../lib/taskAssistantPersona";
import { QUICK_ACTIONS } from "../lib/mockData";

const AGENT = "task-manager";

export function AssistantRail({ onClose }: { onClose?: () => void } = {}) {
  const { data: nextAuthSession } = useSession();
  const userId: string = nextAuthSession?.user?.email ?? "dev@fracktal.in";

  const chatModel = useTaskStore((s) => s.settings.chatModel);
  // WS-45 S4 (D90): a covered task-manager runs on the platform's tier, so the
  // rail passes no `chat_model`. Not covered with the UI flag off.
  const tier = useTierRouted(AGENT);
  const items = useTaskStore((s) => s.items);
  const selectedView = useTaskStore((s) => s.selectedView);
  const selectedItemId = useTaskStore((s) => s.selectedItemId);

  const activeRunIds = useActiveSessions();

  const [showSessions, setShowSessions] = useState(false);
  const [pendingInput, setPendingInput] = useState<string | undefined>();

  // Mem0 parity with the chat + email apps: same memories, same continuity.
  const { memories: memoryObjs } = useChatMemories(userId);
  const memories = useMemo(
    () => memoryObjs.map((m) => m.memory).filter(Boolean),
    [memoryObjs],
  );

  // The list is the signed-in member's in this org, and a restored chat the
  // server refuses gives way to a new one (production bug, 2026-10-05).
  const {
    mine: taskSessions,
    activeId,
    activeSession,
    newSession: openNewSession,
    switchSession: openSession,
    removeSession,
    handleActivity,
    onSessionRefused,
    recoveredInput,
    consumeRecoveredInput,
    notice,
  } = useAgentSessions(AGENT);

  const newSession = useCallback(() => {
    openNewSession();
    setShowSessions(false);
  }, [openNewSession]);

  const switchSession = useCallback((id: string) => {
    openSession(id);
    setShowSessions(false);
  }, [openSession]);

  // Live My Tasks context — refreshed whenever the store or selection changes, so
  // "clarify this" / "process my inbox" need no ids from the user.
  const settings = useTaskStore((s) => s.settings);
  const taskContextStr = useMemo(
    () =>
      buildTaskAssistantPersona({
        items,
        selectedView,
        openItem: items.find((i) => i.id === selectedItemId) ?? null,
        settings,
      }),
    [items, selectedView, selectedItemId, settings],
  );

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
            Assistant
          </span>
        </div>
        <div className="flex items-center gap-0.5">
          <button
            onClick={() => setShowSessions((v) => !v)}
            title="Chat history"
            className={`p-1 rounded transition-colors ${
              showSessions
                ? "text-primary bg-primary/10"
                : "text-muted-foreground hover:text-sidebar-foreground hover:bg-sidebar-accent"
            }`}
          >
            <Icon name="MessagesSquare" size={14} />
          </button>
          <button
            onClick={newSession}
            title="New chat"
            className="p-1 rounded text-muted-foreground hover:text-sidebar-foreground hover:bg-sidebar-accent transition-colors"
          >
            <Icon name="Plus" size={15} />
          </button>
          {onClose && (
            <button
              onClick={onClose}
              title="Close assistant"
              aria-label="Close assistant"
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
              onClick={() => setShowSessions(false)}
              className="text-muted-foreground hover:text-foreground"
            >
              <Icon name="X" size={12} />
            </button>
          </div>
          {taskSessions.length === 0 ? (
            <div className="px-3 py-2 text-[11px] text-muted-foreground">
              No conversations yet.
            </div>
          ) : (
            taskSessions.map((s) => (
              <div
                key={s.id}
                className={`group flex items-center gap-2 px-3 py-2 cursor-pointer transition-colors ${
                  s.id === activeId ? "bg-primary/10" : "hover:bg-secondary/60"
                }`}
                onClick={() => switchSession(s.id)}
              >
                {activeRunIds.has(s.id) ? (
                  <span
                    className="w-1.5 h-1.5 rounded-full bg-emerald-400 animate-pulse flex-shrink-0"
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
                  onClick={(e) => {
                    e.stopPropagation();
                    removeSession(s.id);
                  }}
                  title="Delete conversation"
                  className="reveal-on-hover text-muted-foreground hover:text-destructive flex-shrink-0"
                >
                  <Icon name="Trash2" size={12} />
                </button>
              </div>
            ))
          )}
        </div>
      )}

      {/* My Tasks quick actions — drop the prompt into the composer (the user
          reviews & sends; same pattern as the email app's "Fix" flow).
          Shown only while the conversation is empty. */}
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
            agentName={AGENT}
            sessionId={activeSession.id}
            compact
            {...governedModelProps(tier.covered, chatModel)}
            persona={taskContextStr}
            memories={memories}
            memoryUserId={userId}
            expectedMessageCount={activeSession.messageCount}
            onActivity={handleActivity}
            onSessionRefused={onSessionRefused}
            notice={notice}
            pendingInput={pendingInput ?? recoveredInput}
            onPendingInputConsumed={() => {
              setPendingInput(undefined);
              consumeRecoveredInput();
            }}
          />
        )}
      </div>
    </div>
  );
}
