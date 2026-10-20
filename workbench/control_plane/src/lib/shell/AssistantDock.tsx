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
 * **The peek strip** (CF-1, `chat_first_surface.md` §4.1, flag
 * `NEXT_PUBLIC_CHAT_FIRST`). On a phone the sheet can drop to a 56 px strip
 * above the bottom nav: the chevron, a drag down on the handle, a pill link,
 * or a delegation that moves the page. A tap on the strip opens the sheet.
 * The strip is the same element, and the chat stays laid out under it,
 * hidden and inert, so the scroll and the draft stay.
 *
 * **The motion** (owner, 2026-10-14). The panel opens and closes as the left
 * sidebar folds: the same tokens, from the right, and the page reflows
 * beside it (`lib/panelMotion.ts`). The switch to the full view and back is a
 * view transition: the panel's edge glides, and the two forms cross-fade. A
 * closed panel stays mounted, so a close never cuts a live run. Focus goes to
 * the composer on an open, and back to the opener or the toggle on a close.
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
 * that app, once per run and per app. A full view on a desktop becomes the
 * side panel for that moment, so the member sees the app. A toast offers
 * the way back.
 *
 * Fences (R7): `assistant.test.ts`, `chatHistory.test.ts`, `seams.test.ts`.
 */

import { usePathname, useRouter } from "next/navigation";
import { useCallback, useEffect, useMemo, useRef, useState, useSyncExternalStore } from "react";
import { useSession } from "next-auth/react";

import { useAccess } from "@/components/AccessProvider";
import AgentChat from "@/components/AgentChat";
import ArtifactSidebar, { type FileEntry } from "@/components/ArtifactSidebar";
import ArtifactViewerModal from "@/components/ArtifactViewerModal";
import Icon from "@/components/Icon";
import { useSteadyRunView } from "@/components/RunStatusRow";
import Button from "@/components/ui/Button";
import { PillNavigateContext } from "@/components/ui/EntityPill";
import { Input } from "@/components/ui/Input";
import { useToast } from "@/components/ui/Toast";
import type { ArtifactEntry } from "@/hooks/useAgentChat";
import { useThreadNeedsInput } from "@/hooks/useActiveSessions";
import { useChatMemories } from "@/hooks/useChatMemories";
import { useChatScope, useRestoredSessionGuard } from "@/hooks/useChatSessions";
import { useAgentEvents } from "@/lib/agentEvents";
import { getSessionState, subscribeSession } from "@/lib/chatStore";
import { visibleSections } from "@/lib/nav";
import {
  formDurationMs,
  isMoving,
  isShown,
  pickFocusReturn,
  swapPanelForm,
  usePanelPhase,
  type PanelForm,
} from "@/lib/panelMotion";
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
import { IDLE_RUN, runViewText, type RunStatus } from "@/lib/runStatus";
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
  dockFloats,
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
  onChatLink,
  onPageChange,
  openAssistant,
  paneAt,
  raiseOnNeed,
  setAssistantMode,
  setAssistantSession,
  setDockWidth,
  barMayJoin,
  stripText,
  surfaceContext,
  switchTarget,
  useAssistant,
  useCurrentFacts,
  useViewportWidth,
  type AssistantMode,
} from "./assistant";
import { chatFirstOn } from "./chatFlag";
import { chatFilesHref, mergeFileEntry, sessionTitle } from "./chatHistory";
import { SessionPicker } from "./SessionPicker";

/** How long a first visit waits for the server's list before it starts a chat. */
const MERGE_WAIT_MS = 3_000;
/** How many times, 50 ms apart, an open tries to put the cursor in the composer. */
const COMPOSER_FOCUS_TRIES = 40;

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
    // Asked while the member watched this chat: it goes in, however old the
    // chat is. Never into a room or an archived chat, for the reason above.
    const onScreen = Boolean(
      ask.join && cur && !cur.archivedAt && cur.isOwner !== false && (cur.participantCount ?? 0) <= 1,
    );
    if (!ownEmpty && !onScreen && !barMayJoin(cur, Date.now())) {
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

// ── The peek strip (CF-1) ────────────────────────────────────────────────────

/** The attribute on <html> that lifts the page and the toasts above the strip (globals.css). */
export const PEEK_ATTR = "data-assistant-peek";
/** A drag down on the sheet's handle longer than this drops it to the strip. */
export const PEEK_DRAG_PX = 48;

/** The run state of one chat, live. An empty id reads the idle state. */
function useChatRun(threadId: string): RunStatus {
  return useSyncExternalStore(
    (l) => (threadId ? subscribeSession(threadId, l) : () => {}),
    () => (threadId ? getSessionState(threadId).run ?? IDLE_RUN : IDLE_RUN),
    () => IDLE_RUN,
  );
}

/**
 * The strip's row: the mark, one line, the way up and the X. A tap anywhere
 * but the X opens the sheet. It mounts only while the strip shows, so its
 * clock and its watch on the card run only then.
 */
function PeekStrip({
  threadId,
  lastPreview,
  onOpen,
  onRaise,
}: {
  threadId: string;
  lastPreview: string | undefined;
  onOpen: () => void;
  onRaise: () => void;
}) {
  const run = useChatRun(threadId);
  // The status row's own view, with its no-flash rule, so the strip and the
  // chat say the same thing at the same moment.
  const view = useSteadyRunView(run);
  const line = stripText({ phase: run.phase, statusText: view ? runViewText(view) : null, lastPreview });

  // A card that waits for the member raises the sheet (WS-51 S2). The run
  // state of this tab and the server's shared list are the two existing
  // sources. Only a wait that BEGINS while the strip shows raises it.
  const serverNeeds = useThreadNeedsInput(threadId || null);
  const needsInput = serverNeeds || run.phase === "waiting_for_you" || run.phase === "parked";
  const wasNeeding = useRef(needsInput);
  useEffect(() => {
    const began = needsInput && !wasNeeding.current;
    wasNeeding.current = needsInput;
    if (began && raiseOnNeed({ phone: true, form: "peek", needsInput }) === "full") onRaise();
  }, [needsInput, onRaise]);

  const rowRef = useRef<HTMLDivElement>(null);
  // Focus was in the sheet, which is inert now. It goes to the way back up.
  useEffect(() => {
    const at = document.activeElement;
    if (!at || at === document.body || !at.isConnected || at.closest("[inert]")) {
      rowRef.current?.querySelector<HTMLElement>("[data-assistant-unpeek]")?.focus({ preventScroll: true });
    }
  }, []);

  return (
    <div
      ref={rowRef}
      data-assistant-strip=""
      onClick={onOpen}
      className="flex h-14 w-full cursor-pointer items-center gap-2 px-3"
    >
      <Icon name="Sparkles" size={18} className="shrink-0 text-primary" />
      <span data-assistant-strip-text="" className="min-w-0 flex-1 truncate text-sm text-foreground" title={line}>
        {line}
      </span>
      <Button
        data-assistant-unpeek=""
        variant="ghost"
        size="icon-sm"
        icon="ChevronUp"
        aria-label="Open the chat"
        title="Open"
        onClick={(e) => {
          e.stopPropagation();
          onOpen();
        }}
      />
      <Button
        variant="ghost"
        size="icon-sm"
        icon="X"
        aria-label={`Close ${ASSISTANT_NAME}`}
        title="Close"
        onClick={(e) => {
          e.stopPropagation();
          closeAssistant();
        }}
      />
    </div>
  );
}

/**
 * The sheet's drag handle, as the shell drawer draws it. A drag down longer
 * than `PEEK_DRAG_PX`, or a tap, drops the sheet to the strip. The header's
 * chevron is the way for a keyboard, so the handle stays out of the tab order.
 */
function SheetHandle({ onPeek }: { onPeek: () => void }) {
  const start = useRef<number | null>(null);
  const end = (y: number) => {
    if (start.current === null) return;
    const moved = y - start.current;
    start.current = null;
    // A tap, or a drag down. A drag up does nothing.
    if (moved > PEEK_DRAG_PX || Math.abs(moved) < 8) onPeek();
  };
  return (
    <div
      aria-hidden
      data-assistant-handle=""
      className="flex shrink-0 cursor-grab touch-none justify-center pt-2 pb-1"
      onPointerDown={(e) => {
        start.current = e.clientY;
        e.currentTarget.setPointerCapture?.(e.pointerId);
      }}
      onPointerUp={(e) => end(e.clientY)}
      onPointerCancel={() => {
        start.current = null;
      }}
    >
      <div className="w-10 h-1 rounded-full bg-muted-foreground/30" />
    </div>
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
  // Read once, as the other shell flags are. Off, a phone has no strip.
  const [chatFirst] = useState(() => chatFirstOn());
  const strip = phone && form === "peek";
  // Every desktop has the side panel. Below the floor it floats over the
  // page instead of sitting beside it (owner, 2026-10-14).
  const canDock = !phone;
  const floats = dockFloats(vw, phone);
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
  // The motion form: a phone draws the sheet or the strip.
  const motionForm: PanelForm = phone ? (form === "peek" ? "peek" : "sheet") : form;
  const { phase, settle } = usePanelPhase(st.open, formDurationMs(motionForm));
  const shown = isShown(phase);
  const moving = isMoving(phase);

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
  // It waits for the panel to be on screen: a closed panel is inert. ⚠️
  // `preventScroll`: the panel moves in from the edge, and a plain focus
  // would scroll the page to the composer mid-way.
  // The first open loads the chat, so the composer can come late, or come
  // disabled. So the effect tries again for a short while. It stops once
  // the composer has focus, or once the member puts focus elsewhere in the
  // panel.
  useEffect(() => {
    if (!st.open || !shown) return;
    let tries = 0;
    const t = setInterval(() => {
      const root = rootRef.current;
      const box = root?.querySelector<HTMLTextAreaElement>("textarea") ?? null;
      const at = document.activeElement;
      const elsewhere = Boolean(root && at && at !== box && root.contains(at));
      if (box && !box.disabled && !elsewhere) box.focus({ preventScroll: true });
      if (!root || elsewhere || document.activeElement === box || ++tries >= COMPOSER_FOCUS_TRIES) clearInterval(t);
    }, 50);
    return () => clearInterval(t);
  }, [st.open, shown, st.focusSeq, chat.activeId]);

  // ── Focus back on a close ───────────────────────────────────────────────
  // The opener is what had focus outside the panel when it opened: the
  // toggle, a page's button. A close that leaves focus inside the panel, or
  // nowhere, sends it back there, else to the toggle. The panel is hidden
  // once it closes, and focus left in it would fall to the page's top.
  const openerRef = useRef<HTMLElement | null>(null);
  useEffect(() => {
    const root = rootRef.current;
    const at = document.activeElement;
    if (st.open) {
      openerRef.current = at instanceof HTMLElement && at !== document.body && !root?.contains(at) ? at : null;
      return;
    }
    const lost = !at || at === document.body || Boolean(root?.contains(at));
    if (!lost) return;
    const target = pickFocusReturn<HTMLElement>(
      [
        openerRef.current,
        document.querySelector<HTMLElement>("[data-assistant-toggle]"),
        document.querySelector<HTMLElement>("[data-ask-tab]"),
      ],
      (el) => Boolean(root?.contains(el)),
    );
    target?.focus({ preventScroll: true });
  }, [st.open]);

  // ── A page opened under the full view ───────────────────────────────────
  // The full view covers the page column, so an app the member opens from
  // the sidebar would open out of sight. The view gives way to the side
  // panel, on every desktop (owner, 2026-10-13 and 2026-10-14). The first
  // page is the one the view opened over, so only a CHANGE counts.
  const lastPathRef = useRef(pathname);
  const pageRuleRef = useRef({ open: st.open, form, canDock, phone });
  useEffect(() => {
    pageRuleRef.current = { open: st.open, form, canDock, phone };
  }, [st.open, form, canDock, phone]);
  useEffect(() => {
    if (pathname === lastPathRef.current) return;
    lastPathRef.current = pathname;
    const act = onPageChange(pageRuleRef.current);
    if (act === "dock") setAssistantMode("dock");
    else if (act === "close") closeAssistant();
  }, [pathname]);
  // A task pill in the chat opens its page. On a phone the sheet covers it,
  // so the sheet drops to the strip with the flag on, and closes with it off
  // (`onChatLink`, review of PR #1156, CF-1).
  const beforePillNavigate = useCallback(() => {
    const act = onChatLink({ phone, chatFirst });
    if (act === "peek") setAssistantMode("peek");
    else if (act === "close") closeAssistant();
  }, [phone, chatFirst]);

  // ── The strip ───────────────────────────────────────────────────────────
  // The page's last row and the toasts move up while the strip shows.
  const stripShown = st.open && strip;
  useEffect(() => {
    if (!stripShown || typeof document === "undefined") return;
    document.documentElement.setAttribute(PEEK_ATTR, "");
    return () => document.documentElement.removeAttribute(PEEK_ATTR);
  }, [stripShown]);
  const toPeek = useCallback(() => {
    setPickerOpen(false);
    setAssistantMode("peek");
  }, []);
  // A tap on the strip: the sheet, with the cursor in the composer.
  const fromPeek = useCallback(() => {
    setAssistantMode("full");
    focusAssistant();
  }, []);
  // A card raises the sheet. The member answers it with a tap, so the
  // keyboard does not open.
  const raise = useCallback(() => setAssistantMode("full"), []);

  // ── The app switch ──────────────────────────────────────────────────────
  const movedRef = useRef<Set<string>>(new Set());
  const liveRef = useRef({ open: st.open, activeId: chat.activeId, pathname, sections, form, canDock, phone, chatFirst });
  useEffect(() => {
    liveRef.current = { open: st.open, activeId: chat.activeId, pathname, sections, form, canDock, phone, chatFirst };
  }, [st.open, chat.activeId, pathname, sections, form, canDock, phone, chatFirst]);
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
      // A full view hides the app it just opened.
      if (live.form === "full" && live.canDock) setAssistantMode("dock");
      // On a phone the sheet hides it too. With the flag on it drops to the
      // strip, so the member sees the app the run opened (CF-1). With the
      // flag off nothing changes: the sheet stays.
      if (onChatLink({ phone: live.phone, chatFirst: live.chatFirst }) === "peek") setAssistantMode("peek");
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

  // The shape of each form. The motion lives in `globals.css`
  // (`.assistant-panel`), keyed on the data attributes below: the panel
  // rides in from the right in the side form, fades in the full form, and
  // rises as the shell's drawer does on a phone.
  const shape = phone
    ? strip
      ? // The strip: a row on the bottom nav. The sheet's body stays under it.
        "assistant-peek-bottom fixed inset-x-0 z-40 overflow-hidden border-t border-border bg-card"
      : "fixed inset-0 z-50 flex flex-col bg-background pt-safe pb-safe"
    : form === "full"
      ? "absolute inset-0 z-20 flex flex-col bg-background"
      : floats
        ? // A narrow desktop: the same panel, over the right of the page.
          "absolute inset-y-0 right-0 z-20 flex max-w-[90%] flex-col border-l border-border bg-card shadow-xl"
        : // A wide desktop: the panel sits over the spacer, beside the page.
          "absolute inset-y-0 right-0 z-20 flex flex-col border-l border-border bg-card";

  // ⚠️ The spacer is what the page reflows around, as it does for the
  // sidebar's fold. It keeps the side panel's width under the full view too,
  // so the page behind does not reflow on a switch, and the view transition
  // has one page to show on either side.
  const spaced = !phone && !floats;

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
    <>
    {spaced ? (
      <div
        aria-hidden
        data-assistant-spacer=""
        data-moving={moving ? "" : undefined}
        className="assistant-spacer h-full shrink-0"
        style={{ width: shown ? st.width : 0 }}
        // The page grows or shrinks with no window `resize`, as for the fold
        // (`Sidebar.tsx`). Tell the layouts that measure on it.
        onTransitionEnd={(e) => {
          if (e.target === e.currentTarget && e.propertyName === "width") {
            window.dispatchEvent(new Event("resize"));
          }
        }}
      />
    ) : null}
    <section
      ref={rootRef}
      data-assistant={st.open ? form : "closed"}
      data-panel-form={motionForm}
      data-panel-phase={phase}
      data-shown={shown ? "" : undefined}
      data-moving={moving ? "" : undefined}
      aria-label={`${ASSISTANT_NAME} assistant`}
      inert={phase === "closed"}
      className={`assistant-panel ${shape}`}
      style={form === "dock" && !phone ? { width: st.width } : undefined}
      onTransitionEnd={(e) => {
        if (e.target === e.currentTarget && (e.propertyName === "transform" || e.propertyName === "opacity")) {
          settle();
        }
      }}
    >
      {st.open && form === "dock" && !phone ? <ResizeEdge width={st.width} /> : null}
      {/* Follow-up: the notes RecordingDock sits at this same height above the nav, and nobody has looked at the two together. */}
      {strip ? (
        <PeekStrip threadId={chat.activeId} lastPreview={active?.lastPreview} onOpen={fromPeek} onRaise={raise} />
      ) : null}

      {/* The sheet's body. Under the strip it stays laid out, hidden and
          inert, so the chat keeps its scroll and its draft. The wrapper is
          there in every form, so `AgentChat` never remounts. */}
      <div
        data-assistant-body=""
        inert={strip}
        className={strip ? "assistant-peek-body invisible absolute inset-x-0 flex flex-col" : "flex min-h-0 flex-1 flex-col"}
      >
      {phone && chatFirst ? <SheetHandle onPeek={toPeek} /> : null}
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
              const next = form === "full" ? "dock" : "full";
              // The same element changes its classes inside a view
              // transition, so the chat is never remounted.
              swapPanelForm(rootRef.current, () => setAssistantMode(next));
              focusAssistant();
            }}
          />
        ) : null}
        {phone && chatFirst ? (
          <Button
            variant="ghost"
            size="icon-sm"
            icon="ChevronDown"
            aria-label="Show as a strip"
            title="Minimize"
            onClick={toPeek}
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
          <div data-assistant-chat="" className="flex min-h-0 w-full flex-1 flex-col">
            {active ? (
              // No page under the assistant renders the side-panel editor
              // (Projects mounts it only with its own chat on, and the flag
              // turns that off). So a file card opens the chat's own viewer,
              // never a store that nothing draws (review of 2026-10-13).
              <PillNavigateContext.Provider value={beforePillNavigate}>
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
                assistantName={ASSISTANT_NAME}
                onAutoSendConsumed={consumeAsk}
              />
              </SidePanelFitContext.Provider>
              </PillNavigateContext.Provider>
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
      </div>

      {viewerEntry ? (
        <ArtifactViewerModal
          origin="workspace"
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
    </>
  );
}
