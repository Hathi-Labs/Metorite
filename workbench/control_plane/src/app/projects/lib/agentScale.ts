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
  type LensGroup,
  type LensModel,
  type LensPerson,
  type LensSession,
  type LensState,
  type SubCounts,
  DEFAULT_STATES,
  LENS_STATES,
  compareSessions,
  countStates,
  matchesFind,
  membersOf,
  personSessions,
} from "./agentLenses";

/** The most agent cards on one canvas. */
export const CARDS_MAX = 40;
/** The sub-agent rows under one session card. The rest are in the details. */
export const SUB_ROWS_MAX = 8;
/** The squares of one group. The rest read "+N". */
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
  /** The starts of `rememberStarts`. Absent: the start of the listed run. */
  starts?: ReadonlyMap<string, number>;
  /** The address of the viewer. Their column comes first (decision G4). */
  me?: string;
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
  /**
   * The state that a card ranks by: its own, and for a folded orchestrator the
   * most urgent one that it holds, because its card stands for those agents.
   */
  rankOf: (s: LensSession) => LensState;
  /** Session id to the orchestrator that must draw with it. */
  holder: Map<string, LensSession>;
  /** How many cards the person needs: the ranked ones and their orchestrators. */
  cards: number;
  open: boolean;
  /** The idle and done sessions that the rest node stands for. */
  quiet: LensSession[] | null;
}

/**
 * When each session started, as the view first saw it, in epoch milliseconds.
 * The read holds no start of a session, and its one listed run changes when a
 * new run opens. So the view keeps the first value, and the order holds still.
 */
export function rememberStarts(
  known: ReadonlyMap<string, number>,
  sessions: readonly LensSession[],
  nowMs: number,
): ReadonlyMap<string, number> {
  const fresh = sessions.filter((s) => !known.has(s.id));
  if (fresh.length === 0) return known;
  const next = new Map(known);
  for (const s of fresh) next.set(s.id, nowMs - s.startedAge * 1000);
  return next;
}

/**
 * The groups of a person (decision G4): the orchestrators by urgency, then the
 * oldest first. The agents with no orchestrator come last. Only a change of a
 * state moves a group.
 */
export function orderGroups(groups: readonly LensGroup[], starts?: ReadonlyMap<string, number>): LensGroup[] {
  const urgency = (g: LensGroup) => Math.min(...membersOf(g).map((s) => LENS_STATES.indexOf(s.state)));
  const start = (s: LensSession) => starts?.get(s.id) ?? -s.startedAge * 1000;
  return [...groups].sort((a, b) => {
    if (!a.orch || !b.orch) return Number(!a.orch) - Number(!b.orch);
    return urgency(a) - urgency(b) || start(a.orch) - start(b.orch) || a.key.localeCompare(b.key);
  });
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
  const held = new Map<string, LensState>();
  const rankOf = (s: LensSession): LensState => held.get(s.id) ?? s.state;
  const holder = new Map<string, LensSession>();
  const quiet: LensSession[] = [];
  /** The place of each session among the groups of the person (decision G4). */
  const placeOf = new Map<string, number>();
  for (const [place, group] of orderGroups(person.groups, options.starts).entries()) {
    for (const s of membersOf(group)) placeOf.set(s.id, place);
    const orch = group.orch;
    const folded = orch !== null && !q && folds.get(sessionNodeId(orch.id)) === false;
    const lanes = group.sessions.filter(visible);
    if (orch && folded) {
      const states = membersOf(group).map((s) => s.state);
      held.set(orch.id, LENS_STATES.find((state) => states.includes(state))!);
    }
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
  // The start rule: a person with active work starts open, any other as their
  // card. The person of the selected session is always open.
  const open = q || hasPin ? true : (override ?? all.some(isActive));
  return {
    person: person.person,
    sessions: matches,
    // By state, then by group, then by name. So the bound keeps an orchestrator
    // with its agents. Never by the last event, so an activity moves no card.
    ranked: [...wanted].sort(
      (a, b) =>
        LENS_STATES.indexOf(rankOf(a)) - LENS_STATES.indexOf(rankOf(b)) ||
        placeOf.get(a.id)! - placeOf.get(b.id)! ||
        compareSessions(a, b),
    ),
    rankOf,
    holder,
    cards: wanted.length,
    open,
    quiet: !q && mode === "active" && quiet.length > 0 ? quiet : null,
  };
}

/** Takes a session and the orchestrator that must draw with it, if both fit under `cap`. */
function takeInto(shown: Set<string>, draft: Draft, s: LensSession, cap: number): boolean {
  const need = [s.id, draft.holder.get(s.id)?.id].filter((id): id is string => id !== undefined && !shown.has(id));
  if (shown.size + need.length > cap) return false;
  need.forEach((id) => shown.add(id));
  return true;
}

/**
 * The slots that a click on the state node of `state` needs, at most
 * `WINDOW_ROOM`: its agents, and each orchestrator that must draw with one. A
 * slot that an orchestrator holds already goes back to the most urgent card.
 */
function roomFor(draft: Draft, state: LensState): number {
  const ids = new Set<string>();
  for (const s of draft.ranked) {
    if (draft.rankOf(s) !== state) continue;
    ids.add(s.id);
    const orch = draft.holder.get(s.id);
    if (orch) ids.add(orch.id);
  }
  return Math.min(WINDOW_ROOM, ids.size);
}

/**
 * The cards that a person keeps while the state node of `state` is open: the
 * selected agent, then the most urgent ones. They leave only the room that the
 * node needs, so an urgent card folds only when no other room is left.
 */
function reducedCards(draft: Draft, quota: number, pin: string | null, state: LensState): Set<string> {
  const shown = new Set<string>();
  const pinned = draft.ranked.find((s) => s.id === pin);
  if (pinned) takeInto(shown, draft, pinned, quota);
  const need = roomFor(draft, state);
  draft.ranked.forEach((s) => takeInto(shown, draft, s, Math.max(quota - need, 0)));
  return shown;
}

/** The sessions of a state that a state node walks: those outside the cards it keeps. */
function walked(draft: Draft, kept: ReadonlySet<string>, state: LensState): LensSession[] {
  return draft.ranked.filter((s) => draft.rankOf(s) === state && !kept.has(s.id));
}

/** The person that the member opened, in at most `quota` cards. An open state node walks. */
function boundLatest(draft: Draft, quota: number, options: PlanOptions): { shown: Set<string>; nexts: Map<LensState, number> } {
  const nexts = new Map<LensState, number>();
  const window = options.window?.person === draft.person ? options.window : null;
  let shown: Set<string>;
  if (window) {
    shown = reducedCards(draft, quota, options.pin, window.state);
    const list = walked(draft, shown, window.state);
    let at = window.start < list.length ? window.start : 0;
    while (at < list.length && takeInto(shown, draft, list[at], quota)) at += 1;
    nexts.set(window.state, at < list.length ? at : 0);
  } else {
    shown = new Set<string>();
    const pinned = draft.ranked.find((s) => s.id === options.pin);
    if (pinned) takeInto(shown, draft, pinned, quota);
  }
  // Each free slot takes the most urgent card that is left. No room stays empty.
  draft.ranked.forEach((s) => takeInto(shown, draft, s, quota));
  for (const state of LENS_STATES) {
    if (window?.state === state) continue;
    // A first click skips the cards of the state that show now.
    const list = walked(draft, reducedCards(draft, quota, options.pin, state), state);
    nexts.set(state, Math.max(list.findIndex((s) => !shown.has(s.id)), 0));
  }
  return { shown, nexts };
}

const URGENT: readonly LensState[] = ["you", "failed"];

/**
 * Which cards draw when the open people want more than `CARDS_MAX` (decision
 * G3). Urgency wins: the selected agent, then each Needs you and Failed agent,
 * then the person that the member opened, then the rest by state. So Done folds
 * first, then Idle, Waiting and Working. Inside one state, the people with the
 * most agents fold first.
 */
function share(open: readonly Draft[], options: PlanOptions): Map<string, { shown: Set<string>; nexts: Map<LensState, number> }> {
  const cards = new Map(open.map((d) => [d.person, new Set<string>()]));
  let used = 0;
  const take = (d: Draft, s: LensSession, cap: number) => {
    const mine = cards.get(d.person)!;
    const before = mine.size;
    takeInto(mine, d, s, before + Math.max(cap - used, 0));
    used += mine.size - before;
  };
  const bySize = [...open].sort((a, b) => a.cards - b.cards || a.person.localeCompare(b.person));
  const latest = open.find((d) => d.person === options.latest) ?? null;
  for (const d of open) {
    const pinned = d.ranked.find((s) => s.id === options.pin);
    if (pinned) take(d, pinned, CARDS_MAX);
  }
  /**
   * The room that the click of the member keeps for the person they opened: what
   * the open state node needs, and with none, what the person needs.
   */
  const window = latest && options.window?.person === latest.person ? options.window : null;
  const kept = !latest ? 0 : window ? roomFor(latest, window.state) : Math.min(WINDOW_ROOM, latest.cards);
  if (latest) for (const s of latest.ranked) if (URGENT.includes(latest.rankOf(s))) take(latest, s, CARDS_MAX);
  const own = () => (latest ? cards.get(latest.person)!.size : 0);
  /** The cards of the selection among them. They take none of the kept room. */
  const selection = own();
  for (const state of URGENT) {
    for (const d of bySize) {
      if (d === latest) continue;
      for (const s of d.ranked) if (d.rankOf(s) === state) take(d, s, CARDS_MAX - Math.max(kept - (own() - selection), 0));
    }
  }
  const nexts = new Map<string, Map<LensState, number>>();
  if (latest) {
    const quota = CARDS_MAX - (used - own());
    const bound = boundLatest(latest, quota, options);
    used += bound.shown.size - own();
    cards.set(latest.person, bound.shown);
    nexts.set(latest.person, bound.nexts);
  }
  for (const state of LENS_STATES) {
    for (const d of bySize) {
      if (d === latest) continue;
      for (const s of d.ranked) if (d.rankOf(s) === state) take(d, s, CARDS_MAX);
    }
  }
  // Where a click on a state node of another person starts: after the cards that show now.
  for (const d of open) {
    if (d === latest) continue;
    const mine = cards.get(d.person)!;
    const urgentOfOthers = open
      .filter((o) => o !== d)
      .reduce((n, o) => n + o.ranked.filter((s) => URGENT.includes(o.rankOf(s))).length, 0);
    /** The slots of this person after a click on the node of `state`. */
    const quotaFor = (state: LensState) => CARDS_MAX - Math.min(urgentOfOthers, CARDS_MAX - roomFor(d, state));
    nexts.set(
      d.person,
      new Map(
        LENS_STATES.map((state) => {
          const list = walked(d, reducedCards(d, quotaFor(state), options.pin, state), state);
          return [state, Math.max(list.findIndex((s) => !mine.has(s.id)), 0)];
        }),
      ),
    );
  }
  return new Map(open.map((d) => [d.person, { shown: cards.get(d.person)!, nexts: nexts.get(d.person)! }]));
}

/** The order of the people (decision G4): me, then Needs you, Failed and active counts. */
function peopleOrder(model: LensModel, me: string): LensPerson[] {
  const mine = me.trim().toLowerCase();
  const rank = (p: LensPerson) => {
    const all = personSessions(p);
    const counts = countStates(all);
    return [Number(p.person.toLowerCase() === mine), counts.you, counts.failed, all.filter(isActive).length];
  };
  return model.people
    .map((p) => ({ p, rank: rank(p) }))
    .sort((a, b) => {
      for (let i = 0; i < a.rank.length; i += 1) if (a.rank[i] !== b.rank[i]) return b.rank[i] - a.rank[i];
      return a.p.person.localeCompare(b.p.person);
    })
    .map((x) => x.p);
}

/**
 * Which people are open and which agents draw (AMR-5). At most `CARDS_MAX`
 * agent cards, orchestrators included. Past that `share` folds by urgency, and
 * each state that does not fit draws one state node. A person with no card
 * left draws as their folded card.
 */
export function planMap(model: LensModel, options: PlanOptions): PersonPlan[] {
  const drafts = peopleOrder(model, options.me ?? "").flatMap((p) => {
    if (options.person && p.person !== options.person) return [];
    const draft = draftOf(p, options);
    return draft ? [draft] : [];
  });
  const open = drafts.filter((d) => d.open);
  const over = open.reduce((n, d) => n + d.cards, 0) > CARDS_MAX;
  const shared = over ? share(open, options) : null;
  return drafts.map((draft) => {
    const { person, sessions } = draft;
    const mine = shared?.get(person);
    const shown = mine?.shown ?? new Set(draft.ranked.flatMap((s) => [s.id, ...(draft.holder.has(s.id) ? [draft.holder.get(s.id)!.id] : [])]));
    if (!draft.open || (draft.cards > 0 && shown.size === 0)) {
      return { person, sessions, open: false, shown: new Set<string>(), rest: null, buckets: [] };
    }
    const buckets = LENS_STATES.flatMap((state): StateNode[] => {
      // A folded orchestrator joins the node of the most urgent agent it holds.
      const folded = draft.ranked.filter((s) => draft.rankOf(s) === state && !shown.has(s.id));
      return folded.length > 0 ? [{ state, sessions: folded, next: mine?.nexts.get(state) ?? 0 }] : [];
    });
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
