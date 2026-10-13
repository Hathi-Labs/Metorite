/**
 * A message sent while the agent works ALWAYS steers the live run (owner,
 * 2026-10-12). `lib/chatSteer.ts` holds the rules. This file proves them,
 * and that the composer, the hook and the bubble use them.
 *
 * Vitest runs in node, so no effect runs. The send is driven with a fake
 * `fetch` against the real module store (`lib/chatStore.ts`). The composer
 * is drawn to markup with the store already in a live run.
 *
 * Mutations this file catches (R7), each run red before the change:
 *
 * - the client stops on send (`stopGeneration` in the send path) -> "steering
 *   never stops the run…" and "the composer steers, and never stops…";
 * - the client enqueues instead of posting -> "steering POSTs once…" and
 *   "the composer steers…";
 * - the button ignores the box state (`primaryAction` returns "send" always)
 *   -> "Stop with an empty box…" and "the live composer draws Stop";
 * - a refused steer drops the words -> "a refusal leaves no bubble…";
 * - an old queued item sends itself after the upgrade -> "an old queue goes
 *   into the box once".
 */
import { readFileSync } from "node:fs";
import { join } from "node:path";
import { createElement } from "react";
import { renderToStaticMarkup } from "react-dom/server";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

vi.mock("next/navigation", () => ({ useRouter: () => ({ push: vi.fn() }) }));

import AgentChat from "@/components/AgentChat";
import ChatSendButton from "@/components/ChatSendButton";
import MessageBubble from "@/components/MessageBubble";
import { getSessionState, setSessionState, type ChatMessage } from "@/lib/chatStore";
import { editableLastUserId, lastUserMessageId, supersededIds } from "@/lib/chatEdit";
import {
  STEERED,
  STEER_WAITING,
  FOLLOW_UP_TRIES,
  cancelFollowUp,
  followUpDue,
  followUpRetryDelay,
  followUpRetryState,
  owedAfterSteer,
  scheduleFollowUpRetry,
  sendShortcut,
  settleFollowUpAnswer,
  isSteered,
  lastNonSteer,
  primaryAction,
  refusalNotice,
  sendRoute,
  sendSteer,
  takeLeftoverQueue,
} from "@/lib/chatSteer";
import { bindChatScope, chatScope, forgetChatSessions, getQueue, saveQueue } from "@/lib/sessions";

const read = (rel: string) => readFileSync(join(__dirname, rel), "utf-8");
/** Source without comments, so a word in a comment proves nothing. */
const code = (src: string) => src.replace(/\/\*[\s\S]*?\*\//g, "").replace(/\/\/.*/g, "");

let n = 0;
/** A fresh thread in the module store, mid-run: a reply is streaming. */
function liveThread(): { threadId: string; controller: AbortController; streaming: ChatMessage } {
  const threadId = `steer-${++n}`;
  const controller = new AbortController();
  const streaming: ChatMessage = {
    id: "a1", role: "assistant", content: "Adding tasks to the proj", timestamp: 2, streaming: true,
  };
  setSessionState(threadId, (prev) => ({
    ...prev,
    isLoading: true,
    abortController: controller,
    messages: [{ id: "u1", role: "user", content: "Plan the release", timestamp: 1 }, streaming],
  }));
  return { threadId, controller, streaming };
}

/** The body the hook builds, reduced to what this file reads. */
const body = (turn: { message: string; userMessageId: string; history: ChatMessage[] }) => ({
  agentName: "orchestrator", ...turn,
});
const json = (status: number, b: unknown) =>
  new Response(JSON.stringify(b), { status, headers: { "content-type": "application/json" } });

describe("one primary control: Send or Stop", () => {
  it("Stop with an empty box during a run, Send as soon as it has text", () => {
    expect(primaryAction({ runActive: true, hasText: false })).toBe("stop");
    expect(primaryAction({ runActive: true, hasText: true })).toBe("send");
    expect(primaryAction({ runActive: false, hasText: false })).toBe("send");
    expect(primaryAction({ runActive: false, hasText: true })).toBe("send");
  });

  it("a send during a run steers, on the gateway path only", () => {
    expect(sendRoute({ runActive: false, mode: "copilot" })).toBe("send");
    expect(sendRoute({ runActive: true, mode: "copilot" })).toBe("steer");
    expect(sendRoute({ runActive: true, mode: "litellm" })).toBe("unsupported");
    expect(sendRoute({ runActive: false, mode: "litellm" })).toBe("send");
  });

  it("the button draws one face per state, and Stop is never a submit", () => {
    const stop = renderToStaticMarkup(createElement(ChatSendButton, { action: "stop" }));
    expect(stop).toContain('aria-label="Stop the reply"');
    expect(stop).toContain('data-chat-primary="stop"');
    expect(stop).toContain('type="button"');
    const send = renderToStaticMarkup(createElement(ChatSendButton, {}));
    expect(send).toContain('aria-label="Send"');
    expect(send).toContain('type="submit"');
    expect(send).toContain("rounded-full");
  });
});

describe("the composer", () => {
  const draw = (sessionId: string) =>
    renderToStaticMarkup(createElement(AgentChat, { agentName: "projects-assistant", sessionId }));

  it("draws no send-mode menu, no split button and no second Stop", () => {
    const html = draw(`idle-${++n}`);
    expect(html).not.toMatch(/send mode|Change send mode|Queue|Steer/i);
    expect(html.match(/data-chat-primary=/g)).toHaveLength(1);
    expect(html).toContain('data-chat-primary="send"');
    // The message takes the whole width: the textarea comes first in the
    // box, before the bar with Attach.
    const box = html.slice(html.indexOf("data-chat-composer"));
    expect(box.indexOf("<textarea")).toBeLessThan(box.indexOf('aria-label="Attach files"'));
  });

  it("the live composer draws Stop for an empty box, with the steer placeholder", () => {
    const { threadId } = liveThread();
    const html = draw(threadId);
    expect(html).toContain('data-chat-primary="stop"');
    expect(html).not.toContain('data-chat-primary="send"');
    expect(html).toContain("Add to the reply of");
    expect(html).not.toContain("Stop generation");
  });

  it("the composer steers, and never stops, enqueues or drops the reply", () => {
    const src = code(read("../components/AgentChat.tsx"));
    const start = src.indexOf("const submitText = useCallback(");
    const block = src.slice(start, src.indexOf("const handleStop", start));
    expect(start).toBeGreaterThan(0);
    expect(block).toContain("sendRoute({ runActive: isRunActive, mode: effectiveRuntime })");
    expect(block).toContain("steerMessage(trimmed)");
    expect(block).not.toMatch(/stopGeneration|enqueue|setMessages|slice\(0, -1\)/);
    // A refusal gives the words back with its notice.
    expect(block).toContain('if (out.kind === "refused") giveBack(trimmed, out.notice);');
    expect(src).toMatch(/setInput\(\(prev\) => mergeCappedText\(prev, text\)\);\s*setComposerNotice\(notice\);/);
    // No send mode and no queue anywhere in the composer.
    expect(src).not.toMatch(/\bSendMode\b|\bsendMode\b|queueRef|enqueue\(|showSendMenu|saveQueue|getQueue/);
    // The one control follows the box.
    expect(src).toContain("primaryAction({ runActive: isRunActive && !loadingHistory, hasText: !!input.trim() })");
    expect(src).toMatch(/<ChatSendButton\s+action=\{primary\}\s+onClick=\{primary === "stop" \? handleStop : undefined\}/);
  });

  it("Esc stops a live run only from an empty box", () => {
    const src = code(read("../components/AgentChat.tsx"));
    expect(src).toMatch(/e\.key === "Escape" && primaryAction\(\{[^}]*hasText: !!input\.trim\(\) \}\) === "stop"\) \{\s*e\.preventDefault\(\);\s*handleStop\(\);/);
  });
});

describe("sendSteer", () => {
  let fetchMock: ReturnType<typeof vi.fn>;
  beforeEach(() => { fetchMock = vi.fn(); });

  it("steering POSTs once and never stops the run, drops the reply or opens a stream", async () => {
    const { threadId, controller, streaming } = liveThread();
    fetchMock.mockResolvedValue(json(202, { steered: true, delivered: true, pendingReplay: false }));
    const out = await sendSteer({ threadId, text: " Also add a due date ", buildBody: body, fetchImpl: fetchMock as typeof fetch });

    expect(out).toEqual({ kind: "folded", pendingReplay: false });
    expect(fetchMock).toHaveBeenCalledTimes(1);
    const [url, init] = fetchMock.mock.calls[0] as [string, RequestInit];
    expect(url).toBe("/api/agent/chat");
    expect(init.method).toBe("POST");
    const sent = JSON.parse(String(init.body));
    expect(sent.message).toBe("Also add a due date");
    expect(sent.userMessageId).toBeTruthy();
    // The history is the thread without the steer itself.
    expect(sent.history.map((m: ChatMessage) => m.id)).toEqual(["u1", "a1"]);

    const st = getSessionState(threadId);
    // The live run keeps its stream, its controller and its partial reply.
    expect(st.isLoading).toBe(true);
    expect(st.abortController).toBe(controller);
    expect(controller.signal.aborted).toBe(false);
    expect(st.messages[1]).toBe(streaming);
    // The words show at once, marked, after the reply they join.
    const bubble = st.messages[2];
    expect(bubble.content).toBe("Also add a due date");
    expect(isSteered(bubble)).toBe(true);
    expect(bubble.steerAck).toBe("added");
    expect(bubble.awaitingServer).toBeUndefined();
    expect(st.messages).toHaveLength(3);
    // The run owes a follow-up once it ends.
    expect(st.steerOwed).toBe(true);
  });

  it("draws the bubble as 'sending' before the server answers", async () => {
    const { threadId } = liveThread();
    let answer: (r: Response) => void = () => {};
    fetchMock.mockReturnValue(new Promise<Response>((r) => { answer = r; }));
    const pending = sendSteer({ threadId, text: "and the owner", buildBody: body, fetchImpl: fetchMock as typeof fetch });
    const bubble = getSessionState(threadId).messages.at(-1)!;
    expect(bubble.steerAck).toBe("sending");
    expect(bubble.awaitingServer).toBe(true);
    // A second press of the same words is one steer.
    expect(await sendSteer({ threadId, text: "and the owner", buildBody: body, fetchImpl: fetchMock as typeof fetch }))
      .toEqual({ kind: "collapsed" });
    answer(json(202, { steered: true }));
    await pending;
    expect(fetchMock).toHaveBeenCalledTimes(1);
  });

  it("a refusal leaves no bubble and says why, so the words go back to the box", async () => {
    const { threadId } = liveThread();
    const refusal = `data: ${JSON.stringify({
      type: "error",
      content: JSON.stringify({ detail: { error: "steer_outside_run_floor", message: "This run started before you joined the room." } }),
    })}\n\n`;
    fetchMock.mockResolvedValue(new Response(refusal, { status: 409 }));
    const out = await sendSteer({ threadId, text: "skip staging", buildBody: body, fetchImpl: fetchMock as typeof fetch });
    expect(out).toEqual({ kind: "refused", notice: "This run started before you joined the room." });
    const st = getSessionState(threadId);
    expect(st.messages.map((m) => m.id)).toEqual(["u1", "a1"]);
    expect(st.steerOwed).toBeFalsy();
    expect(st.isLoading).toBe(true);
  });

  it("a network failure is a refusal too, never a silent drop", async () => {
    const { threadId } = liveThread();
    fetchMock.mockRejectedValue(new TypeError("Failed to fetch"));
    const out = await sendSteer({ threadId, text: "and the owner", buildBody: body, fetchImpl: fetchMock as typeof fetch });
    expect(out.kind).toBe("refused");
    expect(getSessionState(threadId).messages).toHaveLength(2);
  });

  it("a stream means the run had ended: the words started a new run", async () => {
    const { threadId } = liveThread();
    const stream = new Response("data: {\"type\":\"done\"}\n\n", { status: 200, headers: { "content-type": "text/event-stream" } });
    fetchMock.mockResolvedValue(stream);
    const out = await sendSteer({ threadId, text: "Now the budget", buildBody: body, fetchImpl: fetchMock as typeof fetch });
    expect(out.kind).toBe("started");
    if (out.kind !== "started") return;
    expect(out.response).toBe(stream);
    const bubble = getSessionState(threadId).messages.find((m) => m.id === out.bubbleId)!;
    // A normal turn now: no steer mark, and saved like any turn.
    expect(isSteered(bubble)).toBe(false);
    expect(bubble.steerAck).toBeUndefined();
    expect(bubble.awaitingServer).toBeUndefined();
  });

  it("a bare stop ends the run, and its bubble goes as the server saved none", async () => {
    const { threadId } = liveThread();
    fetchMock.mockResolvedValue(json(202, { steered: false, aborted: true }));
    expect(await sendSteer({ threadId, text: "stop", buildBody: body, fetchImpl: fetchMock as typeof fetch }))
      .toEqual({ kind: "stopped" });
    expect(getSessionState(threadId).messages).toHaveLength(2);
  });

  it("the run cap refusal names the cap, not the raw body", () => {
    expect(refusalNotice(429, "")).toMatch(/Too many of your replies are running/);
    expect(refusalNotice(500, "Internal Server Error")).toBe(
      "The agent could not take your message now. It is back in the box.",
    );
  });
});

describe("the hook", () => {
  const hook = code(read("../hooks/useAgentChat.ts"));

  it("asks for the follow-up once the run ends, and a Stop asks for none", () => {
    expect(followUpDue({ steerOwed: true, isLoading: false, recovering: false, outage: false }, "copilot")).toBe(true);
    expect(followUpDue({ steerOwed: true, isLoading: true, recovering: false, outage: false }, "copilot")).toBe(false);
    expect(followUpDue({ steerOwed: true, isLoading: false, recovering: true, outage: false }, "copilot")).toBe(false);
    expect(followUpDue({ steerOwed: true, isLoading: false, recovering: false, outage: true }, "copilot")).toBe(false);
    expect(followUpDue({ steerOwed: false, isLoading: false, recovering: false, outage: false }, "copilot")).toBe(false);
    expect(followUpDue({ steerOwed: true, isLoading: false, recovering: false, outage: false }, "litellm")).toBe(false);
    expect(hook).toMatch(/if \(!followUpDue\(st, modeRef\.current\)\) return;\s*setSessionState\(threadId, \(prev\) => \(\{ \.\.\.prev, steerOwed: false \}\)\);\s*void sendMessage\("", \{ steerFollowUp: true \}\);/);
    expect(hook).toMatch(/runStatus: "idle",\s*steerOwed: false,/);
  });

  it("a steer that started a run follows that response, in place of its bubble", () => {
    expect(hook).toMatch(/if \(out\.kind === "started"\) \{\s*void sendMessage\(content, \{\s*heldId: out\.bubbleId,\s*adopt: \{ response: out\.response, controller: out\.controller, assistantId: replyId \},/);
    expect(hook).toContain("const res = adopt ? adopt.response : await fetch(\"/api/agent/chat\", {");
    // A follow-up draws no user bubble, and names itself to the server.
    expect(hook).toContain("...(followUp ? [] : [userMsg]),");
    expect(hook).toContain("...(followUp ? { steerFollowUp: true } : {}),");
  });

  it("any steer owes a follow-up, and one that met a live run asks again (review of #920)", () => {
    // A plain send that the gateway steered: another member's run in a room.
    expect(hook).toContain("if (outcome.steered) owedAfterSteer(threadId, stopEpochAtSend);");
    expect(hook).toContain("else if (followUp) settleFollowUpAnswer(threadId, outcome.reason);");
    expect(followUpRetryDelay(1)).toBe(2000);
    expect(followUpRetryDelay(2)).toBe(4000);
    expect(followUpRetryDelay(6)).toBe(30_000);
    expect(followUpRetryDelay(FOLLOW_UP_TRIES)).toBe(30_000);
    expect(followUpRetryDelay(FOLLOW_UP_TRIES + 1)).toBeNull();
    expect(followUpRetryDelay(0)).toBeNull();
  });

  it("a steer that started a run keeps one id for its reply row", () => {
    expect(hook).toContain("assistantMessageId: replyId,");
    expect(hook).toContain("adopt: { response: out.response, controller: out.controller, assistantId: replyId },");
    expect(hook).toContain("const assistantId = adopt?.assistantId ?? nanoid();");
  });

  it("the BFF forwards the follow-up on the gateway path only", () => {
    const route = code(read("../app/api/agent/chat/route.ts"));
    expect(route).toContain('const followUp = steerFollowUp === true && mode === "copilot" && !!threadId;');
    expect(route).toContain("...(followUp ? { steer_followup: true } : {}),");
  });
});

describe("the steered bubble", () => {
  const steered = (ack?: "sending" | "added"): ChatMessage => ({
    id: "s1", role: "user", content: "Also add a due date", timestamp: 3,
    customEvents: [{ name: STEERED, value: {} }], ...(ack ? { steerAck: ack } : {}),
  });

  it("draws the fold acknowledgement, and the wait before it", () => {
    const added = renderToStaticMarkup(createElement(MessageBubble, { message: steered("added"), sessionId: "s" }));
    expect(added).toContain("Added to the reply");
    expect(added).toContain('data-chat-steer="added"');
    const sending = renderToStaticMarkup(createElement(MessageBubble, { message: steered("sending"), sessionId: "s" }));
    expect(sending).toContain("Adding to the reply…");
    // A reload has no ack field: the saved mark alone draws it as added.
    const reloaded = renderToStaticMarkup(createElement(MessageBubble, { message: steered(), sessionId: "s" }));
    expect(reloaded).toContain("Added to the reply");
  });

  it("the current turn looks past a steer at the end of the thread", () => {
    const msgs: ChatMessage[] = [
      { id: "u1", role: "user", content: "a", timestamp: 1 },
      { id: "a1", role: "assistant", content: "b", timestamp: 2 },
      steered("added"),
    ];
    expect(lastNonSteer(msgs)?.id).toBe("a1");
  });
});

describe("an old queue goes into the box once", () => {
  class MemoryStorage {
    private map = new Map<string, string>();
    get length() { return this.map.size; }
    key(i: number) { return [...this.map.keys()][i] ?? null; }
    getItem(k: string) { return this.map.has(k) ? this.map.get(k)! : null; }
    setItem(k: string, v: string) { this.map.set(k, String(v)); }
    removeItem(k: string) { this.map.delete(k); }
  }

  beforeEach(() => {
    const storage = new MemoryStorage();
    vi.stubGlobal("window", { localStorage: storage });
    vi.stubGlobal("localStorage", storage);
    vi.stubGlobal("fetch", vi.fn(async () => new Response("{}", { status: 200 })));
    bindChatScope(chatScope("vj@x.test", "org-x"));
  });
  afterEach(() => {
    forgetChatSessions();
    vi.unstubAllGlobals();
  });

  it("takes the words out of storage once, and sends nothing", () => {
    saveQueue("s-old", ["first queued", "second queued"]);
    expect(takeLeftoverQueue("s-old")).toBe("first queued\n\nsecond queued");
    expect(getQueue("s-old")).toEqual([]);
    expect(takeLeftoverQueue("s-old")).toBe("");
    expect(fetch).not.toHaveBeenCalledWith("/api/agent/chat", expect.anything());
  });

  it("the composer puts them in the box and has no drain that sends", () => {
    const src = code(read("../components/AgentChat.tsx"));
    expect(src).toMatch(/const left = takeLeftoverQueue\(sessionId\);\s*if \(!left\) return;\s*setInput\(\(prev\) => mergeCappedText\(prev, left\)\);/);
    expect(src).not.toMatch(/sendMessage\(next\)/);
  });
});


describe("a Stop asks for no follow-up (round 2 of the review of #920)", () => {
  beforeEach(() => { vi.useFakeTimers(); });
  afterEach(() => { vi.useRealTimers(); });

  const stop = (threadId: string) => setSessionState(threadId, (prev) => ({
    ...prev, steerOwed: false, stopEpoch: (prev.stopEpoch ?? 0) + 1,
  }));

  it("(a) a steer that ends in a bare stop owes nothing, and a waiting retry goes", async () => {
    const { threadId } = liveThread();
    scheduleFollowUpRetry(threadId);
    const fetchMock = vi.fn().mockResolvedValue(json(202, { steered: false, aborted: true }));
    expect((await sendSteer({ threadId, text: "stop", buildBody: body, fetchImpl: fetchMock as typeof fetch })).kind)
      .toBe("stopped");
    expect(followUpRetryState(threadId)).toBeUndefined();
    vi.advanceTimersByTime(60_000);
    expect(getSessionState(threadId).steerOwed).toBeFalsy();
  });

  it("(b) a steer 202 that lands after a Stop owes no follow-up", async () => {
    const { threadId } = liveThread();
    let answer: (r: Response) => void = () => {};
    const fetchMock = vi.fn().mockReturnValue(new Promise<Response>((r) => { answer = r; }));
    const pending = sendSteer({ threadId, text: "add a due date", buildBody: body, fetchImpl: fetchMock as typeof fetch });
    stop(threadId); // the member presses Stop while the steer is on its way
    answer(json(202, { steered: true }));
    expect((await pending).kind).toBe("folded");
    expect(getSessionState(threadId).steerOwed).toBeFalsy();
    // The same rule on the plain-send path.
    owedAfterSteer(threadId, 0);
    expect(getSessionState(threadId).steerOwed).toBeFalsy();
  });

  it("(c) a retry waiting when the Stop comes never fires, and 'stopped' settles it", () => {
    const { threadId } = liveThread();
    expect(scheduleFollowUpRetry(threadId)).toBe("scheduled");
    cancelFollowUp(threadId); // what stopGeneration calls
    vi.advanceTimersByTime(60_000);
    expect(getSessionState(threadId).steerOwed).toBeFalsy();
    // Another tab's follow-up meets the server's "stopped".
    settleFollowUpAnswer(threadId, "run_in_flight");
    expect(followUpRetryState(threadId)).toEqual({ tries: 1, waiting: true });
    settleFollowUpAnswer(threadId, "stopped");
    expect(followUpRetryState(threadId)).toBeUndefined();
    vi.advanceTimersByTime(60_000);
    expect(getSessionState(threadId).steerOwed).toBeFalsy();
  });

  it("the hook's Stop cancels the follow-up and moves the stop count", () => {
    const hook = code(read("../hooks/useAgentChat.ts"));
    expect(hook).toMatch(/steerOwed: false,\s*stopEpoch: \(prev\.stopEpoch \?\? 0\) \+ 1,\s*\}\)\);\s*cancelFollowUp\(threadId\);/);
  });
});

describe("a refused follow-up (round 2 of the review of #920)", () => {
  beforeEach(() => { vi.useFakeTimers(); });
  afterEach(() => { vi.useRealTimers(); });

  it("draws no card for a 409 or a 429, and asks again with the same wait", () => {
    const hook = code(read("../hooks/useAgentChat.ts"));
    const start = hook.indexOf("if (followUp && failedStatus !== null) {");
    expect(start).toBeGreaterThan(0);
    // It returns before the one failure seam, so no card and no cap notice.
    expect(start).toBeLessThan(hook.indexOf("const outcome = settleFailedTurn({"));
    expect(hook.slice(start, start + 600)).toContain(
      "if (failedStatus === 409 || failedStatus === 429) scheduleFollowUpRetry(threadId);",
    );
  });

  it("asks again until the tries run out, then the steer says it waits", () => {
    const { threadId } = liveThread();
    setSessionState(threadId, (prev) => ({
      ...prev,
      messages: [...prev.messages, {
        id: "s1", role: "user", content: "add a due date", timestamp: 3,
        customEvents: [{ name: STEERED, value: {} }], steerAck: "added",
      }],
    }));
    for (let i = 1; i <= FOLLOW_UP_TRIES; i++) {
      expect(scheduleFollowUpRetry(threadId)).toBe("scheduled");
      vi.advanceTimersByTime(followUpRetryDelay(i)!);
      expect(getSessionState(threadId).steerOwed).toBe(true);
      setSessionState(threadId, (prev) => ({ ...prev, steerOwed: false }));
    }
    expect(scheduleFollowUpRetry(threadId)).toBe("exhausted");
    const bubble = getSessionState(threadId).messages.find((m) => m.id === "s1")!;
    expect(bubble.steerWaiting).toBe(true);
    const html = renderToStaticMarkup(createElement(MessageBubble, { message: bubble, sessionId: "s" }));
    expect(html).toContain(STEER_WAITING);
    expect(STEER_WAITING).toBe("Your added message waits for the next reply.");
  });
});

describe("Edit after a steer (round 2 of the review of #920)", () => {
  it("offers Edit on the prompt, not on the steer row of its turn", () => {
    const msgs: ChatMessage[] = [
      { id: "u1", role: "user", content: "Plan the release", timestamp: 1 },
      { id: "a1", role: "assistant", content: "Here it is.", timestamp: 2 },
      { id: "s1", role: "user", content: "add a due date", timestamp: 3, customEvents: [{ name: STEERED, value: {} }] },
    ];
    expect(lastUserMessageId(msgs)).toBe("u1");
    expect(editableLastUserId(msgs, undefined, false)).toBe("u1");
    // The edit drops the steer row with its turn, as the gateway does.
    expect(supersededIds(msgs, "u1")).toEqual(["u1", "a1", "s1"]);
  });
});

describe("the send keys", () => {
  it("say Cmd on a Mac and Ctrl elsewhere", () => {
    expect(sendShortcut("MacIntel")).toBe("⌘ ⏎");
    expect(sendShortcut("macOS")).toBe("⌘ ⏎");
    expect(sendShortcut("iPhone")).toBe("⌘ ⏎");
    expect(sendShortcut("Win32")).toBe("Ctrl ⏎");
    expect(sendShortcut("")).toBe("Ctrl ⏎");
  });

  it("the composer bar gives the free width to the spacer, not the controls", () => {
    const src = read("../components/AgentChat.tsx");
    expect(src).toContain("sm:basis-auto sm:flex-initial");
    expect(src).not.toMatch(/sm:basis-0 sm:flex-1 sm:self-center/);
    expect(src).toContain('<span className="flex-1" aria-hidden="true" />');
  });
});
