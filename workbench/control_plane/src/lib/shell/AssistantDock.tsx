"use client";

/**
 * THE one assistant (D99, `navigation_shell.md` §7.1, NS-6 slice 6b, flag
 * `NEXT_PUBLIC_SHELL_CHAT`). This is "the one dock" of `seams.test.ts`: the
 * only file in `src/lib/shell/` that mounts `AgentChat`.
 *
 * **One element, two forms.** `dock` is a panel at the right of the page.
 * `full` covers the page column, with the session list at the left and the
 * chat's files at the right. The element changes its classes, not its
 * place in the tree, so `AgentChat` never remounts on a switch: a running
 * reply, the draft and the scroll stay. On a phone it is a full-screen sheet.
 *
 * **Sessions, as Claude Code keeps them** (owner, 2026-10-12). One list:
 * start a new chat, reopen one, rename it, archive it. The side panel and
 * the phone open the list on demand. The full view shows it as a column.
 * An archived chat keeps its files, and it waits under "Archived chats".
 *
 * **One agent.** A new chat goes to `orchestrator`. An old chat opens with
 * the agent it recorded, so a specialist chat from before D99 still opens.
 *
 * **The context.** Each turn carries `ASSISTANT_PERSONA` and the page's
 * context (`surfaceContext`): the app in view and its facts. The member sets
 * nothing (owner, 2026-10-12).
 *
 * **The app switch** (owner, 2026-10-12). When the live run of the open chat
 * calls the specialist of another app the member holds, the page moves to
 * that app, once per run and per app. A full view on a wide screen becomes
 * the side panel for that moment, so the member sees the app. A toast offers
 * the way back.
 *
 * Fences (R7): `assistant.test.ts`, `chatHistory.test.ts`, `seams.test.ts`.
 */

import { usePathname, useRouter } from "next/navigation";
import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { useSession } from "next-auth/react";

import { useAccess } from "@/components/AccessProvider";
import AgentChat from "@/components/AgentChat";
import ArtifactSidebar, { type FileEntry } from "@/components/ArtifactSidebar";
import ArtifactViewerModal from "@/components/ArtifactViewerModal";
import Icon from "@/components/Icon";
import Button from "@/components/ui/Button";
import { Input } from "@/components/ui/Input";
import { useToast } from "@/components/ui/Toast";
import type { ArtifactEntry } from "@/hooks/useAgentChat";
import { useChatMemories } from "@/hooks/useChatMemories";
import { useChatScope, useRestoredSessionGuard } from "@/hooks/useChatSessions";
import { useAgentEvents } from "@/lib/agentEvents";
import { visibleSections } from "@/lib/nav";
import { SidePanelFitContext } from "@/lib/sidePanelFit";
import {
  NO_PICK,
  carriedText,
  findSession,
  recoverRefused,
  recoveredNotice,
  startFresh,
  type RailPick,
} from "@/lib/railSessions";
import { CHAT_GONE_NOTICE } from "@/lib/runActivity";
import {
  deleteSession,
  enrichSession,
  fetchAndMergeSessionsFromDb,
  getSessions,
  renameSession,
  setSessionArchived,
  type ChatSession,
} from "@/lib/sessions";

import {
  ASSISTANT_AGENT,
  ASSISTANT_NAME,
  ASSISTANT_PERSONA,
  DOCK_MAX,
  DOCK_MIN,
  closeAssistant,
  consumeAsk,
  consumeDraft,
  effectiveMode,
  focusAssistant,
  openAssistant,
  paneAt,
  setAssistantMode,
  setAssistantSession,
  setDockWidth,
  barMayJoin,
  surfaceContext,
  switchTarget,
  useAssistant,
  useCurrentFacts,
  useViewportWidth,
  type AssistantMode,
} from "./assistant";
import { chatFilesHref, mergeFileEntry, sessionTitle } from "./chatHistory";
import { SessionPicker } from "./SessionPicker";

/** How long a first visit waits for the server's list before it starts a chat. */
const MERGE_WAIT_MS = 3_000;

// ── The chat to show ─────────────────────────────────────────────────────────

/**
 * Which chat the assistant shows, and the recovery when the server refuses a
 * restored one. The store's `sessionId` is the one answer. This hook fills it
 * when it is empty: the newest chat of the one agent while it is recent, else
 * a new chat (`startsFresh`). `freshSeq` asks for a new chat.
 */
function useAssistantChat(open: boolean) {
  const st = useAssistant();
  const scope = useChatScope();
  const [sessions, setSessions] = useState<ChatSession[]>([]);
  const [restoredId, setRestoredId] = useState<string | null>(null);
  const [notice, setNotice] = useState<string | null>(null);
  const [recoveredInput, setRecoveredInput] = useState<string | undefined>();
  // From 0, not from the store: a `/chat?do=new-chat` that asked for a new
  // chat before this mounted must still be honoured (review of 2026-10-12).
  const handledFresh = useRef(0);
  const refresh = useCallback(() => setSessions(getSessions()), []);

  /* eslint-disable react-hooks/set-state-in-effect */
  // A new member or org: nothing carries over.
  useEffect(() => {
    setRestoredId(null);
    setNotice(null);
    refresh();
  }, [scope, refresh]);

  // The server's list, each time the assistant opens.
  useEffect(() => {
    if (!scope || !open) return;
    let cancelled = false;
    fetchAndMergeSessionsFromDb()
      .then((merged) => { if (!cancelled) setSessions(merged); })
      .catch(() => {});
    return () => { cancelled = true; };
  }, [scope, open]);

  // Fill an empty choice, or honour a request for a new chat.
  useEffect(() => {
    if (!scope || !open) return;
    if (st.freshSeq !== handledFresh.current) {
      handledFresh.current = st.freshSeq;
      const pick = startFresh(ASSISTANT_AGENT);
      setRestoredId(null);
      setNotice(null);
      setAssistantSession(pick.activeId);
      refresh();
      return;
    }
    if (st.sessionId) return;
    let cancelled = false;
    const choose = () => {
      if (cancelled) return;
      // The newest of the member's own private chats with the one agent. A
      // room or an archived chat is never chosen for them (`barMayJoin`).
      const last = getSessions()
        .filter((s) => s.agentName === ASSISTANT_AGENT && !s.archivedAt && s.isOwner !== false && (s.participantCount ?? 0) <= 1)
        .sort((a, b) => (Date.parse(b.updatedAt) || 0) - (Date.parse(a.updatedAt) || 0))[0];
      if (last && barMayJoin(last, Date.now())) {
        setRestoredId(last.id);
        setAssistantSession(last.id);
      } else {
        setRestoredId(null);
        setAssistantSession(startFresh(ASSISTANT_AGENT).activeId);
      }
      refresh();
    };
    if (getSessions().length > 0) {
      choose();
    } else {
      // A first visit on this browser waits a little for the server's list,
      // so it does not start an empty chat while the member has chats.
      const timer = setTimeout(choose, MERGE_WAIT_MS);
      fetchAndMergeSessionsFromDb().then(choose, choose).finally(() => clearTimeout(timer));
    }
    return () => { cancelled = true; };
  }, [scope, open, st.sessionId, st.freshSeq, refresh]);

  // The bar's words join the open chat only while it is recent. Each ask is
  // decided once, and `AgentChat` gets the words only for the chat the
  // decision named, so an old chat never sends them before the switch.
  const [decided, setDecided] = useState<{ seq: number; sessionId: string } | null>(null);
  useEffect(() => {
    const ask = st.ask;
    if (!open || !ask || !st.sessionId || decided?.seq === ask.seq) return;
    const cur = getSessions().find((s) => s.id === st.sessionId);
    // An empty chat of the member's own takes the words. Any other chat
    // takes them only if the bar may join it: never a room, never an
    // archived chat, never a stale one.
    const ownEmpty = cur && !cur.messageCount && !cur.archivedAt && cur.isOwner !== false;
    if (!ownEmpty && !barMayJoin(cur, Date.now())) {
      openAssistant({ fresh: true });
      return;
    }
    setDecided({ seq: ask.seq, sessionId: st.sessionId });
  }, [open, st.ask, st.sessionId, decided]);

  // A link named a chat this browser has not seen: look for it once.
  useEffect(() => {
    const id = st.sessionId;
    if (!scope || !open || !id || getSessions().some((s) => s.id === id)) return;
    let cancelled = false;
    void findSession(id).then((found) => {
      if (cancelled) return;
      if (found) {
        refresh();
        return;
      }
      setNotice(CHAT_GONE_NOTICE);
      setAssistantSession(null);
    });
    return () => { cancelled = true; };
  }, [scope, open, st.sessionId, refresh]);
  /* eslint-enable react-hooks/set-state-in-effect */

  const activeId = st.sessionId ?? "";
  const recover = useCallback(
    (refusedId: string, pendingText?: string): boolean => {
      const pick: RailPick = activeId ? { activeId, restoredId } : NO_PICK;
      const next = recoverRefused(pick, refusedId, ASSISTANT_AGENT);
      if (!next) return false;
      const carried = carriedText(refusedId, pendingText);
      setRestoredId(null);
      setAssistantSession(next.activeId);
      setRecoveredInput(carried);
      setNotice(recoveredNotice(carried));
      refresh();
      return true;
    },
    [activeId, restoredId, refresh],
  );
  const onSessionRefused = useRestoredSessionGuard(activeId, restoredId, recover);

  const active = useMemo(
    () => sessions.find((s) => s.id === activeId) ?? null,
    [sessions, activeId],
  );

  const sendable =
    st.ask && decided?.seq === st.ask.seq && decided.sessionId === activeId ? st.ask : null;

  return {
    sessions,
    active,
    activeId,
    sendable,
    refresh,
    onSessionRefused,
    notice: notice ? { text: notice, onDismiss: () => setNotice(null) } : null,
    recoveredInput,
    consumeRecoveredInput: () => setRecoveredInput(undefined),
    /** Open a chat the member chose: a refusal of it is an error they see. */
    select: (id: string) => {
      setRestoredId(null);
      setNotice(null);
      setAssistantSession(id);
    },
    remove: (id: string) => {
      deleteSession(id);
      if (id === activeId) setAssistantSession(null);
      refresh();
    },
    rename: (id: string, title: string) => {
      if (renameSession(id, title)) refresh();
    },
    /**
     * Archive a chat, or restore it. Archiving the chat on screen opens a
     * new one, as Claude Code does, so the member never types into a chat
     * they just put away.
     */
    archive: (id: string, archived: boolean) => {
      if (!setSessionArchived(id, archived)) return;
      if (archived && id === activeId) openAssistant({ fresh: true });
      refresh();
    },
    activity: (info: { firstUserMessage?: string; lastPreview?: string; messageCount: number }) => {
      if (!activeId) return;
      enrichSession(activeId, info);
      refresh();
    },
  };
}

// ── The title ────────────────────────────────────────────────────────────────

function ChatTitle({ session }: { session: ChatSession | null }) {
  const title = session ? sessionTitle(session) : ASSISTANT_NAME;
  return (
    <h2 className="min-w-0 flex-1 truncate px-1 text-sm font-semibold text-foreground" title={title}>
      {title}
    </h2>
  );
}

// ── The panel's drag edge ────────────────────────────────────────────────────

function ResizeEdge({ width }: { width: number }) {
  const start = useRef<{ x: number; w: number } | null>(null);
  return (
    <div
      role="separator"
      aria-orientation="vertical"
      aria-label="Resize the assistant"
      aria-valuemin={DOCK_MIN}
      aria-valuemax={DOCK_MAX}
      aria-valuenow={width}
      tabIndex={0}
      onKeyDown={(e) => {
        if (e.key === "ArrowLeft") { e.preventDefault(); setDockWidth(width + 16); }
        if (e.key === "ArrowRight") { e.preventDefault(); setDockWidth(width - 16); }
      }}
      onPointerDown={(e) => {
        start.current = { x: e.clientX, w: width };
        (e.target as HTMLElement).setPointerCapture(e.pointerId);
      }}
      onPointerMove={(e) => {
        if (!start.current) return;
        // The panel is on the right, so a drag to the left widens it.
        setDockWidth(start.current.w + (start.current.x - e.clientX), false);
      }}
      onPointerUp={(e) => {
        if (!start.current) return;
        setDockWidth(start.current.w + (start.current.x - e.clientX), true);
        start.current = null;
      }}
      className="absolute inset-y-0 left-0 z-10 w-1 cursor-col-resize bg-transparent tech-transition hover:bg-primary/40 focus-visible:bg-primary/40 focus-visible:outline-none"
    />
  );
}

// ── The assistant ────────────────────────────────────────────────────────────

export default function AssistantDock({ phone = false }: { phone?: boolean }) {
  const st = useAssistant();
  // Mounted from the first open, then kept: a closed assistant keeps its
  // draft and its scroll for the next open.
  const [mounted, setMounted] = useState(false);
  useEffect(() => {
    // eslint-disable-next-line react-hooks/set-state-in-effect -- latch on first open
    if (st.open) setMounted(true);
  }, [st.open]);
  if (!mounted) return null;
  return <AssistantSurface phone={phone} />;
}

function AssistantSurface({ phone }: { phone: boolean }) {
  const st = useAssistant();
  const vw = useViewportWidth();
  const form: AssistantMode = effectiveMode(st.mode, vw, phone);
  const canDock = effectiveMode("dock", vw, phone) === "dock";
  const chat = useAssistantChat(st.open);
  const pathname = usePathname();
  const router = useRouter();
  const toast = useToast();
  const { access, loading } = useAccess();
  const sections = useMemo(
    () => visibleSections(loading ? null : access.features, access.is_admin),
    [loading, access.features, access.is_admin],
  );
  const here = paneAt(pathname, sections);
  const facts = useCurrentFacts();
  const { data: authSession } = useSession();
  const userId = authSession?.user?.email ?? null;
  const { memories: memoryObjs } = useChatMemories(userId);
  const memories = useMemo(() => memoryObjs.map((m) => m.memory).filter(Boolean), [memoryObjs]);
  const [listOpen, setListOpen] = useState(true);
  const [pickerOpen, setPickerOpen] = useState(false);
  const [filesOpen, setFilesOpen] = useState(false);
  const [viewerEntry, setViewerEntry] = useState<FileEntry | null>(null);
  const [fileUpdates, setFileUpdates] = useState<FileEntry[]>([]);
  const rootRef = useRef<HTMLElement>(null);

  const active = chat.active;
  const agent = active?.agentName ?? ASSISTANT_AGENT;
  const context = surfaceContext(here, facts);
  const persona = agent === ASSISTANT_AGENT ? `${ASSISTANT_PERSONA}\n\n${context}` : context;

  // A new chat's files start empty.
  useEffect(() => {
    // eslint-disable-next-line react-hooks/set-state-in-effect -- follows the chat
    setFileUpdates([]);
  }, [chat.activeId]);

  // The cursor goes to the composer each time the assistant is asked to open.
  useEffect(() => {
    if (!st.open) return;
    const t = setTimeout(() => {
      rootRef.current?.querySelector<HTMLTextAreaElement>("textarea")?.focus();
    }, 50);
    return () => clearTimeout(t);
  }, [st.open, st.focusSeq, chat.activeId]);

  // ── The app switch ──────────────────────────────────────────────────────
  const movedRef = useRef<Set<string>>(new Set());
  const liveRef = useRef({ open: st.open, activeId: chat.activeId, pathname, sections, form, canDock });
  useEffect(() => {
    liveRef.current = { open: st.open, activeId: chat.activeId, pathname, sections, form, canDock };
  }, [st.open, chat.activeId, pathname, sections, form, canDock]);
  useAgentEvents({
    onRunStarted: ({ threadId }) => {
      if (threadId === liveRef.current.activeId) movedRef.current = new Set();
    },
    onDelegate: ({ name, threadId }) => {
      const live = liveRef.current;
      // Only while the member watches the assistant. A closed assistant
      // keeps its chat mounted, and a page that moves under a member who is
      // writing in another app loses their place (review of 2026-10-12).
      if (!live.open || !threadId || threadId !== live.activeId) return;
      const target = switchTarget({ name, pathname: live.pathname, sections: live.sections, done: movedRef.current });
      if (!target) return;
      movedRef.current.add(target.href);
      const back = live.pathname ?? "/";
      router.push(target.href);
      // A full view on a wide screen hides the app it just opened.
      if (live.form === "full" && live.canDock) setAssistantMode("dock", false);
      toast.show({
        key: `assistant-switch-${target.href}`,
        variant: "success",
        title: `Opened ${target.label}`,
        description: `${ASSISTANT_NAME} is working in ${target.label}.`,
        action: { label: "Go back", onClick: () => router.push(back) },
      });
    },
  });

  const onArtifact = (entry: ArtifactEntry) => {
    setFileUpdates((prev) => mergeFileEntry(prev, entry, new Date().toISOString()));
  };

  const shape = !st.open
    ? "hidden"
    : phone
      ? "fixed inset-0 z-50 flex flex-col bg-background pt-safe pb-safe"
      : form === "full"
        ? "absolute inset-0 z-20 flex flex-col bg-background"
        : "relative flex h-full shrink-0 flex-col border-l border-border bg-card";

  // The full view on a desktop shows the chats as a column, as ChatGPT and
  // Claude do. The side panel and the phone open the same list on demand,
  // as Claude Code does in an editor.
  const columnForm = form === "full" && !phone;
  const pickerShown = columnForm ? listOpen : pickerOpen;
  const showFiles = columnForm && filesOpen && Boolean(chat.activeId);
  const pickerProps = {
    sessions: chat.sessions,
    activeId: chat.activeId,
    onSelect: (id: string) => {
      chat.select(id);
      setPickerOpen(false);
    },
    onRename: chat.rename,
    onArchive: chat.archive,
    onRemove: chat.remove,
  };

  return (
    <section
      ref={rootRef}
      data-assistant={st.open ? form : "closed"}
      aria-label={`${ASSISTANT_NAME} assistant`}
      className={shape}
      style={st.open && form === "dock" && !phone ? { width: st.width } : undefined}
    >
      {st.open && form === "dock" && !phone ? <ResizeEdge width={st.width} /> : null}

      <header className="flex h-11 shrink-0 items-center gap-1 border-b border-border px-2">
        <Button
          variant="ghost"
          size="icon-sm"
          icon="History"
          aria-label={pickerShown ? "Hide your chats" : "Your chats"}
          aria-expanded={pickerShown}
          title="Your chats"
          onClick={() => {
            if (columnForm) setListOpen((o) => !o);
            else setPickerOpen((o) => !o);
          }}
        />
        <ChatTitle session={active} />
        <Button
          variant="ghost"
          size="icon-sm"
          icon="SquarePen"
          aria-label="New chat"
          title="New chat"
          onClick={() => {
            setPickerOpen(false);
            openAssistant({ fresh: true });
          }}
        />
        {columnForm ? (
          <Button
            variant="ghost"
            size="icon-sm"
            icon="FolderOpen"
            aria-label={filesOpen ? "Hide this chat's files" : "Show this chat's files"}
            aria-pressed={filesOpen}
            title="Files"
            onClick={() => setFilesOpen((o) => !o)}
          />
        ) : chat.activeId ? (
          // The side panel and the phone have no room for a files column. The
          // chat's files open in Artifacts, and on a desktop the chat stays
          // open beside them (D99, NS-6 slice 6c).
          <Button
            variant="ghost"
            size="icon-sm"
            icon="FolderOpen"
            aria-label="Open this chat's files in Artifacts"
            title="Files"
            onClick={() => {
              if (phone) closeAssistant();
              router.push(chatFilesHref(chat.activeId));
            }}
          />
        ) : null}
        {canDock ? (
          <Button
            variant="ghost"
            size="icon-sm"
            icon={form === "full" ? "Minimize2" : "Maximize2"}
            aria-label={form === "full" ? "Show as a side panel" : "Show full screen"}
            title={form === "full" ? "Side panel" : "Full screen"}
            onClick={() => {
              setPickerOpen(false);
              setAssistantMode(form === "full" ? "dock" : "full");
              focusAssistant();
            }}
          />
        ) : null}
        <Button
          variant="ghost"
          size="icon-sm"
          icon="X"
          aria-label={`Close ${ASSISTANT_NAME}`}
          title="Close"
          onClick={closeAssistant}
        />
      </header>

      <div className="relative flex min-h-0 flex-1">
        {columnForm && listOpen ? (
          <SessionPicker variant="column" {...pickerProps} />
        ) : null}
        {!columnForm && pickerOpen ? (
          phone ? (
            <div className="absolute inset-0 z-30">
              <SessionPicker variant="sheet" autoFocus {...pickerProps} />
            </div>
          ) : (
            <>
              {/* A press outside the dropdown closes it. */}
              <div
                aria-hidden
                onClick={() => setPickerOpen(false)}
                className="absolute inset-0 z-20 bg-background/40"
              />
              <div
                className="absolute inset-x-2 top-2 z-30"
                onKeyDown={(e) => {
                  if (e.key === "Escape") {
                    e.stopPropagation();
                    setPickerOpen(false);
                  }
                }}
              >
                <SessionPicker variant="dropdown" autoFocus {...pickerProps} />
              </div>
            </>
          )
        ) : null}
        <div className="flex min-w-0 flex-1 flex-col">
          <div className={`flex min-h-0 w-full flex-1 flex-col ${form === "full" ? "mx-auto max-w-4xl" : ""}`}>
            {active ? (
              // No page under the assistant renders the side-panel editor
              // (Projects mounts it only with its own chat on, and the flag
              // turns that off). So a file card opens the chat's own viewer,
              // never a store that nothing draws (review of 2026-10-13).
              <SidePanelFitContext.Provider value={false}>
              <AgentChat
                key={active.id}
                agentName={agent}
                sessionId={active.id}
                visible={st.open}
                compact={form === "dock"}
                persona={persona}
                memories={memories}
                memoryUserId={userId ?? undefined}
                expectedMessageCount={active.messageCount}
                onActivity={chat.activity}
                onArtifact={onArtifact}
                onSessionRefused={chat.onSessionRefused}
                notice={chat.notice}
                pendingInput={chat.recoveredInput ?? st.draft?.text}
                onPendingInputConsumed={() => {
                  chat.consumeRecoveredInput();
                  consumeDraft();
                }}
                autoSend={chat.sendable}
                assistantName={agent === ASSISTANT_AGENT ? ASSISTANT_NAME : undefined}
                onAutoSendConsumed={consumeAsk}
              />
              </SidePanelFitContext.Provider>
            ) : (
              <div className="flex flex-1 items-center justify-center gap-2 text-sm text-muted-foreground" aria-busy="true">
                <Icon name="Loader2" size={16} className="animate-spin" />
                Opening your chat…
              </div>
            )}
          </div>
        </div>
        {showFiles ? (
          <ArtifactSidebar
            side="right"
            sessionId={chat.activeId}
            open
            onToggle={() => setFilesOpen(false)}
            onFileOpen={(entry) => setViewerEntry(entry)}
            artifactUpdates={fileUpdates}
          />
        ) : null}
      </div>

      {viewerEntry ? (
        <ArtifactViewerModal
          sessionId={chat.activeId}
          entry={viewerEntry}
          onClose={() => setViewerEntry(null)}
          onDelete={(entry) => {
            setViewerEntry(null);
            setFileUpdates((prev) => prev.filter((f) => f.path !== entry.path));
          }}
        />
      ) : null}
    </section>
  );
}
