/**
 * runSignals — the signals of an assistant run outside its open chat
 * (WS-51 S5, `project-docs/specs/chat_run_continuity.md` §4 S5).
 *
 * Four signals, one source. Each reads the S1 store (`lib/liveRuns.ts`) and
 * the S2 `state`, plus this tab's own runs. Nothing here polls, and nothing
 * here adds a data path: every thread id comes from `/chat/active-sessions`,
 * which the server already filters for this member and org.
 *
 *   1. **Unread.** A run that leaves the live list while the member does not
 *      look at its chat marks that chat unread. Opening the chat clears it.
 *   2. **The toast.** One toast per finished run, "<Agent> finished · Open",
 *      never for the open chat.
 *   3. **The hidden tab.** The title and the favicon carry the count.
 *   4. **The phone pill.** The newest live run, with its step.
 *
 * **Where unread lives: in this browser, per member and org.** The map sits in
 * the member's own chat namespace (scope `<email>|<orgId>`, kind `unread`),
 * and `chatKey("unread")` builds its key. So it follows the rules of every
 * chat cache (PR #652): a switch of account or org reads another map and
 * deletes nothing, and a sign-out clears it with the rest of that account. The cost: a reply read on
 * the phone stays unread on the laptop. The server keeps no read state, and
 * a server `read_at` would need a table, an RLS policy and a migration for a
 * dot. The spec asks for none of that, so this is the smallest correct store.
 *
 * **What "looks at" means.** A chat is open while an `AgentChat` shows it
 * (`markChatOpen`). The member looks at it while it is open AND the tab is
 * visible. A run that ends in an open chat of a hidden tab marks it unread, so
 * the hidden tab's title can say "New reply", and no toast shows for it.
 * The tab shows again with the chat open, and that clears it.
 *
 * Fence (R7): `runSignals.test.ts`.
 */

import { agentLabel, type ActivityRow } from "@/lib/runActivity";
import { UNREAD_EVENT, chatKey } from "@/lib/sessions";

// ── The unread map ───────────────────────────────────────────────────────────

/** One chat with a reply the member has not read. */
export type UnreadEntry = {
  /** When the run ended, epoch ms. */
  at: number;
  /** The agent's slug, for the dot's app and the toast's name. */
  agent: string;
  /** The chat's title when the run ended, or null. */
  title: string | null;
  /** The run, by its start time. Two tabs that see one run end agree on it. */
  run: string | null;
  /** A toast already showed for this run, in some tab of this browser. */
  toasted: boolean;
};

export type UnreadMap = Readonly<Record<string, UnreadEntry>>;

/** The most chats the map keeps. The oldest go first. */
export const UNREAD_MAX = 50;
/** An unread chat that nobody opens for this long leaves the map. */
export const UNREAD_TTL_MS = 7 * 24 * 60 * 60 * 1000;

const EMPTY_MAP: UnreadMap = Object.freeze({});

/** The map from its stored text. A bad entry is dropped, never trusted. */
export function parseUnread(raw: string | null): UnreadMap {
  if (!raw) return EMPTY_MAP;
  let data: unknown;
  try {
    data = JSON.parse(raw);
  } catch {
    return EMPTY_MAP;
  }
  if (!data || typeof data !== "object" || Array.isArray(data)) return EMPTY_MAP;
  const out: Record<string, UnreadEntry> = {};
  for (const [id, v] of Object.entries(data as Record<string, unknown>)) {
    const e = v as Partial<UnreadEntry> | null;
    if (!id || !e || typeof e !== "object" || typeof e.at !== "number" || !Number.isFinite(e.at)) continue;
    out[id] = {
      at: e.at,
      agent: typeof e.agent === "string" && e.agent ? e.agent : "unknown",
      title: typeof e.title === "string" ? e.title : null,
      run: typeof e.run === "string" ? e.run : null,
      toasted: e.toasted === true,
    };
  }
  return Object.keys(out).length ? Object.freeze(out) : EMPTY_MAP;
}

/** Keep the map small: drop what is too old, then keep the newest. */
export function pruneUnread(map: UnreadMap, now: number): Record<string, UnreadEntry> {
  const kept = Object.entries(map)
    .filter(([, e]) => now - e.at < UNREAD_TTL_MS)
    .sort(([, a], [, b]) => b.at - a.at)
    .slice(0, UNREAD_MAX);
  return Object.fromEntries(kept);
}

let _cacheKey: string | null = null;
let _cacheRaw: string | null = null;
let _cache: UnreadMap = EMPTY_MAP;

function _storageOn(): boolean {
  return typeof window !== "undefined" && typeof localStorage !== "undefined";
}

/**
 * The bound member's unread map. A stable reference while the stored text is
 * the same, so `useSyncExternalStore` can read it. Empty while no member
 * scope is bound: an unknown viewer reads nobody's map.
 */
export function getUnread(): UnreadMap {
  const key = chatKey("unread");
  if (!key || !_storageOn()) return EMPTY_MAP;
  let raw: string | null = null;
  try {
    raw = localStorage.getItem(key);
  } catch {
    raw = null;
  }
  if (key === _cacheKey && raw === _cacheRaw) return _cache;
  _cacheKey = key;
  _cacheRaw = raw;
  _cache = parseUnread(raw);
  return _cache;
}

/** The server snapshot for `useSyncExternalStore`: always empty. */
export function getServerUnread(): UnreadMap {
  return EMPTY_MAP;
}

const _unreadListeners = new Set<() => void>();

function _notifyUnread(): void {
  _unreadListeners.forEach((l) => l());
}

function _writeUnread(map: UnreadMap, now: number): void {
  const key = chatKey("unread");
  if (!key || !_storageOn()) return;
  const next = pruneUnread(map, now);
  try {
    if (Object.keys(next).length === 0) localStorage.removeItem(key);
    else localStorage.setItem(key, JSON.stringify(next));
  } catch {
    /* storage full or off: the dot is lost, and nothing breaks */
  }
  _notifyUnread();
}

/** Subscribe to the unread map: this tab's writes, and another tab's. */
export function subscribeUnread(listener: () => void): () => void {
  _unreadListeners.add(listener);
  const canListen = typeof window !== "undefined" && typeof window.addEventListener === "function";
  const onEvent = () => listener();
  if (canListen) {
    window.addEventListener("storage", onEvent);
    window.addEventListener(UNREAD_EVENT, onEvent);
  }
  return () => {
    _unreadListeners.delete(listener);
    if (canListen) {
      window.removeEventListener("storage", onEvent);
      window.removeEventListener(UNREAD_EVENT, onEvent);
    }
  };
}

/** The member opened this chat: it is read. True when it was unread. */
export function clearUnread(threadId: string, now: number = Date.now()): boolean {
  const cur = getUnread();
  if (!(threadId in cur)) return false;
  const next: Record<string, UnreadEntry> = { ...cur };
  delete next[threadId];
  _writeUnread(next, now);
  return true;
}

// ── Which chats are open ─────────────────────────────────────────────────────
//
// An `AgentChat` that shows a thread holds it open. A count per thread, so two
// views of one chat (a rail and /chat in one tab) release it only when both go.

const _open = new Map<string, number>();
let _openSnapshot: ReadonlySet<string> = new Set();
const _openListeners = new Set<() => void>();

function _publishOpen(): void {
  _openSnapshot = new Set(_open.keys());
  _openListeners.forEach((l) => l());
}

function _tabVisible(): boolean {
  return typeof document === "undefined" || document.visibilityState !== "hidden";
}

/**
 * Hold a chat open while it shows. Opening it clears its unread mark when the
 * tab is visible. Returns the release.
 */
export function markChatOpen(threadId: string): () => void {
  if (!threadId) return () => {};
  _open.set(threadId, (_open.get(threadId) ?? 0) + 1);
  if (_open.get(threadId) === 1) _publishOpen();
  if (_tabVisible()) clearUnread(threadId);
  let released = false;
  return () => {
    if (released) return;
    released = true;
    const n = (_open.get(threadId) ?? 1) - 1;
    if (n > 0) {
      _open.set(threadId, n);
    } else {
      _open.delete(threadId);
      _publishOpen();
    }
  };
}

export function isChatOpen(threadId: string): boolean {
  return _open.has(threadId);
}

/** The open chats. A stable reference while unchanged. */
export function getOpenChats(): ReadonlySet<string> {
  return _openSnapshot;
}

const NO_OPEN: ReadonlySet<string> = new Set();
export function getServerOpenChats(): ReadonlySet<string> {
  return NO_OPEN;
}

export function subscribeOpenChats(listener: () => void): () => void {
  _openListeners.add(listener);
  return () => {
    _openListeners.delete(listener);
  };
}

// ── Finished runs ────────────────────────────────────────────────────────────

/** A run as the tracker saw it in the last list. */
export type RunSeen = {
  threadId: string;
  agentName: string;
  title: string | null;
  /** The run's start time from the server, or null for a run of this tab. */
  run: string | null;
};

/** A run that ended, for the toast. */
export type FinishedRun = { threadId: string; agent: string; title: string | null };

/**
 * The runs that left the list. A run counts only when the server listed it
 * once. A run that only this tab knew can drop out of the tab's set when its
 * view unmounts, while it still runs, and that is no finish.
 */
export function leftRuns(
  prev: ReadonlyMap<string, RunSeen>,
  next: ReadonlyMap<string, RunSeen>,
  seenOnServer: ReadonlySet<string>,
): RunSeen[] {
  const out: RunSeen[] = [];
  for (const [id, run] of prev) {
    if (!next.has(id) && seenOnServer.has(id)) out.push(run);
  }
  return out;
}

let _gen: number | null = null;
let _prev = new Map<string, RunSeen>();
const _seenOnServer = new Set<string>();
/** Runs that ended while the tab was hidden. Their toast waits for the tab. */
const _toastQueue = new Map<string, FinishedRun>();

/** The part of a server row that the tracker reads. */
export type ObservedRun = {
  threadId: string;
  agentName?: string | null;
  title?: string | null;
  startedAt?: string | null;
};

export type ObserveInput = {
  /** `liveRunsGeneration()`: a new member resets the tracker. */
  generation: number;
  server: readonly ObservedRun[];
  localIds: Iterable<string>;
  localAgent: (threadId: string) => string | undefined;
  localTitle: (threadId: string) => string | null | undefined;
  /** The tab is visible. */
  visible: boolean;
  now: number;
};

/**
 * Compare this list with the last one. Each run that ended marks its chat
 * unread, unless the member looks at that chat now. Returns the runs to toast
 * now. A run of a hidden tab waits for `onTabVisible`.
 */
export function observeRuns(input: ObserveInput): FinishedRun[] {
  if (input.generation !== _gen) {
    // A new member: the list emptied because the member changed, not because
    // every run ended. Start again, and finish nothing.
    _gen = input.generation;
    _prev = new Map();
    _seenOnServer.clear();
    _toastQueue.clear();
  }
  const next = new Map<string, RunSeen>();
  for (const r of input.server) {
    if (next.has(r.threadId)) continue;
    _seenOnServer.add(r.threadId);
    const named = r.agentName && r.agentName !== "unknown" ? r.agentName : input.localAgent(r.threadId);
    next.set(r.threadId, {
      threadId: r.threadId,
      agentName: named ?? r.agentName ?? "unknown",
      title: r.title ?? input.localTitle(r.threadId) ?? null,
      run: r.startedAt ?? null,
    });
  }
  for (const id of input.localIds) {
    if (next.has(id)) continue;
    const before = _prev.get(id);
    next.set(id, {
      threadId: id,
      agentName: input.localAgent(id) ?? before?.agentName ?? "unknown",
      title: input.localTitle(id) ?? before?.title ?? null,
      run: before?.run ?? null,
    });
  }
  const left = leftRuns(_prev, next, _seenOnServer);
  _prev = next;
  for (const run of left) _seenOnServer.delete(run.threadId);
  return _settle(left, input.visible, input.now);
}

function _settle(left: readonly RunSeen[], visible: boolean, now: number): FinishedRun[] {
  if (left.length === 0 || !chatKey("unread")) return [];
  const map: Record<string, UnreadEntry> = { ...getUnread() };
  const toasts: FinishedRun[] = [];
  for (const run of left) {
    const id = run.threadId;
    const open = isChatOpen(id);
    if (open && visible) {
      // The member watched it end. It is read.
      delete map[id];
      continue;
    }
    const finished: FinishedRun = { threadId: id, agent: run.agentName, title: run.title };
    // Another tab of this browser saw the same run end, and showed its toast.
    const sameRun = map[id] && run.run !== null && map[id].run === run.run;
    const alreadyToasted = Boolean(sameRun && map[id].toasted);
    const toastNow = !open && visible && !alreadyToasted;
    map[id] = {
      at: sameRun ? map[id].at : now,
      agent: run.agentName,
      title: run.title,
      run: run.run,
      // The open chat never gets a toast (it is the member's own chat).
      toasted: open || alreadyToasted || toastNow,
    };
    if (toastNow) toasts.push(finished);
    else if (!open && !alreadyToasted) _toastQueue.set(id, finished);
  }
  _writeUnread(map, now);
  return toasts;
}

/**
 * The tab shows again. The open chats are read now. A run that ended while the
 * tab was hidden gets its one toast, unless another tab showed it or the
 * member opened the chat since. Returns the runs to toast.
 */
export function onTabVisible(now: number = Date.now()): FinishedRun[] {
  for (const id of _open.keys()) clearUnread(id, now);
  if (_toastQueue.size === 0) return [];
  const cur = getUnread();
  const toasts: FinishedRun[] = [];
  for (const [id, run] of _toastQueue) {
    const e = cur[id];
    if (e && !e.toasted && !isChatOpen(id)) toasts.push(run);
  }
  _toastQueue.clear();
  if (toasts.length) {
    const next: Record<string, UnreadEntry> = { ...cur };
    for (const t of toasts) next[t.threadId] = { ...next[t.threadId], toasted: true };
    _writeUnread(next, now);
  }
  return toasts;
}

/** The toast of a finished run: "<Agent> finished", the chat title, and Open. */
export function finishedToast(run: FinishedRun): { key: string; title: string; description?: string; actionLabel: string } {
  const title = run.title?.trim();
  return {
    key: `run-finished:${run.threadId}`,
    title: `${agentLabel(run.agent)} finished`,
    description: title || undefined,
    actionLabel: "Open",
  };
}

// ── The hidden tab ───────────────────────────────────────────────────────────

/** The product name in the tab title. */
export const TAB_BRAND = "Metorite";

/**
 * The hidden tab's title, or null for the page's own. "Needs you" wins over
 * "New reply", the order of `runBadge`.
 */
export function attentionTitle(needsInput: number, unread: number): string | null {
  if (needsInput > 0) return `(${needsInput}) Needs you · ${TAB_BRAND}`;
  if (unread > 0) return `(${unread}) New reply · ${TAB_BRAND}`;
  return null;
}

/**
 * The favicon variants with a dot, made once and kept in `public/`. A swap of
 * the link costs nothing. A canvas drawn on each poll would cost a paint.
 */
export const FAVICON_NEEDS = "/favicon-needs.png";
export const FAVICON_REPLY = "/favicon-reply.png";

export function attentionFavicon(needsInput: number, unread: number): string | null {
  if (needsInput > 0) return FAVICON_NEEDS;
  if (unread > 0) return FAVICON_REPLY;
  return null;
}

/** The part of a `<link>` that the swap touches. */
export type IconLink = {
  getAttribute(name: string): string | null;
  setAttribute(name: string, value: string): void;
  remove?: () => void;
};

/** The part of `document` that the tab signal touches. A test passes a fake. */
export type TabDocument = {
  title: string;
  visibilityState: string;
  querySelectorAll(selector: string): ArrayLike<IconLink>;
  createElement(tag: "link"): IconLink;
  head: { appendChild(node: IconLink): unknown };
};

let _savedTitle: string | null = null;
let _savedIcons: { el: IconLink; href: string | null; type: string | null }[] | null = null;
let _addedIcon: IconLink | null = null;

/**
 * Set or restore the title and the favicon. A hidden tab with something for
 * the member shows the count and the dot. A visible tab, or a quiet one, gets
 * back exactly what it had. Idempotent, so every poll may call it.
 */
export function applyTabSignal(doc: TabDocument, needsInput: number, unread: number): void {
  const hidden = doc.visibilityState === "hidden";
  const title = hidden ? attentionTitle(needsInput, unread) : null;
  const icon = hidden ? attentionFavicon(needsInput, unread) : null;

  if (title) {
    if (_savedTitle === null) _savedTitle = doc.title;
    if (doc.title !== title) doc.title = title;
  } else if (_savedTitle !== null) {
    doc.title = _savedTitle;
    _savedTitle = null;
  }

  if (icon) {
    if (_savedIcons === null) {
      const links = Array.from(doc.querySelectorAll('link[rel~="icon"]'));
      _savedIcons = links.map((el) => ({ el, href: el.getAttribute("href"), type: el.getAttribute("type") }));
      if (links.length === 0) {
        _addedIcon = doc.createElement("link");
        _addedIcon.setAttribute("rel", "icon");
        doc.head.appendChild(_addedIcon);
      }
    }
    const targets = _addedIcon ? [_addedIcon] : _savedIcons.map((s) => s.el);
    for (const el of targets) {
      if (el.getAttribute("href") !== icon) el.setAttribute("href", icon);
      if (el.getAttribute("type") !== "image/png") el.setAttribute("type", "image/png");
    }
  } else if (_savedIcons !== null) {
    for (const { el, href, type } of _savedIcons) {
      if (href !== null) el.setAttribute("href", href);
      if (type !== null) el.setAttribute("type", type);
    }
    _addedIcon?.remove?.();
    _addedIcon = null;
    _savedIcons = null;
  }
}

// ── The phone pill ───────────────────────────────────────────────────────────

/** The pill's words when the server sends no step. */
export const PILL_WORKING = "Working";

/** The pill's text: "<Agent> · <step or Working>", or the ask. */
export function pillText(row: Pick<ActivityRow, "agentName" | "state" | "lastStep">): string {
  const what = row.state === "needs_input" ? "Needs your answer" : row.lastStep?.trim() || PILL_WORKING;
  return `${agentLabel(row.agentName)} · ${what}`;
}

export type PillModel = { row: ActivityRow; more: number; text: string };

/**
 * What the pill shows: the newest live run that is not an open chat, and how
 * many more run. `rows` come newest first (`activityRows`). Null hides it.
 */
export function pillModel(rows: readonly ActivityRow[], open: ReadonlySet<string>): PillModel | null {
  const live = rows.filter((r) => r.state !== "new_reply" && !open.has(r.threadId));
  if (live.length === 0) return null;
  const row = live[0];
  return { row, more: live.length - 1, text: pillText(row) };
}

// ── Tests only ───────────────────────────────────────────────────────────────

/** Tests only: forget the tracker, the open chats and the saved tab state. */
export function _resetRunSignalsForTests(): void {
  _gen = null;
  _prev = new Map();
  _seenOnServer.clear();
  _toastQueue.clear();
  _open.clear();
  _openSnapshot = new Set();
  _cacheKey = null;
  _cacheRaw = null;
  _cache = EMPTY_MAP;
  _savedTitle = null;
  _savedIcons = null;
  _addedIcon = null;
}
