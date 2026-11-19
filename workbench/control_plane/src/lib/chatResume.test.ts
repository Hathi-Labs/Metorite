/**
 * RESUME-3a — the chat takes a resumed run (`chat_run_continuity.md` §4.12.4
 * item 5, task 3262). A late Approve of a parked card starts a NEW run on the
 * thread (case B): its own run id, its own reply row at the end of the chat,
 * no member turn, and no envelope.
 *
 * Each block names the rule it fences. The mutations of the slice:
 *   * a park still emits `onRunFinalized`: "the hook" fails;
 *   * the `resumed` event is ignored: "the resumed event" fails;
 *   * `respondInput` ignores the 2xx body: "the answer POST" fails;
 *   * the 409 `run_restarted` branch changes: "the resend branch" fails.
 *
 * RESUME-3b (task 3475) adds case A: the parked run goes on in the SAME
 * reply (RESUME-2b). Its mutations:
 *   * `reduceServer` ignores `parkedRunKey`: "case A: the list" fails;
 *   * the BFF sends no `run_resumed` frame: "case A: the BFF" fails;
 *   * `run_resumed` does not open the end again: "case A: the end" fails;
 *   * `resumedOf` ignores `sameReply`: "case A: the answer POST" fails.
 *
 * RESUME-3d (task 3785) adds the reply row that the list names for a live
 * continuation (`messageId`). Its mutations:
 *   * `reduceServer` ignores the row's `messageId`: "RESUME-3d" fails;
 *   * `attachPickOf` reads the saved rows with a listed reply: "RESUME-3d"
 *     fails;
 *   * the hook does not use `attachPickOf`: "RESUME-3d: the hook" fails.
 */

import { readFileSync } from "node:fs";
import { fileURLToPath } from "node:url";

import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import type { ChatMessage } from "@/lib/chatStore";
import {
  REPLAY_OPEN,
  RUN_RESUMED_FRAME,
  attachPickOf,
  goesOnInParkedReply,
  receiptAnchorBeforeRun,
  replayEndAfter,
  runResumedFrameOf,
  waitingCardIds,
  type ReplayEnd,
} from "@/lib/chatResume";
import {
  EMPTY_CONFIRMATIONS,
  confirmationReducer,
  receiptText,
  type ConfirmationAction,
  type ConfirmationQueueState,
} from "@/lib/confirmationQueue";
import { cardsToSettle, type PendingAsk } from "@/lib/pendingAsks";
import { respondFailure, resumedOf, runCapAnswerReason, sendRespondInputResult } from "@/lib/respondInput";
import { RUN_ERROR_WORDS } from "@/lib/runErrors";
import { planReplay, rejectFor, replayIdOf } from "@/lib/runReplay";
import {
  DONE_HOLD_MS,
  IDLE_RUN,
  START_GRACE_MS,
  WAITING_LABEL,
  isRunActiveForUi,
  reduceRun,
  runKeyOf,
  runView,
  type RunEvent,
  type RunStatus,
  type ServerRun,
} from "@/lib/runStatus";

const T0 = 1_763_000_000_000;
const run = (s: RunStatus, ...events: RunEvent[]) => events.reduce(reduceRun, s);
const server = (at: number, row: ServerRun | null, streamOpen = false): RunEvent => ({ type: "server", at, run: row, streamOpen });
/** The parked row after a reload: the gateway sends no run id (C1). */
const PARKED_ROW: ServerRun = { state: "needs_input", live: false };
const R1: ServerRun = { state: "running", live: true, runId: "r1" };
const R2: ServerRun = { state: "running", live: true, runId: "r2", startedAt: new Date(T0 + 60_000).toISOString() };

/** This tab's run r1 drew a card, then parked on it (the `done` with `parked`). */
function parkedLive(): RunStatus {
  return run(
    IDLE_RUN,
    { type: "send", at: T0, messageId: "reply-1" },
    { type: "event", at: T0 + 500, step: "Writing the reply" },
    server(T0 + 1_000, R1, true),
    { type: "card", at: T0 + 2_000, open: true },
    { type: "stream_end", at: T0 + 600_000, terminal: true, parked: true },
  );
}

const src = (rel: string) =>
  readFileSync(fileURLToPath(new URL(rel, import.meta.url)), "utf8").replace(/\r\n/g, "\n");

// ── A park is never "Done" ──────────────────────────────────────────────────

describe("a park is never done (D-RES-6)", () => {
  it("a parked `done` goes to parked, keeps the card, and names the parked reply", () => {
    const p = parkedLive();
    expect(p.phase).toBe("parked");
    expect(p.cardOpen).toBe(true);
    expect(p.doneMessageId).toBe("reply-1");
    expect(p.afterPark).toBe(true);
    const v = runView(p, T0 + 600_500)!;
    expect(v.tone).toBe("parked");
    expect(v.label).toBe(WAITING_LABEL);
    expect(v.elapsed).toBeNull();
    // The composer sends: a parked run has nothing to stop.
    expect(isRunActiveForUi(p)).toBe(false);
  });

  it("a plain `done` is still done", () => {
    const d = run(parkedLive(), { type: "send", at: T0 + 700_000, messageId: "reply-2" },
      { type: "stream_end", at: T0 + 701_000, terminal: true });
    expect(d.phase).toBe("done");
  });

  it("the server's parked row keeps the park, and a lagging list of the same run is held", () => {
    const p = parkedLive();
    expect(reduceRun(p, server(T0 + 601_000, PARKED_ROW)).phase).toBe("parked");
    expect(reduceRun(p, server(T0 + 600_000 + DONE_HOLD_MS - 1, R1))).toBe(p);
  });

  it("a park that the server stops listing rests quietly, with no \"Done\"", () => {
    const idle = reduceRun(parkedLive(), server(T0 + 650_000, null));
    expect(idle.phase).toBe("idle");
    expect(runView(idle, T0 + 650_000)).toBeNull();
    expect(idle.afterPark).toBe(true);
  });

  it("no path from a park reads \"Done\"", () => {
    const p = parkedLive();
    const paths: RunEvent[][] = [
      [server(T0 + 650_000, null)],
      [server(T0 + 601_000, PARKED_ROW), server(T0 + 650_000, null)],
      [{ type: "tick", at: T0 + 700_000, streamOpen: false }],
      [{ type: "remount", at: T0 + 620_000, streamOpen: false }],
    ];
    for (const events of paths) {
      const s = run(p, ...events);
      expect(runView(s, s.changedAt)?.label ?? "").not.toBe("Done");
    }
  });
});

// ── The resumed event ──────────────────────────────────────────────────────

describe("the resumed event opens a NEW run, even with no run key (C1)", () => {
  // After a reload the run state is new, and the parked row has no run id.
  const reloaded = () => reduceRun(IDLE_RUN, server(T0 + 1_000, PARKED_ROW));

  it("the parked row after a reload has no run key", () => {
    const p = reloaded();
    expect(p.phase).toBe("parked");
    expect(p.runKey).toBeNull();
  });

  it("an Approve's 200 `resumed` makes the next run a new one, with no old reply as its target", () => {
    const at = T0 + 3_600_000;
    const r = reduceRun(reloaded(), { type: "resumed", at, runId: "r2" });
    expect(r.phase).toBe("working");
    expect(r.runKey).toBe(runKeyOf({ runId: "r2" }));
    expect(r.staleTail).toBe(true);
    expect(r.messageId).toBeNull();
    expect(r.cardOpen).toBe(false);
    expect(r.startedAt).toBe(at);
    expect(r.seq).toBe(0);
    // The run list names r2: the same run, so the chat attaches at once.
    const listed = reduceRun(r, server(at + 500, R2));
    expect(listed.phase).toBe("working");
    expect(listed.attach).toBe(true);
    expect(listed.staleTail).toBe(true);
  });

  it("a list from before the Approve, or one that lags the new run, changes nothing", () => {
    const at = T0 + 3_600_000;
    const r = reduceRun(reloaded(), { type: "resumed", at, runId: "r2" });
    // Taken before the 200: it still names the parked row.
    expect(reduceRun(r, server(at - 100, PARKED_ROW))).toBe(r);
    // Taken after it, before the gateway lists r2.
    expect(reduceRun(r, server(at + START_GRACE_MS - 1, null))).toBe(r);
    // No row for long: the run settles as any run does.
    expect(reduceRun(r, server(at + START_GRACE_MS + 1, null)).phase).toBe("done");
  });

  it("a named reply row is the replay target", () => {
    const r = reduceRun(reloaded(), { type: "resumed", at: T0 + 5_000, runId: "r2", messageId: "reply-r2" });
    expect(r.messageId).toBe("reply-r2");
    expect(replayIdOf(null, r.resumedAt > 0 ? r.messageId : null, () => "mint")).toBe("reply-r2");
  });

  it("a late `resumed` for a run the list already bound changes only resumedAt", () => {
    const listed = reduceRun(reloaded(), server(T0 + 9_000, R2));
    const bound = reduceRun(listed, { type: "attach", at: T0 + 9_500, messageId: "reply-r2" });
    expect(bound.messageId).toBe("reply-r2");
    expect(bound.staleTail).toBe(false);
    const r =reduceRun(bound, { type: "resumed", at: T0 + 10_000, runId: "r2" });
    expect(r.messageId).toBe(bound.messageId);
    expect(r.staleTail).toBe(bound.staleTail);
    expect(r.seq).toBe(bound.seq);
    expect(r.runKey).toBe(bound.runKey);
    expect(r.resumedAt).toBe(T0 + 10_000);
  });

  it("an empty run id is no continuation", () => {
    const p = reloaded();
    expect(reduceRun(p, { type: "resumed", at: T0 + 5_000, runId: "" })).toBe(p);
  });

  it("with no event, a run listed after a park is a new run too", () => {
    // Another device approved: the list names r2 while this chat is parked.
    const n = reduceRun(reloaded(), server(T0 + 9_000, R2));
    expect(n.phase).toBe("working");
    expect(n.staleTail).toBe(true);
    expect(n.messageId).toBeNull();
    // The same after the quiet rest that a list with no row gave.
    const rest = run(reloaded(), server(T0 + 8_000, null), server(T0 + 9_000, R2));
    expect(rest.staleTail).toBe(true);
  });
});

// ── The replay target, and one bubble after the fold (C2) ──────────────────

const msg = (id: string, role: ChatMessage["role"], content: string, timestamp: number): ChatMessage =>
  ({ id, role, content, timestamp } as ChatMessage);

describe("one bubble after the fold (C2)", () => {
  // Case B: the member wrote after the park. The continuation's reply row is
  // the gateway's own, minted at the run's start, at the end of the chat.
  const shown = [
    msg("u-1", "user", "Move Acme to Payment Complete", 1),
    msg("reply-1", "assistant", "I need your answer first.", 2),
  ];
  const minted = msg("reply-r2", "assistant", "", 10);
  const folded = msg("reply-r2", "assistant", "Acme is now in Payment Complete.", 10);

  /** The poll's merge, by id: a row of the same id takes the newer text. */
  const mergeById = (local: ChatMessage[], remote: ChatMessage[]) => {
    const out = [...local];
    for (const r of remote) {
      const at = out.findIndex((m) => m.id === r.id);
      if (at >= 0) out[at] = { ...out[at], ...r };
      else out.push(r);
    }
    return out;
  };
  const repliesAfterPark = (ms: ChatMessage[]) => ms.filter((m) => m.role === "assistant" && m.id !== "reply-1");

  it("with no message id, the replay goes into the server's own row, never the parked reply", () => {
    const plan = planReplay([...shown, minted], shown, rejectFor(shown, null, true));
    expect(plan.target?.id).toBe("reply-r2");
    const id = replayIdOf(plan.target, null, () => "placeholder");
    expect(id).toBe("reply-r2");
    const live = [...shown, { ...minted, content: "Acme is now" }];
    expect(repliesAfterPark(mergeById(live, [...shown, folded]))).toHaveLength(1);
  });

  it("with a message id, the placeholder takes that id, so the fold writes the same row", () => {
    // The server has not saved the row yet: no target from the saved rows.
    const plan = planReplay(shown, shown, rejectFor(shown, null, true));
    expect(plan.target).toBeNull();
    const id = replayIdOf(plan.target, "reply-r2", () => "placeholder");
    expect(id).toBe("reply-r2");
    const live = [...shown, { ...minted, id, content: "Acme is now" }];
    expect(repliesAfterPark(mergeById(live, [...shown, folded]))).toHaveLength(1);
  });

  it("with neither, the placeholder is new", () => {
    expect(replayIdOf(null, null, () => "placeholder")).toBe("placeholder");
    expect(replayIdOf(undefined, "", () => "placeholder")).toBe("placeholder");
  });
});

// ── The answer POST ────────────────────────────────────────────────────────

const BODY = { request_id: "rid-1", answer: "APPROVE", was_freeform: false, thread_id: "t-1" };
const reply = (body: string, status: number) => vi.fn(async () => new Response(body, { status })) as unknown as typeof fetch;

describe("the answer POST reads `resumed` (RESUME-3a)", () => {
  it("a 200 `resumed` names the continuation run", async () => {
    const res = await sendRespondInputResult(BODY, reply('{"ok":true,"resumed":true,"threadId":"t-1","runId":"r2"}', 200));
    expect(res).toEqual({ outcome: "ok", resend: null, resumed: { runId: "r2", threadId: "t-1" } });
  });

  it("a plain 200 is a plain ok", async () => {
    expect(await sendRespondInputResult(BODY, reply('{"ok":true}', 200))).toEqual({ outcome: "ok", resend: null });
    expect(await sendRespondInputResult(BODY, reply("", 200))).toEqual({ outcome: "ok", resend: null });
  });

  it("only `resumed: true` with string ids counts", () => {
    expect(resumedOf('{"resumed":true,"threadId":"t","runId":"r","messageId":"m"}')).toEqual({ runId: "r", threadId: "t", messageId: "m" });
    expect(resumedOf('{"resumed":"true","threadId":"t","runId":"r"}')).toBeNull();
    expect(resumedOf('{"resumed":true,"threadId":"t","runId":7}')).toBeNull();
    expect(resumedOf('{"resumed":true,"runId":"r"}')).toBeNull();
    expect(resumedOf('{"resumed":true,"threadId":"t","runId":"r","messageId":3}')).toEqual({ runId: "r", threadId: "t" });
    expect(resumedOf("not json")).toBeNull();
  });

  it("the run cap's refusal keeps the card, and says why", async () => {
    const cap = JSON.stringify({ error: "too_many_runs", limit: 5, running: ["a", "b", "c", "d", "e"] });
    const res = await sendRespondInputResult(BODY, reply(cap, 429));
    expect(res.outcome).toBe("retry");
    expect(res.resend).toBeNull();
    expect(res.reason).toBe("You have 5 assistants running. Wait for one to finish or stop one, then answer the card again.");
    expect(runCapAnswerReason(429, JSON.stringify({ error: "too_many_runs" })))
      .toBe("You have too many assistants running. Wait for one to finish or stop one, then answer the card again.");
    expect(runCapAnswerReason(429, JSON.stringify({ error: "too_many_runs", limit: 1 })))
      .toBe("You have 1 assistant running. Wait for one to finish or stop one, then answer the card again.");
  });

  it("any other 429 drops as before, and the status alone decides `respondFailure`", async () => {
    expect((await sendRespondInputResult(BODY, reply('{"detail":"slow down"}', 429))).outcome).toBe("drop");
    expect(runCapAnswerReason(409, JSON.stringify({ error: "too_many_runs", limit: 5 }))).toBeNull();
    expect(respondFailure(429)).toBe("drop");
  });
});

// ── The words ──────────────────────────────────────────────────────────────

describe("the words (§4.12.4 item 5)", () => {
  it("a card that cannot resume says the chat had stopped waiting", () => {
    expect(RUN_ERROR_WORDS.run_restarted.title).toBe("The chat had stopped waiting");
    expect(RUN_ERROR_WORDS.run_restarted_steered.title).toBe("The chat had stopped waiting");
    // The bodies stay.
    expect(RUN_ERROR_WORDS.run_restarted.body).toBe("Your answer went to it as a new message.");
    expect(RUN_ERROR_WORDS.run_restarted_steered.body).toBe("Your answer went into the current reply.");
    for (const w of Object.values(RUN_ERROR_WORDS)) expect(w.title).not.toMatch(/restarted/i);
  });

  it("a live wait and a park read the same", () => {
    expect(WAITING_LABEL).toBe("Waiting for your answer");
    const row = src("../components/RunStatusRow.tsx");
    expect(row).toMatch(/waiting: "MessageCircleQuestion",[\s\S]*parked: "MessageCircleQuestion",/);
    expect(src("./runStatus.ts")).not.toContain("Paused. Answer the card to go on");
  });
});

// ── Another device answered ────────────────────────────────────────────────

describe("another device approved a parked card", () => {
  const card = { request_id: "rid-1", title: "Move Acme to Payment Complete?" };
  const asked = [{ type: "requested", value: card }] as ConfirmationAction[];
  const reduce = (actions: ConfirmationAction[], from: ConfirmationQueueState = EMPTY_CONFIRMATIONS) =>
    actions.reduce(confirmationReducer, from);

  it("the card leaves with \"Approved\"", () => {
    const s = reduce([...asked, { type: "resolved", requestId: "rid-1", answer: "APPROVE", elsewhere: true, afterId: "reply-1" }]);
    expect(s.cards).toHaveLength(0);
    expect(s.receipts.map(receiptText)).toEqual(["Approved · Move Acme to Payment Complete?"]);
    expect(s.receipts[0].afterId).toBe("reply-1");
  });

  it("a card that never parked here, a reject and this tab's own answer add no line", () => {
    expect(reduce([...asked, { type: "resolved", requestId: "rid-1", answer: "APPROVE" }]).receipts).toHaveLength(0);
    expect(reduce([...asked, { type: "resolved", requestId: "rid-1", answer: "REJECT", elsewhere: true }]).receipts).toHaveLength(0);
    const mine = reduce([
      ...asked,
      { type: "answering", key: "rid-1", label: card.title, intent: "approved", afterId: "reply-1" },
      { type: "answered", key: "rid-1" },
      { type: "resolved", requestId: "rid-1", answer: "APPROVE", elsewhere: true },
    ]);
    expect(mine.receipts.map(receiptText)).toEqual(["Approved · Move Acme to Payment Complete?"]);
  });

  it("the line draws under the last message before the reply that goes on", () => {
    const ms = [
      { id: "u-1", role: "user" as const, content: "Move Acme" },
      { id: "reply-1", role: "assistant" as const, content: "I need your answer first." },
      { id: "n-1", role: "system" as const, content: "__ERROR__{}" },
      { id: "reply-r2", role: "assistant" as const, content: "" },
    ];
    expect(receiptAnchorBeforeRun(ms, "reply-r2")).toBe("reply-1");
    expect(receiptAnchorBeforeRun(ms.slice(0, 2), null)).toBe("reply-1");
    expect(receiptAnchorBeforeRun([], null)).toBeNull();
  });

  it("a stale card: a real list without it settles it, and a failed read settles nothing", () => {
    const waiting = waitingCardIds(
      [{ requestId: "rid-1" }, { requestId: "rid-slot", redraft: "updating" }, {}],
      ["q-1", null],
      new Set(["rid-log"]),
    );
    expect([...waiting].sort()).toEqual(["q-1", "rid-1", "rid-log"]);
    const listed: PendingAsk[] = [{ requestId: "q-1", kind: "ask_user", event: "user_input_requested", payload: {}, askedAt: null, answerBy: "new_run" }];
    expect(cardsToSettle(listed, waiting).sort()).toEqual(["rid-1", "rid-log"]);
    expect(cardsToSettle(null, waiting)).toEqual([]);
  });
});

// ── The source: the seams that a pure test cannot reach ────────────────────

describe("the hook: a park keeps the card and ends the slot (C3)", () => {
  const hook = src("../hooks/useAgentChat.ts");

  it("the live loop's parked `done` emits `onRunRested` and never `onRunFinalized`", () => {
    const at = hook.indexOf('case "done":\n                sawDone = true;');
    expect(at).toBeGreaterThan(0);
    const block = hook.slice(at, hook.indexOf("break;\n              case \"state\":", at));
    const parkAt = block.indexOf("if (evt.parked === true) {");
    expect(parkAt).toBeGreaterThan(0);
    // The parked branch: everything before the first `onRunFinalized`.
    const park = block.slice(parkAt, block.indexOf('emitAgentEvent("onRunFinalized"'));
    expect(park).toContain('emitAgentEvent("onRunRested", { threadId });');
    expect(park).toContain("break;");
    expect(park).not.toContain("onRunFinalized");
    // The card log still clears, before the branch.
    expect(block.indexOf("clearCardLog(threadId);")).toBeLessThan(block.indexOf("if (evt.parked === true) {"));
    expect(hook).toContain('dispatchRun(threadId, { type: "stream_end", at, terminal: true, ...(sawPark ? { parked: true } : {}) });');
  });

  it("the attach loop's parked `done` ends the slot and tells the run state", () => {
    // RESUME-3b: the end comes from `replayEndAfter`, fed EVERY frame before
    // the switch, so a `run_resumed` opens it again (the pure rule is below).
    const loop = hook.indexOf("end = replayEndAfter(end, evt);");
    expect(loop).toBeGreaterThan(0);
    expect(loop).toBeLessThan(hook.indexOf("switch (evt.type) {", loop));
    expect(hook).toContain("let end: ReplayEnd = REPLAY_OPEN;");
    expect(hook).toContain('if (end.parked) emitAgentEvent("onRunRested", { threadId });');
    expect(hook).toContain("terminal: end.terminal, ...(end.terminal && end.parked ? { parked: true } : {})");
    expect(hook).toContain("if (!end.terminal) updLast((m) => ({ ...m, streaming: false, isThinkingActive: false }));");
    // RESUME-3d: the id of the 200 now comes through `attachPickOf`, which
    // keeps the rule `resumedAt > 0 ? messageId : null` (fenced below).
    expect(hook).toContain("replayIdOf(target, pick.resumeId, nanoid)");
  });

  it("the attach loop's `run_resumed` makes the reply stream again (RESUME-3b)", () => {
    const at = hook.indexOf("case RUN_RESUMED_FRAME:");
    expect(at).toBeGreaterThan(0);
    const block = hook.slice(at, hook.indexOf("break;", at));
    expect(block).toContain("updLast((m) => ({ ...m, streaming: true, isThinkingActive: true }));");
    // No seed of a replay that starts at the second start: RESUME-2d owns
    // that replay (review of 2026-11-19, P2-3, the seed was deleted).
    expect(block).not.toContain("shadow");
  });

  it("the BFF keeps `parked` on the done frame", () => {
    const route = src("../app/api/agent/chat/route.ts");
    expect(route).toContain('out = { type: "done", run_id: ev.runId || ev.run_id };\n');
    expect(route).toContain("if (ev.parked === true) out.parked = true;");
  });

  it("the BFF sends `run_resumed` only after the run binding accepted the start (RESUME-3b)", () => {
    const route = src("../app/api/agent/chat/route.ts");
    const bind = route.indexOf("if (!binding.accept(ev)) { superseded = true; break; }");
    const frame = route.indexOf('} else if (t === "RUN_STARTED") {\n');
    expect(bind).toBeGreaterThan(0);
    expect(frame).toBeGreaterThan(bind);
    expect(route.slice(frame, route.indexOf("}", frame + 40))).toContain("out = runResumedFrameOf(ev);");
  });
});

describe("the chat: an Approve that resumed sends no envelope and draws no notice", () => {
  const chat = src("../components/AgentChat.tsx");
  const fn = chat.slice(chat.indexOf("const postRespondInput = useCallback("), chat.indexOf("[setMessages],", chat.indexOf("const postRespondInput = useCallback(")));

  it("the resumed branch tells the run state, asks for the run list, and comes before the resend", () => {
    const resumed = fn.slice(fn.indexOf("if (resumed) {"), fn.indexOf("if (sessionIdRef.current !== forSession) return;"));
    expect(resumed).toContain('type: "resumed", at: Date.now(), runId: resumed.runId,');
    expect(resumed).toContain("refreshLiveRuns();");
    expect(resumed).not.toContain("sendMessageRef");
    expect(resumed).not.toContain("setMessages");
    expect(fn.indexOf("if (resumed) {")).toBeLessThan(fn.indexOf("if (resend) {"));
  });

  it("the resend branch of the 409 `run_restarted` is as it was", () => {
    const resend = fn.slice(fn.indexOf("if (resend) {"), fn.indexOf("settle?.(outcome, reason);"));
    for (const line of [
      "const cardAnswer = display ?? parseCardAnswerEnvelope(resend);",
      "const plan = resendPlan(outcome);",
      "id: `restarted-${payload.request_id}`,",
      'content: `__ERROR__${JSON.stringify({ code, ref: null, raw: "" })}`,',
      'if (plan.card === "keep") restoreCard();',
      'else settle?.("resent");',
      "void steerMessageRef.current(resend, { cardAnswer }).then((out) => {",
      'if (route === "steer") { steer(); return; }',
      "void sendMessageRef.current(resend, {",
      'onSettled: (outcome) => (outcome === "busy" ? steer() : finish(outcome)),',
    ]) expect(resend).toContain(line);
    expect(resend).not.toContain("resumed");
  });

  it("the resumed branch passes `sameReply` on (RESUME-3b)", () => {
    const resumed = fn.slice(fn.indexOf("if (resumed) {"), fn.indexOf("if (sessionIdRef.current !== forSession) return;"));
    expect(resumed).toContain("...(resumed.sameReply ? { sameReply: true } : {}),");
  });
});

// ══ RESUME-3b: case A, the same reply ══════════════════════════════════════
//
// A late Approve of a parked turn that is the last turn goes on in the SAME
// reply, under the parked run's id (RESUME-2b). Its stream holds a second
// `RUN_STARTED` of that run, marked `resumed`, after the place of the parked
// end. The 200 says `sameReply` and names the reply (`messageId`). A list of
// a parked thread names the run that parked (`parkedRunId`).

/** The parked row of a gateway with both resume flags on (RESUME-3b). */
const PARKED_A: ServerRun = { state: "needs_input", live: false, parkedRunId: "r1" };
const R1_AGAIN: ServerRun = { state: "running", live: true, runId: "r1", startedAt: new Date(T0 + 3_600_000).toISOString() };
/** An earlier run of the chat, from before the park. */
const R0: ServerRun = { state: "running", live: true, runId: "r0" };

/** After a reload: the parked row names r1. */
const reloadedA = () => reduceRun(IDLE_RUN, server(T0 + 1_000, PARKED_A));

/** The saved rows of a chat whose parked reply is the last turn. */
const caseAShown = [
  msg("u-1", "user", "Move Acme to Payment Complete", 1),
  msg("reply-1", "assistant", "I need your answer first.", 2),
];

/** The reply that a re-attach replays into, as `useAgentChat.attach` picks it. */
function replayTarget(s: RunStatus, shown: ChatMessage[]): string {
  const known = s.messageId ? shown.find((m) => m.id === s.messageId && m.role === "assistant") : undefined;
  const plan = planReplay(shown, shown, rejectFor(shown, s.doneMessageId, s.staleTail));
  return replayIdOf(known ?? plan.target, s.resumedAt > 0 ? s.messageId : null, () => "placeholder");
}

describe("case A: the list names the parked run, and the same run goes on", () => {
  it("a parked row keeps the key of its run", () => {
    const p = reloadedA();
    expect(p.phase).toBe("parked");
    expect(p.parkedRunKey).toBe(runKeyOf({ runId: "r1" }));
    expect(p.runKey).toBeNull();
  });

  it("a list that names the parked run is the SAME run: not new, no stale tail, the parked reply is the target", () => {
    const at = T0 + 3_600_500;
    const s = reduceRun(reloadedA(), server(at, R1_AGAIN));
    expect(s.phase).toBe("working");
    expect(s.staleTail).toBe(false);
    expect(s.attach).toBe(true);
    expect(s.afterPark).toBe(false);
    expect(s.runKey).toBe(runKeyOf({ runId: "r1" }));
    expect(goesOnInParkedReply(s)).toBe(true);
    // The row says the run works, never "Waiting" and never "Done".
    expect(runView(s, at)?.label).not.toBe(WAITING_LABEL);
    expect(replayTarget(s, caseAShown)).toBe("reply-1");
  });

  it("the same after the quiet rest that a list with no row gave", () => {
    const s = run(reloadedA(), server(T0 + 8_000, null), server(T0 + 9_000, R1_AGAIN));
    expect(s.phase).toBe("working");
    expect(s.staleTail).toBe(false);
    expect(replayTarget(s, caseAShown)).toBe("reply-1");
  });

  it("the tab that watched the park, then remounted: its parked reply is never refused", () => {
    // `doneMessageId` holds the parked reply after the park, and a remount
    // drops `messageId`. The run that goes on must still replay into it.
    const s = run(
      parkedLive(),
      { type: "remount", at: T0 + 620_000, streamOpen: false },
      server(T0 + 621_000, PARKED_A),
      server(T0 + 3_600_500, R1_AGAIN),
    );
    expect(s.staleTail).toBe(false);
    expect(s.doneMessageId).toBeNull();
    expect(replayTarget(s, caseAShown)).toBe("reply-1");
  });

  it("an older run key that the chat still holds does not make the parked run another run", () => {
    // The chat knew r0. A parked row has no run id, so the key stays r0.
    const s0 = run(IDLE_RUN, server(T0, R0), server(T0 + 70_000, null), server(T0 + 100_000, PARKED_A));
    expect(s0.runKey).toBe(runKeyOf({ runId: "r0" }));
    const s = reduceRun(s0, server(T0 + 3_600_500, R1_AGAIN));
    expect(s.staleTail).toBe(false);
    expect(goesOnInParkedReply(s)).toBe(true);
    expect(replayTarget(s, caseAShown)).toBe("reply-1");
  });

  it("the run key alone, with no parkedRunId, marks the parked run too, so the line draws under its reply (P2-1)", () => {
    // A room member's tab has no waiting ask, so its list sends no
    // `parkedRunId`. And another device can approve within DONE_HOLD_MS of
    // the park, before any parked list lands. The chat knew r1 from a list.
    expect(parkedLive().runKey).toBe(runKeyOf({ runId: "r1" }));
    expect(parkedLive().parkedRunKey).toBeNull();
    const afterRest = run(parkedLive(), server(T0 + 605_000, null), server(T0 + 606_000, R1_AGAIN));
    const afterHold = run(parkedLive(), server(T0 + 611_000, R1_AGAIN));
    for (const s of [afterRest, afterHold]) {
      expect(s.phase).toBe("working");
      expect(s.staleTail).toBe(false);
      expect(s.parkedRunKey).toBe(runKeyOf({ runId: "r1" }));
      expect(goesOnInParkedReply(s)).toBe(true);
      expect(receiptAnchorBeforeRun(caseAShown, s.messageId, goesOnInParkedReply(s))).toBe("reply-1");
    }
  });

  it("case B stays new: a parked row that names r1, then a listed r2", () => {
    const n = reduceRun(reloadedA(), server(T0 + 9_000, R2));
    expect(n.phase).toBe("working");
    expect(n.staleTail).toBe(true);
    expect(n.messageId).toBeNull();
    expect(goesOnInParkedReply(n)).toBe(false);
    expect(replayTarget(n, caseAShown)).toBe("placeholder");
  });

  it("with no parkedRunId the rule of RESUME-3a holds, byte for byte", () => {
    const n = reduceRun(reduceRun(IDLE_RUN, server(T0 + 1_000, PARKED_ROW)), server(T0 + 9_000, R1_AGAIN));
    expect(n.staleTail).toBe(true);
    expect(n.parkedRunKey).toBeNull();
  });
});

describe("case A: the 200 of the Approve", () => {
  const at = T0 + 3_600_000;
  const sameReply = { type: "resumed", at, runId: "r1", messageId: "reply-1", sameReply: true } as const;

  it("after a reload, `sameReply` goes on in the parked reply", () => {
    for (const before of [reloadedA(), reduceRun(IDLE_RUN, server(T0 + 1_000, PARKED_ROW))]) {
      const r = reduceRun(before, sameReply);
      expect(r.phase).toBe("working");
      expect(r.messageId).toBe("reply-1");
      expect(r.doneMessageId).toBeNull();
      expect(r.staleTail).toBe(false);
      expect(r.runKey).toBe(runKeyOf({ runId: "r1" }));
      expect(goesOnInParkedReply(r)).toBe(true);
      expect(replayTarget(r, caseAShown)).toBe("reply-1");
      // The list of r1 is the same run: the chat attaches into that reply.
      const listed = reduceRun(r, server(at + 500, R1_AGAIN));
      expect(listed.attach).toBe(true);
      expect(listed.messageId).toBe("reply-1");
      expect(listed.staleTail).toBe(false);
    }
  });

  it("the tab that watched the park: the 200 of its own run moves the row off \"Waiting\" (D2)", () => {
    // `parkedLive` knew r1 from a list. The 200 names r1, with no other mark.
    const r = reduceRun(parkedLive(), { type: "resumed", at, runId: "r1" });
    expect(r.phase).toBe("working");
    expect(r.messageId).toBe("reply-1");
    expect(r.doneMessageId).toBeNull();
    expect(runView(r, at + 100)?.label).not.toBe(WAITING_LABEL);
  });

  it("a list that bound and attached the run first: the 200 changes only resumedAt", () => {
    const listed = reduceRun(reloadedA(), server(at - 2_000, R1_AGAIN));
    const bound = reduceRun(listed, { type: "attach", at: at - 1_500, messageId: "reply-1" });
    const ev = reduceRun(bound, { type: "event", at: at - 1_000, step: "Writing the reply" });
    const r = reduceRun(ev, sameReply);
    expect(r.messageId).toBe(ev.messageId);
    expect(r.seq).toBe(ev.seq);
    expect(r.attach).toBe(ev.attach);
    expect(r.attachedAt).toBe(ev.attachedAt);
    expect(r.phase).toBe(ev.phase);
    expect(r.resumedAt).toBe(at);
  });

  it("a decline is never case A: a new run id and no messageId is a new run", () => {
    const r = reduceRun(reloadedA(), { type: "resumed", at, runId: "r9" });
    expect(r.staleTail).toBe(true);
    expect(r.messageId).toBeNull();
    expect(goesOnInParkedReply(r)).toBe(false);
  });

  it("the \"Approved\" line of another device draws under the parked reply, the run's own", () => {
    const s = run(reloadedA(), server(at, R1_AGAIN), { type: "attach", at: at + 100, messageId: "reply-1" });
    expect(goesOnInParkedReply(s)).toBe(true);
    expect(receiptAnchorBeforeRun(caseAShown, s.messageId, goesOnInParkedReply(s))).toBe("reply-1");
    // A notice after the reply changes nothing.
    const withNotice = [...caseAShown, msg("n-1", "system", "__ERROR__{}", 3)];
    expect(receiptAnchorBeforeRun(withNotice, "reply-1", true)).toBe("reply-1");
    // Before the attach names the reply: the last turn, which is the reply.
    expect(receiptAnchorBeforeRun(caseAShown, null, true)).toBe("reply-1");
    // Case B keeps the line under the parked reply, before the new one.
    const b = run(reduceRun(IDLE_RUN, server(T0 + 1_000, PARKED_ROW)),
      { type: "resumed", at, runId: "r2", messageId: "reply-r2" },
      { type: "attach", at: at + 100, messageId: "reply-r2" });
    expect(goesOnInParkedReply(b)).toBe(false);
    const bShown = [...caseAShown, msg("reply-r2", "assistant", "", 10)];
    expect(receiptAnchorBeforeRun(bShown, b.messageId, goesOnInParkedReply(b))).toBe("reply-1");
  });
});

describe("case A: the answer POST reads `sameReply`", () => {
  it("only the JSON `true` counts, and a decline's 200 stays as before", () => {
    expect(resumedOf('{"ok":true,"resumed":true,"threadId":"t","runId":"r1","sameReply":true,"messageId":"reply-1"}'))
      .toEqual({ runId: "r1", threadId: "t", messageId: "reply-1", sameReply: true });
    expect(resumedOf('{"resumed":true,"threadId":"t","runId":"r1","sameReply":"true"}')).toEqual({ runId: "r1", threadId: "t" });
    expect(resumedOf('{"resumed":true,"threadId":"t","runId":"r2","sameReply":false}')).toEqual({ runId: "r2", threadId: "t" });
    // RESUME-4a-2: the 200 of a late decline has no `sameReply` and no `messageId`.
    expect(resumedOf('{"ok":true,"resumed":true,"declined":true,"threadId":"t","runId":"r9"}')).toEqual({ runId: "r9", threadId: "t" });
  });
});

describe("case A: the end of a replay", () => {
  const ends = (frames: { type: string; parked?: boolean }[]): ReplayEnd =>
    frames.reduce<ReplayEnd>((e, f) => replayEndAfter(e, f), REPLAY_OPEN);

  it("the end is the last `done` after the last `run_resumed`", () => {
    expect(ends([{ type: "delta" }])).toEqual({ terminal: false, parked: false });
    expect(ends([{ type: "delta" }, { type: "done" }])).toEqual({ terminal: true, parked: false });
    expect(ends([{ type: "done", parked: true }])).toEqual({ terminal: true, parked: true });
    // A park, then the same run goes on: open again.
    expect(ends([{ type: "done", parked: true }, { type: RUN_RESUMED_FRAME }])).toEqual(REPLAY_OPEN);
    // A Stop's `done` in the window of the start, then part two, then the end.
    expect(ends([{ type: "done" }, { type: RUN_RESUMED_FRAME }, { type: "delta" }, { type: "done" }]))
      .toEqual({ terminal: true, parked: false });
    // Part two parks again.
    expect(ends([{ type: "done", parked: true }, { type: RUN_RESUMED_FRAME }, { type: "done", parked: true }]))
      .toEqual({ terminal: true, parked: true });
    // A replay that starts at the marked start (RESUME-2d may cut part one).
    expect(ends([{ type: RUN_RESUMED_FRAME }, { type: "delta" }])).toEqual(REPLAY_OPEN);
  });

  it("a cut after `run_resumed` reads as not finished, and no path shows \"Done\"", () => {
    const at = T0 + 3_600_000;
    const attached = run(reloadedA(), server(at, R1_AGAIN), { type: "attach", at: at + 100, messageId: "reply-1" },
      { type: "event", at: at + 200, step: "Writing the reply" });
    const e = ends([{ type: "delta" }, { type: "done" }, { type: RUN_RESUMED_FRAME }, { type: "delta" }]);
    const cut = reduceRun(attached, { type: "stream_end", at: at + 300, terminal: e.terminal, ...(e.terminal && e.parked ? { parked: true } : {}) });
    expect(cut.phase).toBe("finishing");
    expect(runView(cut, at + 400)?.label).not.toBe("Done");
    // The whole run then ends as the last frame says.
    const whole = ends([{ type: "done" }, { type: RUN_RESUMED_FRAME }, { type: "delta" }, { type: "done" }]);
    expect(reduceRun(attached, { type: "stream_end", at: at + 300, terminal: whole.terminal }).phase).toBe("done");
  });
});

describe("case A: the BFF frame", () => {
  it("only a RUN_STARTED marked `resumed` makes a frame", () => {
    expect(runResumedFrameOf({ type: "RUN_STARTED", runId: "r1", resumed: true })).toEqual({ type: "run_resumed", run_id: "r1" });
    expect(runResumedFrameOf({ type: "RUN_STARTED", runId: "r1" })).toBeNull();
    expect(runResumedFrameOf({ type: "RUN_STARTED", runId: "r1", resumed: "true" })).toBeNull();
    expect(runResumedFrameOf({ type: "RUN_FINISHED", runId: "r1", resumed: true })).toBeNull();
  });
});

/** Run the BFF route's reconnect path over *events*, as the gateway replays
 *  them (the harness of `runPhaseDrop.test.ts`). Gives the browser's frames. */
async function replayThroughRoute(events: Record<string, unknown>[]): Promise<Record<string, unknown>[]> {
  vi.doMock("@/auth", () => ({
    auth: async () => ({ user: { email: "ana@a.test" } }),
    isAuthEnabled: true,
  }));
  vi.doMock("@/lib/gateway", () => ({
    GATEWAY_URL: "http://gw.test",
    requireIdentity: async () => ({ email: "ana@a.test", role: "employee" }),
    gatewayHeaders: async (extra?: Record<string, string>) => ({ ...(extra ?? {}) }),
    gatewayFetch: async (input: RequestInfo | URL) => {
      const url = String(input);
      if (url.includes("/reconnect")) {
        const body = events.map((e) => `data: ${JSON.stringify(e)}\n\n`).join("");
        return new Response(body, { status: 200, headers: { "Content-Type": "text/event-stream" } });
      }
      if (url.includes("/messages")) return new Response("{}", { status: 200 });
      return new Response("{}", { status: 404 });
    },
  }));
  const { POST } = await import("@/app/api/agent/chat/route");
  const req = new Request("http://app.test/api/agent/chat", {
    method: "POST",
    body: JSON.stringify({
      agentName: "orchestrator", message: "(reconnect)", threadId: "t1",
      assistantMessageId: "reply-1", reconnect: true,
    }),
  });
  const res = await POST(req as never);
  return (await res.text())
    .split("\n\n")
    .filter((s) => s.startsWith("data: "))
    .map((s) => JSON.parse(s.slice(6)) as Record<string, unknown>);
}

describe("case A: the BFF sends `run_resumed` (the real route)", () => {
  beforeEach(() => {
    vi.resetModules();
  });
  afterEach(() => {
    vi.doUnmock("@/auth");
    vi.doUnmock("@/lib/gateway");
  });

  const delta = (d: string) => ({ type: "TEXT_MESSAGE_CONTENT", messageId: "m1", delta: d });

  it("a second start of the same run, marked `resumed`, adds ONE frame and no other change", async () => {
    const frames = await replayThroughRoute([
      { type: "RUN_STARTED", runId: "r1", threadId: "t1" },
      delta("I need your answer first."),
      { type: "RUN_FINISHED", runId: "r1", threadId: "t1", parked: true },
      { type: "RUN_STARTED", runId: "r1", threadId: "t1", resumed: true },
      delta(" Acme is now in Payment Complete."),
      { type: "RUN_FINISHED", runId: "r1", threadId: "t1" },
    ]);
    expect(frames.map((f) => f.type)).toEqual(["delta", "done", RUN_RESUMED_FRAME, "delta", "done"]);
    expect(frames[2]).toEqual({ type: RUN_RESUMED_FRAME, run_id: "r1" });
    expect(frames[1].parked).toBe(true);
  });

  it("a plain run sends no such frame, and a resumed start of ANOTHER run stops the reader", async () => {
    const plain = await replayThroughRoute([
      { type: "RUN_STARTED", runId: "r1", threadId: "t1" },
      delta("Hello."),
      { type: "RUN_STARTED", runId: "r1", threadId: "t1" },
      { type: "RUN_FINISHED", runId: "r1", threadId: "t1" },
    ]);
    expect(plain.map((f) => f.type)).toEqual(["delta", "done"]);
    vi.resetModules();
    const other = await replayThroughRoute([
      { type: "RUN_STARTED", runId: "r1", threadId: "t1" },
      delta("Part one."),
      { type: "RUN_STARTED", runId: "r2", threadId: "t1", resumed: true },
      delta("Another run."),
    ]);
    expect(other.map((f) => f.type)).toEqual(["delta"]);
  });
});

// ── RESUME-3d: the list names the reply row of a continuation ──────────────

describe("RESUME-3d: a listed continuation replays into its own reply row", () => {
  /** What `useAgentChat.attach` does: the pick, then the saved rows only
   *  when the pick asks for them, then the id. `saved` is the server's rows. */
  function attachId(s: RunStatus, shown: ChatMessage[], saved: ChatMessage[] = shown): { id: string; readSaved: boolean } {
    const pick = attachPickOf(s, shown, interrupted);
    let target = pick.target;
    if (pick.readSaved) target = planReplay(saved, shown, rejectFor(shown, s.doneMessageId, s.staleTail)).target ?? null;
    return { id: replayIdOf(target, pick.resumeId, () => "placeholder"), readSaved: pick.readSaved };
  }
  /** A saved reply reads as interrupted when it is the last row. */
  const interrupted = (m: ChatMessage | undefined) => m?.role === "assistant" && m.id.startsWith("cut-");
  /** Part one of the parked reply, as a new tab reads it from the saved rows. */
  const parkedShown = [
    msg("u-1", "user", "Move Acme to Payment Complete", 1),
    msg("reply-1", "assistant", "I need your answer first.", 2),
  ];
  const A_LISTED: ServerRun = { ...R1_AGAIN, messageId: "reply-1" };
  const B_LISTED: ServerRun = { ...R2, messageId: "reply-r2" };

  it("case A, a new tab: the run replays into the parked reply, with no read of the saved rows", () => {
    const s = reduceRun(IDLE_RUN, server(T0 + 3_600_500, A_LISTED));
    expect(s.listedReplyId).toBe("reply-1");
    expect(s.messageId).toBe("reply-1");
    expect(s.attach).toBe(true);
    expect(attachId(s, parkedShown)).toEqual({ id: "reply-1", readSaved: false });
    // The chat has not drawn the saved rows yet: the replay takes that id.
    expect(attachId(s, [])).toEqual({ id: "reply-1", readSaved: false });
  });

  it("case B, a new tab: the run replays into the NEW reply, never the parked one", () => {
    const s = reduceRun(IDLE_RUN, server(T0 + 9_000, B_LISTED));
    expect(s.listedReplyId).toBe("reply-r2");
    expect(attachId(s, parkedShown)).toEqual({ id: "reply-r2", readSaved: false });
    // The server saved the new reply at a checkpoint: that row is the target.
    const withNew = [...parkedShown, msg("reply-r2", "assistant", "Acme is now", 10)];
    expect(attachPickOf(s, withNew, interrupted).target?.id).toBe("reply-r2");
  });

  it("case B with no messageId stays as before: the saved rows pick the parked reply (the gap of task 3785)", () => {
    const s = reduceRun(IDLE_RUN, server(T0 + 9_000, R2));
    expect(s.listedReplyId).toBeNull();
    expect(attachId(s, parkedShown)).toEqual({ id: "reply-1", readSaved: true });
  });

  it("case B, the tab that watched the park: the new reply, and the stale tail settles", () => {
    const s = reduceRun(parkedLive(), server(T0 + 3_600_500, B_LISTED));
    expect(s.staleTail).toBe(true);
    expect(s.messageId).toBe("reply-r2");
    expect(attachId(s, parkedShown)).toEqual({ id: "reply-r2", readSaved: false });
  });

  it("case A, the tab that watched the park: the same run keeps its reply", () => {
    const s = reduceRun(parkedLive(), server(T0 + 3_600_500, A_LISTED));
    expect(goesOnInParkedReply(s)).toBe(true);
    expect(s.staleTail).toBe(false);
    expect(s.messageId).toBe("reply-1");
    expect(attachId(s, parkedShown)).toEqual({ id: "reply-1", readSaved: false });
  });

  it("a known run keeps the reply that this tab holds, and takes the listed one after a remount", () => {
    const own = run(IDLE_RUN, { type: "send", at: T0, messageId: "mine" }, server(T0 + 1_000, { ...R1, messageId: "listed" }, true));
    expect(own.messageId).toBe("mine");
    expect(own.listedReplyId).toBe("listed");
    const back = run(own, { type: "remount", at: T0 + 2_000, streamOpen: false });
    expect(back.listedReplyId).toBeNull();
    expect(back.messageId).toBeNull();
    expect(reduceRun(back, server(T0 + 3_000, { ...R1, messageId: "listed" })).messageId).toBe("listed");
  });

  it("a parked row names no reply, and a list with none clears it", () => {
    const s = reduceRun(IDLE_RUN, server(T0 + 1_000, { ...PARKED_A, messageId: "reply-x" }));
    expect(s.listedReplyId).toBeNull();
    const named = reduceRun(IDLE_RUN, server(T0 + 1_000, A_LISTED));
    expect(reduceRun(named, server(T0 + 6_000, R1_AGAIN)).listedReplyId).toBeNull();
  });

  it("an Approve's 200 forgets the reply of a list from before it", () => {
    const named = reduceRun(IDLE_RUN, server(T0 + 1_000, B_LISTED));
    const resumed = reduceRun(named, { type: "resumed", at: T0 + 2_000, runId: "r3", messageId: "reply-r3" });
    expect(resumed.listedReplyId).toBeNull();
    expect(resumed.messageId).toBe("reply-r3");
  });

  it("with no messageId, the pick is the rule of before, for each state", () => {
    const states = [
      reloadedA(),
      reduceRun(reloadedA(), server(T0 + 3_600_500, R1_AGAIN)),
      reduceRun(reloadedA(), server(T0 + 9_000, R2)),
      parkedLive(),
      reduceRun(IDLE_RUN, server(T0 + 1_000, R1)),
      run(IDLE_RUN, { type: "send", at: T0, messageId: "reply-1" }, server(T0 + 1_000, R1, true)),
      reduceRun(parkedLive(), { type: "resumed", at: T0 + 700_000, runId: "r2", messageId: "reply-r2" }),
    ];
    for (const s of states) {
      expect(s.listedReplyId).toBeNull();
      expect(attachId(s, caseAShown).id).toBe(replayTarget(s, caseAShown));
    }
    // An interrupted last reply is still the target when no stale tail holds.
    const cut = [...parkedShown, msg("cut-1", "assistant", "Half", 3)];
    expect(attachPickOf(IDLE_RUN, cut, interrupted)).toEqual({ target: cut[2], readSaved: false, resumeId: null });
    // The id of the 200 counts only after this tab's Approve (RESUME-3a, C2).
    expect(attachPickOf({ ...IDLE_RUN, resumedAt: T0, messageId: "m" }, [], interrupted).resumeId).toBe("m");
    expect(attachPickOf({ ...IDLE_RUN, messageId: "m" }, [], interrupted).resumeId).toBeNull();
  });

  it("the same list with and without the field is one list to an old reader", () => {
    // A server with the flags off sends no field: the run state is the state
    // of before, field for field, except the new key, which stays null.
    const before = reduceRun(IDLE_RUN, server(T0 + 9_000, R2));
    const { listedReplyId, ...rest } = before;
    expect(listedReplyId).toBeNull();
    expect(rest.messageId).toBeNull();
    expect(rest.staleTail).toBe(false);
  });
});

describe("RESUME-3d: the hook", () => {
  const hook = src("../hooks/useAgentChat.ts");

  it("the attach takes its target from attachPickOf, and reads the saved rows only when it asks", () => {
    expect(hook).toContain("attachPickOf(st0.run, st0.messages, isInterruptedReply)");
    expect(hook).toContain("if (pick.readSaved) {");
    expect(hook).toContain("replayIdOf(target, pick.resumeId, nanoid)");
  });
});
