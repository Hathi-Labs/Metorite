/**
 * A link asks a rail to open one chat (WS-51 S3, `chat_run_continuity.md`
 * §4 S3). The activity panel links a Projects, My Tasks or Email row to its
 * app with the shell job `open-chat`. The app asks its rail, and the rail's
 * `useAgentSessions` opens the chat once it has restored.
 *
 * What this file holds (R7):
 *
 * - an ask is read, never taken, so the Projects dock and the chat slot can
 *   both see it while the chat moves between them;
 * - each rail opens an ask once, and a stale ask opens nothing;
 * - `findSession` finds only the bound member's own chat, from this browser
 *   or from the server's list.
 */

import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import {
  RAIL_ASK_MS,
  _resetRailAsksForTests,
  askRailSession,
  findSession,
  railAsk,
  subscribeRailAsks,
} from "./railSessions";
import {
  bindChatScope,
  chatScope,
  createSession,
  forgetChatSessions,
  upsertSession,
  type ChatSession,
} from "./sessions";

class MemoryStorage {
  private map = new Map<string, string>();
  get length(): number {
    return this.map.size;
  }
  key(i: number): string | null {
    return [...this.map.keys()][i] ?? null;
  }
  getItem(k: string): string | null {
    return this.map.has(k) ? (this.map.get(k) as string) : null;
  }
  setItem(k: string, v: string): void {
    this.map.set(k, String(v));
  }
  removeItem(k: string): void {
    this.map.delete(k);
  }
}

beforeEach(() => {
  const storage = new MemoryStorage();
  vi.stubGlobal("window", { localStorage: storage });
  vi.stubGlobal("localStorage", storage);
  vi.stubGlobal("fetch", vi.fn(async () => new Response("{}", { status: 200 })));
  _resetRailAsksForTests();
});

afterEach(() => {
  forgetChatSessions();
  vi.unstubAllGlobals();
});

describe("an ask", () => {
  it("tells every listening rail", () => {
    const heard = vi.fn();
    const off = subscribeRailAsks(heard);
    askRailSession("task-manager", "t-1", 1000);
    expect(heard).toHaveBeenCalledTimes(1);
    off();
    askRailSession("task-manager", "t-2", 1000);
    expect(heard).toHaveBeenCalledTimes(1);
  });

  // Mutation: delete the ask when a rail reads it, and the second rail
  // (the Projects chat slot, after the dock) sees nothing.
  it("is read, never taken: two rails both see it", () => {
    askRailSession("projects-assistant", "t-1", 1000);
    const dock = railAsk("projects-assistant", 0, 1001);
    const slot = railAsk("projects-assistant", 0, 1002);
    expect(dock?.id).toBe("t-1");
    expect(slot?.id).toBe("t-1");
  });

  it("opens once in each rail", () => {
    askRailSession("email-assistant", "t-1", 1000);
    const first = railAsk("email-assistant", 0, 1001);
    expect(first).not.toBeNull();
    expect(railAsk("email-assistant", first!.seq, 1002)).toBeNull();
    // A second link is a new ask.
    askRailSession("email-assistant", "t-2", 1003);
    expect(railAsk("email-assistant", first!.seq, 1004)?.id).toBe("t-2");
  });

  // Mutation: drop the freshness check, and a rail that mounts later opens
  // a chat that nobody asked for now.
  it("goes stale", () => {
    askRailSession("task-manager", "t-1", 1000);
    expect(railAsk("task-manager", 0, 1000 + RAIL_ASK_MS)).not.toBeNull();
    expect(railAsk("task-manager", 0, 1001 + RAIL_ASK_MS)).toBeNull();
  });

  it("belongs to one agent", () => {
    askRailSession("task-manager", "t-1", 1000);
    expect(railAsk("email-assistant", 0, 1001)).toBeNull();
  });

  it("needs an agent and an id", () => {
    askRailSession("", "t-1", 1000);
    askRailSession("task-manager", "", 1000);
    expect(railAsk("", 0, 1001)).toBeNull();
    expect(railAsk("task-manager", 0, 1001)).toBeNull();
  });
});

describe("findSession finds only the member's own chat", () => {
  const OWNER = chatScope("owner@a.test", "org-a") as string;
  const OTHER = chatScope("other@b.test", "org-b") as string;

  it("finds a chat in this browser's list", async () => {
    bindChatScope(OWNER);
    const s = createSession("task-manager");
    upsertSession(s);
    const merge = vi.fn(async () => [] as ChatSession[]);
    expect((await findSession(s.id, merge))?.id).toBe(s.id);
    expect(merge).not.toHaveBeenCalled();
  });

  it("asks the server's list for a chat this browser does not hold", async () => {
    bindChatScope(OWNER);
    const remote = { ...createSession("email-assistant"), id: "remote-1" };
    const merge = vi.fn(async () => [remote]);
    expect((await findSession("remote-1", merge))?.agentName).toBe("email-assistant");
    expect(merge).toHaveBeenCalledTimes(1);
  });

  it("does not find another member's chat", async () => {
    bindChatScope(OWNER);
    const s = createSession("task-manager");
    upsertSession(s);
    bindChatScope(OTHER);
    expect(await findSession(s.id, async () => [])).toBeNull();
  });

  it("gives null when the server cannot answer", async () => {
    bindChatScope(OWNER);
    expect(await findSession("x", async () => { throw new Error("down"); })).toBeNull();
    expect(await findSession("", async () => [])).toBeNull();
  });
});
