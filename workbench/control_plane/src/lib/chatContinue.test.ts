/**
 * WT-2: the pill "Continue in a fresh chat". The pill shows only when the
 * server marks the turn. The tap calls the route, the shell fills the
 * composer, and nothing sends. Each refusal has plain copy.
 */
import { readFileSync } from "node:fs";
import { describe, expect, it, vi } from "vitest";

import {
  CONTINUE_COPY,
  CONTINUE_LABEL,
  CONTINUE_OFFER_EVENT,
  continueCopy,
  continueOffered,
  continuePath,
  newestOfferId,
  requestContinue,
} from "./chatContinue";

/** The name of the link column, built in two parts so this file does not name it. */
const COLUMN = new RegExp("continued_?" + "[Ff]rom");

const reply = (status: number, body: unknown = {}) =>
  vi.fn(async () => new Response(JSON.stringify(body), { status }));

describe("the offer comes from the server", () => {
  it("is the event the server sends, and no client flag", () => {
    expect(CONTINUE_OFFER_EVENT).toBe("chat_continue_offer");
    const src = readFileSync(new URL("./chatContinue.ts", import.meta.url), "utf-8");
    expect(src).not.toMatch(/process\.env|NEXT_PUBLIC|CHAT_CONTINUE_ORGS\s*[=:]/);
  });

  it("shows only on a turn that carries the event", () => {
    expect(continueOffered(undefined)).toBe(false);
    expect(continueOffered([{ name: "ai.route" }])).toBe(false);
    expect(continueOffered([{ name: CONTINUE_OFFER_EVENT }])).toBe(true);
  });

  it("marks the newest settled turn only, and nothing while it streams", () => {
    const mark = [{ name: CONTINUE_OFFER_EVENT }];
    const msgs = [
      { id: "a1", role: "assistant", customEvents: mark },
      { id: "u1", role: "user" },
      { id: "a2", role: "assistant", customEvents: mark },
    ];
    expect(newestOfferId(msgs)).toBe("a2");
    expect(newestOfferId([{ id: "a1", role: "assistant" }, { id: "u", role: "user" }])).toBeNull();
    expect(newestOfferId([{ id: "a3", role: "assistant", streaming: true, customEvents: mark }])).toBeNull();
  });

  it("has the pill words", () => {
    expect(CONTINUE_LABEL).toBe("Continue in a fresh chat");
  });
});

describe("the tap", () => {
  it("posts to the route of the source chat with no body", async () => {
    const f = reply(200, { session_id: "new-1", text: "Make a task" });
    const got = await requestContinue("src 1", f as unknown as typeof fetch);
    expect(f).toHaveBeenCalledTimes(1);
    const [url, init] = f.mock.calls[0] as unknown as [string, RequestInit];
    expect(url).toBe(continuePath("src 1"));
    expect(url).toBe("/api/chat/sessions/src%201/continue");
    expect(init.method).toBe("POST");
    expect(init.body).toBeUndefined();
    expect(got).toEqual({ ok: true, sessionId: "new-1", text: "Make a task" });
  });

  it("gives the server's words back as they are", async () => {
    const text = "  two\n\nlines  ";
    const got = await requestContinue("s", reply(200, { session_id: "n", text }) as unknown as typeof fetch);
    expect(got).toMatchObject({ ok: true, text });
  });

  it.each([
    [404, CONTINUE_COPY.closed],
    [409, CONTINUE_COPY.nothing],
    [503, CONTINUE_COPY.retry],
    [500, CONTINUE_COPY.retry],
  ])("answers %i with short plain copy", async (status, copy) => {
    const got = await requestContinue("s", reply(status) as unknown as typeof fetch);
    expect(got).toEqual({ ok: false, status, copy });
    expect(continueCopy(status)).toBe(copy);
  });

  it("reads a network fault or a bad body as a retry", async () => {
    const down = vi.fn(async () => { throw new Error("offline"); });
    expect(await requestContinue("s", down as unknown as typeof fetch)).toMatchObject({ ok: false, copy: CONTINUE_COPY.retry });
    const bad = reply(200, { session_id: "", text: "" });
    expect(await requestContinue("s", bad as unknown as typeof fetch)).toMatchObject({ ok: false, copy: CONTINUE_COPY.retry });
  });

  it("uses no 'open a new chat' wording", () => {
    for (const line of Object.values(CONTINUE_COPY)) {
      expect(line).not.toMatch(/new chat/i);
      expect(line.split(/\s+/).length).toBeLessThanOrEqual(20);
    }
  });
});

describe("the shell fills the composer and never sends", () => {
  const dock = readFileSync(new URL("./shell/AssistantDock.tsx", import.meta.url), "utf-8");
  const body = dock.slice(dock.indexOf("openContinued:"), dock.indexOf("/** Open a chat the member chose"));

  it("puts the words in the composer slot and opens the new chat", () => {
    expect(body).toContain("setRecoveredInput(opened.text)");
    expect(body).toContain("setAssistantSession(opened.sessionId)");
  });

  it("does not auto-send, and does not build the words", () => {
    expect(body).not.toMatch(/autoSend|askAssistant|sendMessage|handleSubmit/);
    expect(body).not.toMatch(COLUMN);
  });

  it("names no link column in the web shell", () => {
    const lib = readFileSync(new URL("./chatContinue.ts", import.meta.url), "utf-8");
    expect(lib).not.toMatch(COLUMN);
  });
});
