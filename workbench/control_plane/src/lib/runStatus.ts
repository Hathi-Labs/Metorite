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

import { placementOf } from "@/lib/chatPlacement";

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

/**
 * True for each phase a run rests in: it ended, or none runs. A cut stream
 * is not one: `finishing` asks the server, and the run may go on.
 */
export function isRestingPhase(p: RunPhase): boolean {
  return p === "done" || p === "stopped" || p === "failed" || p === "idle";
}

/**
 * A rest that a slot of a redraft may end on (§18.11, fix round 5): any rest
 * but one that the `FINISH_MAX_MS` settle alone made. A cut stream with no
 * server answer can still be a live run, and its next draft would then come
 * with no slot to take.
 */
export function restConfirmed(s: Pick<RunStatus, "phase" | "restBlind">): boolean {
  return isRestingPhase(s.phase) && !s.restBlind;
}

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
/** The longest wait between re-attaches that stream nothing. */
export const ATTACH_MAX_GAP_MS = 60_000;

/** The wait before the next re-attach, after `fails` that streamed nothing. */
export function attachGapMs(fails: number): number {
  return Math.min(ATTACH_MAX_GAP_MS, ATTACH_GAP_MS * 2 ** Math.max(0, Math.min(fails, 10)));
}
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
  /** The reply of the last run that ended in this chat. A later run never
   *  replays into it. */
  doneMessageId: string | null;
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
  /** Re-attaches in a row that streamed nothing. Each one doubles the wait
   *  before the next, up to `ATTACH_MAX_GAP_MS`. */
  attachFails: number;
  /**
   * The server's identity of the run: its run id when the row has one, else
   * its start time (re-review of #1171). A listed run with ANOTHER identity
   * is a new run in every phase. Null until a server row names it.
   */
  runKey: string | null;
  /**
   * The server named a run that is NOT the one this chat streamed. A reply
   * the chat still holds as streaming belongs to the old run, so the attach
   * settles it and never replays into it.
   */
  staleTail: boolean;
  /**
   * A mount found a live phase kept in the store, with no stream. The run is
   * not known to be live: the row says "checking", and the composer offers
   * no Stop until the server lists the run.
   */
  checking: boolean;
  /** Ids of the write tools the run started. A replay sends an id again, so
   *  an id counts once. */
  writesAsked: readonly string[];
  /** Ids of the write tools that ended with success: the changes made. */
  writesDone: readonly string[];
  /**
   * The run rests only because no word came for `FINISH_MAX_MS` (§18.11, fix
   * round 5). The next server list either names the run, which works again,
   * or not, which confirms the rest and clears this. A slot of a redraft
   * ends only on a confirmed rest (`restConfirmed`).
   */
  restBlind: boolean;
  /**
   * CS-3b: the fixed words of the server's phase ("Gathering what I need"), or null. It lives
   * only while the run has no real step: the first event, the end of the run and a re-attach
   * clear it. Never saved, and never a server string (`RUN_PHASE_WORDS` in `runStep.ts`).
   */
  phaseWord: string | null;
  /**
   * RESUME-3a: the chat rests after a park (`parked`, or the quiet rest that
   * follows it). A run that the server lists next is a continuation, never
   * the parked run, so it never replays into the parked reply.
   */
  afterPark: boolean;
  /**
   * RESUME-3a: when this tab's Approve started a continuation run (the 200
   * `resumed`), epoch ms, else 0. A list taken before it says nothing about
   * that run.
   */
  resumedAt: number;
  /**
   * RESUME-3b: the key of the run that parked on its card, when the server
   * names it (the parked row's `parkedRunId`) or the Approve's 200 says
   * `sameReply`. A late Approve of case A goes on under that run id, in the
   * parked reply. So a listed run with this key is the SAME run: it is not
   * new, and it replays into the parked reply. Null until a park names it.
   */
  parkedRunKey: string | null;
  /**
   * RESUME-3d: the reply row that the server list names for a live
   * continuation run (the row's `messageId`). The attach replays into this
   * row, with no read of the saved rows. That is the parked reply in case A,
   * and the new reply in case B. Null when the last list named none.
   */
  listedReplyId: string | null;
}

export const IDLE_RUN: RunStatus = Object.freeze({
  phase: "idle",
  step: null,
  startedAt: null,
  changedAt: 0,
  messageId: null,
  doneMessageId: null,
  seq: 0,
  lastEventAt: 0,
  serverSeenAt: 0,
  serverGoneAt: 0,
  terminalAt: 0,
  stoppedAt: 0,
  attach: false,
  attachedAt: 0,
  cardOpen: false,
  attachFails: 0,
  runKey: null,
  staleTail: false,
  checking: false,
  writesAsked: [],
  writesDone: [],
  restBlind: false,
  phaseWord: null,
  afterPark: false,
  resumedAt: 0,
  parkedRunKey: null,
  listedReplyId: null,
}) as RunStatus;

/** One server row, as `lib/liveRuns.ts` reads it. */
export interface ServerRun {
  state?: "running" | "needs_input";
  /** False when no run runs: the question waits, and an answer starts a new
   *  run (parked). Absent from an older gateway, which means live. */
  live?: boolean;
  lastStep?: string | null;
  startedAt?: string | null;
  /** The run's id, from a gateway that sends one. */
  runId?: string | null;
  /** RESUME-3b: on a parked row, the id of the run that parked. Only a
   *  gateway with both resume flags on for the org sends it. */
  parkedRunId?: string | null;
  /** RESUME-3d: on the row of a live continuation run, its reply row. Only a
   *  gateway with both resume flags on for the org sends it. */
  messageId?: string | null;
}

/** RESUME-3d: the reply row that a live row names, or null. A parked row
 *  has no run, so it names none. */
function listedReplyOf(run: ServerRun): string | null {
  if (run.state === "needs_input" && run.live === false) return null;
  const id = typeof run.messageId === "string" ? run.messageId.trim() : "";
  return id || null;
}

/** RESUME-3b: the key of the run that a parked row names, or null. */
function parkedKeyOf(run: ServerRun): string | null {
  if (run.state !== "needs_input" || run.live !== false) return null;
  const id = typeof run.parkedRunId === "string" ? run.parkedRunId.trim() : "";
  return id ? runKeyOf({ runId: id }) : null;
}

export type RunEvent =
  /** This tab sent a turn. */
  | { type: "send"; at: number; messageId: string }
  /** A stream (live or re-attached) began to read the run. */
  | { type: "attach"; at: number; messageId: string }
  /** A stream event arrived. `step` is the step after it, in words. */
  | { type: "event"; at: number; step: string | null }
  /** A reader loop ended. `terminal`: it saw `done`. `parked`: that `done`
   *  was a park (RESUME-3a). The card waits, and the run is not done. */
  | { type: "stream_end"; at: number; terminal: boolean; parked?: boolean }
  /** RESUME-3a: an Approve of a parked card started a continuation run (the
   *  200 `resumed` of `respond-input`). `messageId`: its reply row, when the
   *  server names it. RESUME-3b: `sameReply` is true when the run goes on in
   *  the parked reply, under the parked run's id (case A). */
  | { type: "resumed"; at: number; runId: string; messageId?: string | null; sameReply?: boolean }
  /** The turn failed: the error card shows. */
  | { type: "failed"; at: number }
  /** The send left no run: a refusal, the run cap, a held send. */
  | { type: "abandon"; at: number }
  /** The member pressed Stop. */
  | { type: "stop"; at: number }
  /** A re-attach could not start (another one held the thread, or a send
   *  took it). The next server list asks again. */
  | { type: "attach_skipped"; at: number }
  /** A re-attach got no stream (refused, or no run to replay). The next one
   *  waits longer, so a run that cannot replay is never asked in a loop. */
  | { type: "attach_failed"; at: number }
  /** The chat mounted over a state kept in the store, with no stream that
   *  reads it: show "checking" until the server answers. */
  | { type: "remount"; at: number; streamOpen: boolean }
  /** A tool call began. Only a write counts, as `chatPlacement` names it. */
  | { type: "toolStart"; at: number; id: string; name: string }
  /** A tool call ended. Only a successful write counts as a change made. */
  | { type: "toolDone"; at: number; id: string; name: string; ok: boolean }
  /** A blocking card opened or closed in this chat. */
  | { type: "card"; at: number; open: boolean }
  /** A complete server list, taken at `at`. `run` is this thread's row. */
  | { type: "server"; at: number; run: ServerRun | null; streamOpen: boolean }
  /** CS-3b: the server's phase of the run, as fixed words (`phaseWordOf`). It moves no phase of the
   *  run: only the words of the row change, and only before the first real event. */
  | { type: "phase"; at: number; word: string }
  /** Time passes. */
  | { type: "tick"; at: number; streamOpen: boolean };

function to(s: RunStatus, phase: RunPhase, at: number, extra: Partial<RunStatus> = {}): RunStatus {
  const next = { ...s, ...extra, phase, changedAt: phase === s.phase ? s.changedAt : at };
  // A blind rest lasts only while the phase it marks lasts.
  if (phase !== s.phase && !extra.restBlind) next.restBlind = false;
  // "Checking" lasts only while a remount waits in `finishing`.
  if (phase !== "finishing" && next.checking) next.checking = false;
  // The phase words last only while the run starts or works (CS-3b).
  if (phase !== "starting" && phase !== "working") next.phaseWord = null;
  return next;
}

/** The server's identity of a run: its id, else its start time, else null. */
export function runKeyOf(run: ServerRun | null | undefined): string | null {
  if (!run) return null;
  const id = typeof run.runId === "string" ? run.runId.trim() : "";
  if (id) return `id:${id}`;
  const t = typeof run.startedAt === "string" ? run.startedAt.trim() : "";
  return t ? `at:${t}` : null;
}

/**
 * What a composer may offer: Stop and "working…" (owner nit on #1171). A
 * remount's "checking" offers neither until the server lists the run.
 */
export function isRunActiveForUi(s: RunStatus): boolean {
  return isLivePhase(s.phase) && !s.checking;
}

/** The end a run settles into: `done` when anything of it arrived. */
function settled(s: RunStatus, at: number): RunStatus {
  const seen = s.seq > 0 || s.serverSeenAt > 0 || s.checking || s.phase === "working" || s.phase === "waiting_for_you";
  return to(s, seen ? "done" : "idle", at, {
    step: null, attach: false, serverGoneAt: 0, doneMessageId: s.messageId ?? s.doneMessageId,
  });
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
        doneMessageId: s.doneMessageId,
        // The new run's identity binds from the first server row after it.
        runKey: null,
        phaseWord: null,
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
        writesAsked: live ? s.writesAsked : [],
        writesDone: live ? s.writesDone : [],
        terminalAt: 0,
        staleTail: false,
        // CS-3b: a joined run has no phase until a new one arrives.
        phaseWord: null,
        afterPark: false,
      });
    }

    case "resumed": {
      // RESUME-3a (§4.12.4 item 5): this tab's Approve started a NEW run. It
      // is new even when no run key is known, as after a reload, where the
      // parked row has no run id (C1). Nothing of the parked run carries
      // over, and the parked reply is never its target.
      const key = runKeyOf({ runId: e.runId });
      if (!key) return s;
      // RESUME-3b: the parked run's own key, while the chat still rests after
      // its park. Only case A keeps the run id, so this is the same reply.
      const parkedOwn = s.runKey === key && (s.phase === "parked" || s.afterPark);
      // The live-run list may have bound and attached this run before the
      // 200 arrived. Do not reset it: keep its reply id and stale-tail flag.
      if (s.runKey === key && !parkedOwn) return s.resumedAt === e.at ? s : { ...s, resumedAt: e.at };
      if (e.sameReply === true || key === s.parkedRunKey || parkedOwn) {
        // RESUME-3b, case A: the parked run goes on in the parked reply. That
        // reply is the target, and never a reply that a replay must refuse.
        const target = e.messageId || s.messageId || null;
        return to(s, "working", e.at, {
          messageId: target,
          doneMessageId: target && s.doneMessageId !== target ? s.doneMessageId : null,
          step: null, seq: 0, terminalAt: 0, cardOpen: false, phaseWord: null,
          writesAsked: [], writesDone: [],
          startedAt: e.at,
          lastEventAt: e.at,
          serverGoneAt: 0,
          runKey: key,
          parkedRunKey: key,
          staleTail: false,
          attach: false,
          attachedAt: 0,
          attachFails: 0,
          afterPark: false,
          resumedAt: e.at,
          listedReplyId: null,
        });
      }
      return to(s, "working", e.at, {
        messageId: e.messageId || null,
        doneMessageId: s.messageId ?? s.doneMessageId,
        step: null, seq: 0, terminalAt: 0, cardOpen: false, phaseWord: null,
        writesAsked: [], writesDone: [],
        startedAt: e.at,
        lastEventAt: e.at,
        serverGoneAt: 0,
        runKey: key,
        staleTail: true,
        attach: false,
        attachedAt: 0,
        attachFails: 0,
        afterPark: false,
        resumedAt: e.at,
        listedReplyId: null,
      });
    }

    case "phase": {
      // Only a run that has shown no real event takes a phase, and the phase moves nothing else.
      if (s.phase !== "starting" && s.phase !== "working") return s;
      if (s.seq > 0 || s.step) return s;
      return s.phaseWord === e.word ? s : { ...s, phaseWord: e.word };
    }

    case "event": {
      if (s.phase === "stopped" && e.at - s.stoppedAt < STOP_HOLD_MS) return s;
      // Most events change only the clock. Write it once a second at most,
      // so a fast stream does not notify the store twice per token.
      const phase: RunPhase = s.phase === "waiting_for_you" && s.cardOpen ? "waiting_for_you" : "working";
      if (s.phase === phase && s.step === e.step && e.at - s.lastEventAt < 1_000) {
        return s.seq === 0 ? { ...s, seq: 1, phaseWord: null } : s;
      }
      return to(s, phase, e.at, {
        phaseWord: null,
        attachFails: 0,
        step: e.step,
        seq: s.seq + 1,
        lastEventAt: e.at,
        serverGoneAt: 0,
        startedAt: s.startedAt ?? e.at,
        attach: false,
      });
    }

    case "stream_end":
      if (e.terminal && e.parked) {
        // RESUME-3a: the run parked on its card (D-RES-6). It ended, but it
        // is not done: the card stays, and the row says the run waits.
        if (!isLivePhase(s.phase) && s.phase !== "parked") return { ...s, terminalAt: e.at, attach: false };
        return to(s, "parked", e.at, {
          step: null, terminalAt: e.at, attach: false, afterPark: true,
          doneMessageId: s.messageId ?? s.doneMessageId,
        });
      }
      if (e.terminal) {
        if (!isLivePhase(s.phase) && s.phase !== "parked") return { ...s, terminalAt: e.at, attach: false };
        return to(s, "done", e.at, {
          step: null, terminalAt: e.at, attach: false, cardOpen: false, doneMessageId: s.messageId ?? s.doneMessageId,
        });
      }
      // No terminal event: the proxy's budget, a restart, a lost network.
      // The run may go on. The server answers that, not the closed stream.
      if (!isLivePhase(s.phase)) return s;
      return to(s, "finishing", e.at, { attach: false });

    case "failed":
      return to(s, "failed", e.at, {
        step: null, terminalAt: e.at, attach: false, doneMessageId: s.messageId ?? s.doneMessageId,
      });

    case "abandon":
      // A send the server never took: no run, and nothing to report.
      if (s.phase !== "starting") return s;
      return to(s, "idle", e.at, { step: null, attach: false, startedAt: null });

    case "stop":
      return to(s, "stopped", e.at, {
        step: null, stoppedAt: e.at, attach: false, cardOpen: false, doneMessageId: s.messageId ?? s.doneMessageId,
      });

    case "toolStart":
      if (placementOf(e.name) !== "write" || s.writesAsked.includes(e.id)) return s;
      return { ...s, writesAsked: [...s.writesAsked, e.id] };

    case "toolDone": {
      if (!e.ok || placementOf(e.name) !== "write" || s.writesDone.includes(e.id)) return s;
      return {
        ...s,
        writesDone: [...s.writesDone, e.id],
        // A write that ended was asked, even if its start was not seen.
        writesAsked: s.writesAsked.includes(e.id) ? s.writesAsked : [...s.writesAsked, e.id],
      };
    }

    case "attach_skipped":
      // `attachedAt` stays, so the next list asks again at once.
      return s.attach ? { ...s, attach: false } : s;

    case "attach_failed":
      return { ...s, attach: false, attachFails: s.attachFails + 1, attachedAt: e.at };

    case "remount": {
      if (e.streamOpen) return s;
      // No stream reads now, so a re-attach may start at once. And no kept
      // target is trusted: the run may have ended, and another may have
      // started, while the chat was away (re-review of #1171). The attach
      // finds the target again, by the server's rows.
      const base = s.attachedAt === 0 && s.messageId === null && s.seq === 0 && s.step === null && s.listedReplyId === null
        ? s
        : { ...s, attachedAt: 0, messageId: null, seq: 0, step: null, listedReplyId: null };
      // A live phase kept from before an unmount is not known to be live.
      // "Checking" until the server answers.
      if (!(s.phase === "working" || s.phase === "starting" || s.phase === "waiting_for_you" || s.phase === "finishing")) return base;
      return to(base, "finishing", e.at, { attach: false, checking: true, attachFails: 0 });
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
    // A list from before this tab's send cannot hold its run.
    if (s.phase === "starting" && s.startedAt !== null && at < s.startedAt) return s;
    // Nor a list from before the Approve that started a continuation.
    if (s.resumedAt > 0 && at < s.resumedAt) return s;
    const key = runKeyOf(run);
    // RESUME-3b: the run that a parked row names, by `parkedRunId`. A later
    // list that names this key lists the parked run itself, gone on (case A).
    const parkedKey = parkedKeyOf(run);
    // ANOTHER identity than the run this chat knows is a new run, in every
    // phase (re-review of #1171): another tab, a phone or a room member
    // started it while this chat was away or idle. Two keys compare only in
    // kind: a run id with a run id, a start time with a start time.
    // Defence in depth: a start time can move once early in a run (an old
    // gateway registers the run twice), so while this tab's stream reads the
    // run, a changed start time with no run id is the SAME run.
    const other = key !== null && s.runKey !== null && key !== s.runKey &&
      key !== s.parkedRunKey &&
      key.slice(0, 3) === s.runKey.slice(0, 3) &&
      !(streamOpen && key.startsWith("at:"));
    const same = key !== null && (key === s.runKey || key === s.parkedRunKey);
    // A park's own `done` (RESUME-3a) holds a lagging list as a `done` does.
    const ended = s.phase === "done" || s.phase === "failed" || (s.phase === "parked" && s.terminalAt > 0);
    if (!other && ended && at - s.terminalAt < DONE_HOLD_MS) return s;
    const seen = { serverSeenAt: Math.max(s.serverSeenAt, at), serverGoneAt: 0, runKey: key ?? s.runKey };
    const resting = !isLivePhase(s.phase) && s.phase !== "parked";
    // RESUME-3a (C1): after a park, a run that the server lists as running or
    // as waiting live is a continuation, never the parked run. After a reload
    // the parked row has no run id, so the keys cannot tell the two apart.
    const parkedRow = run.state === "needs_input" && run.live === false;
    const afterPark = (s.phase === "parked" || s.afterPark) && !parkedRow && !same;
    // A list that names a run while this chat rests names a NEW run too,
    // unless its identity says it is the same one (review of #1171, P1).
    // A new run gets its own bubble: nothing of the last run (its reply, its
    // step, its start) carries over.
    const isNew = other || (resting && !same) || afterPark;
    const fresh: Partial<RunStatus> = isNew
      ? {
        messageId: null, step: null, seq: 0, terminalAt: 0, cardOpen: false, phaseWord: null,
        writesAsked: [], writesDone: [],
        startedAt: parseTime(run.startedAt) ?? at,
        // The old run's streaming reply, if any, is not this run's. After a
        // park, no reply that shows is this run's.
        staleTail: other || afterPark,
        // The old run's failed re-attaches say nothing about this one
        // (review of #1171, P2): it may attach at once.
        attachFails: 0,
        attachedAt: 0,
        afterPark: false,
      }
      : {};
    // RESUME-3b, case A: after its park, the SAME run goes on in the parked
    // reply. It is not new and has no stale tail. Its reply stays the target,
    // so a replay must not refuse it (`doneMessageId`). It may attach at once.
    const continued = same && !parkedRow && (s.phase === "parked" || s.afterPark);
    const goOn: Partial<RunStatus> = continued
      ? {
        step: null, seq: 0, terminalAt: 0, cardOpen: false, phaseWord: null,
        writesAsked: [], writesDone: [],
        startedAt: parseTime(run.startedAt) ?? at,
        staleTail: false,
        attachFails: 0,
        attachedAt: 0,
        afterPark: false,
        doneMessageId: s.messageId && s.doneMessageId !== s.messageId ? s.doneMessageId : null,
      }
      : {};
    // A parked row names its run. A new run forgets the old one's key. A run
    // that goes on after its park keeps its key as the parked key, also when
    // only the run key found it (no `parkedRunId`), so the line of case A
    // draws under its reply (review of 2026-11-19, P2-1).
    const parked = { parkedRunKey: parkedKey ?? (continued ? key : isNew ? null : s.parkedRunKey) };
    // RESUME-3d: a live continuation that the list names with its reply row
    // replays into that row. A new run takes it as its reply. A known run
    // keeps the reply that this tab holds, and takes the listed one when it
    // holds none (after a remount). A list with no reply row clears it, so a
    // gateway with a flag off gives the state of before.
    const listed = listedReplyOf(run);
    const reply: Partial<RunStatus> = listed
      ? { listedReplyId: listed, ...(isNew || !s.messageId ? { messageId: listed } : {}) }
      : s.listedReplyId === null ? {} : { listedReplyId: null };
    const startedAt = isNew ? fresh.startedAt! : continued ? goOn.startedAt! : s.startedAt ?? parseTime(run.startedAt) ?? at;
    const since = isNew || continued ? 0 : s.attachedAt;
    const canAttach = !streamOpen && at - since >= attachGapMs(isNew || continued ? 0 : s.attachFails);
    if (run.state === "needs_input") {
      if (run.live === false) {
        // No run runs. The card waits, and an answer starts a new run.
        return to(s, "parked", at, { ...fresh, ...seen, ...parked, ...reply, step: null, attach: false, startedAt, afterPark: true });
      }
      return to(s, "waiting_for_you", at, { ...fresh, ...goOn, ...seen, ...parked, ...reply, step: null, startedAt, attach: canAttach });
    }
    const step = run.lastStep?.trim() || (isNew || continued ? null : s.step);
    const phase: RunPhase = !isNew && s.phase === "waiting_for_you" && s.cardOpen ? "waiting_for_you" : "working";
    const next = to(s, phase, at, {
      ...fresh,
      ...goOn,
      ...seen,
      ...parked,
      ...reply,
      // The stream's own step is fresher than the list's while it reads.
      step: streamOpen && !isNew && s.step ? s.step : step,
      startedAt,
      attach: canAttach,
    });
    return sameRun(s, next) ? s : next;
  }

  // The server does not name this thread: no run, and no question waits.
  // A blind rest is now a known one (§18.11, fix round 5).
  if (s.restBlind) return { ...s, restBlind: false };
  if (s.phase === "parked") {
    // RESUME-3a: the card was answered or closed elsewhere. A park never
    // reads "Done": the run had ended already, so the row goes quiet. The
    // rest keeps `afterPark`, so a continuation listed next is a new run.
    return to(s, "idle", at, {
      step: null, attach: false, serverGoneAt: 0, cardOpen: false, afterPark: true,
      doneMessageId: s.messageId ?? s.doneMessageId,
    });
  }
  if (!isLivePhase(s.phase)) return s;
  // The list can lag a continuation that this tab's Approve started.
  if (s.resumedAt > 0 && at < s.resumedAt + START_GRACE_MS) return s;
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
    a.messageId === b.messageId && a.cardOpen === b.cardOpen &&
    a.runKey === b.runKey && a.staleTail === b.staleTail && a.checking === b.checking &&
    a.parkedRunKey === b.parkedRunKey && a.listedReplyId === b.listedReplyId
  );
}

function reduceTick(s: RunStatus, at: number, streamOpen: boolean): RunStatus {
  if (s.phase === "finishing" && at - s.changedAt >= FINISH_MAX_MS) {
    // No word for this long: rest, but blind, until a server list says (§18.11).
    const out = settled(s, at);
    return out.phase === s.phase ? out : { ...out, restBlind: true };
  }
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
 * The words of the first line (CS-1, `chat_failure_modes.md`). They show from the moment of Send, before
 * the server has said anything, because the first model request takes 5 to 10 seconds. The line
 * says what Mira does now: she reads the message. One fixed phrase, never model text.
 */
export const FIRST_STEP = "Reading your message";

/** The words of a card that waits, live or parked (D-RES-6, §4.12.4 item 5). */
export const WAITING_LABEL = "Waiting for your answer";

/**
 * What the status row says, or null for nothing. `done` and `stopped` show
 * for `RESTING_SHOW_MS`, then the row goes quiet. `failed` draws nothing
 * here: the error card in the thread says it.
 */
export function runView(s: RunStatus, now: number): RunView | null {
  const elapsed = s.startedAt !== null ? formatElapsed(now - s.startedAt) : null;
  switch (s.phase) {
    case "starting":
      return { tone: "working", label: s.phaseWord || FIRST_STEP, elapsed };
    case "working":
      return { tone: "working", label: s.step || s.phaseWord || "Working", elapsed };
    case "waiting_for_you":
      return { tone: "waiting", label: WAITING_LABEL, elapsed };
    case "parked":
      // D-RES-6: a park is a server detail. The member reads the words of a
      // live wait, with no clock. The tone stays, so the composer sends.
      return { tone: "parked", label: WAITING_LABEL, elapsed: null };
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
      return { tone: "stopped", label: stoppedLabel(s), elapsed: null };
    default:
      return null;
  }
}

/**
 * "Stopped · made 2 of 5 changes", "Stopped · made 2 changes" when the total
 * is not above the made count, and "Stopped" when the run made none.
 */
function stoppedLabel(s: RunStatus): string {
  const made = s.writesDone.length;
  if (made === 0) return "Stopped";
  const asked = s.writesAsked.length;
  if (asked > made) return `Stopped · made ${made} of ${asked} changes`;
  return `Stopped · made ${made} ${made === 1 ? "change" : "changes"}`;
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
