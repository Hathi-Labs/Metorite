"use client";

import Icon from "@/components/Icon";
import { useState, useRef, useEffect, useMemo } from "react";
import React from "react";
import type { ChatMessage } from "@/hooks/useAgentChat";
import type { FileEntry } from "@/components/ArtifactSidebar";
import type { ParsedAgentError } from "@/lib/parseAgentError";
import MarkdownMessage from "@/components/MarkdownMessage";
import MessageActionBar from "@/components/MessageActionBar";
import GenerativeUIPanel from "@/components/GenerativeUIPanel";
import ArtifactCard, { type ArtifactMeta } from "@/components/ArtifactCard";
import EmailToolCards from "@/components/email/EmailToolCards";
import TaskToolCards from "@/components/tasks/TaskToolCards";
import ProjectToolCards from "@/components/projects/ProjectToolCards";
import GenerativeUINode from "@/components/GenerativeUINode";
import ErrorCard from "@/components/ChatErrorCard";
import { DismissableCard } from "@/components/ToolCardShell";
import { useDismissedToolCards, dismissToolCard } from "@/lib/dismissedTools";
import { openDoc, openGenUI } from "@/lib/sidePanelStore";
import { useSidePanelFits } from "@/lib/sidePanelFit";
import { AgentAvatar, useAgentAvatars } from "@/components/AgentAvatar";
import { capabilityLabel, type RoomParticipant } from "@/lib/rooms";
import { EntityIndexContext } from "@/components/ChatEntityPill";
import { buildEntityIndex } from "@/lib/entityIndex";
import { pillsForTurn } from "@/lib/projectsAgent";

/** The only part of a room participant a message bubble needs: a face. */
export type BubbleParticipant = Pick<
  RoomParticipant,
  "subject" | "displayName" | "avatarUrl"
>;

/** Initials for a person with no avatar image — two letters at most. */
function initialsOf(name: string): string {
  const parts = name.trim().split(/[\s._-]+/).filter(Boolean);
  const letters = parts.slice(0, 2).map((p) => p[0]).join("");
  return (letters || name.slice(0, 1) || "?").toUpperCase();
}

/**
 * Small circular face for another person's turn. `avatarUrl` when they have a
 * picture, initials on a neutral surface when they don't — never a blank gap,
 * because the avatar is what makes "not you" readable at a glance.
 */
function PersonAvatar({
  name,
  avatarUrl,
}: {
  name: string;
  avatarUrl?: string | null;
}) {
  if (avatarUrl) {
    return (
      // eslint-disable-next-line @next/next/no-img-element
      <img
        src={avatarUrl}
        alt={name}
        title={name}
        className="w-7 h-7 shrink-0 rounded-full border border-border object-cover"
      />
    );
  }
  return (
    <div
      title={name}
      className="w-7 h-7 shrink-0 rounded-full border border-border bg-secondary flex items-center justify-center text-[10px] font-semibold text-muted-foreground"
    >
      {initialsOf(name)}
    </div>
  );
}

/**
 * Name plate over an agent's turn, used only when the thread has more than one
 * voice in it. Lives in its own component so the avatar map is fetched for the
 * handful of labelled turns rather than once per message bubble in the thread.
 */
function AgentLabel({ agentName }: { agentName: string }) {
  const avatars = useAgentAvatars();
  return (
    <div className="flex items-center gap-1.5 mb-1">
      <AgentAvatar
        libraryId={avatars[agentName]}
        size={18}
        title={agentName}
        fallback={<Icon name="Bot" size={13} className="text-muted-foreground shrink-0" />}
      />
      <span className="text-[11px] font-medium text-muted-foreground">
        {agentName}
      </span>
    </div>
  );
}

function MessageBubble({
  message,
  sessionId,
  onChoice,
  onHitlRespond,
  onFileOpen,
  onResend,
  onRetryMessage,
  emailContext,
  viewerEmail,
  participants,
  sessionAgentName,
  entityPills = false,
}: {
  message: ChatMessage;
  sessionId: string;
  onChoice?: (choice: string) => void;
  /** Resolve a BLOCKING generative-UI interaction (spec carried a request_id):
   *  answers resume the parked run via /agent/respond-input instead of being
   *  sent as a new chat message. Falls back to onChoice when absent. */
  onHitlRespond?: (requestId: string, answer: string) => void;
  onFileOpen?: (entry: FileEntry) => void;
  onResend?: (content: string) => void;
  onRetryMessage?: (m: ChatMessage) => void;
  emailContext?: { accountId?: string | null; emailId?: string | null };
  /** Who is reading. Absent in a solo thread, where every human turn is yours. */
  viewerEmail?: string;
  /** Room members, for putting a name and a face on someone else's turn. */
  participants?: BubbleParticipant[];
  /** The thread's own agent — a turn from any OTHER agent gets a name plate. */
  sessionAgentName?: string;
  /** Draw «names» as entity pills (WS-27bm S9). The caller passes true for
   *  the Projects assistant only. A turn by another agent in the thread
   *  still draws none. */
  entityPills?: boolean;
}) {
  // Whether a document may open in the side panel here (`lib/sidePanelFit.ts`).
  const panelFits = useSidePanelFits();
  // Deliberately NOT `role === "user"`.  `role` is the model's vocabulary: it
  // says which side of the conversation a turn sits on, and in a room every
  // person's turn is `role: "user"` — mine and yours alike.  Ownership is an
  // authorship question, so it is answered from authorKind/authorEmail, with
  // one fallback: a turn with no author at all is a pre-rooms row from a solo
  // thread, and the only human who could have written it is the reader.
  const isHuman = message.authorKind === "human" || message.authorKind === undefined;
  const isMine =
    isHuman &&
    (!message.authorEmail ||
      !viewerEmail ||
      message.authorEmail.toLowerCase() === viewerEmail.toLowerCase());
  const isUser = message.role === "user";
  const isSystem = message.role === "system";
  const author = message.authorEmail
    ? participants?.find((p) => p.subject === message.authorEmail)
    : undefined;
  const authorName =
    author?.displayName || message.authorEmail?.split("@")[0] || "Someone";
  // Name an agent turn only where the name earns its space: a thread with a
  // single agent already says whose reply this is in the header.
  const otherVoices =
    participants?.filter((p) => p.subject !== viewerEmail).length ?? 0;
  const showAgentLabel =
    message.authorKind === "agent" &&
    !!message.authorEmail &&
    (sessionAgentName
      ? message.authorEmail !== sessionAgentName
      : otherVoices > 0);
  const [editing, setEditing] = useState(false);
  const [editText, setEditText] = useState(message.content);
  const editRef = useRef<HTMLTextAreaElement>(null);

  // Auto-grow the edit textarea + focus when entering edit mode
  useEffect(() => {
    if (editing && editRef.current) {
      const t = editRef.current;
      t.focus();
      t.style.height = "auto";
      t.style.height = `${Math.max(t.scrollHeight, 60)}px`;
    }
  }, [editing]);

  // Dedup tool events by id before rendering.  A streamed tool call can arrive
  // more than once (MAF surfaces a function_call across several updates as its
  // args fill in), and React throws (#185 / duplicate-key) when sibling
  // elements share a key.  Both the thinking timeline AND the email cards key
  // by tool id, so dedup once here and feed both the same clean list.
  const dedupedToolEvents = useMemo(() => {
    const seen = new Set<string>();
    return (message.toolEvents ?? []).filter((t) => {
      if (seen.has(t.id)) return false;
      seen.add(t.id);
      return true;
    });
  }, [message.toolEvents]);

  // The names this turn's tools printed (WS-27bm S9), built once. The answer
  // gets it as a prop, and a generative-UI `markdown` node reads it through
  // the context. Only a Projects turn builds it: another agent's tools hold
  // email bodies and other text that no pill may resolve against.
  const pills = pillsForTurn(entityPills, message);
  const entityIndex = useMemo(
    () => (pills ? buildEntityIndex(dedupedToolEvents) : null),
    [pills, dedupedToolEvents],
  );

  // Dismissed tool/artifact cards (persisted) — filter them out of every card
  // surface so closing a card sticks across reloads.
  const dismissed = useDismissedToolCards();

  // ── Extract artifact events from custom events ──────────────────────────
  // Dedup by path (last write wins) so an artifact_created followed by an
  // artifact_updated for the SAME file renders as one card, not two.
  const artifactEvents: ArtifactMeta[] = (() => {
    const byPath = new Map<string, ArtifactMeta>();
    for (const e of message.customEvents ?? []) {
      if (
        (e.name === "artifact_created" || e.name === "artifact_updated") &&
        e.value &&
        typeof e.value === "object"
      ) {
        const v = e.value as Record<string, unknown>;
        const path = String(v.path ?? "");
        byPath.set(path, {
          path,
          name: path.split("/").pop() ?? path,
          size: typeof v.size === "number" ? v.size : undefined,
          mimeType: typeof v.mime_type === "string" ? v.mime_type : undefined,
          sha256: typeof v.sha256 === "string" ? v.sha256 : undefined,
        } satisfies ArtifactMeta);
      }
    }
    return [...byPath.values()];
  })();

  // ── Generative-UI events → inline declarative component trees ───────────
  // Agents push `generative_ui` CUSTOM events carrying a safe component tree
  // (data, not code — GenerativeUINode whitelists the node types). Rendered
  // inline as a first-class element (not buried in the "Interactive view"
  // fold) so on-the-fly UI is prominent. Button actions route through onChoice
  // — the same follow-up contract as the ```choices``` MCQ block.
  const genUiEvents = (message.customEvents ?? [])
    .filter((e) => e.name === "generative_ui" && e.value != null)
    .map((e) => e.value);

  const timestamp = new Date(message.timestamp).toLocaleTimeString([], {
    hour: "2-digit",
    minute: "2-digit",
  });

  // ═══ Redacted turn — this reader is not cleared for it ═══
  // Checked before every other branch because the notice, not the turn, is what
  // exists here: there is no markdown, no tools, no author to align against.
  // Styled as a quiet aside rather than an error — nothing went wrong, the
  // room's access boundary held.
  if (message.redacted) {
    const caps = message.redactedCaps ?? [];
    return (
      <div className="rounded-xl border border-border bg-secondary/40 px-3.5 py-2.5">
        <div className="flex items-start gap-2">
          <Icon name="EyeOff" size={14} className="mt-0.5 shrink-0 text-muted-foreground" />
          <div className="min-w-0 space-y-1">
            <p className="text-[12px] leading-relaxed text-muted-foreground">
              {message.content}
            </p>
            {caps.length > 0 && (
              <p className="text-[10px] text-muted-foreground/70">
                Requires {caps.map(capabilityLabel).join(", ")}
              </p>
            )}
          </div>
          <div className="ml-auto text-[10px] text-muted-foreground/70 shrink-0">
            {timestamp}
          </div>
        </div>
      </div>
    );
  }

  if (isSystem) {
    const content = message.content;
    if (content.startsWith("__ERROR__")) {
      try {
        const parsed: ParsedAgentError = JSON.parse(content.slice(9));
        return <ErrorCard parsed={parsed} />;
      } catch {
        // fall through
      }
    }
    // Context-compaction summary pill — styled distinctly so users know
    // the conversation was compressed.
    if (content.startsWith("[CONTEXT SUMMARY")) {
      const lines = content.split("\n");
      const header = lines[0];
      const body = lines.slice(2).join("\n").trim();
      return (
        <div className="rounded-lg border border-primary/20 bg-primary/5 px-3 py-2 text-[11px] text-primary/80">
          <div className="flex items-center gap-1.5 font-medium mb-1">
            <svg width="12" height="12" viewBox="0 0 16 16" fill="none" stroke="currentColor" strokeWidth="2">
              <path d="M3 8h10M8 3l5 5-5 5" strokeLinecap="round" strokeLinejoin="round" />
            </svg>
            {header}
          </div>
          {body && (
            <pre className="whitespace-pre-wrap text-[10px] text-primary/60 leading-relaxed">{body}</pre>
          )}
        </div>
      );
    }
    return (
      <div className="text-center text-xs text-muted-foreground italic py-1">
        {content}
      </div>
    );
  }

  const handleEditSubmit = () => {
    const trimmed = editText.trim();
    if (trimmed && onResend) {
      onResend(trimmed);
    }
    setEditing(false);
  };

  const handleEditKeyDown = (e: React.KeyboardEvent<HTMLTextAreaElement>) => {
    // Enter inserts a newline (matches the main composer) — only Ctrl/Cmd+
    // Enter (or the Send button) submits the edited message.
    if (e.key === "Enter" && (e.ctrlKey || e.metaKey)) {
      e.preventDefault();
      handleEditSubmit();
    } else if (e.key === "Escape") {
      setEditing(false);
      setEditText(message.content);
    }
  };

  const handleEditInput = (e: React.ChangeEvent<HTMLTextAreaElement>) => {
    setEditText(e.target.value);
    const t = e.currentTarget;
    t.style.height = "auto";
    t.style.height = `${Math.min(Math.max(t.scrollHeight, 60), 300)}px`;
  };

  // ═══ Someone ELSE's turn — left-aligned, with a face and a name ═══
  // Mirrors the right-aligned "mine" bubble below rather than replacing it, so
  // a thread reads as two columns: your side and theirs.  No edit affordance —
  // resending another person's words as your own is not an edit.
  if (isUser && isHuman && !isMine) {
    return (
      <div className="flex items-start gap-2.5 group">
        <PersonAvatar name={authorName} avatarUrl={author?.avatarUrl} />
        <div className="min-w-0 max-w-[88%] sm:max-w-[78%]">
          <div className="flex items-baseline gap-2 mb-1 pl-0.5">
            <span className="text-[11px] font-medium text-foreground">
              {authorName}
            </span>
            <span className="text-[10px] text-muted-foreground">{timestamp}</span>
          </div>
          <div className="px-4 py-2.5 text-[13px] sm:text-sm leading-relaxed bg-secondary text-foreground rounded-2xl rounded-tl-md">
            <p className="whitespace-pre-wrap break-words">{message.content}</p>
          </div>
          {message.content.trim() && (
            <div className="flex items-center gap-2 mt-1 pl-0.5">
              <MessageActionBar
                content={message.content}
                messageId={message.id}
                role="user"
              />
            </div>
          )}
        </div>
      </div>
    );
  }

  if (isUser) {
    return (
      <div className="flex justify-end group">
        {editing ? (
          /* ═══ Edit mode ═══ */
          <div className="w-full max-w-full sm:max-w-[85%]">
            <div className="rounded-2xl rounded-tr-sm border-2 border-amber-500/50 bg-secondary shadow-lg shadow-amber-500/5 overflow-hidden">
              {/* Edit header */}
              <div className="flex items-center justify-between px-4 py-2 border-b border-border/60 bg-secondary/80">
                <span className="text-[11px] text-amber-400/80 font-medium">
                  ✏️ Editing message
                </span>
                <span className="text-[10px] text-muted-foreground hidden sm:block">
                  Ctrl+Enter to send · Esc to cancel · Enter for new line
                </span>
              </div>

              {/* Textarea */}
              <div className="px-3 py-3">
                <textarea
                  ref={editRef}
                  value={editText}
                  onChange={handleEditInput}
                  onKeyDown={handleEditKeyDown}
                  rows={3}
                  className="w-full resize-none rounded-xl bg-card border border-border px-4 py-3 text-[16px] sm:text-sm text-foreground placeholder-muted-foreground focus:outline-none focus:border-amber-500/60 transition-colors"
                  style={{ minHeight: "60px", maxHeight: "300px" }}
                />
              </div>

              {/* Action buttons */}
              <div className="flex items-center justify-between px-4 py-2.5 border-t border-border/60 bg-secondary/60">
                <button
                  onClick={() => { setEditing(false); setEditText(message.content); }}
                  className="text-[12px] px-3 py-1.5 rounded-lg text-muted-foreground hover:text-foreground hover:bg-secondary/60 transition-colors"
                >
                  Cancel
                </button>
                <div className="flex items-center gap-2">
                  <span className="text-[10px] text-muted-foreground">{editText.length} chars</span>
                  <button
                    onClick={handleEditSubmit}
                    disabled={!editText.trim()}
                    className="text-[12px] px-4 py-1.5 rounded-lg bg-primary text-primary-foreground font-semibold disabled:opacity-30 hover:opacity-90 tech-transition flex items-center gap-1.5"
                  >
                    <span>↑</span> Send
                  </button>
                </div>
              </div>
            </div>
          </div>
        ) : (
          /* ═══ Normal user bubble — compact, right-aligned, no avatar ═══ */
          <div className="max-w-[88%] sm:max-w-[78%]">
            <div
              onDoubleClick={() => { setEditText(message.content); setEditing(true); }}
              className="px-4 py-2.5 text-[13px] sm:text-sm leading-relaxed bg-primary/15 text-foreground rounded-2xl rounded-tr-md cursor-pointer select-none hover:bg-primary/20 tech-transition"
              title="Double-click to edit"
            >
              <p className="whitespace-pre-wrap break-words">{message.content}</p>
            </div>
            <div className="flex items-center justify-end gap-2 mt-1 pr-0.5 opacity-100 transition-opacity">
              {message.content.trim() && (
                <MessageActionBar
                  content={message.content}
                  messageId={message.id}
                  role="user"
                  onEdit={() => { setEditText(message.content); setEditing(true); }}
                />
              )}
              <div className="text-[10px] text-muted-foreground">{timestamp}</div>
            </div>
          </div>
        )}
      </div>
    );
  }

  // ═══ Assistant message — no bubble, renders directly ═══
  return (
    <div className="group">
      {/* Which agent is speaking — only in a thread where that is a real
          question (see showAgentLabel). The turn's layout is otherwise
          untouched. */}
      {showAgentLabel && <AgentLabel agentName={message.authorEmail!} />}
      {/* Content renders directly in the chat window — no wrapper bubble.
          ThinkingContainer, code blocks, and artifact cards have their own
          visual containers. Only the timestamp and action bar are added. */}
      <MarkdownMessage
        content={message.content}
        streaming={message.streaming}
        toolEvents={dedupedToolEvents}
        progressLines={message.progressLines}
        isThinkingActive={message.isThinkingActive}
        reasoningBlocks={message.reasoningBlocks}
        segments={message.segments}
        onChoice={onChoice}
        sessionId={sessionId}
        entityPills={pills}
        entityIndex={entityIndex ?? undefined}
      />
      {/* Inline artifact cards — dismissable (persisted), keyed by sha/path. */}
      {(() => {
        const visible = artifactEvents.filter(
          (a) => !dismissed.has(a.sha256 ?? a.path),
        );
        if (visible.length === 0) return null;
        return (
          <div className="mt-3 space-y-2">
            {visible.map((a) => {
              const id = a.sha256 ?? a.path;
              return (
                <DismissableCard key={id} onDismiss={() => dismissToolCard(id)}>
                  <ArtifactCard
                    artifact={a}
                    sessionId={sessionId}
                    onOpen={onFileOpen}
                    // Absent where the board would lose its minimum width
                    // (`lib/sidePanelFit.ts`): the card then opens the
                    // full-screen viewer instead.
                    onOpenInSidePanel={
                      panelFits
                        ? (entry) =>
                            openDoc({ path: entry.path, name: entry.name, sessionId })
                        : undefined
                    }
                  />
                </DismissableCard>
              );
            })}
          </div>
        );
      })()}
      {/* Inline generative-UI trees — agent-pushed declarative components.
          surface:"panel" specs render as a compact open-chip (the immersive
          view lives in the side panel); specs carrying a request_id route
          interactions through the blocking HITL resume path. */}
      {genUiEvents.length > 0 && (
        <EntityIndexContext.Provider value={entityIndex}>
        <div className="mt-3 space-y-2">
          {genUiEvents.map((spec, i) => {
            const rec = (spec && typeof spec === "object"
              ? spec : {}) as Record<string, unknown>;
            const requestId =
              typeof rec.request_id === "string" ? rec.request_id : null;
            const act = (msg: string) => {
              if (requestId && onHitlRespond) onHitlRespond(requestId, msg);
              else onChoice?.(msg);
            };
            if (rec.surface === "panel") {
              const title = typeof rec.title === "string" && rec.title
                ? rec.title : "Interactive view";
              return (
                <button
                  key={i}
                  type="button"
                  onClick={() => openGenUI({
                    id: `${message.id}:${i}`,
                    title,
                    sessionId,
                    spec,
                  })}
                  className="flex items-center gap-2 rounded-lg border border-border/60 bg-card/50 px-3 py-2 text-xs text-foreground hover:bg-secondary/60 transition-colors"
                >
                  <Icon name="AppWindow" size={13} className="text-primary" />
                  <span className="font-medium">{title}</span>
                  <span className="text-muted-foreground">
                    — open in side panel
                  </span>
                </button>
              );
            }
            return <GenerativeUINode key={i} spec={spec} onAction={act} />;
          })}
        </div>
        </EntityIndexContext.Provider>
      )}
      {/* Inline email-assistant cards (editable draft, rule disable/delete).
          Inert unless the message contains email-assistant tool calls, so this
          renders in both the chat app and the email app. */}
      <EmailToolCards
        toolEvents={dedupedToolEvents}
        accountId={emailContext?.accountId}
        emailId={emailContext?.emailId}
      />
      {/* Inline task-manager cards (clickable task lists, plan Apply, action
          confirmations). Inert unless the message contains my_tasks_* tool calls,
          so this renders in both the chat app and the Tasks assistant rail. */}
      <TaskToolCards toolEvents={dedupedToolEvents} />
      {/* Inline projects-assistant cards (task lists that open in Projects,
          titled reads). Inert unless the message contains skill-projects tool
          calls, so this renders in both the chat app and the Projects app's
          AI chat (WS-27bm). */}
      <ProjectToolCards toolEvents={dedupedToolEvents} />
      <GenerativeUIPanel
        agentState={message.agentState}
        customEvents={message.customEvents}
      />
      {!message.streaming && (
        <div className="flex items-center gap-2 mt-1.5 opacity-100 transition-opacity">
          {message.content.trim() && (
            <MessageActionBar
              content={message.content}
              messageId={message.id}
              role="assistant"
              sessionId={sessionId}
              onRetry={onRetryMessage ? () => onRetryMessage(message) : undefined}
            />
          )}
          <div className="text-[10px] text-muted-foreground">{timestamp}</div>
        </div>
      )}
    </div>
  );
}

// Memoised: the message store updates immutably (every change yields a NEW
// message object), so a reference check on `message` re-renders exactly the
// messages that changed and skips the rest — without this, every streamed token
// re-ran ReactMarkdown for every message in the thread. Callbacks are stable
// (the parent useCallback's them), so comparing their identity is safe.
//
// Authorship rides on `message`, so the reference check above already catches a
// turn that gains an author or becomes redacted (the store replaces the object).
// What it does NOT catch is the room changing around a message that didn't:
// the reader's own identity, and the participant list a name and avatar are
// looked up in — both compared here, the latter field-wise because callers
// commonly derive the array inline and a fresh array every render would defeat
// the memo entirely.
function sameParticipants(
  a?: BubbleParticipant[],
  b?: BubbleParticipant[],
): boolean {
  if (a === b) return true;
  if (!a || !b || a.length !== b.length) return false;
  return a.every(
    (p, i) =>
      p.subject === b[i].subject &&
      p.displayName === b[i].displayName &&
      p.avatarUrl === b[i].avatarUrl,
  );
}

export default React.memo(MessageBubble, (a, b) =>
  a.message === b.message &&
  a.sessionId === b.sessionId &&
  a.onChoice === b.onChoice &&
  a.onResend === b.onResend &&
  a.onRetryMessage === b.onRetryMessage &&
  a.onFileOpen === b.onFileOpen &&
  a.emailContext?.accountId === b.emailContext?.accountId &&
  a.emailContext?.emailId === b.emailContext?.emailId &&
  a.viewerEmail === b.viewerEmail &&
  a.sessionAgentName === b.sessionAgentName &&
  a.entityPills === b.entityPills &&
  sameParticipants(a.participants, b.participants),
);
