/**
 * An edit REPLACES the last user message; it never forks (owner, 2026-10-09).
 *
 * Mutations this suite catches (R7):
 * - `submitEdit` sends before the stop settled, or sends without stopping
 *   a run in flight;
 * - an edit APPENDS (the thread shows the old message and the new one);
 * - an edit of an earlier message, or of another person's, is offered or sent;
 * - the browser removes the old turn, or tombstones it, before the server
 *   accepts the edit, so a refusal leaves a fork on reload (review of #795);
 * - a stale poll brings back a row an accepted edit removed;
 * - an edit held during an app update loses its target or its answer, goes
 *   out as a plain send, or draws a bubble before the server accepts it.
 */
import { readFileSync } from "node:fs";
import { join } from "node:path";
import { afterEach, describe, expect, it, vi } from "vitest";
import { getSessionState, setSessionState, type ChatMessage } from "@/lib/chatStore";
import {
  heldEditFor, holdForUpdate, registerSender, retryHeldNow, setRecoveryDeps, type SendOptions,
} from "@/lib/chatRecovery";
import {
  EDIT_NOT_LAST,
  EDIT_NOT_YOURS,
  EDIT_RUN_BUSY,
  editableLastUserId,
  editedMarker,
  isEdited,
  isSuperseded,
  lastUserMessageId,
  settleEditAnswer,
  submitEdit,
  supersedeLocal,
  withoutSuperseded,
} from "@/lib/chatEdit";

const msg = (id: string, role: ChatMessage["role"], content = id, extra: Partial<ChatMessage> = {}): ChatMessage => ({
  id, role, content, timestamp: 0, ...extra,
});

const thread = (): ChatMessage[] => [
  msg("u1", "user", "list tasks"),
  msg("a1", "assistant"),
  msg("u2", "user", "create three tasks"),
  { ...msg("a2", "assistant", "working"), streaming: true },
];

const pair = (text: string): ChatMessage[] => [
  msg("u2b", "user", text, { customEvents: [editedMarker("u2")] }),
  msg("a2b", "assistant", "", { streaming: true }),
];

describe("only the member's own last user message is editable", () => {
  it("names the last user turn, whoever wrote it", () => {
    expect(lastUserMessageId(thread())).toBe("u2");
    expect(lastUserMessageId([...thread(), msg("u3", "user")])).toBe("u3");
    expect(lastUserMessageId([msg("a", "assistant")])).toBeNull();
  });

  it("offers Edit only when that turn is the viewer's", () => {
    const me = "alice@x.test";
    const mine = [...thread().slice(0, 2), msg("u2", "user", "x", { authorEmail: me, authorKind: "human" })];
    const theirs = [...thread().slice(0, 2), msg("u2", "user", "x", { authorEmail: "bob@x.test", authorKind: "human" })];
    const unattributed = thread().slice(0, 3);
    expect(editableLastUserId(mine, "ALICE@x.test", true)).toBe("u2");
    expect(editableLastUserId(theirs, me, true)).toBeNull();
    expect(editableLastUserId(theirs, me, false)).toBeNull();
    // A row with no author is the reader's in a solo thread, nobody's in a room.
    expect(editableLastUserId(unattributed, me, false)).toBe("u2");
    expect(editableLastUserId(unattributed, me, true)).toBeNull();
  });

  it("refuses an edit of an earlier message, and sends nothing", async () => {
    const calls: string[] = [];
    const out = await submitEdit({
      threadId: "t-earlier",
      getMessages: thread,
      isRunning: () => false,
      stop: async () => { calls.push("stop"); },
      send: async () => { calls.push("send"); return { ok: true }; },
    }, "u1", "list open tasks");
    expect(out).toEqual({ ok: false, reason: EDIT_NOT_LAST });
    expect(calls).toEqual([]);
  });

  it("AgentChat offers Edit to the member's own last user message only", () => {
    const src = readFileSync(join(__dirname, "..", "components", "AgentChat.tsx"), "utf8");
    expect(src).toMatch(/onEditLast=\{msg\.id === lastUserId \? handleEditLast : undefined\}/);
    expect(src).toMatch(/editableLastUserId\(messages, viewerEmail \|\| undefined, isRoom\)/);
    // The old path re-sent the text as a new turn. It must not come back.
    expect(src).not.toMatch(/onResend=/);
  });
});

describe("an edit during a run stops it first, then replaces", () => {
  it("awaits the stop before it sends, and the thread holds the edit once", async () => {
    let state = thread();
    const calls: string[] = [];
    let stopSettled = false;
    const out = await submitEdit({
      threadId: "t-run",
      getMessages: () => state,
      isRunning: () => true,
      stop: async () => {
        calls.push("stop");
        await new Promise((r) => setTimeout(r, 5));
        stopSettled = true;
      },
      send: async (text, opts) => {
        calls.push(`send:${opts.supersedes}`);
        // The send started only after the server answered the cancel.
        expect(stopSettled).toBe(true);
        const r = settleEditAnswer("t-run", state, opts.supersedes, 200, true, "", pair(text));
        state = r.messages;
        return r.outcome;
      },
    }, "u2", "  create two tasks  ");
    expect(out).toEqual({ ok: true });
    expect(calls).toEqual(["stop", "send:u2"]);
    expect(state.map((m) => m.id)).toEqual(["u1", "a1", "u2b", "a2b"]);
    expect(state.filter((m) => m.role === "user").map((m) => m.content))
      .toEqual(["list tasks", "create two tasks"]);
    expect(isEdited(state[2])).toBe(true);
    // The removed rows stay removed against a stale read in this page.
    expect(isSuperseded("t-run", "u2")).toBe(true);
    expect(isSuperseded("t-run", "a2")).toBe(true);
    expect(withoutSuperseded("t-run", thread()).map((m) => m.id)).toEqual(["u1", "a1"]);
  });

  it("does not stop anything when no run is in flight", async () => {
    const calls: string[] = [];
    await submitEdit({
      threadId: "t-idle",
      getMessages: () => thread().slice(0, 3),
      isRunning: () => false,
      stop: async () => { calls.push("stop"); },
      send: async () => { calls.push("send"); return { ok: true }; },
    }, "u2", "x");
    expect(calls).toEqual(["send"]);
  });

  it("supersedeLocal refuses an unknown id rather than appending", () => {
    expect(supersedeLocal(thread(), "nope", [msg("x", "user")])).toBeNull();
  });
});

describe("a refused edit changes nothing (review of #795)", () => {
  it.each([
    [409, '{"detail":{"error":"not_last"}}', EDIT_NOT_LAST],
    [403, '{"detail":{"error":"not_yours"}}', EDIT_NOT_YOURS],
    [403, "forbidden", EDIT_NOT_YOURS],
    [409, '{"detail":{"error":"run_in_progress"}}', EDIT_RUN_BUSY],
  ])("status %i keeps the old turn, tombstones nothing, and says why", (status, body, reason) => {
    const before = thread();
    const tid = `t-refused-${status}-${body.length}`;
    const r = settleEditAnswer(tid, before, "u2", status, false, body, pair("edited"));
    expect(r.messages).toBe(before);           // same array: nothing to save
    expect(r.outcome).toEqual({ ok: false, reason });
    expect(isSuperseded(tid, "u2")).toBe(false);
    expect(isSuperseded(tid, "a2")).toBe(false);
  });

  it("sendMessage leaves the thread alone until the server answers, then settles it once", () => {
    const src = readFileSync(join(__dirname, "..", "hooks", "useAgentChat.ts"), "utf8");
    // The first state change of an edit moves only the loading state.
    expect(src).toMatch(/if \(supersedes\) \{\s*return \{ \.\.\.prev, isLoading: true, error: null, abortController: controller \};/);
    // The answer decides the thread, through the one pure helper.
    expect(src).toMatch(/const accepted = res\.status !== 202 && res\.ok && !!res\.body;/);
    expect(src).toMatch(/settleEditAnswer\(\s*threadId, prev\.messages, supersedes, res\.status, accepted, body,/);
    expect(src).not.toMatch(/supersedeLocal\(prev\.messages/);
    expect(src).toMatch(/\.\.\.\(supersedes \? \{ supersedes \} : \{\}\)/);
    // WS-51 S4: every send names its own row, so the gateway saves the turn
    // under the id the browser save writes. An edit keeps it too.
    expect(src).toMatch(/userMessageId: userMsg\.id,\s*userMessageTs: userMsg\.timestamp,/);
    // The BFF forwards the id on every send, and not only with an edit.
    const route = readFileSync(join(__dirname, "..", "app", "api", "agent", "chat", "route.ts"), "utf8");
    expect(route).toMatch(/\.\.\.\(userMessageId\s*\?\s*\{\s*user_message_id: userMessageId,/);
    expect(route).not.toMatch(/supersedes, user_message_id/);
    expect(src).toMatch(/stopGeneration = useCallback\(\(\): Promise<void> =>/);
    // A stale poll must not resurrect a superseded row.
    expect(src).toMatch(/if \(isSuperseded\(threadId, rm\.id\)\) continue;/);
  });
});

describe("an edit held during an app update is sent as a supersede (#797 + #795)", () => {
  afterEach(() => setRecoveryDeps(null));

  it("keeps its target and its answer, keeps the old turn, and goes out as an edit", async () => {
    const tid = `t-held-edit-${Math.random()}`;
    const before = thread();
    setSessionState(tid, (p) => ({ ...p, messages: before }));
    const sent: Array<[string, SendOptions | undefined]> = [];
    registerSender(tid, async (text, opts) => { sent.push([text, opts]); });
    setRecoveryDeps({ probe: async () => false, sleep: () => new Promise(() => {}) });

    const outcomes: unknown[] = [];
    const onEditOutcome = (o: unknown) => outcomes.push(o);
    holdForUpdate(tid, "create two tasks", { edit: { supersedes: "u2", onEditOutcome } });

    // While held: no bubble, the old turn stays, the notice is up.
    const held = getSessionState(tid);
    expect(held.messages).toBe(before);
    expect(held.outage).toBe(true);
    expect(heldEditFor(tid, "create two tasks")?.supersedes).toBe("u2");

    await retryHeldNow(tid);
    await vi.waitFor(() => expect(sent).toHaveLength(1));
    const [text, opts] = sent[0];
    expect(text).toBe("create two tasks");
    expect(opts?.supersedes).toBe("u2");
    expect(opts?.fromHold).toBe(true);
    expect(opts?.heldId).toBeUndefined();
    // The answer is the original one: a refusal after the hold reaches it.
    opts?.onEditOutcome?.({ ok: false, reason: EDIT_NOT_LAST });
    expect(outcomes).toEqual([{ ok: false, reason: EDIT_NOT_LAST }]);
    expect(heldEditFor(tid, "create two tasks")).toBeUndefined();
  });

  it("sendMessage holds an edit on an outage instead of answering it", () => {
    const src = readFileSync(join(__dirname, "..", "hooks", "useAgentChat.ts"), "utf8");
    expect(src).toMatch(/if \(before\.outage\) \{ holdEdit\(false\); return; \}/);
    expect(src).toMatch(/if \(opts\?\.fromHold\) holdEdit\(true\);/);
    expect(src).toMatch(/!accepted && isUpdateOutage\(\{ status: res\.status, gotResponse: true \}\)/);
    expect(src).toMatch(/if \(isUpdateOutage\(\{ status: failedStatus, gotResponse, err \}\)\) holdEdit\(!!opts\?\.fromHold\);/);
    // An edit is never collapsed into a held plain send.
    expect(src).toMatch(/const plan = supersedes \? "send" : planSend\(before, text, held\?\.id\);/);
  });
});
