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
 */

import { readFileSync } from "node:fs";
import { fileURLToPath } from "node:url";

import { describe, expect, it, vi } from "vitest";

import type { ChatMessage } from "@/lib/chatStore";
import { receiptAnchorBeforeRun, waitingCardIds } from "@/lib/chatResume";
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
    expect(hook).toContain("parkedEnd = evt.parked === true;");
    expect(hook).toContain('if (parkedEnd) emitAgentEvent("onRunRested", { threadId });');
    expect(hook).toContain("...(terminal && parkedEnd ? { parked: true } : {})");
    expect(hook).toContain("replayIdOf(target, st0.run.resumedAt > 0 ? st0.run.messageId : null, nanoid)");
  });

  it("the BFF keeps `parked` on the done frame", () => {
    const route = src("../app/api/agent/chat/route.ts");
    expect(route).toContain('out = { type: "done", run_id: ev.runId || ev.run_id };\n');
    expect(route).toContain("if (ev.parked === true) out.parked = true;");
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
});

describe("RESUME-2d: a withheld reconnect never attaches in a loop (review P1)", () => {
  const hook = src("../hooks/useAgentChat.ts");

  it("the attach loop reads the marker and tells the run state `attach_withheld`", () => {
    expect(hook).toContain("withheldEnd = evt.withheld === true;");
    const at = hook.indexOf('if (streamed && withheldEnd) dispatchRun(threadId, { type: "attach_withheld", at: Date.now() });');
    expect(at).toBeGreaterThan(0);
    // It comes before the normal `stream_end`, so a withheld end is no Done.
    expect(at).toBeLessThan(hook.indexOf('else if (streamed) dispatchRun(threadId, { type: "stream_end"'));
    // And it sets no "recovering" state: the run is not this reader's.
    expect(hook).toContain("recovering: !recovered && !withheldEnd,");
  });

  it("the BFF keeps `withheld` on the done frame", () => {
    const route = src("../app/api/agent/chat/route.ts");
    expect(route).toContain("if (ev.withheld === true) out.withheld = true;");
  });
});
