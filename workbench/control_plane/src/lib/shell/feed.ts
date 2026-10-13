/**
 * My Day's live feed, as the browser reads it (`my_day_feed.md` §14.1 and
 * §14.1a, WS-57 MD-1b).
 *
 * `GET /api/shell/feed` gives the ranked cards, their groups and the needs
 * view of the same provider run. This file is the ONE client of that route:
 * the shape, the cache key, the fetch, the groups and the acts a card may
 * draw. `FeedCard.tsx` only draws what it decides.
 *
 * **One provider run for each poll.** The answer carries the needs view
 * (`answer.needs`, exactly `GET /shell/needs?limit=30`). `fetchFeed` stores it
 * under the needs key when a network answer lands, so the bell and the
 * sidebar counts read it with no second request. ⚠️ Only the fetcher writes
 * it, through `guardedPut`. An effect on the feed's data would also run for a
 * cached feed of any age, and it would replace a newer needs entry.
 *
 * **The acts call the OWNING app's code**, as NS-3 does. A `tap` act becomes
 * a needs row, and `runAct` in `needs.ts` runs it. An `open` act is a link.
 * A way this client does not know draws nothing.
 *
 * Fence: `feed.test.ts`.
 */
import { PROJECTS_CACHE } from "@/app/projects/lib/api";
import { cacheKey, guardedPut } from "@/lib/dataCache";
import { NAV_SECTIONS } from "@/lib/nav";
import { relativeTime } from "@/lib/taskCard";

import { APP_NAMES, appIcon, failedLines } from "./myDay";
import {
  KIND_ORDER,
  type NeedsApp,
  type NeedsFeed,
  type NeedsItem,
  type NeedsKind,
  type SourceState,
  needsKey,
  readFeed,
} from "./needs";

// ── The shape (§14.1a) ──────────────────────────────────────────────────────

/** The app ids of the feed. `calendar` is a feed source only, never a needs app. */
export type FeedApp = NeedsApp | "calendar";

/** The group ids, in their one order. MD-1 emits the first four. */
export const GROUP_ORDER = ["now", "needs_you", "ready", "fyi", "later", "handled"] as const;
export type FeedGroupId = (typeof GROUP_ORDER)[number];

export const GROUP_LABELS: Readonly<Record<FeedGroupId, string>> = {
  now: "Now",
  needs_you: "Needs you",
  ready: "Ready for you",
  fyi: "For your information",
  later: "Later",
  handled: "Handled",
};

export type FeedWay = "tap" | "expand" | "open";

export interface FeedAct {
  id: string;
  label: string;
  class?: string;
  way: FeedWay;
  /** A `tap` act: the needs act it runs, `done` or `read`. */
  act?: string | null;
  /** The first ref. For a merged `read` act it is the first of `act_refs`. */
  act_ref?: string | null;
  /**
   * A `read` act: every unread notification id on the task, newest first
   * (§14.1a, MD-1a repair round 1). Absent, the act reads as `[act_ref]`.
   */
  act_refs?: string[] | null;
  /** An `open` act: where it goes. */
  href?: string | null;
}

export interface FeedCard {
  signal_id: string;
  app: FeedApp;
  kind: string;
  folded: string[];
  group: FeedGroupId;
  score: number | null;
  title: string;
  why: string | null;
  at: string | null;
  href: string;
  acts: FeedAct[];
}

export interface FeedAnswer {
  cards: FeedCard[];
  /** The groups that hold a card, in the server's order. */
  groups: { id: FeedGroupId; count: number }[];
  /** The needs view of the same run, or null from a gateway that sent none. */
  needs: NeedsFeed | null;
  sources: Partial<Record<FeedApp, SourceState>>;
  capped: FeedApp[];
}

// ── The cache key and the fetch ─────────────────────────────────────────────

/** The cards the page asks for: the route's default (§14.1a). */
export const FEED_LIMIT = 40;

/** The cards shown at once. The member asks for the next 20 (§14.1). */
export const FEED_SHOWN = 20;

/**
 * The key lives in the Projects family ON PURPOSE, as `NEEDS_CACHE` does.
 * Each Projects write drops that family, so a Done drops the feed and the
 * needs view together, and both read again.
 */
export const FEED_CACHE = `${PROJECTS_CACHE}shell/feed`;

export const feedKey = (limit = FEED_LIMIT): string => cacheKey(FEED_CACHE, { limit });

const isGroup = (v: unknown): v is FeedGroupId =>
  typeof v === "string" && (GROUP_ORDER as readonly string[]).includes(v);

const str = (v: unknown): string | null => (typeof v === "string" ? v : null);

function readAct(raw: unknown): FeedAct | null {
  const a = raw as Partial<FeedAct> | null;
  if (!a || typeof a !== "object" || typeof a.label !== "string") return null;
  if (a.way !== "tap" && a.way !== "expand" && a.way !== "open") return null;
  return {
    id: typeof a.id === "string" ? a.id : a.label,
    label: a.label,
    class: str(a.class) ?? undefined,
    way: a.way,
    act: str(a.act),
    act_ref: str(a.act_ref),
    act_refs: Array.isArray(a.act_refs) ? a.act_refs.filter((r): r is string => typeof r === "string" && !!r) : null,
    href: str(a.href),
  };
}

function readCard(raw: unknown): FeedCard | null {
  const c = raw as Record<string, unknown> | null;
  if (!c || typeof c !== "object") return null;
  if (typeof c.signal_id !== "string" || typeof c.title !== "string" || typeof c.href !== "string") return null;
  if (typeof c.app !== "string" || typeof c.kind !== "string" || !isGroup(c.group)) return null;
  return {
    signal_id: c.signal_id,
    app: c.app as FeedApp,
    kind: c.kind,
    folded: Array.isArray(c.folded) ? c.folded.filter((k): k is string => typeof k === "string") : [],
    group: c.group,
    score: typeof c.score === "number" ? c.score : null,
    title: c.title,
    why: str(c.why),
    at: str(c.at),
    href: c.href,
    acts: Array.isArray(c.acts) ? c.acts.map(readAct).filter((a): a is FeedAct => a !== null) : [],
  };
}

/**
 * Read an answer defensively. A field the server left out reads as empty, and
 * a card with no id, title, link or known group is dropped, so the column
 * never throws on a gateway older or newer than this client.
 */
export function readFeedAnswer(raw: unknown): FeedAnswer {
  const body = (raw ?? {}) as Record<string, unknown>;
  const cards = Array.isArray(body.cards) ? body.cards.map(readCard).filter((c): c is FeedCard => c !== null) : [];
  const groups = Array.isArray(body.groups)
    ? body.groups
        .filter((g): g is { id: FeedGroupId; count: unknown } => !!g && isGroup((g as { id?: unknown }).id))
        .map((g) => ({ id: g.id, count: typeof g.count === "number" ? g.count : 0 }))
    : [];
  return {
    cards,
    groups,
    needs: body.needs && typeof body.needs === "object" ? readFeed(body.needs) : null,
    sources: body.sources && typeof body.sources === "object" ? (body.sources as FeedAnswer["sources"]) : {},
    capped: Array.isArray(body.capped) ? (body.capped as FeedApp[]) : [],
  };
}

/**
 * Fetch the feed. A refusal or an outage throws, so the column can say so.
 *
 * When the answer lands, its needs view goes into the needs key, through
 * `guardedPut`: a write that landed during this request, or a needs answer
 * newer than it, keeps the needs entry as it is.
 */
export async function fetchFeed(limit = FEED_LIMIT): Promise<FeedAnswer> {
  const putNeeds = guardedPut<NeedsFeed>(feedKey(limit), needsKey());
  const res = await fetch(`/api/shell/feed?limit=${limit}`, { cache: "no-store" });
  if (!res.ok) throw new Error("Your feed could not load.");
  const answer = readFeedAnswer(await res.json());
  if (answer.needs) putNeeds(answer.needs);
  return answer;
}

// ── The groups ──────────────────────────────────────────────────────────────

export interface ShownGroup {
  id: FeedGroupId;
  label: string;
  cards: FeedCard[];
}

/**
 * The groups to draw. The order of the groups is the order of `groups`, and
 * the order inside a group is the server's: the client sorts nothing again.
 * A group that holds a card and is missing from `groups` follows, in the
 * order of `GROUP_ORDER`.
 *
 * The cut runs over the whole feed in that order, then groups, so `shown`
 * means cards and not cards per group. `hidden` is what the cut held back.
 *
 * `marked` is the needs rows an act took off (`needsActs.ts`). A card an act
 * closed whole leaves (`cardHidden`).
 */
export function feedGroups(
  answer: Pick<FeedAnswer, "cards" | "groups">,
  shown: number,
  marked: ReadonlySet<string> = new Set(),
): { groups: ShownGroup[]; hidden: number } {
  const cards = answer.cards.filter((c) => !cardHidden(c, marked));
  const order: FeedGroupId[] = [];
  for (const g of answer.groups) if (!order.includes(g.id)) order.push(g.id);
  for (const id of GROUP_ORDER) if (!order.includes(id) && cards.some((c) => c.group === id)) order.push(id);
  const sorted = order.flatMap((id) => cards.filter((c) => c.group === id));
  const cut = sorted.slice(0, Math.max(0, shown));
  const groups = order
    .map((id) => ({ id, label: GROUP_LABELS[id], cards: cut.filter((c) => c.group === id) }))
    .filter((g) => g.cards.length > 0);
  return { groups, hidden: sorted.length - cut.length };
}

// ── The acts ────────────────────────────────────────────────────────────────

const TAP_ACTS = new Set(["done", "read"]);

/**
 * The acts a card draws, in the server's order. A `tap` act needs its needs
 * act and its ref, and an `open` act needs its link. An `expand` act waits
 * for MD-8, and an act this client does not know draws nothing.
 */
export function drawnActs(card: Pick<FeedCard, "acts">): FeedAct[] {
  return card.acts.filter((a) =>
    a.way === "tap" ? !!a.act && TAP_ACTS.has(a.act) && actRows(a).length > 0 : a.way === "open" ? !!a.href : false,
  );
}

/** The refs of a `tap` act: `act_refs` for a merged read, else `[act_ref]`. */
export function actRefs(act: Pick<FeedAct, "act" | "act_ref" | "act_refs">): string[] {
  if (act.act === "read" && act.act_refs && act.act_refs.length > 0) return act.act_refs;
  return act.act_ref ? [act.act_ref] : [];
}

/**
 * The needs rows a `tap` act closes, in the id grammar of the gateway
 * (`routes/shell/needs.py`): a done closes `tasks:<task id>`, and a read
 * closes `projects:<notification id>` for each id in `act_refs`.
 *
 * ⚠️ These are NOT the card's id. A notification folded into a task card has
 * the card id `tasks:<task id>`, and its needs rows are
 * `projects:<notification id>` (§4.1 rule 3). Every mark keys on these rows,
 * in the one store `needsActs.ts`, so the bell, the sidebar and the feed
 * drop the same rows, and the store's 30 s hold ends each mark.
 */
export function actRows(act: Pick<FeedAct, "way" | "act" | "act_ref" | "act_refs">): string[] {
  if (act.way !== "tap") return [];
  if (act.act === "done") return act.act_ref ? [`tasks:${act.act_ref}`] : [];
  if (act.act === "read") return actRefs(act).map((id) => `projects:${id}`);
  return [];
}

/**
 * The needs row a `tap` act runs as, for `runAct`. Its id is the act's first
 * row. A read carries every id in `act_refs`, so `runAct` marks each one read.
 */
export function needsRowFor(card: FeedCard, act: FeedAct): NeedsItem | null {
  const rows = actRows(act);
  if (rows.length === 0) return null;
  const done = act.act === "done";
  const kind: NeedsKind = (KIND_ORDER as readonly string[]).includes(card.kind)
    ? (card.kind as NeedsKind)
    : done
      ? "due_today"
      : "notification";
  const refs = actRefs(act);
  return {
    id: rows[0],
    app: done ? "tasks" : "projects",
    kind,
    title: card.title,
    detail: null,
    href: card.href,
    at: card.at,
    act: done ? "done" : "read",
    act_ref: refs[0],
    ...(done ? {} : { act_refs: refs }),
  };
}

/**
 * The kinds each act closes. A done closes the task, and a block belongs to
 * its task (§4.1), so it closes the task's blocks too. A read closes the
 * task's notifications and nothing else.
 */
const CLOSES: Readonly<Record<string, ReadonlySet<string>>> = {
  done: new Set(["overdue", "due_today", "block_soon", "block_overlap"]),
  read: new Set(["notification"]),
};

/**
 * Whether a `tap` act takes its whole card away: it closes the card's kind
 * and every kind folded into it. A done on a task with an unread note keeps
 * the card, because the note still needs the member. A read on a note card
 * with a block that starts soon keeps the card, because the block is live.
 */
export function actLeavesCard(card: Pick<FeedCard, "kind" | "folded">, act: Pick<FeedAct, "act">): boolean {
  const closes = CLOSES[act.act ?? ""];
  return !!closes && closes.has(card.kind) && card.folded.every((k) => closes.has(k));
}

/** Whether every needs row of a `tap` act is marked: the act is spent. */
export function actSpent(act: FeedAct, marked: ReadonlySet<string>): boolean {
  const rows = actRows(act);
  return rows.length > 0 && rows.every((r) => marked.has(r));
}

/**
 * Whether a card leaves: its spent acts close its kind and every folded kind.
 * The marks come from the one store, so a Done in the bell hides the card
 * too. A card that comes back with a live signal keys on new rows, or on a
 * kind no spent act closes, so it shows.
 */
export function cardHidden(card: FeedCard, marked: ReadonlySet<string>): boolean {
  if (marked.size === 0) return false;
  const closed = new Set<string>();
  for (const act of card.acts) if (actSpent(act, marked)) for (const k of CLOSES[act.act ?? ""] ?? []) closed.add(k);
  return closed.has(card.kind) && card.folded.every((k) => closed.has(k));
}

/**
 * Whether the feed's poll covers the needs key. Only an answer that holds a
 * needs view, from a read that did not fail, fills that key. Else the needs
 * poller keeps its own read, so the bell's counts keep their poll.
 */
export function coversNeeds(answer: Pick<FeedAnswer, "needs"> | undefined, error: string | null): boolean {
  return !!answer?.needs && error === null;
}

// ── The words ───────────────────────────────────────────────────────────────

/** The member-facing name of each feed app. Calendar is the feed's own. */
export const FEED_APP_NAMES: Readonly<Record<FeedApp, string>> = { ...APP_NAMES, calendar: "Calendar" };

/** A card's icon: its app's own icon from the manifest, never a second list. */
export function feedAppIcon(app: FeedApp | string): string {
  if (app === "calendar") {
    return NAV_SECTIONS.flatMap((s) => s.items).find((p) => p.href === "/calendar")?.icon ?? "Circle";
  }
  return app in APP_NAMES ? appIcon(app as NeedsApp) : "Circle";
}

/** The state of a card, by kind, for the source line. A kind with no entry says none. */
export const FEED_KIND_LABELS: Readonly<Record<string, string>> = {
  overdue: "Overdue",
  due_today: "Due today",
  approval: "Waiting for your approval",
  notification: "New activity",
  needs_reply: "Waiting for your reply",
  block_soon: "Starts soon",
  block_overlap: "Overlap",
  day_overbooked: "Day is full",
  no_plan_yet: "No plan yet",
};

/** "My Tasks · Overdue": the app's name, a dot, and the state of the card. */
export function sourceLine(card: Pick<FeedCard, "app" | "kind">): string {
  const app = FEED_APP_NAMES[card.app as FeedApp] ?? card.app;
  const state = FEED_KIND_LABELS[card.kind];
  return state ? `${app} · ${state}` : app;
}

/** The time at the right of the source line. A due card says when it is due. */
export function cardTime(card: Pick<FeedCard, "kind" | "at">, nowMs: number): string {
  const rel = relativeTime(card.at, nowMs);
  if (!rel) return "";
  return card.kind === "overdue" || card.kind === "due_today" ? `Due ${rel}` : rel;
}

/** One muted line per source that failed, Calendar included. */
export function feedFailedLines(sources: FeedAnswer["sources"]): string[] {
  const { calendar, ...needs } = sources;
  const lines = failedLines(needs);
  if (calendar === "failed") lines.push("Calendar did not answer, so its time blocks may be missing.");
  return lines;
}
