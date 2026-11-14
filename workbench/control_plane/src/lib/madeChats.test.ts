/**
 * Two tabs of one member share one stored chat list (task 2459, WS-BUD BUD-1).
 *
 * The e2e test "two tabs at once make one row" (`e2e/buddy-hello.spec.ts`)
 * failed in 4 of 10 local runs on 2026-11-14. The browser gives each tab its
 * own copy of storage, and a write of one tab gets to the copy of the other
 * tab some time later. A trace of the dock showed this order:
 *
 *   1. Tab A makes its chat and writes [A].
 *   2. Tab B has not seen [A]. It makes its chat and writes [B] over [A].
 *   3. Tab A gets the storage event. Its row is gone, and it must put it back.
 *   4. React ran the scope effect of the dock a second time (development), and
 *      that run forgot every chat that A made. A had nothing to put back.
 *   5. The hello lands in A, but A has no row and no chat to show. Tab B lost,
 *      and deletes its own chat from an old copy of the list.
 *
 * Tab A then showed "Opening your chat…" with no end.
 *
 * R7 fence: this file. It fails when
 *   - a bind with the same scope forgets the chats of the page, or
 *   - a put-back writes the empty copy that `startFresh` made, and not the
 *     newest stored copy (the hello's title "Welcome" goes), or
 *   - the dock stops binding, noting or putting back through `MadeChats`.
 */
import { readFileSync } from "node:fs";

import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { MadeChats, startFresh } from "./railSessions";
import {
  __newPageForTests,
  bindChatScope,
  chatKey,
  deleteSession,
  enrichSession,
  getSessions,
  type ChatSession,
} from "./sessions";

const SCOPE = "priya@example.com|org1";

/**
 * The storage of one tab. A write goes to the tab's copy and to the shared
 * store. The tab sees a write of another tab only when `sync` runs, as when
 * the browser sends it the storage event.
 */
class TabStorage {
  private copy = new Map<string, string>();
  constructor(private shared: Map<string, string>) {
    this.sync();
  }
  sync(): void {
    this.copy = new Map(this.shared);
  }
  get length(): number {
    return this.copy.size;
  }
  key(i: number): string | null {
    return [...this.copy.keys()][i] ?? null;
  }
  getItem(k: string): string | null {
    return this.copy.get(k) ?? null;
  }
  setItem(k: string, v: string): void {
    this.copy.set(k, String(v));
    this.shared.set(k, String(v));
  }
  removeItem(k: string): void {
    this.copy.delete(k);
    this.shared.delete(k);
  }
}

/** The code that follows runs in this tab. */
function inTab(tab: TabStorage): void {
  vi.stubGlobal("window", { localStorage: tab });
  vi.stubGlobal("localStorage", tab);
}

function storedIds(shared: Map<string, string>): string[] {
  const raw = shared.get(chatKey("sessions") as string) ?? "[]";
  return (JSON.parse(raw) as ChatSession[]).map((s) => s.id);
}

let shared: Map<string, string>;
let tabA: TabStorage;
let tabB: TabStorage;
let clock = Date.UTC(2026, 10, 14, 9, 0, 0);

beforeEach(() => {
  __newPageForTests();
  vi.stubGlobal("fetch", vi.fn(async () => new Response("{}", { status: 200 })));
  // Each save gets a later time, as it does in a real page.
  vi.useFakeTimers();
  vi.setSystemTime(clock);
  shared = new Map();
  tabA = new TabStorage(shared);
  tabB = new TabStorage(shared);
  bindChatScope(SCOPE);
});

afterEach(() => {
  vi.useRealTimers();
  vi.unstubAllGlobals();
  bindChatScope(null);
  clock += 60_000;
});

function later(): void {
  vi.setSystemTime(Date.now() + 1_000);
}

describe("two tabs at once: the winner of the hello keeps its chat", () => {
  it("puts the chat back after each old list of the other tab, with the hello's title", () => {
    const madeA = new MadeChats();
    madeA.bind(SCOPE);

    // 1. Tab A makes its chat.
    inTab(tabA);
    const mine = startFresh("orchestrator").session;
    madeA.set(mine.id, { ...mine });
    // 4. React runs the scope effect of the dock again, with the same scope.
    madeA.bind(SCOPE);

    // 2. Tab B has not seen A's write. Its new chat writes [B] over [A].
    later();
    inTab(tabB);
    const theirs = startFresh("orchestrator").session;
    expect(storedIds(shared)).toEqual([theirs.id]);

    // 3. Tab A gets the storage event: its row is gone. It puts the row back.
    tabA.sync();
    inTab(tabA);
    expect(getSessions().map((s) => s.id)).toEqual([theirs.id]);
    expect(madeA.keep(mine.id)).toBe(true);
    expect(storedIds(shared).sort()).toEqual([mine.id, theirs.id].sort());

    // 5. The hello lands in A. The dock saves it and looks at the list again.
    later();
    enrichSession(mine.id, { title: "Welcome", messageCount: 1, lastPreview: "Good morning, Priya." });
    madeA.note(getSessions());

    // Tab B lost. It deletes its own empty chat, from a copy that is old:
    // the copy holds [B] only, so the write is [] over A's saved hello.
    inTab(tabB);
    deleteSession(theirs.id);
    expect(storedIds(shared)).toEqual([]);

    // Tab A gets the storage event and puts its chat back once more.
    tabA.sync();
    inTab(tabA);
    expect(getSessions()).toEqual([]);
    expect(madeA.keep(mine.id)).toBe(true);

    // One row stays: the winner's, with the hello's title and count.
    expect(storedIds(shared)).toEqual([mine.id]);
    const [row] = getSessions();
    expect(row.title).toBe("Welcome");
    expect(row.messageCount).toBe(1);
  });
});

describe("MadeChats", () => {
  it("forgets the chats for a new member or org, and only then", () => {
    inTab(tabA);
    const made = new MadeChats();
    expect(made.bind(SCOPE)).toBe(true);
    const mine = startFresh("orchestrator").session;
    made.set(mine.id, { ...mine });
    expect(made.bind(SCOPE)).toBe(false);
    expect(made.has(mine.id)).toBe(true);
    expect(made.bind("someone@else.in|org2")).toBe(true);
    expect(made.has(mine.id)).toBe(false);
  });

  it("never takes an older copy that another tab wrote back", () => {
    inTab(tabA);
    const made = new MadeChats();
    made.bind(SCOPE);
    const mine = startFresh("orchestrator").session;
    made.set(mine.id, { ...mine });
    later();
    enrichSession(mine.id, { title: "Welcome", messageCount: 1 });
    made.note(getSessions());
    // An old copy, from before the hello, comes back with a save of tab B.
    made.note([{ ...mine }]);
    expect(made.get(mine.id)?.title).toBe("Welcome");
    expect(made.get(mine.id)?.messageCount).toBe(1);
  });

  it("puts back nothing for a chat that this page did not make", () => {
    inTab(tabA);
    const made = new MadeChats();
    made.bind(SCOPE);
    expect(made.keep("not-mine")).toBe(false);
    expect(getSessions()).toEqual([]);
  });
});

describe("the dock wiring (source fence)", () => {
  const dock = readFileSync(new URL("./shell/AssistantDock.tsx", import.meta.url), "utf-8");

  it("keeps its chats in one MadeChats for each mount", () => {
    expect(dock).toMatch(/const madeHere = useRef\(new MadeChats\(\)\);/);
  });

  it("starts over only when the member or org changed", () => {
    expect(dock).toMatch(/if \(madeHere\.current\.bind\(scope\)\) \{/);
    expect(dock).not.toMatch(/madeHere\.current\.clear\(\)/);
  });

  it("notes the newest stored copy on each look, and puts back through keep", () => {
    expect(dock).toMatch(/madeHere\.current\.note\(list\);/);
    expect(dock).toMatch(/madeHere\.current\.keep\(id\)/);
  });
});
