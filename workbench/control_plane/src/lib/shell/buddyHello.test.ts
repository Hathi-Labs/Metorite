/**
 * The hello (WS-BUD BUD-1, D177). Fences the client half: the one call, the
 * once-per-chat claim, the three chips, and the rule that opens it.
 */
import { afterEach, describe, expect, it, vi } from "vitest";

import { closeAssistant, openAssistant, assistantState } from "./assistant";
import { helloOpens } from "./ChatNudge";
import { EMPTY_SHELL, type StoredShell } from "./presets";
import { nudgeShows } from "./shellPrefs";
import { HELLO_QUESTION, __resetHelloForTests, claimHello, helloPath, opensHello, requestHello, runHelloChip } from "./buddyHello";

const startGuide = vi.hoisted(() => vi.fn());
vi.mock("./guide", async (orig) => ({ ...(await orig<typeof import("./guide")>()), startGuide }));

const NEW: StoredShell = { ...EMPTY_SHELL, answered: "answered", preset: "engineer" };
const ok = (body: unknown) => vi.fn(async () => new Response(JSON.stringify(body), { status: 200 }));

afterEach(() => {
  __resetHelloForTests();
  startGuide.mockClear();
  closeAssistant();
});

describe("requestHello", () => {
  it("posts to the BFF route of the chat and reads the stored words", async () => {
    const f = ok({ posted: true, preview: "Hi Priya", title: "Welcome" });
    expect(await requestHello("abc/1", f as unknown as typeof fetch)).toEqual({ preview: "Hi Priya", title: "Welcome" });
    expect(f).toHaveBeenCalledWith(helloPath("abc/1"), { method: "POST", cache: "no-store" });
    expect(helloPath("abc/1")).toBe("/api/chat/sessions/abc%2F1/buddy-hello");
  });
  it("answers null on a 204, a refusal and a network fault", async () => {
    expect(await requestHello("a", (async () => new Response(null, { status: 204 })) as typeof fetch)).toBeNull();
    expect(await requestHello("a", (async () => new Response("{}", { status: 404 })) as typeof fetch)).toBeNull();
    expect(await requestHello("a", (async () => { throw new Error("down"); }) as typeof fetch)).toBeNull();
    expect(await requestHello("a", ok({ posted: false }) as unknown as typeof fetch)).toBeNull();
  });
});

describe("claimHello", () => {
  it("is true once for each chat, and the chat is then a hello chat", () => {
    expect(claimHello("s1")).toBe(true);
    expect(claimHello("s1")).toBe(false);
    expect(opensHello("s1")).toBe(true);
    expect(opensHello("s2")).toBe(false);
    expect(claimHello("")).toBe(false);
  });
});

describe("the chips", () => {
  it("'Show me around' starts the tour on a tap", () => {
    runHelloChip("tour", undefined);
    expect(startGuide).toHaveBeenCalledWith("home.tour", "button");
  });
  it("'What can you do here?' sends the question as the member's message", () => {
    const send = vi.fn();
    runHelloChip("ask", send);
    expect(send).toHaveBeenCalledWith(HELLO_QUESTION);
    expect(HELLO_QUESTION).toBe("What can you do here?");
    expect(startGuide).not.toHaveBeenCalled();
  });
  it("'I'll look around' closes the panel and starts nothing", () => {
    openAssistant({});
    expect(assistantState().open).toBe(true);
    runHelloChip("look", vi.fn());
    expect(assistantState().open).toBe(false);
    expect(startGuide).not.toHaveBeenCalled();
  });
  it("an unknown chip does nothing", () => {
    const send = vi.fn();
    runHelloChip("x", send);
    expect(send).not.toHaveBeenCalled();
    expect(startGuide).not.toHaveBeenCalled();
  });
});

describe("helloOpens", () => {
  const on = { guideAllowed: true, fresh: NEW, hasChats: false as boolean | null, guideActive: false };
  it("opens for a new member with the switch on", () => {
    expect(helloOpens(on)).toBe(true);
  });
  it("never opens with the switch off", () => {
    expect(helloOpens({ ...on, guideAllowed: false })).toBe(false);
  });
  it("waits for the session list and for the server's layout", () => {
    expect(helloOpens({ ...on, hasChats: null })).toBe(false);
    expect(helloOpens({ ...on, fresh: undefined })).toBe(false);
  });
  it("an existing member who has chatted sees no hello", () => {
    expect(helloOpens({ ...on, hasChats: true })).toBe(false);
  });
  it("each stamp ends it: hello, first message, tour", () => {
    const at = "2026-11-05T09:00:00.000Z";
    for (const k of ["buddy_hello_at", "chat_first_message_at", "tour_seen_at"] as const) {
      expect(helloOpens({ ...on, fresh: { ...NEW, [k]: at } })).toBe(false);
    }
  });
  it("a running guide holds it back", () => {
    expect(helloOpens({ ...on, guideActive: true })).toBe(false);
  });
});

describe("the nudge gives way to the hello", () => {
  const base = {
    chatFirst: true, fresh: NEW, retiredHere: false, retiredInBrowser: false,
    welcomeOpen: false, assistantOpen: false, hasChats: false as boolean | null,
  };
  it("shows with no hello in play", () => {
    expect(nudgeShows(base)).toBe(true);
  });
  it("stays away while the hello is pending, and once its stamp is set", () => {
    expect(nudgeShows({ ...base, helloPending: true })).toBe(false);
    expect(nudgeShows({ ...base, fresh: { ...NEW, buddy_hello_at: "2026-11-05T09:00:00.000Z" } })).toBe(false);
  });
});

describe("the hello row", () => {
  it("is known by its id, so it gets no follow-ups and no feedback row", async () => {
    const { isHelloRow } = await import("./buddyHello");
    expect(isHelloRow("buddy-hello")).toBe(true);
    expect(isHelloRow("brief-2026-11-05")).toBe(false);
    expect(isHelloRow(undefined)).toBe(false);
  });
});
