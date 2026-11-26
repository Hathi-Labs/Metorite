/**
 * Task 3564, chat sidebar fault 2: the brief mark of the chat list.
 *
 * The gateway's list says `isBrief` for a chat that holds the day's brief
 * (`_IS_BRIEF_SQL` in `routes/chat.py`). The merge carries it into the local
 * list, and the server's answer wins. No client save ever sends it: the mark
 * is the server's, computed and never stored.
 *
 * Mutations (R7): drop `isBrief: r.isBrief` from the merge of a new row, or
 * the `s.isBrief = r.isBrief` line for a known row: the merge tests go red.
 * Send `...s` in `_syncSessionToDb` or `_patchSessionInDb`: the save test
 * goes red.
 */
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import {
  bindChatScope,
  enrichSession,
  fetchAndMergeSessionsFromDb,
  getSessions,
  renameSession,
  setSessionArchived,
  upsertSession,
  type ChatSession,
} from "./sessions";

class MemoryStorage {
  private map = new Map<string, string>();
  getItem(k: string): string | null {
    return this.map.has(k) ? (this.map.get(k) as string) : null;
  }
  setItem(k: string, v: string): void {
    this.map.set(k, String(v));
  }
  removeItem(k: string): void {
    this.map.delete(k);
  }
  clear(): void {
    this.map.clear();
  }
}

const SENT: { url: string; method: string; body: string }[] = [];

function serve(list: unknown[]): void {
  vi.stubGlobal(
    "fetch",
    vi.fn(async (url: string, init?: RequestInit) => {
      SENT.push({ url: String(url), method: init?.method ?? "GET", body: String(init?.body ?? "") });
      if ((init?.method ?? "GET") === "GET") return new Response(JSON.stringify(list), { status: 200 });
      return new Response("{}", { status: 200 });
    }),
  );
}

function row(id: string, extra: Record<string, unknown> = {}): Record<string, unknown> {
  return {
    id, agentName: "orchestrator", title: null, lastPreview: null, messageCount: 1,
    createdAt: "2026-11-16T04:30:00Z", updatedAt: "2026-11-16T04:30:00Z",
    visibility: "private", isOwner: true, participantCount: 0,
    hasUserMessage: false, archivedAt: null, ...extra,
  };
}

function local(id: string, extra: Partial<ChatSession> = {}): ChatSession {
  return {
    id, name: id, agentName: "orchestrator", createdAt: "2026-11-16T04:00:00Z",
    updatedAt: "2026-11-16T04:00:00Z", messageCount: 1, ...extra,
  };
}

beforeEach(() => {
  const storage = new MemoryStorage();
  vi.stubGlobal("window", { localStorage: storage });
  vi.stubGlobal("localStorage", storage);
  bindChatScope("member@example.com|org-1");
  SENT.length = 0;
});

afterEach(() => {
  bindChatScope(null);
  vi.unstubAllGlobals();
});

describe("the merge carries the server's brief mark", () => {
  it("sets isBrief on a chat the browser has not seen", async () => {
    serve([row("brief-chat", { isBrief: true }), row("plain-chat", { isBrief: false })]);
    const merged = await fetchAndMergeSessionsFromDb();
    const byId = new Map(merged.map((s) => [s.id, s]));
    expect(byId.get("brief-chat")?.isBrief).toBe(true);
    expect(byId.get("plain-chat")?.isBrief).toBe(false);
  });

  it("takes the server's answer for a chat the browser knows", async () => {
    serve([]);
    upsertSession(local("known", { isBrief: false }));
    serve([row("known", { isBrief: true })]);
    await fetchAndMergeSessionsFromDb();
    expect(getSessions().find((s) => s.id === "known")?.isBrief).toBe(true);
  });

  it("keeps the mark when an old server sends no isBrief", async () => {
    serve([]);
    upsertSession(local("known", { isBrief: true }));
    serve([row("known")]);
    await fetchAndMergeSessionsFromDb();
    expect(getSessions().find((s) => s.id === "known")?.isBrief).toBe(true);
  });
});

describe("no client save sends the brief mark", () => {
  it("leaves isBrief out of every session write", async () => {
    serve([row("chat-1", { isBrief: true })]);
    await fetchAndMergeSessionsFromDb();
    expect(getSessions()[0].isBrief).toBe(true);

    upsertSession({ ...getSessions()[0], title: "Mine" });
    enrichSession("chat-1", { lastPreview: "A reply", messageCount: 2 });
    renameSession("chat-1", "My day");
    setSessionArchived("chat-1", true);

    const writes = SENT.filter((w) => w.method !== "GET");
    expect(writes.map((w) => w.method).sort()).toEqual(["PATCH", "PATCH", "PATCH", "POST"]);
    for (const w of writes) {
      expect(Object.keys(JSON.parse(w.body)).filter((k) => /brief/i.test(k)), w.body).toEqual([]);
    }
  });
});
