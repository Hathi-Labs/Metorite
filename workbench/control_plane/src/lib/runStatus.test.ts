/**
 * The one run state of an open chat (owner report, 2026-10-19,
 * `chat_run_continuity.md` §4.10). Each block names the symptom it fences.
 */

import { describe, expect, it } from "vitest";

import type { ChatMessage } from "@/lib/chatStore";
import {
  ATTACH_GAP_MS,
  DONE_HOLD_MS,
  FINISH_MAX_MS,
  FLASH_MS,
  GONE_MS,
  IDLE_RUN,
  RESTING_SHOW_MS,
  STALE_MS,
  START_GRACE_MS,
  STOP_HOLD_MS,
  formatElapsed,
  isLivePhase,
  reduceRun,
  runView,
  runViewText,
  steadyPresence,
  type RunEvent,
  type RunStatus,
  type ServerRun,
} from "./runStatus";
import { THINKING_STEP, WRITING_STEP, stepOfMessage } from "./runStep";

const T0 = 1_760_000_000_000;
const run = (s: RunStatus, ...events: RunEvent[]) => events.reduce(reduceRun, s);
const send = (at = T0): RunEvent => ({ type: "send", at, messageId: "a1" });
const ev = (at: number, step: string | null = "Search tasks"): RunEvent => ({ type: "event", at, step });
const server = (at: number, row: ServerRun | null, streamOpen = false): RunEvent => ({ type: "server", at, run: row, streamOpen });
const RUNNING: ServerRun = { state: "running", live: true };
const working = () => run(IDLE_RUN, send(), ev(T0 + 500));

describe("the phases", () => {
  it("only starting, working, waiting and finishing are live", () => {
    expect(["starting", "working", "waiting_for_you", "finishing"].every((p) => isLivePhase(p as never))).toBe(true);
    expect(["idle", "parked", "done", "failed", "stopped"].some((p) => isLivePhase(p as never))).toBe(false);
  });
});

describe("a run this tab sends", () => {
  it("idle → starting → working → done", () => {
    const s1 = run(IDLE_RUN, send());
    expect(s1.phase).toBe("starting");
    expect(s1.startedAt).toBe(T0);
    const s2 = reduceRun(s1, ev(T0 + 200));
    expect(s2.phase).toBe("working");
    expect(s2.step).toBe("Search tasks");
    const s3 = reduceRun(s2, { type: "stream_end", at: T0 + 9_000, terminal: true });
    expect(s3.phase).toBe("done");
    expect(s3.step).toBeNull();
  });

  it("a failed turn is failed, and a refused send leaves no run", () => {
    expect(reduceRun(working(), { type: "failed", at: T0 + 1 }).phase).toBe("failed");
    expect(reduceRun(run(IDLE_RUN, send()), { type: "abandon", at: T0 + 1 }).phase).toBe("idle");
    // A run that already streamed is not abandoned by a late refusal.
    expect(reduceRun(working(), { type: "abandon", at: T0 + 1 }).phase).toBe("working");
  });

  it("Stop is stopped, and a list that still names the run is the cancel's lag", () => {
    const s = reduceRun(working(), { type: "stop", at: T0 + 1_000 });
    expect(s.phase).toBe("stopped");
    expect(reduceRun(s, server(T0 + 2_000, RUNNING)).phase).toBe("stopped");
    expect(reduceRun(s, ev(T0 + 2_000)).phase).toBe("stopped");
    // Well after: a new run (another tab, a room member).
    expect(reduceRun(s, server(T0 + 1_000 + STOP_HOLD_MS, RUNNING)).phase).toBe("working");
  });

  it("an event that changes nothing returns the same state, so nobody re-renders", () => {
    const s = working();
    expect(reduceRun(s, ev(T0 + 600))).toBe(s);
    expect(reduceRun(s, ev(T0 + 2_000))).not.toBe(s);
  });
});

describe("a stream that closes with no terminal event (the owner's symptom)", () => {
  // The proxy cut the owner's long delegated run at 310 s. The chat read the
  // clean close as "done", and showed Send while the run went on.
  it("is finishing, never done", () => {
    const s = reduceRun(working(), { type: "stream_end", at: T0 + 310_000, terminal: false });
    expect(s.phase).toBe("finishing");
    expect(isLivePhase(s.phase)).toBe(true);
  });

  it("goes back to working, and asks to re-attach, when the server still lists it", () => {
    const f = reduceRun(working(), { type: "stream_end", at: T0 + 310_000, terminal: false });
    const s = reduceRun(f, server(T0 + 310_100, { ...RUNNING, lastStep: "Asking Projects assistant" }));
    expect(s.phase).toBe("working");
    expect(s.attach).toBe(true);
    expect(s.step).toBe("Asking Projects assistant");
    // The re-attach clears the ask.
    const a = reduceRun(s, { type: "attach", at: T0 + 310_200, messageId: "a1" });
    expect(a.attach).toBe(false);
    expect(a.phase).toBe("working");
    expect(a.startedAt).toBe(T0);
  });

  it("is done once the server no longer lists it", () => {
    const f = reduceRun(working(), { type: "stream_end", at: T0 + 30_000, terminal: false });
    expect(reduceRun(f, server(T0 + 30_100, null)).phase).toBe("done");
    // A list taken BEFORE the stream ended says nothing about it.
    expect(reduceRun(f, server(T0 + 29_000, null)).phase).toBe("finishing");
  });

  it("never stays finishing: it settles after FINISH_MAX_MS with no answer", () => {
    const f = reduceRun(working(), { type: "stream_end", at: T0 + 30_000, terminal: false });
    expect(reduceRun(f, { type: "tick", at: T0 + 30_000 + FINISH_MAX_MS - 1, streamOpen: false }).phase).toBe("finishing");
    expect(reduceRun(f, { type: "tick", at: T0 + 30_000 + FINISH_MAX_MS, streamOpen: false }).phase).toBe("done");
  });
});

describe("a delegation that sends nothing for minutes", () => {
  it("stays working while the stream is open, the server lists it, and minutes pass", () => {
    let s = reduceRun(working(), ev(T0 + 1_000, "Asking Projects assistant"));
    for (let m = 1; m <= 10; m++) {
      const at = T0 + m * 60_000;
      s = run(s, server(at, RUNNING, true), { type: "tick", at, streamOpen: true });
      expect(s.phase).toBe("working");
      expect(s.step).toBe("Asking Projects assistant");
    }
    expect(runViewText(runView(s, T0 + 10 * 60_000)!)).toBe("Asking Projects assistant · 10m 0s");
  });

  it("stays working with NO open stream while the server keeps listing it", () => {
    let s = working();
    s = reduceRun(s, { type: "stream_end", at: T0 + 1_000, terminal: false });
    for (let m = 1; m <= 10; m++) {
      const at = T0 + m * 30_000;
      s = run(s, server(at, RUNNING, false), { type: "tick", at: at + 1_000, streamOpen: false });
      expect(s.phase).toBe("working");
    }
  });
});

describe("the server says done while the client thinks working", () => {
  it("settles a run with no open stream at once", () => {
    const s = reduceRun(working(), server(T0 + START_GRACE_MS + 1, null, false));
    expect(s.phase).toBe("done");
  });

  it("with an open but silent stream, after GONE_MS it is finishing, then done", () => {
    const s0 = working();
    const a = reduceRun(s0, server(T0 + START_GRACE_MS, null, true));
    expect(a.phase).toBe("working");
    const b = reduceRun(a, server(T0 + START_GRACE_MS + GONE_MS, null, true));
    expect(b.phase).toBe("finishing");
    expect(reduceRun(b, { type: "tick", at: b.changedAt + FINISH_MAX_MS, streamOpen: true }).phase).toBe("done");
  });

  it("an open stream that still brings events is the authority", () => {
    let s = working();
    s = reduceRun(s, server(T0 + START_GRACE_MS, null, true));
    s = reduceRun(s, ev(T0 + START_GRACE_MS + GONE_MS - 1, "Writing the reply"));
    s = reduceRun(s, server(T0 + START_GRACE_MS + GONE_MS, null, true));
    expect(s.phase).toBe("working");
  });

  it("ignores a list that lags a run it just started", () => {
    expect(reduceRun(working(), server(T0 + 2_000, null, true)).phase).toBe("working");
  });

  it("a stale run with no stream and no confirmation settles after STALE_MS", () => {
    const s = reduceRun(working(), server(T0 + 1_000, RUNNING, false));
    expect(reduceRun(s, { type: "tick", at: T0 + 1_000 + STALE_MS - 1, streamOpen: false }).phase).toBe("working");
    expect(reduceRun(s, { type: "tick", at: T0 + 1_000 + STALE_MS, streamOpen: false }).phase).toBe("done");
  });

  it("a list that still names a run just after its terminal event is lag", () => {
    const d = reduceRun(working(), { type: "stream_end", at: T0 + 5_000, terminal: true });
    expect(reduceRun(d, server(T0 + 6_000, RUNNING)).phase).toBe("done");
    expect(reduceRun(d, server(T0 + 5_000 + DONE_HOLD_MS, RUNNING)).phase).toBe("working");
  });
});

describe("a reload into each state (the store starts idle)", () => {
  const at = T0 + 100_000;
  it("running → working, with the server's step and start, and a re-attach", () => {
    const s = reduceRun(IDLE_RUN, server(at, {
      state: "running", live: true, lastStep: "Search tasks", startedAt: new Date(T0).toISOString(),
    }));
    expect(s.phase).toBe("working");
    expect(s.attach).toBe(true);
    expect(s.startedAt).toBe(T0);
    expect(runViewText(runView(s, at)!)).toBe("Search tasks · 1m 40s");
  });

  it("a question on a live run → waiting_for_you, and a re-attach for its card", () => {
    const s = reduceRun(IDLE_RUN, server(at, { state: "needs_input", live: true }));
    expect(s.phase).toBe("waiting_for_you");
    expect(s.attach).toBe(true);
  });

  it("a question with no live run → parked, and no re-attach", () => {
    const s = reduceRun(IDLE_RUN, server(at, { state: "needs_input", live: false }));
    expect(s.phase).toBe("parked");
    expect(s.attach).toBe(false);
    expect(isLivePhase(s.phase)).toBe(false);
  });

  it("an older gateway with no `live` field reads a question as live", () => {
    expect(reduceRun(IDLE_RUN, server(at, { state: "needs_input" })).phase).toBe("waiting_for_you");
  });

  it("no row → idle stays idle", () => {
    expect(reduceRun(IDLE_RUN, server(at, null))).toBe(IDLE_RUN);
  });

  it("a stream that already reads gets no second attach", () => {
    expect(reduceRun(IDLE_RUN, server(at, RUNNING, true)).attach).toBe(false);
  });

  it("re-attaches at most once per ATTACH_GAP_MS", () => {
    const a = run(IDLE_RUN, server(at, RUNNING), { type: "attach", at, messageId: "x" },
      { type: "stream_end", at: at + 100, terminal: false });
    expect(reduceRun(a, server(at + 200, RUNNING)).attach).toBe(false);
    expect(reduceRun(a, server(at + ATTACH_GAP_MS, RUNNING)).attach).toBe(true);
  });
});

describe("waiting for you and parked are not working", () => {
  it("a card opens and closes", () => {
    const w = reduceRun(working(), { type: "card", at: T0 + 1_000, open: true });
    expect(w.phase).toBe("waiting_for_you");
    // The run's own events do not hide the card's wait.
    expect(reduceRun(w, ev(T0 + 3_000)).phase).toBe("waiting_for_you");
    // A list without the durable ask keeps the wait too.
    expect(reduceRun(w, server(T0 + 20_000, RUNNING, true)).phase).toBe("waiting_for_you");
    const back = reduceRun(w, { type: "card", at: T0 + 4_000, open: false });
    expect(back.phase).toBe("working");
  });

  it("a parked run whose card is answered elsewhere ends", () => {
    const p = reduceRun(IDLE_RUN, server(T0, { state: "needs_input", live: false }));
    expect(reduceRun(p, server(T0 + 5_000, null)).phase).toBe("done");
    // The answer started a new run.
    expect(reduceRun(p, server(T0 + 5_000, RUNNING)).phase).toBe("working");
  });

  it("each draws its own words and tone", () => {
    const now = T0 + 1_000;
    expect(runView(reduceRun(working(), { type: "card", at: now, open: true }), now)?.tone).toBe("waiting");
    expect(runView(reduceRun(IDLE_RUN, server(now, { state: "needs_input", live: false })), now)?.tone).toBe("parked");
    expect(runView(working(), now)?.tone).toBe("working");
    const labels = new Set([
      runView(reduceRun(working(), { type: "card", at: now, open: true }), now)!.label,
      runView(reduceRun(IDLE_RUN, server(now, { state: "needs_input", live: false })), now)!.label,
      runView(working(), now)!.label,
    ]);
    expect(labels.size).toBe(3);
  });
});

describe("the view", () => {
  it("done and stopped show for RESTING_SHOW_MS, idle and failed never", () => {
    const d = reduceRun(working(), { type: "stream_end", at: T0 + 72_000, terminal: true });
    expect(runViewText(runView(d, T0 + 72_000)!)).toBe("Done · 1m 12s");
    expect(runView(d, T0 + 72_000 + RESTING_SHOW_MS)).toBeNull();
    expect(runView(reduceRun(working(), { type: "stop", at: T0 + 1 }), T0 + 2)?.label).toBe("Stopped");
    expect(runView(IDLE_RUN, T0)).toBeNull();
    expect(runView(reduceRun(working(), { type: "failed", at: T0 + 1 }), T0 + 2)).toBeNull();
  });

  it("formats the elapsed time", () => {
    expect(formatElapsed(42_000)).toBe("42s");
    expect(formatElapsed(72_000)).toBe("1m 12s");
    expect(formatElapsed(3_840_000)).toBe("1h 4m");
    expect(formatElapsed(-5)).toBe("0s");
  });
});

describe("the step of a message, from the one step vocabulary", () => {
  const msg = (over: Partial<ChatMessage>): ChatMessage => ({
    id: "a1", role: "assistant", content: "", timestamp: T0, ...over,
  });
  it("names a running hand-off by its agent", () => {
    const m = msg({ toolEvents: [{ id: "t1", name: "call_agent", args: { agent: "projects-assistant" }, status: "running" }] });
    expect(stepOfMessage(m)).toMatch(/^Asking /);
  });
  it("falls back to the text, then the reasoning, then nothing", () => {
    expect(stepOfMessage(msg({ content: "Hello" }))).toBe(WRITING_STEP);
    expect(stepOfMessage(msg({ reasoningBlocks: ["hmm"] }))).toBe(THINKING_STEP);
    expect(stepOfMessage(msg({}))).toBeNull();
    expect(stepOfMessage(undefined)).toBeNull();
  });
});

describe("no flicker", () => {
  it("a state shorter than FLASH_MS never shows", () => {
    let p = steadyPresence(false, true, 0, T0);
    expect(p.shown).toBe(false);
    // It went away before the wait ended.
    p = steadyPresence(p.shown, false, p.pendingSince, T0 + FLASH_MS - 50);
    expect(p).toEqual({ shown: false, pendingSince: 0 });
  });

  it("a state that lasts FLASH_MS shows", () => {
    const p = steadyPresence(false, true, 0, T0);
    expect(steadyPresence(p.shown, true, p.pendingSince, T0 + FLASH_MS).shown).toBe(true);
  });

  it("a gap shorter than FLASH_MS never hides the row", () => {
    const p = steadyPresence(true, false, 0, T0);
    expect(p.shown).toBe(true);
    expect(steadyPresence(p.shown, true, p.pendingSince, T0 + 100)).toEqual({ shown: true, pendingSince: 0 });
  });

  it("the member's own send shows at once", () => {
    expect(steadyPresence(false, true, 0, T0, true).shown).toBe(true);
  });
});
