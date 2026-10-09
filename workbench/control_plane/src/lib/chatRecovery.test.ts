/**
 * A chat recovers from an app update (incident 2026-10-09, 16:18 UTC).
 *
 * The owner typed "Continue. I think you stopped midway." four times while a
 * deploy ran. Four bubbles stood in the thread and nothing answered. These
 * tests hold the four browser rules that replaced that. Each `it` names the
 * mutation it was run against.
 */
import { readFileSync } from "node:fs";
import { fileURLToPath } from "node:url";
import { createElement } from "react";
import { renderToStaticMarkup } from "react-dom/server";
import { afterEach, describe, expect, it, vi } from "vitest";

import { ErrorCardView } from "@/components/ChatErrorCard";
import {
  CONTINUE_TEXT,
  RUN_INTERRUPTED_EVENT,
  STALE_RUN_MS,
  holdForUpdate,
  idleAfterStaleRecovery,
  interruptedTurn,
  isStaleRecovery,
  isUpdateOutage,
  isWaiting,
  planSend,
  queueOnce,
  registerSender,
  restartedCardMessage,
  retryHeldNow,
  setRecoveryDeps,
  waitUntilBack,
  type SendOptions,
} from "@/lib/chatRecovery";
import { getSessionState, setSessionState, type ChatMessage } from "@/lib/chatStore";
import { sendRespondInputResult } from "@/lib/respondInput";
import { coverOutage, onOutageCover, outageCovered, uncoverOutage } from "@/lib/shell/serviceHealth";

const SRC = fileURLToPath(new URL("..", import.meta.url));

let n = 0;
const thread = () => `t-recover-${++n}`;
const user = (id: string, content: string, extra: Partial<ChatMessage> = {}): ChatMessage => ({
  id, role: "user", content, timestamp: 1, ...extra,
});
const answer = (id: string, extra: Partial<ChatMessage> = {}): ChatMessage => ({
  id, role: "assistant", content: "half an answer", timestamp: 2, ...extra,
});
const cut = { name: RUN_INTERRUPTED_EVENT, value: { reason: "restart" } };

/** A probe that answers "down" `downs` times, then "up". */
function probeAfter(downs: number) {
  let calls = 0;
  return { probe: async () => ++calls > downs, calls: () => calls };
}

afterEach(() => setRecoveryDeps(null));

// ── 1. The app is updating: hold once, send when back ──────────────────────

describe("an app update holds a send once and sends it when the gateway is back", () => {
  it("reads a 502, a 503 and a refused connection as an update, and nothing else", () => {
    // Mutation: drop 503 from the outage statuses, and the second line fails.
    expect(isUpdateOutage({ status: 502, gotResponse: true })).toBe(true);
    expect(isUpdateOutage({ status: 503, gotResponse: true })).toBe(true);
    expect(isUpdateOutage({ status: null, gotResponse: false, err: new TypeError("Failed to fetch") })).toBe(true);
    // A run that failed is an answer, not an update.
    expect(isUpdateOutage({ status: 500, gotResponse: true })).toBe(false);
    expect(isUpdateOutage({ status: 409, gotResponse: true })).toBe(false);
    // The member pressed Stop, or the stream broke AFTER the app took it.
    expect(isUpdateOutage({ status: null, gotResponse: false, err: new DOMException("x", "AbortError") })).toBe(false);
    expect(isUpdateOutage({ status: null, gotResponse: true, err: new TypeError("network") })).toBe(false);
  });

  it("holds the same words once", () => {
    // Mutation: queueOnce appends without the includes check, and this fails.
    expect(queueOnce(["Continue"], "Continue")).toEqual(["Continue"]);
    expect(queueOnce(["Continue"], " Continue ")).toEqual(["Continue"]);
    expect(queueOnce(["a"], "b")).toEqual(["a", "b"]);
  });

  it("holds the send, shows the notice, and sends it ONCE when /api/health says up", async () => {
    // Mutation: flushHeld returns before it calls the sender, and this fails.
    const tid = thread();
    const sent: Array<[string, SendOptions | undefined]> = [];
    registerSender(tid, async (text, opts) => { sent.push([text, opts]); });
    const probe = probeAfter(2);
    setRecoveryDeps({ probe: probe.probe, sleep: async () => {} });
    setSessionState(tid, (p) => ({ ...p, messages: [user("u1", "Continue")] }));

    holdForUpdate(tid, "Continue", { userMsgId: "u1" });
    holdForUpdate(tid, "Continue", { userMsgId: "u1" }); // a second press
    const held = getSessionState(tid);
    expect(held.outage).toBe(true);
    expect(held.pendingSends).toEqual(["Continue"]);
    expect(held.messages[0].pendingDelivery).toBe(true);

    await vi.waitFor(() => expect(sent).toHaveLength(1));
    expect(sent[0]).toEqual(["Continue", { heldId: "u1" }]);
    expect(probe.calls()).toBe(3);
    const after = getSessionState(tid);
    expect(after.outage).toBe(false);
    expect(after.pendingSends).toEqual([]);
    expect(isWaiting(tid)).toBe(false);
  });

  it("keeps the order of held sends, and stops at the first one the app refuses again", async () => {
    const tid = thread();
    const sent: string[] = [];
    registerSender(tid, async (text) => {
      sent.push(text);
      // The gateway is down again: the send holds itself, at the head.
      if (text === "two") holdForUpdate(tid, text, { front: true });
    });
    setRecoveryDeps({ probe: async () => false, sleep: () => new Promise(() => {}) });
    holdForUpdate(tid, "one");
    holdForUpdate(tid, "two");
    holdForUpdate(tid, "three");
    await retryHeldNow(tid);
    expect(sent).toEqual(["one", "two"]);
    expect(getSessionState(tid).pendingSends).toEqual(["two", "three"]);
    expect(getSessionState(tid).outage).toBe(true);
  });

  it("keeps a held Continue a Continue", async () => {
    // Review of #797, nit. A Continue held during an update used to go out
    // as the bare word. Mutation: drop pendingResume from flushHeld.
    const tid = thread();
    const sent: Array<[string, SendOptions | undefined]> = [];
    registerSender(tid, async (text, opts) => { sent.push([text, opts]); });
    setRecoveryDeps({ probe: async () => false, sleep: () => new Promise(() => {}) });
    holdForUpdate(tid, CONTINUE_TEXT, { resume: true });
    holdForUpdate(tid, "plain");
    await retryHeldNow(tid);
    expect(sent).toEqual([[CONTINUE_TEXT, { resume: true }], ["plain", undefined]]);
    expect(getSessionState(tid).pendingResume).toEqual([]);
  });

  it("lets the shell toast stand down while the chat's notice covers the outage", () => {
    // Review of #797, nit: the member reads "Metorite is updating" once.
    const heard: boolean[] = [];
    const off = onOutageCover(() => heard.push(outageCovered()));
    expect(outageCovered()).toBe(false);
    coverOutage("chat:a");
    coverOutage("chat:a");
    uncoverOutage("chat:a");
    off();
    expect(heard).toEqual([true, false]);
    const notice = readFileSync(`${SRC}/lib/shell/UpdateNotice.tsx`, "utf8");
    expect(notice).toContain('if ((state === "updating" || state === "busy") && outageCovered()) {');
    const chat = readFileSync(`${SRC}/components/AgentChat.tsx`, "utf8");
    expect(chat).toContain("coverOutage(id);");
  });

  it("waits with backoff, and stops when told to", async () => {
    const waits: number[] = [];
    const probe = probeAfter(3);
    const back = await waitUntilBack({
      probe: probe.probe, sleep: async (ms) => { waits.push(ms); },
    });
    expect(back).toBe(true);
    expect(waits).toEqual([1_000, 2_000, 4_000, 8_000]);
    const ctrl = new AbortController();
    ctrl.abort();
    expect(await waitUntilBack({ probe: async () => true, sleep: async () => {}, signal: ctrl.signal })).toBe(false);
  });

  it("draws ONE notice, as a status and not an error, with Retry", () => {
    const html = renderToStaticMarkup(createElement(ErrorCardView, {
      error: { code: "updating", ref: null, raw: "" }, onRetry: () => {},
    }));
    expect(html).toContain("Metorite is updating");
    expect(html).toContain("Your message will send when it is back.");
    expect(html).toContain('role="status"');
    expect(html).toContain('data-tone="notice"');
    expect(html).toContain(">Retry<");
    expect(html).not.toContain("Show full error");
    // Tokens only: the warning ramp, never a raw palette class.
    expect(html).toContain("border-warning/40");
    expect(html).not.toMatch(/\b(?:bg|text|border)-(?:red|amber|yellow|orange)-\d/);
  });
});

// ── 2. No message twice ────────────────────────────────────────────────────

describe("a repeated send collapses while one is pending", () => {
  const base = { isLoading: false, outage: false, pendingSends: [] as string[] };

  it("collapses the incident's four 'Continue' presses into one", () => {
    // Mutation: drop the isRepeatWhilePending check from planSend, and the
    // second line answers "send".
    const msgs = [answer("a0"), user("u1", "Continue", { pendingDelivery: true })];
    expect(planSend({ ...base, messages: msgs }, "Continue")).toBe("collapse");
    expect(planSend({ ...base, messages: msgs }, "  Continue ")).toBe("collapse");
    // Different words are a new turn.
    expect(planSend({ ...base, messages: msgs }, "Also check X")).toBe("send");
  });

  it("collapses a send whose words are already held", () => {
    expect(planSend({ ...base, outage: true, pendingSends: ["Continue"], messages: [] }, "Continue"))
      .toBe("collapse");
    expect(planSend({ ...base, outage: true, pendingSends: ["Continue"], messages: [] }, "Other"))
      .toBe("hold");
  });

  it("does not collapse once an answer came back", () => {
    const msgs = [user("u1", "Continue", { pendingDelivery: true }), answer("a1")];
    expect(planSend({ ...base, messages: msgs }, "Continue")).toBe("send");
    // A turn that did reach a run is not pending: the member may ask again.
    expect(planSend({ ...base, messages: [user("u2", "Continue")] }, "Continue")).toBe("send");
  });

  it("sends a held bubble in place, and requeues it when a turn is running", () => {
    const msgs = [user("u1", "Continue", { pendingDelivery: true })];
    expect(planSend({ ...base, messages: msgs }, "Continue", "u1")).toBe("send");
    expect(planSend({ ...base, isLoading: true, messages: msgs }, "Continue", "u1")).toBe("requeue");
  });
});

// ── 3. The composer is never dead ──────────────────────────────────────────

describe("the composer falls back to idle", () => {
  const now = 1_000_000;

  it("after STALE_RUN_MS of silence, and never while a stream is live", () => {
    // Mutation: return false from isStaleRecovery, and the first line fails.
    const s = { recovering: true, runStatus: "recovering", hasLiveStream: false, since: now - STALE_RUN_MS - 1, now };
    expect(isStaleRecovery(s)).toBe(true);
    expect(isStaleRecovery({ ...s, since: now - 1_000 })).toBe(false);
    expect(isStaleRecovery({ ...s, hasLiveStream: true })).toBe(false);
    expect(isStaleRecovery({ ...s, recovering: false, runStatus: "unknown" })).toBe(true);
    expect(isStaleRecovery({ ...s, recovering: false, runStatus: "idle" })).toBe(false);
  });

  it("clears the spinner and offers Continue on the last answer", () => {
    // Mutation: drop markInterrupted from idleAfterStaleRecovery, and the
    // notice never shows.
    const tid = thread();
    setSessionState(tid, (p) => ({
      ...p, recovering: true, runStatus: "recovering", isLoading: true,
      messages: [user("u1", "Plan it"), answer("a1", { streaming: true })],
    }));
    setSessionState(tid, idleAfterStaleRecovery);
    const st = getSessionState(tid);
    expect(st.recovering).toBe(false);
    expect(st.isLoading).toBe(false);
    expect(st.runStatus).toBe("idle");
    expect(st.messages[1].streaming).toBe(false);
    expect(interruptedTurn(st.messages)).toBe("a1");
  });

  it("keeps the poll alive when a poll fails, so the fallback can fire", () => {
    // The old poll returned on a failed response and never polled again,
    // which left "Reconnecting…" and a Stop button up until a reload.
    const src = readFileSync(`${SRC}/hooks/useAgentChat.ts`, "utf8");
    expect(src).toContain("if (!res.ok) { pollAgain(10000); return; }");
    expect(src).not.toMatch(/if \(!res\.ok\) return;/);
    expect(src).toContain("setSessionState(threadId, idleAfterStaleRecovery)");
  });
});

// ── 4. A run an update ended: the notice and Continue ──────────────────────

describe("the interrupted notice and its Continue button", () => {
  it("shows on the last answer that carries the marker, and only there", () => {
    // Mutation: interruptedTurn ignores the marker, and the third line fails.
    expect(interruptedTurn([user("u1", "x"), answer("a1", { customEvents: [cut] })])).toBe("a1");
    // The member moved on.
    expect(interruptedTurn([answer("a1", { customEvents: [cut] }), user("u2", "y")])).toBeNull();
    expect(interruptedTurn([user("u1", "x"), answer("a1")])).toBeNull();
    // Still streaming: not settled yet.
    expect(interruptedTurn([answer("a1", { customEvents: [cut], streaming: true })])).toBeNull();
  });

  it("names the update and offers Continue", () => {
    const html = renderToStaticMarkup(createElement(ErrorCardView, {
      error: { code: "interrupted", ref: null, raw: "" }, onRetry: () => {},
    }));
    expect(html).toContain("The assistant was interrupted by an update");
    expect(html).toContain(">Continue<");
    expect(html).not.toContain(">Retry<");
  });

  it("Continue sends a flag, and the server writes the words", () => {
    // Mutation: send the note text from the browser, and the second line
    // fails. The words the model reads are gateway/chat_recovery.py's.
    const chat = readFileSync(`${SRC}/components/AgentChat.tsx`, "utf8");
    expect(chat).toContain("sendMessage(CONTINUE_TEXT, { resume: true })");
    expect(chat).toContain('error={{ code: "interrupted", ref: null, raw: "" }} onRetry={handleContinue}');
    expect(CONTINUE_TEXT).toBe("Continue");
    const route = readFileSync(`${SRC}/app/api/agent/chat/route.ts`, "utf8");
    expect(route).toContain("...(resume === true ? { resume: true } : {})");
  });

  it("re-opens a stream that ended early, and never marks it on its own", () => {
    // Review of #797, P2. A run parked on a card outlives the fetch budget,
    // so a stream with no terminal event is NOT proof the run died. The
    // route re-opens it; only the gateway (a dead owner) writes the marker.
    // Mutation: put back the route's own marker, and the last line fails.
    const route = readFileSync(`${SRC}/app/api/agent/chat/route.ts`, "utf8");
    expect(route).toContain("const next = await reattach(lastStreamId).catch(() => null);");
    expect(route).toContain("threadId ? reattachFor(threadId, runHeaders) : undefined");
    expect(route).not.toContain("RUN_INTERRUPTED_EVENT");
  });

  it("hides the marker from the raw Interactive view", () => {
    const panel = readFileSync(`${SRC}/components/GenerativeUIPanel.tsx`, "utf8");
    expect(panel).toContain('"run_interrupted",');
  });
});

// ── 5. A card answer across a restart ──────────────────────────────────────

describe("a card answer to a run that restarted", () => {
  const restarted = JSON.stringify({ detail: {
    error: "run_restarted",
    message: "The assistant restarted. Your answer will be sent as a new message.",
    resumeMessage: 'You asked me: "Ship it?"\n\nMy answer: yes',
  } });

  it("comes back as the message to send, with its question", async () => {
    // Mutation: drop the 409 branch in sendRespondInputResult, and resend is null.
    const fetchFn = (async () => new Response(restarted, { status: 409 })) as typeof fetch;
    const res = await sendRespondInputResult(
      { request_id: "r1", answer: "yes", was_freeform: true, thread_id: "t" }, fetchFn,
    );
    expect(res).toEqual({ outcome: "drop", resend: 'You asked me: "Ship it?"\n\nMy answer: yes' });
  });

  it("keeps the old drop for any other 409", async () => {
    const fetchFn = (async () => new Response(JSON.stringify({ detail: "No pending question" }), { status: 409 })) as typeof fetch;
    const res = await sendRespondInputResult(
      { request_id: "r1", answer: "yes", was_freeform: true, thread_id: "t" }, fetchFn,
    );
    expect(res).toEqual({ outcome: "drop", resend: null });
    expect(restartedCardMessage("not json")).toBeNull();
  });
});
