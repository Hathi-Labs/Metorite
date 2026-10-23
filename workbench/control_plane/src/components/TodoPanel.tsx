"use client";

/**
 * TodoPanel — VS Code Copilot Chat-style "Todos (n/m)" panel.
 *
 * Above the chat input, outside the thread. On a phone it opens folded to
 * its one line (`projects_ai_chat.md` §24.10, rule C1). Mirrors VS Code's chatTodoListWidget:
 *   • Collapsible header with completion count
 *   • Check circles: ✓ done (green), ◐ in-progress (blue pulse), ○ pending
 *   • Auto-expands while the run is active, collapses when all done
 */

import { useEffect, useMemo, useRef, useState } from "react";
import Icon from "@/components/Icon";

export interface TodoItem {
  id: string;
  title: string;
  status: string; // pending | in_progress | done | completed | cancelled
}

function isDone(s: string): boolean {
  return s === "done" || s === "completed";
}

function isActive(s: string): boolean {
  return s === "in_progress" || s === "in-progress" || s === "active";
}

export default function TodoPanel({
  todos,
  running,
  startOpen = true,
}: {
  todos: TodoItem[];
  running: boolean;
  /** False on a phone: the panel sits outside the thread, so it opens
   *  folded to its one line (`projects_ai_chat.md` §24.10, rule C1). */
  startOpen?: boolean;
}) {
  const [expanded, setExpanded] = useState(startOpen);
  const userToggledRef = useRef(false);

  const doneCount = useMemo(
    () => todos.filter((t) => isDone(t.status)).length,
    [todos],
  );
  const allDone = todos.length > 0 && doneCount === todos.length;
  const activeTitle = todos.find((t) => isActive(t.status))?.title ?? "";

  // Auto-collapse shortly after everything completes (unless user toggled).
  useEffect(() => {
    if (userToggledRef.current) return;
    if (allDone && !running) {
      const t = setTimeout(() => setExpanded(false), 1500);
      return () => clearTimeout(t);
    }
    setExpanded(startOpen);
  }, [allDone, running, startOpen]);

  if (todos.length === 0) return null;

  return (
    <div className="max-w-3xl mx-auto mb-2 rounded-lg border border-border/50 bg-card/70 overflow-hidden">
      {/* Header */}
      <button
        type="button"
        onClick={() => { userToggledRef.current = true; setExpanded((o) => !o); }}
        className="w-full flex items-center gap-2 px-3 py-1.5 text-left hover:bg-secondary/40 transition-colors"
      >
        <Icon name="ChevronDown" size={12}
          className={`shrink-0 text-muted-foreground transition-transform duration-150 ${expanded ? "" : "-rotate-90"}`} />
        <span className="text-xs font-semibold text-foreground">
          Todos ({doneCount}/{todos.length})
        </span>
        {/* Folded, the one line still says what the run does now. */}
        {!expanded && activeTitle && (
          <span data-todo-active="" className="min-w-0 flex-1 truncate text-xs text-muted-foreground">
            {activeTitle}
          </span>
        )}
        {running && !allDone && (
          <span className="w-1.5 h-1.5 rounded-full bg-info chat-pulse-dot shrink-0" />
        )}
      </button>

      {/* Items */}
      {expanded && (
        <ul className="px-3 pb-2 space-y-1 max-h-48 overflow-y-auto">
          {todos.map((t) => {
            const done = isDone(t.status);
            const active = isActive(t.status);
            return (
              <li key={t.id} className="flex items-center gap-2.5 text-xs leading-snug">
                <span className="shrink-0 flex items-center justify-center w-4 h-4">
                  {done ? (
                    <svg width="14" height="14" viewBox="0 0 16 16" fill="none">
                      <circle cx="8" cy="8" r="7" stroke="var(--success)" strokeWidth="1.3" />
                      <path d="M5 8.2l2 2 4-4.4" stroke="var(--success)" strokeWidth="1.4"
                        strokeLinecap="round" strokeLinejoin="round" />
                    </svg>
                  ) : active ? (
                    <svg width="14" height="14" viewBox="0 0 16 16" fill="none"
                      className="chat-pulse-dot">
                      <circle cx="8" cy="8" r="7" stroke="var(--primary)" strokeWidth="1.3" />
                      <circle cx="8" cy="8" r="3" fill="var(--primary)" />
                    </svg>
                  ) : (
                    <svg width="14" height="14" viewBox="0 0 16 16" fill="none">
                      <circle cx="8" cy="8" r="7" stroke="var(--muted-foreground)" strokeWidth="1.3" />
                    </svg>
                  )}
                </span>
                <span className={
                  done
                    ? "text-muted-foreground"
                    : active
                    ? "text-foreground font-medium"
                    : "text-muted-foreground"
                }>
                  {t.title}
                </span>
              </li>
            );
          })}
        </ul>
      )}
    </div>
  );
}
