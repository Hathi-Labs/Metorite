/**
 * An edit REPLACES the last user message; it never forks (owner, 2026-10-09).
 *
 * Mutations this suite catches (R7):
 * - `submitEdit` sends before the stop settled, or sends without stopping
 *   a run in flight;
 * - an edit APPENDS (the thread shows the old message and the new one);
 * - an edit of an earlier message is sent;
 * - `sendMessage` stops using `supersedeLocal`, or stops sending the id;
 * - a stale poll brings back a row the edit removed.
 */
import { readFileSync } from "node:fs";
import { join } from "node:path";
import { describe, expect, it } from "vitest";
import type { ChatMessage } from "@/lib/chatStore";
import {
  editedMarker,
  isEdited,
  isSuperseded,
  lastUserMessageId,
  submitEdit,
  supersedeLocal,
  withoutSuperseded,
} from "@/lib/chatEdit";

const msg = (id: string, role: ChatMessage["role"], content = id): ChatMessage => ({
  id, role, content, timestamp: 0,
});

const thread = (): ChatMessage[] => [
  msg("u1", "user", "list tasks"),
  msg("a1", "assistant"),
  msg("u2", "user", "create three tasks"),
  { ...msg("a2", "assistant", "working"), streaming: true },
];

describe("only the last user message is editable", () => {
  it("names the last user turn, whoever wrote it", () => {
    expect(lastUserMessageId(thread())).toBe("u2");
    expect(lastUserMessageId([...thread(), msg("u3", "user")])).toBe("u3");
    expect(lastUserMessageId([msg("a", "assistant")])).toBeNull();
  });

  it("refuses an edit of an earlier message, and sends nothing", async () => {
    const calls: string[] = [];
    const out = await submitEdit({
      threadId: "t-earlier",
      getMessages: thread,
      isRunning: () => false,
      stop: async () => { calls.push("stop"); },
      send: async () => { calls.push("send"); },
    }, "u1", "list open tasks");
    expect(out).toBe("refused");
    expect(calls).toEqual([]);
  });

  it("AgentChat offers Edit to the last user message only", () => {
    const src = readFileSync(join(__dirname, "..", "components", "AgentChat.tsx"), "utf8");
    expect(src).toMatch(/onEditLast=\{msg\.id === lastUserId \? handleEditLast : undefined\}/);
    expect(src).toMatch(/const lastUserId = useMemo\(\(\) => lastUserMessageId\(messages\), \[messages\]\)/);
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
        // What `sendMessage` does with `supersedes` (useAgentChat.ts).
        const next = supersedeLocal(state, opts.supersedes, [
          { ...msg("u2b", "user", text), customEvents: [editedMarker(opts.supersedes)] },
          { ...msg("a2b", "assistant", ""), streaming: true },
        ]);
        expect(next).not.toBeNull();
        state = next!;
      },
    }, "u2", "  create two tasks  ");
    expect(out).toBe("sent");
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
      send: async () => { calls.push("send"); },
    }, "u2", "x");
    expect(calls).toEqual(["send"]);
  });

  it("supersedeLocal refuses an unknown id rather than appending", () => {
    expect(supersedeLocal(thread(), "nope", [msg("x", "user")])).toBeNull();
  });

  it("sendMessage replaces through supersedeLocal and sends the id, and waits on Stop", () => {
    const src = readFileSync(join(__dirname, "..", "hooks", "useAgentChat.ts"), "utf8");
    expect(src).toMatch(/supersedes \? supersedeLocal\(prev\.messages, supersedes, \[userMsg, assistantMsg\]\)/);
    expect(src).toMatch(/\.\.\.\(supersedes \? \{ supersedes, userMessageId: userMsg\.id \} : \{\}\)/);
    expect(src).toMatch(/stopGeneration = useCallback\(\(\): Promise<void> =>/);
    expect(src).toMatch(/return settled;/);
    // A stale poll must not resurrect a superseded row.
    expect(src).toMatch(/if \(isSuperseded\(threadId, rm\.id\)\) continue;/);
  });
});
