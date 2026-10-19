/**
 * runStatus — the ONE run state of an open chat (owner report, 2026-10-19).
 *
 * The owner's words: "I have no idea if the agent is working in the
 * background or not … sometimes I just see a thinking container for a
 * fleeting moment before it disappears." Before this module, eight places
 * each decided "is a run active" on their own: `isLoading`, `recovering`,
 * the old `runStatus`, the message's `streaming` flag, the thinking
 * container's `isThinkingActive`, the composer's Send or Stop, the session
 * list's dot (the server list joined with `isLoading`) and the context
 * ring's pulse. They disagreed. The worst case: a run longer than the
 * proxy's 310 s budget lost its stream, the chat read the clean close as
 * "done", and every indicator went idle while the run went on.
 *
 * Now ONE reducer holds the state, and every indicator reads it. The server
 * is the source of truth: a run is live while `GET /chat/active-sessions`
 * lists it. The local stream only adds detail (the step, the text). A stream
 * that ends with no terminal event is NOT "done": the state goes to
 * `finishing`, and the server answers whether the run goes on.
 *
 * Pure. The store keeps the state per thread (`SessionStreamState.run` in
 * `lib/chatStore.ts`), `hooks/useAgentChat.ts` feeds the events, and
 * `components/RunStatusRow.tsx` draws it. Owning spec:
 * `project-docs/specs/chat_run_continuity.md` §4.10.
 *
 * Fence (R7): `runStatus.test.ts`.
 */


// ── The phases ─────────────────────────────────────────────────────────────

export type RunPhase =
  | "idle"
  | "starting"
  | "working"
  | "waiting_for_you"
  | "parked"
  | "finishing"
  | "done"
  | "failed"
  | "stopped";

/** A run may still run: Stop is offered, a send steers it. */
const LIVE: ReadonlySet<RunPhase> = new Set(["starting", "working", "waiting_for_you", "finishing"]);

/** Live, for the composer, the session dot and the nav badge. */
export function isLivePhase(p: RunPhase): boolean {
  return LIVE.has(p);
}

// ── The timings ────────────────────────────────────────────────────────────

/** A server list this recent that named the run proves it live. */
export const SERVER_FRESH_MS = 15_000;

/** A new run may be missing from the server list for this long. */
export const START_GRACE_MS = 10_000;
/** An open stream that the server no longer lists, and that brought no
 *  event for this long, is finishing. */
export const GONE_MS = 15_000;
/** `finishing` never lasts longer than this. */
export const FINISH_MAX_MS = 20_000;
/** A run with no open stream that the server has not confirmed for this
 *  long settles. Nothing stays "working" forever. */
export const STALE_MS = 60_000;
/** After a Stop, a list that still names the run is the cancel's lag. */
export const STOP_HOLD_MS = 10_000;
/** After a terminal event, a list that still names the run is the
 *  registry's lag, not a new run. */
export const DONE_HOLD_MS = 10_000;
/** Two re-attaches are at least this far apart. */
export const ATTACH_GAP_MS = 5_000;
/** How long "Done" and "Stopped" stay in the status row. */
export const RESTING_SHOW_MS = 5_000;
/** A row that would show or hide for less than this never draws. */
export const FLASH_MS = 400;

/**
 * The server listed this run as live a moment ago. The Postgres recovery
 * poll must not end such a run on a full stop or on its 45 s clock, and
 * the stale recovery must not mark it interrupted (review of #1171).
 */
export function serverHoldsRun(s: RunStatus, now: number): boolean {
  return isLivePhase(s.phase) && s.serverSeenAt > 0 && now - s.serverSeenAt < SERVER_FRESH_MS;
}

// ── The state ──────────────────────────────────────────────────────────────

export interface RunStatus {
  phase: RunPhase;
  /** The step in words: "Asking Projects assistant", "Writing the reply". */
  step: string | null;
  /** When the run started, epoch ms. The elapsed time counts from here. */
  startedAt: number | null;
  /** When `phase` last changed. */
  changedAt: number;
  /** The assistant message the run streams into, when this tab knows it. */
  messageId: string | null;
  /** Stream events read in this run. */
  seq: number;
  /** The last stream event, epoch ms. */
  lastEventAt: number;
  /** The last server list that named this run, by its poll's start. */
  serverSeenAt: number;
  /** The first list, after the grace, that did not name a live run. */
  serverGoneAt: number;
  /** A terminal event arrived (done or error). */
  terminalAt: number;
  /** The member pressed Stop. */
  stoppedAt: number;
  /** The server lists a live run that no stream reads: re-attach. */
  attach: boolean;
  /** The last re-attach, epoch ms. */
  attachedAt: number;
  /** A blocking card waits on the member in this chat. */
  cardOpen: boolean;
}

export const IDLE_RUN: RunStatus = Object.freeze({
  phase: "idle",
  step: null,
  startedAt: null,
  changedAt: 0,
  messageId: null,
  seq: 0,
  lastEventAt: 0,
  serverSeenAt: 0,
  serverGoneAt: 0,
  terminalAt: 0,
  stoppedAt: 0,
  attach: false,
  attachedAt: 0,
  cardOpen: false,
}) as RunStatus;

/** One server row, as `lib/liveRuns.ts` reads it. */
export interface ServerRun {
  state?: "running" | "needs_input";
  /** False when no run runs: the question waits, and an answer starts a new
   *  run (parked). Absent from an older gateway, which means live. */
  live?: boolean;
  lastStep?: string | null;
  startedAt?: string | null;
}

export type RunEvent =
  /** This tab sent a turn. */
  | { type: "send"; at: number; messageId: string }
  /** A stream (live or re-attached) began to read the run. */
  | { type: "attach"; at: number; messageId: string }
  /** A stream event arrived. `step` is the step after it, in words. */
  | { type: "event"; at: number; step: string | null }
  /** A reader loop ended. `terminal`: it saw `done`. */
  | { type: "stream_end"; at: number; terminal: boolean }
  /** The turn failed: the error card shows. */
  | { type: "failed"; at: number }
  /** The send left no run: a refusal, the run cap, a held send. */
  | { type: "abandon"; at: number }
  /** The member pressed Stop. */
  | { type: "stop"; at: number }
  /** A re-attach could not start (another one held the thread, or a send
   *  took it). The next server list asks again. */
  | { type: "attach_skipped"; at: number }
  /** The chat mounted over a state kept in the store, with no stream that
   *  reads it: show "checking" until the server answers. */
  | { type: "remount"; at: number; streamOpen: boolean }
  /** A blocking card opened or closed in this chat. */
  | { type: "card"; at: number; open: boolean }
  /** A complete server list, taken at `at`. `run` is this thread's row. */
  | { type: "server"; at: number; run: ServerRun | null; streamOpen: boolean }
  /** Time passes. */
  | { type: "tick"; at: number; streamOpen: boolean };

function to(s: RunStatus, phase: RunPhase, at: number, extra: Partial<RunStatus> = {}): RunStatus {
  return { ...s, ...extra, phase, changedAt: phase === s.phase ? s.changedAt : at };
}

/** The end a run settles into: `done` when anything of it arrived. */
function settled(s: RunStatus, at: number): RunStatus {
  const seen = s.seq > 0 || s.serverSeenAt > 0 || s.phase === "working" || s.phase === "waiting_for_you";
  return to(s, seen ? "done" : "idle", at, { step: null, attach: false, serverGoneAt: 0 });
}

function parseTime(v: string | null | undefined): number | null {
  if (!v) return null;
  const t = Date.parse(v);
  return Number.isFinite(t) ? t : null;
}

/**
 * The reducer. Pure: the same state and event always give the same state,
 * and an event that changes nothing returns the SAME object, so the store
 * notifies nobody.
 */
export function reduceRun(s: RunStatus, e: RunEvent): RunStatus {
  switch (e.type) {
    case "send":
      return {
        ...IDLE_RUN,
        phase: "starting",
        startedAt: e.at,
        changedAt: e.at,
        messageId: e.messageId,
        lastEventAt: e.at,
        attachedAt: e.at,
        cardOpen: s.cardOpen,
      };

    case "attach": {
      const live = isLivePhase(s.phase) || s.phase === "parked";
      return to(s, s.phase === "waiting_for_you" ? s.phase : "working", e.at, {
        messageId: e.messageId,
        attach: false,
        attachedAt: e.at,
        startedAt: s.startedAt ?? e.at,
        lastEventAt: e.at,
        serverGoneAt: 0,
        // A re-attach to a run this tab did not see start keeps no old step.
        step: live ? s.step : null,
        seq: live ? s.seq : 0,
        terminalAt: 0,
      });
    }

    case "event": {
      if (s.phase === "stopped" && e.at - s.stoppedAt < STOP_HOLD_MS) return s;
      // Most events change only the clock. Write it once a second at most,
      // so a fast stream does not notify the store twice per token.
      const phase: RunPhase = s.phase === "waiting_for_you" && s.cardOpen ? "waiting_for_you" : "working";
      if (s.phase === phase && s.step === e.step && e.at - s.lastEventAt < 1_000) {
        return s.seq === 0 ? { ...s, seq: 1 } : s;
      }
      return to(s, phase, e.at, {
        step: e.step,
        seq: s.seq + 1,
        lastEventAt: e.at,
        serverGoneAt: 0,
        startedAt: s.startedAt ?? e.at,
        attach: false,
      });
    }

    case "stream_end":
      if (e.terminal) {
        if (!isLivePhase(s.phase) && s.phase !== "parked") return { ...s, terminalAt: e.at, attach: false };
        return to(s, "done", e.at, { step: null, terminalAt: e.at, attach: false, cardOpen: false });
      }
      // No terminal event: the proxy's budget, a restart, a lost network.
      // The run may go on. The server answers that, not the closed stream.
      if (!isLivePhase(s.phase)) return s;
      return to(s, "finishing", e.at, { attach: false });

    case "failed":
      return to(s, "failed", e.at, { step: null, terminalAt: e.at, attach: false });

    case "abandon":
      // A send the server never took: no run, and nothing to report.
      if (s.phase !== "starting") return s;
      return to(s, "idle", e.at, { step: null, attach: false, startedAt: null });

    case "stop":
      return to(s, "stopped", e.at, { step: null, stoppedAt: e.at, attach: false, cardOpen: false });

    case "attach_skipped":
      // `attachedAt` stays, so the next list asks again at once.
      return s.attach ? { ...s, attach: false } : s;

    case "remount": {
      if (e.streamOpen) return s;
      // No stream reads now, so a re-attach may start at once.
      const base = s.attachedAt === 0 ? s : { ...s, attachedAt: 0 };
      // A live phase kept from before an unmount is not known to be live.
      // "Checking" until the server answers.
      if (!(s.phase === "working" || s.phase === "starting" || s.phase === "waiting_for_you")) return base;
      return to(base, "finishing", e.at, { attach: false });
    }

    case "card": {
      if (s.cardOpen === e.open) return s;
      if (e.open && (s.phase === "working" || s.phase === "starting" || s.phase === "finishing")) {
        return to(s, "waiting_for_you", e.at, { cardOpen: true, step: null });
      }
      if (!e.open && s.phase === "waiting_for_you") {
        return to(s, "working", e.at, { cardOpen: false, step: null, lastEventAt: e.at });
      }
      return { ...s, cardOpen: e.open };
    }

    case "server":
      return reduceServer(s, e);

    case "tick":
      return reduceTick(s, e.at, e.streamOpen);
  }
}

function reduceServer(
  s: RunStatus,
  e: { at: number; run: ServerRun | null; streamOpen: boolean },
): RunStatus {
  const { at, run, streamOpen } = e;
  if (run) {
    // A list from before a Stop or a terminal event lags it. Only a list
    // taken well after names a NEW run.
    if (s.phase === "stopped" && at - s.stoppedAt < STOP_HOLD_MS) return s;
    if ((s.phase === "done" || s.phase === "failed") && at - s.terminalAt < DONE_HOLD_MS) return s;
    const seen = { serverSeenAt: Math.max(s.serverSeenAt, at), serverGoneAt: 0 };
    const resting = !isLivePhase(s.phase) && s.phase !== "parked";
    // A list that names a run while this chat rests names a NEW run: one
    // started in another tab, on a phone, or by a room member. It gets its
    // own bubble, so nothing of the last run (its reply, its step, its
    // start) carries over (review of #1171, P1).
    const fresh: Partial<RunStatus> = resting
      ? { messageId: null, step: null, seq: 0, terminalAt: 0, cardOpen: false, startedAt: parseTime(run.startedAt) ?? at }
      : {};
    const startedAt = resting ? fresh.startedAt! : s.startedAt ?? parseTime(run.startedAt) ?? at;
    const canAttach = !streamOpen && at - s.attachedAt >= ATTACH_GAP_MS;
    if (run.state === "needs_input") {
      if (run.live === false) {
        // No run runs. The card waits, and an answer starts a new run.
        return to(s, "parked", at, { ...fresh, ...seen, step: null, attach: false, startedAt });
      }
      return to(s, "waiting_for_you", at, { ...fresh, ...seen, step: null, startedAt, attach: canAttach });
    }
    const step = run.lastStep?.trim() || (resting ? null : s.step);
    const phase: RunPhase = s.phase === "waiting_for_you" && s.cardOpen ? "waiting_for_you" : "working";
    const next = to(s, phase, at, {
      ...fresh,
      ...seen,
      // The stream's own step is fresher than the list's while it reads.
      step: streamOpen && !resting && s.step ? s.step : step,
      startedAt,
      attach: canAttach,
    });
    return sameRun(s, next) ? s : next;
  }

  // The server does not name this thread: no run, and no question waits.
  if (s.phase === "parked") return settled(s, at);
  if (!isLivePhase(s.phase)) return s;
  if (!streamOpen) {
    // A list taken before this phase began says nothing about it.
    if (at < s.changedAt) return s;
    return settled(s, at);
  }
  // The list can lag a run that has just started.
  if (s.startedAt !== null && at < s.startedAt + START_GRACE_MS) return s;
  // A stream still reads. It is the authority while it brings events.
  const goneAt = s.serverGoneAt || at;
  if (at - goneAt >= GONE_MS && at - s.lastEventAt >= GONE_MS) {
    return to(s, "finishing", at, { serverGoneAt: goneAt, attach: false });
  }
  return goneAt === s.serverGoneAt ? s : { ...s, serverGoneAt: goneAt };
}

function sameRun(a: RunStatus, b: RunStatus): boolean {
  return (
    a.phase === b.phase && a.step === b.step && a.startedAt === b.startedAt &&
    a.serverSeenAt === b.serverSeenAt && a.serverGoneAt === b.serverGoneAt &&
    a.attach === b.attach && a.seq === b.seq && a.terminalAt === b.terminalAt &&
    a.messageId === b.messageId && a.cardOpen === b.cardOpen
  );
}

function reduceTick(s: RunStatus, at: number, streamOpen: boolean): RunStatus {
  if (s.phase === "finishing" && at - s.changedAt >= FINISH_MAX_MS) return settled(s, at);
  if ((s.phase === "working" || s.phase === "starting" || s.phase === "waiting_for_you") && !streamOpen) {
    const heard = Math.max(s.lastEventAt, s.serverSeenAt, s.changedAt);
    if (at - heard >= STALE_MS) return settled(s, at);
  }
  return s;
}

// ── What the row draws ─────────────────────────────────────────────────────

export type RunTone = "working" | "waiting" | "parked" | "finishing" | "done" | "stopped";

export interface RunView {
  tone: RunTone;
  /** The words, with no elapsed time. */
  label: string;
  /** The elapsed time in words, or null. */
  elapsed: string | null;
}

/** "42s", "1m 12s", "1h 4m". */
export function formatElapsed(ms: number): string {
  const sec = Math.max(0, Math.floor(ms / 1000));
  if (sec < 60) return `${sec}s`;
  const min = Math.floor(sec / 60);
  if (min < 60) return `${min}m ${sec % 60}s`;
  return `${Math.floor(min / 60)}h ${min % 60}m`;
}

/**
 * What the status row says, or null for nothing. `done` and `stopped` show
 * for `RESTING_SHOW_MS`, then the row goes quiet. `failed` draws nothing
 * here: the error card in the thread says it.
 */
export function runView(s: RunStatus, now: number): RunView | null {
  const elapsed = s.startedAt !== null ? formatElapsed(now - s.startedAt) : null;
  switch (s.phase) {
    case "starting":
      return { tone: "working", label: "Starting", elapsed };
    case "working":
      return { tone: "working", label: s.step || "Working", elapsed };
    case "waiting_for_you":
      return { tone: "waiting", label: "Waiting for your answer", elapsed };
    case "parked":
      return { tone: "parked", label: "Paused. Answer the card to go on", elapsed: null };
    case "finishing":
      return { tone: "finishing", label: "Checking the run", elapsed };
    case "done":
      if (now - s.changedAt >= RESTING_SHOW_MS) return null;
      // The time counts to the run's real end, its terminal event. A run
      // that settled because the server stopped listing it has no known
      // end, so it shows no time (review of #1171, P2).
      return {
        tone: "done",
        label: "Done",
        elapsed: s.terminalAt > 0 && s.startedAt !== null ? formatElapsed(s.terminalAt - s.startedAt) : null,
      };
    case "stopped":
      if (now - s.changedAt >= RESTING_SHOW_MS) return null;
      return { tone: "stopped", label: "Stopped", elapsed: null };
    default:
      return null;
  }
}

/** The row's spoken and written line: "Asking Projects assistant · 1m 12s". */
export function runViewText(v: RunView): string {
  return v.elapsed ? `${v.label} · ${v.elapsed}` : v.label;
}

// ── No flicker ─────────────────────────────────────────────────────────────

/**
 * Show or hide, with no flash. The row appears only when the target has
 * wanted it for `FLASH_MS`, and goes only when the target has wanted it gone
 * for `FLASH_MS`. While it shows, only the words change. `immediate` skips
 * the wait to appear: the member pressed Send, and the row answers at once.
 *
 * `pendingSince` is when the target first differed from what shows, or 0.
 * Returns what shows now and the new `pendingSince`.
 */
export function steadyPresence(
  shown: boolean,
  want: boolean,
  pendingSince: number,
  now: number,
  immediate = false,
): { shown: boolean; pendingSince: number } {
  if (want === shown) return { shown, pendingSince: 0 };
  if (want && immediate) return { shown: true, pendingSince: 0 };
  const since = pendingSince || now;
  if (now - since >= FLASH_MS) return { shown: want, pendingSince: 0 };
  return { shown, pendingSince: since };
}
