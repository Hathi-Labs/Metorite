/**
 * What the member does with a My Day card, as the browser writes it
 * (`my_day_feed.md` §6.2, §6.8 and the MD-2b ticket, WS-58).
 *
 * `POST /api/shell/feed/state` takes `{items: [{signal_id, state_fp, kind,
 * action, until}]}` and nothing else. This file is the ONE client of that
 * route. The gateway computes each snooze time and each visit, so the client
 * sends only what happened.
 *
 * Three writers share it:
 *
 *   - **The seen observer** (§6.2). Only the feed column observes, never the
 *     bell. A card is seen when half of it is in view for 1.5 s on a visible
 *     tab (`createDwell`). The sightings wait in `createStateQueue`, which
 *     sends one POST each 10 s and one on `pagehide`, 50 items at most.
 *   - **`acted`** (§6.8 item 4). A tap act that succeeds adds it to the same
 *     queue. It is a record, and it never hides a card.
 *   - **The menu** (`runMenuAct`). Snooze, Dismiss and "Not useful" post at
 *     once. The card hides through an overlay of its own, keyed on
 *     `signal_id`. ⚠️ The overlay is NEVER `needsActs`: a mark there takes a
 *     needs row out of the bell, and §6.3 says snooze and dismiss never touch
 *     the bell (§6.8 item 9). The shared toast offers Undo for 10 seconds, and
 *     Undo posts `restore`.
 *
 * A 422 drops a batch, because the same body never passes. A 5xx, a 502 or a
 * network failure keeps it for the next send. A card with no `state_fp` is
 * never sent (`sendable`).
 *
 * Fence: `feedState.test.ts`.
 */
import { identity, invalidate } from "@/lib/dataCache";
import type { ToastAction, ToastVariant } from "@/lib/toast";

import { type FeedCard, allowCarry, feedKey, forgetCarry, sendable } from "./feed";

// ── The contract (§6.8) ─────────────────────────────────────────────────────

export type StateAction = "seen" | "snooze" | "dismiss" | "not_useful" | "acted" | "restore";
export type SnoozeUntil = "later_today" | "tomorrow" | "next_week" | "on_change";

export interface StateItem {
  signal_id: string;
  state_fp: string;
  kind: string;
  action: StateAction;
  until: SnoozeUntil | null;
}

/** One body holds 50 items at most (§6.8). */
export const STATE_BATCH = 50;
/** The seen queue sends one POST in each of these, in ms (§6.2). */
export const SEEN_FLUSH_MS = 10_000;
/** How long half of a card must stay in view, in ms (§6.2). */
export const SEEN_DWELL_MS = 1_500;
/** The part of a card that must be in view (§6.2). */
export const SEEN_RATIO = 0.5;
/**
 * The client sends one sighting of a card in each 30 minutes at most. The
 * gateway counts two sightings within 30 minutes as one visit anyway (§6.2),
 * so a second send would only cost a request.
 */
export const SEEN_AGAIN_MS = 30 * 60_000;
/** How long the Undo of a menu act stays, in ms. */
export const UNDO_MS = 10_000;
/**
 * How long one POST may take, in ms. A POST past it counts as no answer, so
 * the queue keeps its batch, and a hung request never holds the queue.
 */
export const STATE_TIMEOUT_MS = 8_000;

/** The item of one act on one card. The card must be `sendable`. */
export function stateItem(
  card: Pick<FeedCard, "signal_id" | "state_fp" | "kind">,
  action: StateAction,
  until: SnoozeUntil | null = null,
): StateItem {
  return {
    signal_id: card.signal_id,
    state_fp: card.state_fp ?? "",
    kind: card.kind,
    action,
    until: action === "snooze" ? until : null,
  };
}

/**
 * Post the items. The answer is the HTTP status, and 0 for a network failure
 * or a POST past `STATE_TIMEOUT_MS`. `keepalive` lets a POST outlive the page,
 * for `pagehide`.
 */
export type PostState = (items: StateItem[], keepalive?: boolean) => Promise<number>;

export const postState: PostState = async (items, keepalive = false) => {
  const stop = new AbortController();
  const timer = setTimeout(() => stop.abort(), STATE_TIMEOUT_MS);
  try {
    const res = await fetch("/api/shell/feed/state", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ items }),
      cache: "no-store",
      keepalive,
      signal: stop.signal,
    });
    return res.status;
  } catch {
    return 0;
  } finally {
    clearTimeout(timer);
  }
};

const written = (status: number) => status >= 200 && status < 300;
/** A 5xx, a 502 from the BFF, or no answer at all: send it again later. */
const retry = (status: number) => status === 0 || status >= 500;

// ── The queue of the seen observer and of `acted` ───────────────────────────

export interface StateQueue {
  /** Add a sighting. False when this card and fingerprint went in the last 30 minutes. */
  see(item: StateItem): boolean;
  /** Add an item that is not a sighting: `acted`. */
  add(item: StateItem): void;
  /** Send one batch of 50 at most. One send runs at a time. */
  flush(): Promise<void>;
  /** Send every item now, with `keepalive`, for `pagehide` and the unmount. */
  drain(): void;
  /** The items that wait, in order. */
  pending(): readonly StateItem[];
}

const sameSlot = (a: StateItem, b: StateItem) => a.signal_id === b.signal_id && a.action === b.action;

/**
 * The queue. It holds one item for each card and action, and a newer item
 * replaces the older one in place. An item that cannot reach the gateway goes
 * back to the head of the queue, unless a newer one took its slot.
 *
 * ⚠️ A queue belongs to the member that the cache holds when it is made
 * (`identity()`, fix round 1). Once the cache names another member, or none,
 * the queue drops its items and sends nothing. An account switch changes the
 * cookie first and then leaves the page, and the `pagehide` send would else
 * write one member's sightings into the rows of the other.
 */
export function createStateQueue(post: PostState = postState, now: () => number = Date.now): StateQueue {
  let items: StateItem[] = [];
  const lastSeen = new Map<string, number>();
  let busy = false;
  const owner = identity();
  /** Whether the cache still holds this queue's member. Else forget it all. */
  const ours = () => {
    if (identity() === owner) return true;
    items = [];
    return false;
  };

  const put = (item: StateItem) => {
    if (!ours()) return;
    const at = items.findIndex((i) => sameSlot(i, item));
    if (at >= 0) items[at] = item;
    else items.push(item);
  };
  const putBack = (batch: StateItem[]) => {
    if (!ours()) return;
    items = [...batch.filter((b) => !items.some((i) => sameSlot(i, b))), ...items];
  };
  const settle = (batch: StateItem[], status: number) => {
    if (retry(status)) putBack(batch);
    // A 2xx is written. A 4xx is refused, and the same body never passes.
  };

  return {
    see(item) {
      const key = `${item.signal_id}|${item.state_fp}`;
      const t = now();
      const last = lastSeen.get(key);
      if (last !== undefined && t - last < SEEN_AGAIN_MS) return false;
      lastSeen.set(key, t);
      put(item);
      return true;
    },
    add(item) {
      put(item);
    },
    async flush() {
      if (!ours() || busy || items.length === 0) return;
      busy = true;
      const batch = items.slice(0, STATE_BATCH);
      items = items.slice(batch.length);
      try {
        settle(batch, await post(batch));
      } finally {
        busy = false;
      }
    },
    drain() {
      if (!ours()) return;
      while (items.length > 0) {
        const batch = items.slice(0, STATE_BATCH);
        items = items.slice(batch.length);
        void post(batch, true).then((status) => settle(batch, status));
      }
    },
    pending() {
      return items;
    },
  };
}

// ── The dwell: half in view for 1.5 s, on a visible tab (§6.2) ──────────────

export interface Dwell {
  /** Half of this card came into view. */
  enter(id: string): void;
  /** Less than half of this card is in view now. */
  leave(id: string): void;
  /** The tab became hidden or visible. */
  visibility(visible: boolean): void;
  /** Forget everything, for the unmount. */
  stop(): void;
}

/**
 * The rule of a sighting, with no DOM: a card in view for `dwellMs` on a
 * visible tab is seen, once each time it comes into view. A hidden tab stops
 * every count, and a visible tab starts the count of each card in view again.
 */
export function createDwell(
  onSeen: (id: string) => void,
  opts: { dwellMs?: number; visible?: () => boolean } = {},
): Dwell {
  const dwellMs = opts.dwellMs ?? SEEN_DWELL_MS;
  const visible = opts.visible ?? (() => typeof document === "undefined" || document.visibilityState === "visible");
  const inView = new Set<string>();
  const timers = new Map<string, ReturnType<typeof setTimeout>>();
  const disarm = (id: string) => {
    clearTimeout(timers.get(id));
    timers.delete(id);
  };
  const arm = (id: string) => {
    if (timers.has(id) || !visible()) return;
    timers.set(
      id,
      setTimeout(() => {
        timers.delete(id);
        if (inView.has(id) && visible()) onSeen(id);
      }, dwellMs),
    );
  };
  return {
    enter(id) {
      inView.add(id);
      arm(id);
    },
    leave(id) {
      inView.delete(id);
      disarm(id);
    },
    visibility(shown) {
      if (shown) for (const id of inView) arm(id);
      else for (const id of [...timers.keys()]) disarm(id);
    },
    stop() {
      for (const id of [...timers.keys()]) disarm(id);
      inView.clear();
    },
  };
}

// ── The menu (§6.4, §6.6) ───────────────────────────────────────────────────

export interface MenuChoice {
  action: "snooze" | "dismiss" | "not_useful";
  until: SnoozeUntil | null;
  label: string;
  icon: string;
  /** The toast after the write. */
  done: string;
  /** The error line in the card when the write fails. */
  failed: string;
}

/** The menu, in its order: the four snoozes, then Dismiss and "Not useful". */
export const MENU_CHOICES: readonly MenuChoice[] = [
  { action: "snooze", until: "later_today", label: "Later today", icon: "Clock", done: "Snoozed until later today", failed: "Could not snooze this card. Try again." },
  { action: "snooze", until: "tomorrow", label: "Tomorrow", icon: "Sunrise", done: "Snoozed until tomorrow", failed: "Could not snooze this card. Try again." },
  { action: "snooze", until: "next_week", label: "Next week", icon: "CalendarDays", done: "Snoozed until next week", failed: "Could not snooze this card. Try again." },
  { action: "snooze", until: "on_change", label: "Until it changes", icon: "RefreshCw", done: "Snoozed until it changes", failed: "Could not snooze this card. Try again." },
  { action: "dismiss", until: null, label: "Dismiss", icon: "X", done: "Dismissed until it changes", failed: "Could not dismiss this card. Try again." },
  { action: "not_useful", until: null, label: "Not useful", icon: "ThumbsDown", done: "Marked as not useful", failed: "Could not save this. Try again." },
];

/** The words of a failed Undo. */
export const UNDO_FAILED = "Could not undo. Try again.";

// ── The overlay of the menu acts ────────────────────────────────────────────

/**
 * The cards a menu act hid, with the time its write landed. A card is hidden
 * while its write runs (`MENU_PENDING`), and until a feed answer fetched
 * after that time lands. That answer is the truth.
 */
export type MenuOverlay = ReadonlyMap<string, number>;

export const MENU_PENDING = Number.POSITIVE_INFINITY;

export function menuAdd(overlay: MenuOverlay, id: string, at: number): MenuOverlay {
  const next = new Map(overlay);
  next.set(id, at);
  return next;
}

export function menuDrop(overlay: MenuOverlay, id: string): MenuOverlay {
  if (!overlay.has(id)) return overlay;
  const next = new Map(overlay);
  next.delete(id);
  return next;
}

/** The cards the overlay hides from an answer fetched at `fetchedAt`. */
export function menuHidden(overlay: MenuOverlay, fetchedAt: number): Set<string> {
  const out = new Set<string>();
  for (const [id, at] of overlay) if (fetchedAt <= at) out.add(id);
  return out;
}

/** The overlay once an answer fetched at `fetchedAt` landed: its ended rows go. */
export function menuSettle(overlay: MenuOverlay, fetchedAt: number): MenuOverlay {
  const ended = [...overlay].filter(([, at]) => fetchedAt > at).map(([id]) => id);
  if (ended.length === 0) return overlay;
  const next = new Map(overlay);
  for (const id of ended) next.delete(id);
  return next;
}

/** What a menu act reads and writes, so a test can drive it with no React. */
export interface MenuDeps {
  post?: PostState;
  /** Change the overlay of the menu acts. */
  overlay: (change: (o: MenuOverlay) => MenuOverlay) => void;
  /**
   * Read the feed again. The default drops the feed key (`invalidate`), so a
   * read that began before the write never stores its answer (fix round 1).
   * A forced read would join that read, and its stale answer would hide the
   * card again.
   */
  reread?: () => void;
  /** The shared toast (`useToast().show`). */
  toast: (spec: { key: string; title: string; variant?: ToastVariant; action?: ToastAction; timeout?: number }) => void;
  /** Show or clear the error line of a card. */
  setError: (signalId: string, message: string | null) => void;
  now?: () => number;
}

/**
 * Run one menu act. The card hides at once. A write that lands keeps it
 * hidden, reads the feed again and offers Undo. A write that fails shows the
 * card again, with an error line. No needs mark is set, so the bell and the
 * sidebar counts do not change.
 */
export async function runMenuAct(
  card: FeedCard,
  choice: MenuChoice,
  deps: MenuDeps,
): Promise<"done" | "failed" | null> {
  if (!sendable(card)) return null;
  const id = card.signal_id;
  const post = deps.post ?? postState;
  const now = deps.now ?? Date.now;
  deps.setError(id, null);
  forgetCarry(id);
  deps.overlay((o) => menuAdd(o, id, MENU_PENDING));
  const status = await post([stateItem(card, choice.action, choice.until)]);
  if (!written(status)) {
    allowCarry(id);
    deps.overlay((o) => menuDrop(o, id));
    deps.setError(id, choice.failed);
    return "failed";
  }
  const at = now();
  forgetCarry(id, at);
  deps.overlay((o) => menuAdd(o, id, at));
  (deps.reread ?? rereadFeed)();
  deps.toast({
    key: `feed-menu:${id}`,
    title: choice.done,
    action: { label: "Undo", onClick: () => void undoMenuAct(card, deps) },
    timeout: UNDO_MS,
  });
  return "done";
}

/**
 * The Undo of a menu act: post `restore`, which clears the snooze or the
 * dismiss and leaves the row `seen`. Then the card may show again, and the
 * feed reads again. A failed Undo says so, and the card stays hidden,
 * because the gateway still hides it.
 */
export async function undoMenuAct(card: FeedCard, deps: MenuDeps): Promise<"done" | "failed" | null> {
  if (!sendable(card)) return null;
  const id = card.signal_id;
  const post = deps.post ?? postState;
  const status = await post([stateItem(card, "restore")]);
  if (!written(status)) {
    deps.toast({ key: `feed-menu:${id}`, title: UNDO_FAILED, variant: "error" });
    return "failed";
  }
  allowCarry(id);
  deps.overlay((o) => menuDrop(o, id));
  (deps.reread ?? rereadFeed)();
  return "done";
}

/**
 * Drop the feed key. Its watchers read again, and a read already in flight
 * does not store its answer (`dataCache.ts`, the write counter).
 */
export function rereadFeed(): void {
  invalidate(feedKey());
}
