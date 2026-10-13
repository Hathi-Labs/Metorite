"use client";

/**
 * The member's chats, as Claude Code lists sessions in an editor (D99,
 * owner 2026-10-12): a search, one list newest first with each chat's age,
 * and the archived chats folded under their count. A row opens its chat. On
 * hover a row renames and archives, and an archived row restores or deletes.
 *
 * One component, in four places: a column of the assistant's full view, a
 * dropdown of its side panel, its whole sheet on a phone, and the chat list
 * of the Artifacts app (slice 6c). A file of its own, so Artifacts can show
 * it without the chat (`seams.test.ts` lets one shell file mount AgentChat).
 *
 * Fence (R7): `chatHistory.test.ts` holds the lists. `sessionPicker.test.ts`
 * holds the owner's acts.
 */

import { useMemo, useState } from "react";

import Icon from "@/components/Icon";
import SessionRunDot from "@/components/SessionRunDot";
import Button from "@/components/ui/Button";
import { Input } from "@/components/ui/Input";
import { useActiveSessions, useUnreadIds } from "@/hooks/useActiveSessions";
import type { ChatSession } from "@/lib/sessions";

import { relativeAge, sessionTitle, splitSessions } from "./chatHistory";

// ── The session picker ───────────────────────────────────────────────────────

/** One row: the name and its age, and on hover the row's own acts. */
function SessionRow({
  session,
  on,
  now,
  running,
  unread,
  onSelect,
  onRename,
  onArchive,
  onRestore,
  onRemove,
}: {
  session: ChatSession;
  on: boolean;
  now: number;
  running: boolean;
  unread: boolean;
  onSelect: () => void;
  onRename: (title: string) => void;
  onArchive?: () => void;
  onRestore?: () => void;
  onRemove?: () => void;
}) {
  const [editing, setEditing] = useState(false);
  const [draft, setDraft] = useState("");
  const title = sessionTitle(session);
  if (editing) {
    return (
      <li className="px-1 py-0.5">
        <form
          onSubmit={(e) => {
            e.preventDefault();
            onRename(draft);
            setEditing(false);
          }}
        >
          <Input
            autoFocus
            inputSize="md"
            value={draft}
            maxLength={120}
            onChange={(e) => setDraft(e.target.value)}
            onBlur={() => {
              onRename(draft);
              setEditing(false);
            }}
            onKeyDown={(e) => {
              if (e.key === "Escape") {
                e.preventDefault();
                e.stopPropagation();
                setEditing(false);
              }
            }}
            aria-label="Chat name"
          />
        </form>
      </li>
    );
  }
  return (
    <li className="group relative" data-session-row={session.id}>
      <Button
        variant="ghost"
        size="none"
        layout="flex items-center"
        onClick={onSelect}
        aria-current={on ? "true" : undefined}
        className={`w-full gap-2 rounded-md px-2 py-1.5 text-left text-sm ${
          on ? "bg-primary/10 text-primary" : ""
        }`}
      >
        <SessionRunDot running={running} unread={unread} placeholder />
        <span className={`min-w-0 flex-1 truncate ${on ? "font-medium" : ""}`}>{title}</span>
        <span className="shrink-0 text-[11px] text-muted-foreground group-hover:invisible group-focus-within:invisible">
          {relativeAge(session.updatedAt, now)}
        </span>
      </Button>
      <span className="reveal-on-hover absolute right-1 top-1/2 flex -translate-y-1/2 items-center gap-0.5">
        {/* A room's name is its owner's to change: every member sees it. */}
        {session.isOwner !== false ? (
          <Button
            variant="ghost"
            size="icon-xs"
            icon="Pencil"
            aria-label={`Rename ${title}`}
            title="Rename"
            onClick={() => {
              setDraft(title === "New chat" ? "" : title);
              setEditing(true);
            }}
          />
        ) : null}
        {onArchive ? (
          <Button variant="ghost" size="icon-xs" icon="Archive" aria-label={`Archive ${title}`} title="Archive" onClick={onArchive} />
        ) : null}
        {onRestore ? (
          <Button variant="ghost" size="icon-xs" icon="ArchiveRestore" aria-label={`Restore ${title}`} title="Restore" onClick={onRestore} />
        ) : null}
        {onRemove ? (
          <Button
            variant="ghost"
            size="icon-xs"
            icon="Trash2"
            aria-label={`Delete ${title}`}
            title="Delete for good"
            onClick={() => {
              if (window.confirm(`Delete "${title}" and its files? This cannot be undone.`)) onRemove();
            }}
          />
        ) : null}
      </span>
    </li>
  );
}

/**
 * The member's chats, as Claude Code lists sessions in an editor (owner,
 * 2026-10-12): a search, one list newest first with each chat's age, and the
 * archived chats folded under their count. A row opens its chat. On hover a
 * row renames and archives, and an archived row restores or deletes.
 *
 * Four places: a column of the full view, a dropdown of the side panel, the
 * whole sheet on a phone, and the chat list of the Artifacts app (`list`).
 */
export function SessionPicker({
  sessions,
  activeId,
  onSelect,
  onRename,
  onArchive,
  onRemove,
  variant,
  autoFocus = false,
}: {
  sessions: readonly ChatSession[];
  activeId: string;
  onSelect: (id: string) => void;
  onRename: (id: string, title: string) => void;
  onArchive: (id: string, archived: boolean) => void;
  onRemove: (id: string) => void;
  variant: "column" | "dropdown" | "sheet" | "list";
  autoFocus?: boolean;
}) {
  const [query, setQuery] = useState("");
  const [showArchived, setShowArchived] = useState(false);
  const running = useActiveSessions();
  const unread = useUnreadIds();
  const [now] = useState(() => Date.now());
  const lists = useMemo(() => splitSessions(sessions, query, activeId), [sessions, query, activeId]);
  const row = (s: ChatSession, archived: boolean) => (
    <SessionRow
      key={s.id}
      session={s}
      on={s.id === activeId}
      now={now}
      running={running.has(s.id)}
      unread={unread.has(s.id)}
      onSelect={() => onSelect(s.id)}
      onRename={(t) => onRename(s.id, t)}
      // Archive, restore and delete are the owner's: the server refuses
      // anybody else (`_MAY_DELETE_SQL`), so nobody else sees the buttons.
      onArchive={archived || s.isOwner === false ? undefined : () => onArchive(s.id, true)}
      onRestore={archived && s.isOwner !== false ? () => onArchive(s.id, false) : undefined}
      onRemove={archived && s.isOwner !== false ? () => onRemove(s.id) : undefined}
    />
  );
  const shape =
    variant === "column"
      ? "h-full w-72 shrink-0 border-r border-border bg-card"
      : variant === "sheet" || variant === "list"
        ? "h-full w-full bg-card"
        : "w-full rounded-lg border border-border bg-popover shadow-lg";
  return (
    <nav
      aria-label="Chats"
      data-assistant-sessions={variant}
      className={`flex flex-col ${shape}`}
      style={variant === "dropdown" ? { maxHeight: "min(70vh, 32rem)" } : undefined}
    >
      <div className="p-2">
        <Input
          icon="Search"
          inputSize="md"
          autoFocus={autoFocus}
          value={query}
          onChange={(e) => setQuery(e.target.value)}
          placeholder="Search chats…"
          aria-label="Search chats"
        />
      </div>
      <div className="min-h-0 flex-1 overflow-y-auto px-1 pb-2">
        {lists.open.length === 0 ? (
          <p className="px-3 py-3 text-xs text-muted-foreground">
            {query ? "No chat matches." : "Your chats will show here."}
          </p>
        ) : (
          <ul className="flex flex-col gap-0.5">{lists.open.map((s) => row(s, false))}</ul>
        )}
        {lists.archived.length > 0 ? (
          <div className="mt-1 border-t border-border pt-1">
            <Button
              variant="ghost"
              size="none"
              layout="flex items-center"
              onClick={() => setShowArchived((o) => !o)}
              aria-expanded={showArchived}
              className="w-full gap-2 rounded-md px-2 py-1.5 text-left text-xs"
            >
              <Icon name={showArchived ? "ChevronDown" : "ChevronRight"} size={14} />
              <span className="flex-1">Archived chats</span>
              <span className="rounded-full bg-secondary px-1.5 py-0.5 text-[10px] font-medium text-foreground">
                {lists.archived.length}
              </span>
            </Button>
            {showArchived ? (
              <ul className="flex flex-col gap-0.5">{lists.archived.map((s) => row(s, true))}</ul>
            ) : null}
          </div>
        ) : null}
      </div>
    </nav>
  );
}
