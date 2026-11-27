/**
 * The Agents view at scale (WS-MCP AMR-4 and AMR-5, `agent_coordination.md`
 * §3.12.8 and §3.12.9).
 *
 * The pure rules of a large fleet: the order of attention, the bound of the map,
 * what the member folds and opens, and the order of the questions. It draws
 * nothing and it reads nothing, so a vitest proves each rule.
 *
 * ⚠️ No canvas draws more than `CARDS_MAX` agent cards. Past that it folds, and
 * each fold says what it holds.
 */
import {
  type LensCounts,
  type LensModel,
  type LensPerson,
  type LensSession,
  type LensState,
  type SubCounts,
  DEFAULT_STATES,
  LENS_STATES,
  matchesFind,
  personSessions,
} from "./agentLenses";

/** The most agent cards on one canvas. */
export const CARDS_MAX = 40;
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

/* ── The map of everyone (AMR-5) ────────────────────────────────────────── */

/** The one switch of the toolbar: the active states, or every state. */
export type MapMode = "active" | "all";

/** The room that an open state node keeps for its next cards. */
export const WINDOW_ROOM = 12;

export const personNodeId = (person: string): string => `p:${person}`;
export const sessionNodeId = (id: string): string => `s:${id}`;

/** Active = needs you, failed, working and waiting (`DEFAULT_STATES`). */
export function isActive(s: Pick<LensSession, "state">): boolean {
  return DEFAULT_STATES.includes(s.state);
}

/** The cards that an open state node of one person shows, from `start`. */
export interface MapWindow {
  person: string;
  state: LensState;
  start: number;
}

/** What the member opened and folded on the map. */
export interface MapView {
  /** Node id to open (true) or folded (false): what the member chose. */
  folds: ReadonlyMap<string, boolean>;
  /** The people whose "idle or done" node is open. */
  restOpen: ReadonlySet<string>;
  /** The person that the member opened last. The bound never folds them. */
  latest: string | null;
  window: MapWindow | null;
}

export const NEW_VIEW: MapView = { folds: new Map(), restOpen: new Set(), latest: null, window: null };

/**
 * The chevron of a node: folds it, or opens it. `person` is the person that the
 * node belongs to, and `null` for the root.
 */
export function foldView(view: MapView, nodeId: string, open: boolean, person: string | null): MapView {
  const folds = new Map(view.folds).set(nodeId, open);
  if (person === null) return { ...view, folds };
  const latest = open ? person : view.latest === person ? null : view.latest;
  return { ...view, folds, latest, window: view.window?.person === person ? null : view.window };
}

/** A click on the "idle or done" node of a person. */
export function restView(view: MapView, person: string): MapView {
  const open = !view.restOpen.has(person);
  const restOpen = new Set(view.restOpen);
  if (open) restOpen.add(person);
  else restOpen.delete(person);
  return {
    ...view,
    restOpen,
    latest: open ? person : view.latest,
    window: view.window?.person === person ? null : view.window,
  };
}

/** A click on a state node: its next cards draw, and its person stays open. */
export function bucketView(view: MapView, person: string, state: LensState, next: number): MapView {
  return { ...view, latest: person, window: { person, state, start: next } };
}

export interface PlanOptions extends MapView {
  mode: MapMode;
  /** One person, or "" for everyone. */
  person: string;
  /** A find shows each match, whatever the switch, and ignores the folds. */
  find: string;
  nameOf: (email: string) => string;
  /** The selected session. It always draws. */
  pin: string | null;
}

/** One state node: the sessions of a state that draw no card. */
export interface StateNode {
  state: LensState;
  sessions: LensSession[];
  /** Where the next click starts. */
  next: number;
}

export interface PersonPlan {
  person: string;
  /** The sessions that the person card counts: all of them, or the matches of a find. */
  sessions: LensSession[];
  open: boolean;
  /** The sessions that draw a card, an orchestrator included. */
  shown: ReadonlySet<string>;
  /** The "idle or done" node of the Active switch, or `null`. */
  rest: { count: number; open: boolean } | null;
  buckets: StateNode[];
}

interface Draft {
  person: string;
  sessions: LensSession[];
  /** What wants a card, the most urgent first. */
  ranked: LensSession[];
  /** Session id to the orchestrator that must draw with it. */
  holder: Map<string, LensSession>;
  /** How many cards the person needs: the ranked ones and their orchestrators. */
  cards: number;
  open: boolean;
  /** The idle and done sessions that the rest node stands for. */
  quiet: LensSession[] | null;
}

function draftOf(person: LensPerson, options: PlanOptions): Draft | null {
  const { mode, find, nameOf, folds, restOpen, pin } = options;
  const q = find.trim();
  const all = personSessions(person);
  const matches = q ? all.filter((s) => matchesFind(s, q, nameOf(s.person))) : all;
  if (q && matches.length === 0 && !all.some((s) => s.id === pin)) return null;
  const everyState = mode === "all" || restOpen.has(person.person);
  const visible = (s: LensSession): boolean => s.id === pin || (q ? matches.includes(s) : everyState || isActive(s));
  const wanted: LensSession[] = [];
  const holder = new Map<string, LensSession>();
  const quiet: LensSession[] = [];
  for (const group of person.groups) {
    const orch = group.orch;
    const folded = orch !== null && !q && folds.get(sessionNodeId(orch.id)) === false;
    const lanes = group.sessions.filter(visible);
    // The selected session draws even under a folded orchestrator.
    const drawn = folded ? lanes.filter((s) => s.id === pin) : lanes;
    for (const s of drawn) if (orch) holder.set(s.id, orch);
    wanted.push(...drawn);
    // An orchestrator with a lane to show draws too, whatever its own state.
    const asContext = orch !== null && (drawn.length > 0 || (folded && lanes.length > 0));
    if (orch && (visible(orch) || asContext)) wanted.push(orch);
    if (!folded) quiet.push(...group.sessions.filter((s) => !isActive(s)));
    if (orch && !isActive(orch) && !(asContext && !everyState)) quiet.push(orch);
  }
  const override = folds.get(personNodeId(person.person));
  const hasPin = pin !== null && all.some((s) => s.id === pin);
  // The start rule: a person with active work starts open, any other as their card.
  const open = q ? true : (override ?? (hasPin || all.some(isActive)));
  return {
    person: person.person,
    sessions: matches,
    ranked: rankSessions(wanted),
    holder,
    cards: wanted.length,
    open,
    quiet: !q && mode === "active" && quiet.length > 0 ? quiet : null,
  };
}

/** Draws one person in at most `quota` cards, and the rest as state nodes. */
function boundPerson(draft: Draft, quota: number, options: PlanOptions): { shown: Set<string>; buckets: StateNode[] } {
  const { ranked, holder, person } = draft;
  const shown = new Set<string>();
  /** Takes a session and its orchestrator, if both fit under `cap`. */
  const take = (s: LensSession, cap: number): boolean => {
    const need = [s.id, holder.get(s.id)?.id].filter((id): id is string => id !== undefined && !shown.has(id));
    if (shown.size + need.length > cap) return false;
    need.forEach((id) => shown.add(id));
    return true;
  };
  if (draft.cards <= quota) {
    ranked.forEach((s) => take(s, quota));
    return { shown, buckets: [] };
  }
  const pinned = ranked.find((s) => s.id === options.pin);
  if (pinned) take(pinned, quota);
  const base = Math.max(quota - WINDOW_ROOM, 0);
  ranked.forEach((s) => take(s, base));
  /** The sessions of each state outside the cards that an open state node keeps. */
  const reduced = new Set(shown);
  const rest = (state: LensState) => ranked.filter((s) => s.state === state && !reduced.has(s.id));
  const window = options.window?.person === person ? options.window : null;
  let nextOfWindow = 0;
  if (window) {
    const list = rest(window.state);
    let at = window.start < list.length ? window.start : 0;
    while (at < list.length && take(list[at], quota)) at += 1;
    nextOfWindow = at < list.length ? at : 0;
  } else {
    ranked.forEach((s) => take(s, quota));
  }
  const buckets = LENS_STATES.flatMap((state): StateNode[] => {
    const sessions = ranked.filter((s) => s.state === state && !shown.has(s.id));
    if (sessions.length === 0) return [];
    if (window) return [{ state, sessions, next: window.state === state ? nextOfWindow : 0 }];
    // The first click skips the cards of the state that show already.
    const list = rest(state);
    const seen = list.findIndex((s) => !shown.has(s.id));
    return [{ state, sessions, next: Math.max(seen, 0) }];
  });
  return { shown, buckets };
}

/**
 * Which people are open and which sessions draw (AMR-5). At most `CARDS_MAX`
 * agent cards, orchestrators included. Past that the people with the most
 * agents fold first. The person that the member opened last and the person of
 * the selection stay open, and a person who is still too large folds the rest
 * into one node for each state.
 */
export function planMap(model: LensModel, options: PlanOptions): PersonPlan[] {
  const drafts = model.people.flatMap((p) => {
    if (options.person && p.person !== options.person) return [];
    const draft = draftOf(p, options);
    return draft ? [draft] : [];
  });
  const pinPerson = options.pin ? (model.byId.get(options.pin)?.person ?? null) : null;
  const total = () => drafts.reduce((n, d) => n + (d.open ? d.cards : 0), 0);
  while (total() > CARDS_MAX) {
    const open = drafts.filter((d) => d.open);
    const foldable = open
      .filter((d) => d.person !== options.latest && d.person !== pinPerson)
      .sort((a, b) => b.cards - a.cards || a.person.localeCompare(b.person));
    if (open.length < 2 || foldable.length === 0) break;
    foldable[0].open = false;
  }
  const over = total() > CARDS_MAX;
  const open = drafts.filter((d) => d.open);
  const quotaOf = (draft: Draft): number => {
    if (!over) return CARDS_MAX;
    if (open.length < 2) return CARDS_MAX;
    // Two people stay: the selection keeps its card, and the other one the rest.
    const pinned = open.find((d) => d.person === pinPerson && d.person !== options.latest) ?? open[1];
    const kept = Math.min(pinned.cards, 2);
    return draft === pinned ? kept : CARDS_MAX - kept;
  };
  return drafts.map((draft) => {
    const { person, sessions } = draft;
    if (!draft.open) return { person, sessions, open: false, shown: new Set<string>(), rest: null, buckets: [] };
    const { shown, buckets } = boundPerson(draft, quotaOf(draft), options);
    const rowsOpen = options.restOpen.has(person);
    const hidden = draft.quiet?.filter((s) => rowsOpen || !shown.has(s.id)).length ?? 0;
    return { person, sessions, open: true, shown, buckets, rest: hidden > 0 ? { count: hidden, open: rowsOpen } : null };
  });
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

/* ── The phone ───────────────────────────────────────────────────────────── */

/** How much attention a group wants. 0 is quiet. */
export function attention(counts: LensCounts): number {
  return counts.you * 100 + counts.failed * 50 + counts.working * 2 + counts.waiting;
}

export function allSessionsOf(model: LensModel, person: string): LensSession[] {
  const found = model.people.find((p) => p.person === person);
  return found ? personSessions(found) : [];
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
