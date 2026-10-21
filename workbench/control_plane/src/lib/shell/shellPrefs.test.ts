/**
 * The layout reader (NS-7). "Never asked" opens the first sign-in question,
 * so only a real layout may say it. A body that is not a layout is a failed
 * read, and the shell then asks nothing.
 *
 * Mutation: make `asLayout` accept any object, and the `{}` and `[]` cases
 * fail. In a browser, the catch-all stub of `e2e/sidebar-fold.spec.ts` then
 * opens the question over every test.
 *
 * The nudge mark (WS-62 CF-2, `chat_first_surface.md` §4.7, D-CF-7):
 * `chat_first_message_at` reads with the layout, `nudgeShows` says when the
 * bubble draws, and `markChatFirstMessage` writes the mark to the server
 * once and to this browser at once. Mutation, seen to fail first: drop
 * `a.retiredInBrowser` from `nudgeShows`, and "the browser copy hides it
 * before the read" fails.
 */
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { clearAll, peek, put } from "@/lib/dataCache";
import { chatScope } from "@/lib/sessions";

import { EMPTY_SHELL, type StoredShell } from "./presets";
import { NUDGE_PREFIX, clearNudgeMarks, nudgeKey, readNudgeRetired } from "./shellCache";
import {
  SHELL_PREFS_PATH,
  __newShellPageForTests,
  asLayout,
  bodyFor,
  markChatFirstMessage,
  nudgeShows,
  saveShellPrefs,
} from "./shellPrefs";

describe("asLayout", () => {
  it("reads the gateway's layout", () => {
    expect(asLayout({ ...EMPTY_SHELL, answered: "skipped" })).toEqual({ ...EMPTY_SHELL, answered: "skipped" });
    expect(asLayout(EMPTY_SHELL)).toEqual(EMPTY_SHELL);
  });

  it("refuses a body that does not say whether the member was asked", () => {
    expect(asLayout({})).toBeNull();
    expect(asLayout([])).toBeNull();
    expect(asLayout(null)).toBeNull();
    expect(asLayout("null")).toBeNull();
    // `/auth/me` itself, which a loose stub can answer for this path.
    expect(asLayout({ email: "a@example.com", features: [], roles: ["member"] })).toBeNull();
  });

  it("reads the nudge mark, and a row from before it reads null (CF-2)", () => {
    const stamp = "2026-10-21T09:00:00.000Z";
    expect(asLayout({ ...EMPTY_SHELL, answered: "skipped", chat_first_message_at: stamp })?.chat_first_message_at).toBe(stamp);
    const old: Record<string, unknown> = { ...EMPTY_SHELL, answered: "skipped" };
    delete old.chat_first_message_at;
    expect(asLayout(old)?.chat_first_message_at).toBeNull();
  });
});

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
  keys(): string[] {
    return [...this.map.keys()];
  }
}

const SCOPE = chatScope("Priya@Acme.example", "org-acme") as string;
const NEVER: StoredShell = { ...EMPTY_SHELL, answered: "skipped" };
const WROTE: StoredShell = { ...NEVER, chat_first_message_at: "2026-10-21T09:00:00.000Z" };

describe("the nudge (WS-62 CF-2)", () => {
  let storage: MemoryStorage;
  let fetches: Array<{ method: string; body: unknown }>;

  beforeEach(() => {
    storage = new MemoryStorage();
    vi.stubGlobal("window", { localStorage: storage });
    vi.stubGlobal("localStorage", storage);
    fetches = [];
    __newShellPageForTests();
    clearAll();
  });
  afterEach(() => vi.unstubAllGlobals());

  /** A gateway that answers the read with `layout`, and echoes each write. */
  const gateway = (layout: StoredShell) =>
    vi.fn(async (_url: string, init?: RequestInit) => {
      const method = (init?.method ?? "GET").toUpperCase();
      const body = init?.body ? JSON.parse(String(init.body)) : undefined;
      fetches.push({ method, body });
      return new Response(JSON.stringify(method === "GET" ? layout : body), { status: 200 });
    });

  const shows = {
    chatFirst: true, fresh: NEVER, retiredHere: false, retiredInBrowser: false, welcomeOpen: false, assistantOpen: false,
    hasChats: false as boolean | null,
  };

  it("shows for a member the server says never wrote, and for nobody else", () => {
    expect(nudgeShows(shows)).toBe(true);
    expect(nudgeShows({ ...shows, fresh: WROTE })).toBe(false);
    expect(nudgeShows({ ...shows, chatFirst: false })).toBe(false);
    // Not before the server answers: the browser copy of the layout may be old.
    expect(nudgeShows({ ...shows, fresh: undefined })).toBe(false);
  });

  it("never for a member who already has a chat with a message, and not before the list answers (review of #1206)", () => {
    // A stored layout from before the mark has no key, so every existing
    // member reads as null. Their chats say they wrote (§4.7).
    expect(nudgeShows({ ...shows, hasChats: true })).toBe(false);
    expect(nudgeShows({ ...shows, hasChats: null })).toBe(false);
  });

  it("never shows while the welcome dialog or the assistant is open", () => {
    expect(nudgeShows({ ...shows, welcomeOpen: true })).toBe(false);
    expect(nudgeShows({ ...shows, assistantOpen: true })).toBe(false);
  });

  it("the browser copy hides it before the read, and a dismissal in this page hides it at once", () => {
    expect(nudgeShows({ ...shows, retiredInBrowser: true })).toBe(false);
    expect(nudgeShows({ ...shows, retiredHere: true })).toBe(false);
  });

  it("the copy is per account, keeps the shell prefix, and a sign-out clears it", () => {
    expect(nudgeKey(SCOPE)).toBe(`${NUDGE_PREFIX}priya@acme.example|org-acme`);
    expect(NUDGE_PREFIX.startsWith("cc-chat")).toBe(false);
    expect(nudgeKey(null)).toBeNull();
    expect(nudgeKey("no-org")).toBeNull();
    expect(readNudgeRetired(SCOPE)).toBe(false);
    storage.setItem(nudgeKey(SCOPE) as string, "1");
    expect(readNudgeRetired(SCOPE)).toBe(true);
    clearNudgeMarks(["priya@acme.example"]);
    expect(readNudgeRetired(SCOPE)).toBe(false);
  });

  /** A gateway that merges a key-only write into `layout`, as `me.py` does in SQL. */
  const merging = (layout: StoredShell) =>
    vi.fn(async (_url: string, init?: RequestInit) => {
      const method = (init?.method ?? "GET").toUpperCase();
      const body = init?.body ? JSON.parse(String(init.body)) : undefined;
      fetches.push({ method, body });
      return new Response(JSON.stringify(method === "GET" ? layout : { ...layout, ...body }), { status: 200 });
    });

  it("the first message sends the time alone, and the server's merged layout keeps the stars (review of #1206)", async () => {
    // The server holds a star this page never saw. A whole-layout write
    // would have written it over.
    vi.stubGlobal("fetch", merging({ ...NEVER, pins: ["/tasks", "/email"] }));
    put(SHELL_PREFS_PATH, { ...NEVER, pins: ["/tasks"] });
    await markChatFirstMessage(SCOPE);
    expect(readNudgeRetired(SCOPE)).toBe(true);
    expect(fetches.map((f) => f.method)).toEqual(["PUT"]);
    const body = fetches[0].body as Record<string, unknown>;
    expect(Object.keys(body)).toEqual(["chat_first_message_at"]);
    expect(Number.isFinite(Date.parse(String(body.chat_first_message_at)))).toBe(true);
    const shown = peek<StoredShell>(SHELL_PREFS_PATH)?.data;
    expect(shown?.chat_first_message_at).toBe(body.chat_first_message_at);
    expect(shown?.pins).toEqual(["/tasks", "/email"]);
  });

  it("with no layout in the page yet, it still sends the key alone, and reads nothing first", async () => {
    vi.stubGlobal("fetch", merging({ ...NEVER, pins: ["/email"] }));
    await markChatFirstMessage(SCOPE);
    expect(fetches.map((f) => f.method)).toEqual(["PUT"]);
    expect(Object.keys(fetches[0].body as object)).toEqual(["chat_first_message_at"]);
  });

  it("a layout the server already stamped is left alone, and a second call writes nothing", async () => {
    vi.stubGlobal("fetch", gateway(WROTE));
    put(SHELL_PREFS_PATH, WROTE);
    await markChatFirstMessage(SCOPE);
    await markChatFirstMessage(SCOPE);
    expect(fetches).toEqual([]);
    expect(readNudgeRetired(SCOPE)).toBe(true);
  });

  it("a refused write keeps the browser copy, so this device shows it no more", async () => {
    vi.stubGlobal("fetch", vi.fn(async () => new Response("{}", { status: 503 })));
    put(SHELL_PREFS_PATH, NEVER);
    await markChatFirstMessage(SCOPE);
    expect(readNudgeRetired(SCOPE)).toBe(true);
  });

  it("a layout save never names the mark as null, so the server keeps the stored one", async () => {
    vi.stubGlobal("fetch", gateway(NEVER));
    await saveShellPrefs({ ...NEVER, pins: ["/chat"] });
    expect(fetches[0].method).toBe("PUT");
    expect("chat_first_message_at" in (fetches[0].body as object)).toBe(false);
    expect((fetches[0].body as StoredShell).pins).toEqual(["/chat"]);
    // A stamped layout carries its stamp.
    await saveShellPrefs(WROTE);
    expect((fetches[1].body as StoredShell).chat_first_message_at).toBe(WROTE.chat_first_message_at);
    expect(bodyFor(null)).toBeNull();
  });
});
