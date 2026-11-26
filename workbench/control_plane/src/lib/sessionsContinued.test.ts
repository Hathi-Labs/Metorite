/**
 * CO-13 (`chat_organization.md` §17.5): the merge of the server's list takes
 * the continue mark. The server's answer wins, so a chat made a moment ago
 * gets its mark, and a mark that the server no longer sends is cleared.
 * Fence (R7): this file. Deleting the `s.continuedFrom` line of the merge in
 * `sessions.ts` turns the first two cases red.
 */
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import {
  __newPageForTests,
  bindChatScope,
  chatKey,
  fetchAndMergeSessionsFromDb,
  getSessions,
  type ChatSession,
} from "./sessions";

const SCOPE = "priya@example.com|org1";
const store = new Map<string, string>();
const storage = {
  get length() {
    return store.size;
  },
  key: (i: number) => [...store.keys()][i] ?? null,
  getItem: (k: string) => store.get(k) ?? null,
  setItem: (k: string, v: string) => void store.set(k, String(v)),
  removeItem: (k: string) => void store.delete(k),
};

function seed(...rows: ChatSession[]): void {
  store.set(chatKey("sessions") as string, JSON.stringify(rows));
}

function row(id: string, over: Partial<ChatSession> = {}): ChatSession {
  return {
    id,
    name: id,
    agentName: "orchestrator",
    createdAt: "2026-11-16T09:00:00.000Z",
    updatedAt: "2026-11-16T09:00:00.000Z",
    messageCount: 2,
    title: `Chat ${id}`,
    ...over,
  };
}

const markOf = (id: string) => getSessions().find((s) => s.id === id)?.continuedFrom;

beforeEach(() => {
  store.clear();
  __newPageForTests();
  vi.stubGlobal("window", { localStorage: storage, dispatchEvent: () => true });
  vi.stubGlobal("localStorage", storage);
  bindChatScope(SCOPE);
});

afterEach(() => {
  vi.unstubAllGlobals();
  bindChatScope(null);
});

describe("the merge carries the continue mark (CO-13)", () => {
  it("gives a local row the server's mark", async () => {
    seed(row("new"));
    vi.stubGlobal("fetch", vi.fn(async () => new Response(JSON.stringify([
      { ...row("new"), continuedFrom: { id: "old", title: "Pricing deck" } },
    ]), { status: 200 })));
    await fetchAndMergeSessionsFromDb();
    expect(markOf("new")).toEqual({ id: "old", title: "Pricing deck" });
  });

  it("clears a mark that the server no longer sends", async () => {
    seed(row("new", { continuedFrom: { id: "old", title: "Pricing deck" } }));
    vi.stubGlobal("fetch", vi.fn(async () => new Response(JSON.stringify([
      { ...row("new"), continuedFrom: null },
    ]), { status: 200 })));
    await fetchAndMergeSessionsFromDb();
    expect(markOf("new")).toBeNull();
  });

  it("keeps the no-read mark with no id and no title", async () => {
    seed(row("new"));
    vi.stubGlobal("fetch", vi.fn(async () => new Response(JSON.stringify([
      { ...row("new"), continuedFrom: { id: null, title: null } },
    ]), { status: 200 })));
    await fetchAndMergeSessionsFromDb();
    expect(markOf("new")).toEqual({ id: null, title: null });
  });

  it("pushes a new server row with its mark", async () => {
    seed();
    vi.stubGlobal("fetch", vi.fn(async () => new Response(JSON.stringify([
      { ...row("fresh"), continuedFrom: { id: "old", title: "Pricing deck" } },
      { ...row("plain") },
    ]), { status: 200 })));
    await fetchAndMergeSessionsFromDb();
    expect(markOf("fresh")).toEqual({ id: "old", title: "Pricing deck" });
    expect(markOf("plain")).toBeNull();
  });
});
