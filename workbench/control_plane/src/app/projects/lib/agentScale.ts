/**
 * The Agents view at scale (WS-MCP AMR-4, `agent_coordination.md` §3.12.9).
 *
 * The pure rules of a large fleet: the order of attention, the overview tiles,
 * the bound of one orchestrator's cards, and the order of the questions. It
 * draws nothing and it reads nothing, so a vitest proves each rule.
 *
 * ⚠️ Aggregate first. No canvas draws more than `CARDS_MAX` full cards. Past
 * that it groups, and each group says what it holds.
 */
import {
  type LensCounts,
  type LensFilters,
  type LensGroup,
  type LensModel,
  type LensSession,
  type LensState,
  type SubCounts,
  LENS_STATES,
  countStates,
  matchesFind,
  membersOf,
  personSessions,
  splitSessions,
} from "./agentLenses";

/** A scope that shows more sessions than this draws the overview tiles. */
export const OVERVIEW_ABOVE = 40;
/** The session cards of one orchestrator before a bucket opens. */
export const SESSION_CARDS_MAX = 12;
/** The most full session cards on one canvas. */
export const CARDS_MAX = 40;
/** How many more a bucket opens. */
export const BUCKET_STEP = 12;
/** The sub-agent rows under one session card. The rest are in the details. */
export const SUB_ROWS_MAX = 8;
/** The squares of one tile. The rest read "+N". */
export const SQUARES_MAX = 96;
/** A list longer than this draws only the rows on screen. */
export const WINDOW_ABOVE = 50;
/** The cards of one state on a phone, and how many "Show more" adds. */
export const PHONE_PAGE = 10;
export const PHONE_MORE = 20;
/** The graph under this zoom shows names only. */
export const NAMES_ONLY_BELOW = 0.6;

const RANK = Object.fromEntries(LENS_STATES.map((s, i) => [s, i])) as Record<LensState, number>;

/**
 * The order of attention: by state, then by time. A session that needs you
 * comes oldest first, so the longest wait leads. Any other state comes newest
 * first.
 */
export function rankSessions(sessions: readonly LensSession[]): LensSession[] {
  return [...sessions].sort((a, b) => {
    const byState = RANK[a.state] - RANK[b.state];
    if (byState) return byState;
    const at = (a.lastAt ?? "").localeCompare(b.lastAt ?? "");
    return (a.state === "you" ? at : -at) || a.name.localeCompare(b.name) || a.id.localeCompare(b.id);
  });
}

/** The sessions of one person, or of everyone with "". */
export function scopeSessions(model: LensModel, person: string): LensSession[] {
  return person ? model.sessions.filter((s) => s.person === person) : model.sessions;
}

/**
 * How many cards the Graph and the Board draw for a scope (AMR-5a): the
 * sessions that the chips, the find box and the open folds let through, and
 * each orchestrator card. An orchestrator draws whatever its chip says. Under a
 * find it draws for a match, its own or one under it.
 */
export function drawnCards(
  model: LensModel,
  filters: LensFilters,
  opened: ReadonlySet<string>,
  nameOf: (email: string) => string,
): number {
  const find = filters.find.trim();
  let cards = 0;
  for (const p of model.people) {
    if (filters.person && p.person !== filters.person) continue;
    for (const group of p.groups) {
      const shown = splitSessions(group.sessions, filters, opened.has(group.key), nameOf).shown.length;
      const orch = group.orch !== null && (!find || shown > 0 || matchesFind(group.orch, find, nameOf(group.orch.person)));
      cards += shown + (orch ? 1 : 0);
    }
  }
  return cards;
}

const NO_FOLDS: ReadonlySet<string> = new Set();

/**
 * True when a scope draws too many cards for one canvas. A session that a chip
 * or a find hides does not count.
 */
export function usesOverview(model: LensModel, filters: LensFilters, nameOf: (email: string) => string): boolean {
  return drawnCards(model, filters, NO_FOLDS, nameOf) > OVERVIEW_ABOVE;
}

/**
 * The folds that the Graph and the Board may draw open. A fold that was opened
 * on the List, or before a refresh or a chip added cards, closes when the open
 * folds together pass the bound.
 */
export function boundedFolds(
  model: LensModel,
  filters: LensFilters,
  opened: ReadonlySet<string>,
  nameOf: (email: string) => string,
): ReadonlySet<string> {
  return drawnCards(model, filters, opened, nameOf) > OVERVIEW_ABOVE ? NO_FOLDS : opened;
}

export function subTotals(sessions: readonly LensSession[]): SubCounts {
  const out: SubCounts = { total: 0, working: 0, failed: 0, done: 0 };
  for (const s of sessions) {
    out.total += s.subs.total;
    out.working += s.subs.working;
    out.failed += s.subs.failed;
    out.done += s.subs.done;
  }
  return out;
}

/* ── The overview ───────────────────────────────────────────────────────── */

export interface Tile {
  /** The key of the group: the person, and the orchestrator id or "own". */
  key: string;
  person: string;
  /** `null` for the sessions with no orchestrator. */
  orch: LensSession | null;
  /** The sessions of the tile, the most urgent first. The orchestrator is one of them. */
  sessions: LensSession[];
  counts: LensCounts;
  subs: SubCounts;
  machines: string[];
  /** How much attention it wants. 0 is quiet. */
  score: number;
}

export interface TilePerson {
  person: string;
  counts: LensCounts;
  /** The tiles that want attention, the most first. */
  live: Tile[];
  /** The quiet ones: only idle and done sessions. They fold into one row. */
  quiet: Tile[];
}

export function attention(counts: LensCounts): number {
  return counts.you * 100 + counts.failed * 50 + counts.working * 2 + counts.waiting;
}

export function tileOf(group: LensGroup, person: string, sessions = membersOf(group)): Tile {
  const ranked = rankSessions(sessions);
  const counts = countStates(ranked);
  return {
    key: group.key,
    person,
    orch: group.orch,
    sessions: ranked,
    counts,
    subs: subTotals(ranked),
    machines: [...new Set(ranked.flatMap((s) => (s.host ? [s.host] : [])))],
    score: attention(counts),
  };
}

/** One tile for each orchestrator of each person, and one for the sessions with none. */
export function overviewTiles(
  model: LensModel,
  { person, find }: { person: string; find: string },
  nameOf: (email: string) => string,
): TilePerson[] {
  const out: TilePerson[] = [];
  for (const p of model.people) {
    if (person && p.person !== person) continue;
    const tiles = p.groups
      .flatMap((group) => {
        const sessions = membersOf(group).filter((s) => matchesFind(s, find, nameOf(s.person)));
        return sessions.length > 0 ? [tileOf(group, p.person, sessions)] : [];
      })
      .sort((a, b) => b.score - a.score || a.key.localeCompare(b.key));
    if (tiles.length === 0) continue;
    out.push({
      person: p.person,
      counts: countStates(tiles.flatMap((t) => t.sessions)),
      live: tiles.filter((t) => t.score > 0),
      quiet: tiles.filter((t) => t.score === 0),
    });
  }
  return out.sort((a, b) => attention(b.counts) - attention(a.counts) || a.person.localeCompare(b.person));
}

/** The group of a key, with its person. `null` when it left the map. */
export function findGroup(model: LensModel, key: string | null): { person: string; group: LensGroup } | null {
  if (!key) return null;
  for (const p of model.people) {
    const group = p.groups.find((g) => g.key === key);
    if (group) return { person: p.person, group };
  }
  return null;
}

/** The key of the group that holds a session. */
export function groupKeyOf(model: LensModel, sessionId: string): { person: string; key: string } | null {
  for (const p of model.people) {
    for (const group of p.groups) {
      if (membersOf(group).some((s) => s.id === sessionId)) return { person: p.person, key: group.key };
    }
  }
  return null;
}

export function allSessionsOf(model: LensModel, person: string): LensSession[] {
  const found = model.people.find((p) => p.person === person);
  return found ? personSessions(found) : [];
}

/* ── The bound of one orchestrator ──────────────────────────────────────── */

/** The extra cards that an open bucket shows: `count` of them, from `skip`. */
export interface BucketWindow {
  skip: number;
  count: number;
}
export type BucketWindows = Partial<Record<LensState, BucketWindow>>;

const EXTRA_MAX = CARDS_MAX - SESSION_CARDS_MAX;

/**
 * Open one bucket: `BUCKET_STEP` more cards of its state. The canvas never
 * passes `CARDS_MAX`, so another open bucket gives way first. A bucket that
 * fills the room alone then moves on to its next cards, and starts again at
 * its end.
 *
 * ⚠️ `rest` is `Bucket.rest`: how many sessions of the state sit outside the
 * first cards, drawn or folded. It is never the folded count, which shrinks as
 * the window grows and so wraps the window before its end.
 */
export function openBucket(windows: BucketWindows, state: LensState, rest: number): BucketWindows {
  const now = windows[state] ?? { skip: 0, count: 0 };
  const count = Math.min(now.count + BUCKET_STEP, EXTRA_MAX);
  const moved = now.skip + BUCKET_STEP;
  const own = count > now.count ? { skip: now.skip, count } : { skip: moved < rest ? moved : 0, count };
  const next: BucketWindows = { [state]: own };
  let room = EXTRA_MAX - own.count;
  for (const other of LENS_STATES) {
    const held = windows[other];
    if (other === state || !held || room <= 0) continue;
    next[other] = { skip: held.skip, count: Math.min(held.count, room) };
    room -= Math.min(held.count, room);
  }
  return next;
}

export interface Bucket {
  state: LensState;
  /** The sessions of this state that draw no card, the most urgent first. */
  sessions: LensSession[];
  /** The sessions of this state outside the first cards. The window walks them. */
  rest: number;
}

export interface Bounded {
  shown: LensSession[];
  buckets: Bucket[];
}

export interface BoundOptions {
  /** The states whose chip is on. */
  states: ReadonlySet<LensState>;
  /** A find shows each match, whatever its chip, and hides the rest. */
  find: string;
  nameOf: (email: string) => string;
  windows?: BucketWindows;
  /** A session that must draw: the selected one. */
  pin?: string | null;
}

/**
 * Which sessions of an orchestrator draw a card: the `SESSION_CARDS_MAX` most
 * urgent of the states that are on, then the cards of each open bucket. The
 * rest fold into one bucket for each state.
 */
export function boundSessions(sessions: readonly LensSession[], options: BoundOptions): Bounded {
  const { states, find, nameOf, windows = {}, pin } = options;
  const q = find.trim();
  const ranked = rankSessions(q ? sessions.filter((s) => matchesFind(s, q, nameOf(s.person))) : sessions);
  let first = ranked.filter((s) => q || states.has(s.state)).slice(0, SESSION_CARDS_MAX);
  /** The sessions of a state outside the first cards, in their order. */
  const outside = (state: LensState): LensSession[] => {
    const held = new Set(first.map((s) => s.id));
    return ranked.filter((s) => s.state === state && !held.has(s.id));
  };
  /** The first cards, then the window of each open bucket. */
  const take = (): Set<string> => {
    const taken = new Set(first.map((s) => s.id));
    for (const state of LENS_STATES) {
      const window = windows[state];
      if (!window) continue;
      const rest = outside(state);
      const skip = window.skip < rest.length ? window.skip : 0;
      for (const s of rest.slice(skip, skip + window.count)) taken.add(s.id);
    }
    return taken;
  };
  let taken = take();
  const pinned = pin ? sessions.find((s) => s.id === pin) : undefined;
  if (pinned && !taken.has(pinned.id)) {
    // The selected card takes the place of the least urgent first card. That
    // card joins its bucket again, so a click on the bucket still reaches it.
    if (first.length >= SESSION_CARDS_MAX) first = first.slice(0, -1);
    taken = take().add(pinned.id);
  }
  const all = pinned && !ranked.includes(pinned) ? rankSessions([...ranked, pinned]) : ranked;
  return {
    shown: all.filter((s) => taken.has(s.id)),
    buckets: LENS_STATES.flatMap((state) => {
      const folded = all.filter((s) => s.state === state && !taken.has(s.id));
      return folded.length > 0 ? [{ state, sessions: folded, rest: outside(state).length }] : [];
    }),
  };
}

/** "28 more working", "6 idle": the words of a bucket. */
export function bucketWord(bucket: Pick<Bucket, "sessions">, word: string, shownOfState: number): string {
  const n = bucket.sessions.length;
  return shownOfState > 0 ? `${n} more ${word.toLowerCase()}` : `${n} ${word.toLowerCase()}`;
}

/* ── The questions, one at a time ───────────────────────────────────────── */

/**
 * The sessions that need the member, in the order of their questions, oldest
 * first. `runIds` is the order of the questions list. One stop for each
 * session. A question of a run that the map does not list gives no stop.
 */
export function questionStops(model: LensModel, runIds: readonly string[]): string[] {
  const sessionOfRun = new Map<string, string>();
  for (const s of model.sessions) {
    if (s.state !== "you") continue;
    for (const r of s.runs) sessionOfRun.set(r.run_id, s.id);
  }
  return [...new Set(runIds.flatMap((id) => sessionOfRun.get(id) ?? []))];
}

/** The next stop, `by` steps from `at`, round the list. */
export function stepStop(at: number, by: number, total: number): number {
  return total === 0 ? 0 : (((at + by) % total) + total) % total;
}
