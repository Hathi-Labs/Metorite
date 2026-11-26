// The decisions that a chat keeps, and Undo (HRD-12b).
//
// Mutations (R7): read a list when `enabled` is not true, and the off case goes
// red. Send a member or a tenant in the Undo body, and the body case goes red.
// Treat a 404 as a failure, and the "gone" case goes red.
import { describe, expect, it, vi } from "vitest";

import { fetchDecisions, ITEM_ID, readDecisions, SESSION_ID, undoDecision } from "./chatDecisions";

const ID = "0123456789ab";

describe("readDecisions", () => {
  it("reads the items of an enabled list", () => {
    const got = readDecisions({ enabled: true, items: [{ id: ID, kind: "decision", text: "TEDx is done." }] });
    expect(got).toEqual({ enabled: true, items: [{ id: ID, kind: "decision", text: "TEDx is done." }] });
  });
  it("reads nothing when the flag is off or the shape is wrong", () => {
    expect(readDecisions({ enabled: false, items: [{ id: ID, text: "x" }] })).toEqual({ enabled: false, items: [] });
    expect(readDecisions({ enabled: true })).toEqual({ enabled: false, items: [] });
    expect(readDecisions(null)).toEqual({ enabled: false, items: [] });
    expect(readDecisions("x")).toEqual({ enabled: false, items: [] });
  });
  it("drops an item with a bad id or an empty text", () => {
    const got = readDecisions({
      enabled: true,
      items: [{ id: "nope", text: "a" }, { id: ID, text: "  " }, { id: ID, text: "b" }, null],
    });
    expect(got.items.map((i) => i.text)).toEqual(["b"]);
  });
});

describe("fetchDecisions", () => {
  it("asks the relay for this chat only", async () => {
    const f = vi.fn(async (_url: string) => new Response(JSON.stringify({ enabled: true, items: [] }), { status: 200 }));
    await fetchDecisions("thread-1", f as unknown as typeof fetch);
    expect(f.mock.calls[0][0]).toBe("/api/auth/me/chat-decisions?session_id=thread-1");
  });
  it("gives null for a bad chat id, a failed answer and a fault", async () => {
    const never = vi.fn();
    expect(await fetchDecisions("a b", never as unknown as typeof fetch)).toBeNull();
    expect(never).not.toHaveBeenCalled();
    expect(await fetchDecisions("t", (async () => new Response("{}", { status: 503 })) as unknown as typeof fetch)).toBeNull();
    expect(await fetchDecisions("t", (async () => { throw new Error("down"); }) as unknown as typeof fetch)).toBeNull();
  });
});

describe("undoDecision", () => {
  it("posts the chat and the item and nothing else", async () => {
    const f = vi.fn(async () => new Response("{}", { status: 200 }));
    expect(await undoDecision("thread-1", ID, f as unknown as typeof fetch)).toBe("done");
    const [url, init] = f.mock.calls[0] as unknown as [string, RequestInit];
    expect(url).toBe("/api/auth/me/chat-decisions/undo");
    expect(init.method).toBe("POST");
    expect(JSON.parse(init.body as string)).toEqual({ session_id: "thread-1", id: ID });
  });
  it("reads a 404 as gone, and any other failure as failed", async () => {
    const mk = (status: number) => (async () => new Response("{}", { status })) as unknown as typeof fetch;
    expect(await undoDecision("t", ID, mk(404))).toBe("gone");
    expect(await undoDecision("t", ID, mk(503))).toBe("failed");
    expect(await undoDecision("t", ID, mk(502))).toBe("failed");
    expect(await undoDecision("t", ID, (async () => { throw new Error("x"); }) as unknown as typeof fetch)).toBe("failed");
  });
  it("sends nothing for a bad chat or item id", async () => {
    const f = vi.fn();
    expect(await undoDecision("a b", ID, f as unknown as typeof fetch)).toBe("failed");
    expect(await undoDecision("t", "ZZ", f as unknown as typeof fetch)).toBe("failed");
    expect(f).not.toHaveBeenCalled();
  });
});

describe("the id rules match the server", () => {
  it("accepts a chat id and an item id, and refuses the rest", () => {
    for (const ok of ["thread-1", "a_b.c:d", "x".repeat(128)]) expect(SESSION_ID.test(ok)).toBe(true);
    for (const bad of ["", "a b", "a/b", "../x", "x".repeat(129)]) expect(SESSION_ID.test(bad)).toBe(false);
    expect(ITEM_ID.test(ID)).toBe(true);
    expect(ITEM_ID.test("0123456789AB")).toBe(false);
    expect(ITEM_ID.test("short")).toBe(false);
  });
});
