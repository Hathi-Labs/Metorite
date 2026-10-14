/**
 * My Day's live feed, as the browser reads it (`my_day_feed.md` §14.1 and
 * §14.1a, WS-58 MD-1b).
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
 * **The life of a card** (WS-58 MD-2b, §6). The card keeps the five state
 * fields of MD-2a, so it draws a dot for `new`, "Updated" and "Still open".
 * A source that failed keeps its cards from its last `ok` answer, for 5
 * minutes at most (`carryCards`, §6.1). The order holds during a visit
 * (`columnView`, §7 rule 4). `feedState.ts` writes the state.
 *
 * Fence: `feed.test.ts` and `feedState.test.ts`.
 */
import { PROJECTS_CACHE } from "@/app/projects/lib/api";
import { fmtClock } from "@/app/tasks/lib/utils";
import { cacheKey, guardedPut, onClear } from "@/lib/dataCache";
import { NAV_SECTIONS } from "@/lib/nav";

import { APP_NAMES, appIcon, failedLines } from "./myDay";
import {
  KIND_ORDER,
  type NeedsApp,
  type NeedsFeed,
  type NeedsItem,
  type NeedsKind,
  type SourceState,
  NEEDS_APP_HREF,
  needsKey,
  readFeed,
} from "./needs";
import { ACT_HOLD_MS } from "./needsActs";

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
  // The state fields of MD-2a (`my_day_feed.md` §14.1a, §6.8). Optional: an
  // older gateway sends none. MD-2b draws the dot, "Updated" and "Still open".
  /** `new`, `seen` or `later`. */
  state?: "new" | "seen" | "later";
  /** The card came back because its own facts changed (§6.3 rule 3). */
  updated?: boolean;
  /** A seen card that turned urgent since the member saw it (§6.3 rule 4). */
  still_open?: boolean;
  /** The visits of the member's day. */
  visits?: number;
  /** The state fingerprint that `POST /api/shell/feed/state` sends back (§6.8 item 1). */
  state_fp?: string;
}

export interface FeedAnswer {
  cards: FeedCard[];
  /** The groups that hold a card, in the server's order. */
  groups: { id: FeedGroupId; count: number }[];
  /** The needs view of the same run, or null from a gateway that sent none. */
  needs: NeedsFeed | null;
  sources: Partial<Record<FeedApp, SourceState>>;
  capped: FeedApp[];
  /**
   * When `fetchFeed` sent the request, in ms. The overlay of the feed's own
   * acts compares it with the time of each act (`feedHiddenRows`). An answer
   * that no fetch stamped reads as 0, older than any act.
   */
  fetchedAt: number;
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

/** The three states a shown card can have (§14.1a). */
const CARD_STATES = new Set(["new", "seen", "later"]);

/** The patterns of the gateway's POST contract (§6.8). */
export const STATE_FP = /^[0-9a-f]{8,64}$/;
export const SIGNAL_ID = /^[a-z_]+:[^\s]{1,200}$/;

/**
 * The state fields of a card, only when each is well formed (MD-2b). A
 * `state_fp` outside the gateway's pattern is dropped, because one bad item
 * makes the gateway refuse the whole batch with 422.
 */
function readState(c: Record<string, unknown>): Partial<FeedCard> {
  const out: Partial<FeedCard> = {};
  if (typeof c.state === "string" && CARD_STATES.has(c.state)) out.state = c.state as FeedCard["state"];
  if (c.updated === true) out.updated = true;
  if (c.still_open === true) out.still_open = true;
  if (typeof c.visits === "number" && Number.isFinite(c.visits)) out.visits = c.visits;
  if (typeof c.state_fp === "string" && STATE_FP.test(c.state_fp)) out.state_fp = c.state_fp;
  return out;
}

/**
 * Whether the client may write state for this card: it has a `state_fp`, and
 * its id and fingerprint pass the gateway's patterns. A card that may not
 * gets no menu, and the seen observer never sends it.
 */
export function sendable(card: Pick<FeedCard, "signal_id" | "state_fp">): boolean {
  return !!card.state_fp && STATE_FP.test(card.state_fp) && SIGNAL_ID.test(card.signal_id);
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
    ...readState(c),
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
    fetchedAt: 0,
  };
}

/**
 * Fetch the feed. A refusal or an outage throws, so the column can say so.
 *
 * When the answer lands, its needs view goes into the needs key, through
 * `guardedPut`: a write that landed during this request, or a needs answer
 * newer than it, keeps the needs entry as it is.
 *
 * The cards of a source that failed come from the last answer in which it
 * read `ok`, for 5 minutes at most (`carryCards`, §6.1).
 */
export async function fetchFeed(limit = FEED_LIMIT): Promise<FeedAnswer> {
  const putNeeds = guardedPut<NeedsFeed>(feedKey(limit), needsKey());
  // Stamped BEFORE the request: an act after this moment is newer than
  // everything this answer can know.
  const fetchedAt = Date.now();
  const res = await fetch(`/api/shell/feed?limit=${limit}`, { cache: "no-store" });
  if (!res.ok) throw new Error("Your feed could not load.");
  const answer = readFeedAnswer(await res.json());
  if (answer.needs) putNeeds(answer.needs);
  // A card that a sound answer from after the menu act holds again is back
  // for real, for example after a snooze ended: the carry may keep it again.
  for (const c of answer.cards) {
    const at = noCarry.get(c.signal_id);
    if (at !== undefined && fetchedAt > at) noCarry.delete(c.signal_id);
  }
  const before = lastOk.get(limit) ?? new Map<string, OkCards>();
  const carried = carryCards(before, { ...answer, fetchedAt }, noCarry);
  lastOk.set(limit, noteOk(before, answer, fetchedAt));
  return carried;
}

// ── A failed source keeps its cards for a while (§6.1, MD-2b) ───────────────

/** How long a failed source keeps the cards of its last `ok` answer (§6.1). */
export const CARRY_MS = 5 * 60_000;

/** The cards of one source in the last RAW answer in which it read `ok`. */
export interface OkCards {
  cards: readonly FeedCard[];
  /** When that answer was fetched. The carry ends `CARRY_MS` after it. */
  at: number;
}

/**
 * The last `ok` cards of each source, for each limit. It is not the cache
 * entry, because a Projects write drops that entry, and the read after a Done
 * must still carry a failed source. It holds RAW answers only, never a
 * carried card, so a carry can never restart its own clock. Nothing reads it
 * as an answer. Memory only, and a new member starts with none.
 */
const lastOk = new Map<number, ReadonlyMap<string, OkCards>>();

/** Record the cards of each source that read `ok` in this raw answer. */
export function noteOk(
  before: ReadonlyMap<string, OkCards>,
  raw: Pick<FeedAnswer, "cards" | "sources">,
  fetchedAt: number,
): ReadonlyMap<string, OkCards> {
  const next = new Map(before);
  for (const [app, state] of Object.entries(raw.sources)) {
    if (state === "ok") next.set(app, { cards: raw.cards.filter((c) => c.app === app), at: fetchedAt });
    else if (state !== "failed") next.delete(app);
  }
  return next;
}

/**
 * The cards that the member closed, with the time the write landed: a menu
 * act (`feedState.ts`), or a tap act that takes the whole card away
 * (`runFeedAct`, fix round 1). The carry never keeps one, so a snoozed or done
 * card cannot come back while its source fails. Undo takes the id out again,
 * and so does an answer fetched after that time that holds the card. Memory
 * only, for one member.
 */
const noCarry = new Map<string, number>();

/**
 * Stop the carry of this card. An act calls it before its write, with no
 * time, and again with the time its write landed.
 */
export function forgetCarry(signalId: string, at = Number.POSITIVE_INFINITY): void {
  noCarry.set(signalId, at);
}

/** Let the carry keep this card again: after a failed act, or its Undo. */
export function allowCarry(signalId: string): void {
  noCarry.delete(signalId);
}

onClear(() => {
  noCarry.clear();
  lastOk.clear();
});

/**
 * Whether a source carries its cards in this answer (§6.1, decided in MD-2b
 * fix round 1). Only a source that `failed` does. A capped source does not:
 * its answer is the truth for its top 15, and the rest sit behind "Show all
 * in <app>". An absent source does not: the member no longer holds the app.
 */
export function carries(answer: Pick<FeedAnswer, "sources">, app: string): boolean {
  return answer.sources[app as FeedApp] === "failed";
}

/**
 * The answer, with the cards of each source that failed (§6.1). They come
 * from the last raw answer in which that source read `ok`, and only for
 * `CARRY_MS` after it. After that they leave, and the caveat line stays. A
 * card that `next` holds, or that `forget` holds, never comes from the carry.
 * Calendar is a source too. The kept cards follow the new ones, and `groups`
 * counts them.
 */
export function carryCards(
  lastOkCards: ReadonlyMap<string, OkCards>,
  next: FeedAnswer,
  forget: { has(signalId: string): boolean } = new Set<string>(),
): FeedAnswer {
  const have = new Set(next.cards.map((c) => c.signal_id));
  const kept: FeedCard[] = [];
  for (const [app, ok] of lastOkCards) {
    if (!carries(next, app) || next.fetchedAt - ok.at > CARRY_MS) continue;
    for (const c of ok.cards) if (!have.has(c.signal_id) && !forget.has(c.signal_id)) kept.push(c);
  }
  if (kept.length === 0) return next;
  const cards = [...next.cards, ...kept];
  const order: FeedGroupId[] = next.groups.map((g) => g.id);
  for (const id of GROUP_ORDER) if (!order.includes(id) && cards.some((c) => c.group === id)) order.push(id);
  const groups = order
    .map((id) => ({ id, count: cards.filter((c) => c.group === id).length }))
    .filter((g) => g.count > 0);
  return { ...next, cards, groups };
}

// ── The order holds during a visit (§7 rule 4, MD-2b) ───────────────────────

/**
 * The place of each card at the start of a visit. A visit is the life of one
 * mounted feed column. It ends at a click on the "N new" pill, or at a
 * remount.
 */
export interface Visit {
  readonly place: ReadonlyMap<string, { index: number; group: FeedGroupId }>;
}

/** A new visit, in the order and the groups of these cards. */
export function startVisit(cards: readonly FeedCard[]): Visit {
  const place = new Map<string, { index: number; group: FeedGroupId }>();
  cards.forEach((c, index) => {
    if (!place.has(c.signal_id)) place.set(c.signal_id, { index, group: c.group });
  });
  return { place };
}

/**
 * The cards of a new answer, as the visit draws them. A card that the visit
 * knows keeps its place and its group, and takes the new content. A card that
 * the visit does not know waits: it is `fresh`, and the pill counts it. A card
 * that left the answer goes at once.
 */
export function visitCards(visit: Visit, cards: readonly FeedCard[]): { cards: FeedCard[]; fresh: FeedCard[] } {
  const known: { card: FeedCard; index: number }[] = [];
  const fresh: FeedCard[] = [];
  const taken = new Set<string>();
  for (const card of cards) {
    if (taken.has(card.signal_id)) continue;
    taken.add(card.signal_id);
    const at = visit.place.get(card.signal_id);
    if (at) known.push({ card: at.group === card.group ? card : { ...card, group: at.group }, index: at.index });
    else fresh.push(card);
  }
  known.sort((a, b) => a.index - b.index);
  return { cards: known.map((k) => k.card), fresh };
}

/**
 * What the feed column draws, as one pure rule (MD-2b fix round 1).
 *
 *   - The visit is the one the column holds, or a new one from this answer
 *     when it holds none. The column keeps the `visit` it gets back.
 *   - The groups are the visit's cards, less the cards a menu act hid
 *     (`hidden`), cut to `shown` (`feedGroups`).
 *   - `fresh` counts the new cards that the "N new" pill would bring in: only
 *     those that the cut of a new visit draws outside Later. A new card in
 *     Later, or past the cut, is not counted.
 *
 * The pill starts a new visit: the column passes `null` once.
 */
export function columnView(
  answer: Pick<FeedAnswer, "cards" | "groups"> | undefined,
  held: Visit | null,
  opts: { shown: number; marked: ReadonlySet<string>; hidden: ReadonlySet<string> },
): { visit: Visit | null; groups: ShownGroup[]; hidden: number; fresh: number } {
  if (!answer) return { visit: held, groups: [], hidden: 0, fresh: 0 };
  const visit = held ?? startVisit(answer.cards);
  const live = (cards: readonly FeedCard[]) => cards.filter((c) => !opts.hidden.has(c.signal_id));
  const placed = visitCards(visit, answer.cards);
  const { groups, hidden } = feedGroups({ cards: live(placed.cards), groups: answer.groups }, opts.shown, opts.marked);
  const next = feedGroups({ cards: live(answer.cards), groups: answer.groups }, opts.shown, opts.marked);
  const drawn = new Set(
    next.groups.filter((g) => g.id !== "later").flatMap((g) => g.cards.map((c) => c.signal_id)),
  );
  const fresh = placed.fresh.filter((c) => drawn.has(c.signal_id)).length;
  return { visit, groups, hidden, fresh };
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
 * The Later group is outside the cut: it draws folded, with all its cards
 * (MD-2b).
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
  const sorted = order.flatMap((id) => (id === "later" ? [] : cards.filter((c) => c.group === id)));
  const cut = sorted.slice(0, Math.max(0, shown));
  const groups = order
    .map((id) => ({
      id,
      label: GROUP_LABELS[id],
      cards: id === "later" ? cards.filter((c) => c.group === id) : cut.filter((c) => c.group === id),
    }))
    .filter((g) => g.cards.length > 0);
  return { groups, hidden: sorted.length - cut.length };
}

/** Where "Show all in <app>" goes, for each feed app. */
const FEED_APP_HREF: Readonly<Record<FeedApp, string>> = { ...NEEDS_APP_HREF, calendar: "/calendar" };

/**
 * The "Show all in <app>" links at the end of a group (§6.1, MD-2b): one for
 * each app of the group's cards that stopped at its cap. A card past the cap
 * is unknown, so the member opens the app to see the rest.
 */
export function cappedLinks(
  group: Pick<ShownGroup, "cards">,
  capped: readonly string[],
): { app: FeedApp; label: string; href: string }[] {
  const apps: FeedApp[] = [];
  for (const c of group.cards) {
    const app = c.app as FeedApp;
    if (capped.includes(app) && app in FEED_APP_HREF && !apps.includes(app)) apps.push(app);
  }
  return apps.map((app) => ({ app, label: `Show all in ${FEED_APP_NAMES[app]}`, href: FEED_APP_HREF[app] }));
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

// ── The overlay of the feed's own acts (fix round 2, F1) ────────────────────
//
// The `needsActs` marks end against the NEEDS answer, and the needs read
// answers before the feed does (4 s and no Calendar). So after a Done, a
// needs answer without the row would drop the mark while the FEED answer on
// screen still predates the Done, and the done card would come back with a
// live Done. The feed therefore keeps a short overlay of its own acts: the
// needs row ids of each act, with the time of the act. Memory only, one per
// page, and no cache.

/** Each needs row the feed's own acts took off, with the time of the act. */
export type ActOverlay = ReadonlyMap<string, number>;

/**
 * The time of a row whose write has not begun (fix round 3). A Done hides
 * its row at once, and the first Done of a page session then hydrates the
 * My Tasks store before it writes. No feed answer is the truth for a row in
 * that window, so the row waits at `PENDING` until `keep`, or until the act
 * settles. A pending row never expires: the act ends it either way.
 */
export const PENDING = Number.POSITIVE_INFINITY;

/** Add these rows at `at`, or move their time to `at`. */
export function overlayAdd(overlay: ActOverlay, rows: readonly string[], at: number): ActOverlay {
  const next = new Map(overlay);
  for (const row of rows) next.set(row, at);
  return next;
}

/** Drop these rows. The same object when none of them is there. */
export function overlayDrop(overlay: ActOverlay, rows: readonly string[]): ActOverlay {
  if (!rows.some((r) => overlay.has(r))) return overlay;
  const next = new Map(overlay);
  for (const row of rows) next.delete(row);
  return next;
}

/** The rows whose hold of `ACT_HOLD_MS` has ended at `now`. */
export function overlayExpired(overlay: ActOverlay, now: number): string[] {
  return [...overlay].filter(([, at]) => now - at >= ACT_HOLD_MS).map(([row]) => row);
}

/**
 * How long until the next hold ends, in ms, or null when no row has a hold:
 * an empty overlay, or one with only `PENDING` rows.
 */
export function overlayNextEnd(overlay: ActOverlay, now: number): number | null {
  let next: number | null = null;
  for (const at of overlay.values()) {
    if (!Number.isFinite(at)) continue;
    const left = Math.max(0, at + ACT_HOLD_MS - now);
    if (next === null || left < next) next = left;
  }
  return next;
}

/**
 * The needs rows the feed treats as taken off, for the answer on screen.
 *
 *   - A row of the feed's own act follows the overlay. It is off while the
 *     act is at least as new as the answer's fetch (`at >= fetchedAt`), and
 *     always while the row is `PENDING`. An
 *     answer fetched after the act is the truth: a task it still holds was
 *     really reopened, so its card shows, whatever the needs mark says.
 *   - Every other row follows its `needsActs` mark, so a Done in the bell
 *     hides the feed's card at once.
 */
export function feedHiddenRows(marked: ReadonlySet<string>, overlay: ActOverlay, fetchedAt: number): Set<string> {
  const out = new Set<string>();
  for (const row of marked) if (!overlay.has(row)) out.add(row);
  for (const [row, at] of overlay) if (at >= fetchedAt) out.add(row);
  return out;
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

/**
 * The mark after the source line (§6.3 rules 3 and 4, MD-2b): "Updated" for a
 * card whose own facts changed, "Still open" for a seen card that turned
 * urgent. Else null.
 */
export function stateMark(card: Pick<FeedCard, "updated" | "still_open">): string | null {
  if (card.updated) return "Updated";
  if (card.still_open) return "Still open";
  return null;
}

/** "My Tasks · Overdue": the app's name, a dot, and the state of the card. */
export function sourceLine(card: Pick<FeedCard, "app" | "kind">): string {
  const app = FEED_APP_NAMES[card.app as FeedApp] ?? card.app;
  const state = FEED_KIND_LABELS[card.kind];
  return state ? `${app} · ${state}` : app;
}

/**
 * The time at the right of the source line: WHEN, never how long. The why
 * line already says "Due in 1 h 5 min.", so a duration here only repeats it
 * in a second format (MD-1b visual review).
 *
 * The member's own day gives the clock, through `fmtClock`, the one clock
 * that Today draws. Yesterday and tomorrow give the word. Six days either
 * side give the short weekday, such as "Mon", and older gives "12 Oct".
 */
export function cardTime(card: Pick<FeedCard, "kind" | "at">, nowMs: number): string {
  if (!card.at) return "";
  const at = new Date(card.at);
  if (Number.isNaN(at.getTime())) return "";
  const now = new Date(nowMs);
  const day = (d: Date) => new Date(d.getFullYear(), d.getMonth(), d.getDate()).getTime();
  const days = Math.round((day(at) - day(now)) / 86_400_000);
  if (days === 0) return fmtClock(at);
  if (days === -1) return "Yesterday";
  if (days === 1) return "Tomorrow";
  if (Math.abs(days) <= 6) return at.toLocaleDateString(undefined, { weekday: "short" });
  return at.toLocaleDateString(undefined, { day: "numeric", month: "short" });
}

/** One muted line per source that failed, Calendar included. */
export function feedFailedLines(sources: FeedAnswer["sources"]): string[] {
  const { calendar, ...needs } = sources;
  const lines = failedLines(needs);
  if (calendar === "failed") lines.push("Calendar did not answer, so its time blocks may be missing.");
  return lines;
}
