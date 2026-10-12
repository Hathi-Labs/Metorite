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
  act_ref?: string | null;
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
 */
export function feedGroups(
  answer: Pick<FeedAnswer, "cards" | "groups">,
  shown: number,
  removed: ReadonlySet<string> = new Set(),
): { groups: ShownGroup[]; hidden: number } {
  const cards = answer.cards.filter((c) => !removed.has(c.signal_id));
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
    a.way === "tap" ? !!a.act && TAP_ACTS.has(a.act) && !!a.act_ref : a.way === "open" ? !!a.href : false,
  );
}

/**
 * The needs row a `tap` act runs as. `runAct` reads only `act` and
 * `act_ref`, and the id is the row of the needs view, so the bell and the
 * sidebar drop the same row.
 *
 * ⚠️ The id is NOT the card's id. A notification folded into a task card
 * has the card id `tasks:<task id>`, and its needs row is
 * `projects:<notification id>` (§4.1 rule 3). The id grammar is the
 * gateway's (`routes/shell/needs.py`): a done row is `tasks:<task id>`, a
 * read row is `projects:<notification id>`.
 */
export function needsRowFor(card: FeedCard, act: FeedAct): NeedsItem | null {
  if (act.way !== "tap" || !act.act_ref || (act.act !== "done" && act.act !== "read")) return null;
  const done = act.act === "done";
  const kind: NeedsKind = (KIND_ORDER as readonly string[]).includes(card.kind)
    ? (card.kind as NeedsKind)
    : done
      ? "due_today"
      : "notification";
  return {
    id: done ? `tasks:${act.act_ref}` : `projects:${act.act_ref}`,
    app: done ? "tasks" : "projects",
    kind,
    title: card.title,
    detail: null,
    href: card.href,
    at: card.at,
    act: act.act,
    act_ref: act.act_ref,
  };
}

/**
 * Whether a `tap` act takes its whole card away. A done closes the task, so
 * the card goes. A read closes the card only when the notification IS the
 * card. A read on a task card closes the folded notification only, and the
 * overdue task must stay in view, so only the act goes.
 */
export function actLeavesCard(card: Pick<FeedCard, "kind">, act: Pick<FeedAct, "act">): boolean {
  return act.act === "done" || card.kind === "notification";
}

/** The key of one act of one card, for an act that leaves but keeps its card. */
export const actKey = (card: Pick<FeedCard, "signal_id">, act: Pick<FeedAct, "id">): string =>
  `${card.signal_id}#${act.id}`;

/** Every id an answer holds: each card, and each act of each card. */
export function heldIds(cards: readonly FeedCard[]): string[] {
  return cards.flatMap((c) => [c.signal_id, ...c.acts.map((a) => actKey(c, a))]);
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
