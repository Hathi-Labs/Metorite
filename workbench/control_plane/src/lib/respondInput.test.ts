/**
 * An answer from a tab whose account is no longer the signed-in one never
 * reaches the other account's org (WS-51 S2, `chat_run_continuity.md` §4 S2).
 *
 * A switch in another tab changes the cookie under this one. The tab still
 * shows a card it drew for account A, and the browser now speaks as account
 * B. The BFF route refuses the answer before the gateway, and the chat says
 * "Switch to A to answer". The route's check can only refuse, never grant:
 * the gateway still checks the room under the session's own tenant.
 */

import { readFileSync } from "node:fs";
import { join } from "node:path";
import { fileURLToPath } from "node:url";

import { beforeEach, describe, expect, it, vi } from "vitest";

import {
  ANSWERED_ONCE_TEXT,
  answeredOnceOf,
  otherAccountOf,
  otherAccountRefusal,
  resendsAnswer,
  respondFailure,
  sendRespondInputResult,
} from "./respondInput";

const BODY = { request_id: "a".repeat(32), answer: "APPROVE", was_freeform: false, thread_id: "t1" };

describe("the refusal", () => {
  // Mutation: return null in every case, and this fails.
  it("refuses an answer drawn for another account", async () => {
    const res = otherAccountRefusal("alice@a.test", "bob@b.test");
    expect(res?.status).toBe(409);
    expect(otherAccountOf(await res!.text())).toBe("alice@a.test");
  });

  it("lets the same account through, in any case, and a body with no account", () => {
    expect(otherAccountRefusal("Alice@A.test ", "alice@a.test")).toBeNull();
    expect(otherAccountRefusal(undefined, "alice@a.test")).toBeNull();
    expect(otherAccountRefusal("", "alice@a.test")).toBeNull();
  });
});

describe("the answer POST hears it", () => {
  it("keeps the card (retry) and names the account", async () => {
    const fetchFn = vi.fn(async () =>
      new Response(JSON.stringify({ detail: { error: "answer_in_other_account", account: "alice@a.test" } }), {
        status: 409,
      }),
    );
    const got = await sendRespondInputResult({ ...BODY, as: "alice@a.test" }, fetchFn as unknown as typeof fetch);
    expect(got).toEqual({ outcome: "retry", resend: null, otherAccount: "alice@a.test" });
    const [, init] = fetchFn.mock.calls[0] as unknown as [string, RequestInit];
    const sent = JSON.parse(String(init.body)) as { as?: string };
    expect(sent.as).toBe("alice@a.test");
  });

  it("a 400 or 422 keeps the card, with the server's reason (#916)", async () => {
    for (const status of [400, 422]) {
      const fetchFn = vi.fn(async () => new Response(JSON.stringify({ detail: "Pick a row." }), { status }));
      expect(await sendRespondInputResult(BODY, fetchFn as unknown as typeof fetch)).toEqual({
        outcome: "retry", resend: null, reason: "The server refused the answer: Pick a row.",
      });
    }
  });

  it("a 401 and a 403 still drop", async () => {
    for (const status of [401, 403]) {
      const fetchFn = vi.fn(async () => new Response("{}", { status }));
      expect((await sendRespondInputResult(BODY, fetchFn as unknown as typeof fetch)).outcome).toBe("drop");
    }
  });

  it("a plain 409 is still a drop", async () => {
    const fetchFn = vi.fn(async () => new Response(JSON.stringify({ detail: "No pending question" }), { status: 409 }));
    expect(await sendRespondInputResult(BODY, fetchFn as unknown as typeof fetch)).toEqual({
      outcome: "drop", resend: null, reason: "Nothing waits on this answer now. The run ended, or someone answered it.",
    });
  });
});

// HRD-16, owner decision 2026-11-23: "Distinct 409 detail, client does not
// resend". A run already took an answer for this card, so the second device
// says "someone answered it" and sends nothing. Mutations caught: read every
// 409 as a drop (the answer resends), or read a plain 409 as answered (a stale
// card loses today's resend).
describe("a repeat answer (HRD-16)", () => {
  const answeredOnce = JSON.stringify({
    detail: { error: "answered_once", message: "Someone already answered this card." },
  });

  it("a 409 answered_once is its own outcome, with no resend", async () => {
    const info = vi.spyOn(console, "info").mockImplementation(() => {});
    const fetchFn = vi.fn(async () => new Response(answeredOnce, { status: 409 }));
    expect(await sendRespondInputResult(BODY, fetchFn as unknown as typeof fetch)).toEqual({
      outcome: "answered", resend: null, reason: ANSWERED_ONCE_TEXT,
    });
    info.mockRestore();
  });

  it("only the 409 with that detail reads as answered", () => {
    expect(respondFailure(409, answeredOnce)).toBe("answered");
    expect(respondFailure(409, JSON.stringify({ detail: "No pending question" }))).toBe("drop");
    expect(respondFailure(409)).toBe("drop");
    expect(respondFailure(403, answeredOnce)).toBe("drop");
    expect(answeredOnceOf("not json")).toBe(false);
    expect(answeredOnceOf(JSON.stringify({ detail: { error: "run_restarted" } }))).toBe(false);
  });

  it("a run_restarted 409 still resends, as before", async () => {
    const body = JSON.stringify({ detail: { error: "run_restarted", resumeMessage: "Q and A" } });
    const fetchFn = vi.fn(async () => new Response(body, { status: 409 }));
    const got = await sendRespondInputResult(BODY, fetchFn as unknown as typeof fetch);
    expect(got.outcome).toBe("drop");
    expect(got.resend).toBe("Q and A");
  });

  it("no card sends its answer again on answered, and a drop still does", () => {
    expect(resendsAnswer("answered")).toBe(false);
    expect(resendsAnswer("drop")).toBe(true);
    for (const o of ["ok", "retry", "resent"] as const) expect(resendsAnswer(o)).toBe(false);
  });

  // Mutation caught: a card handler that tests `outcome === "drop"` or
  // `!== "drop"` by itself again, past the one rule.
  it("every card handler in the chat asks resendsAnswer before it sends", () => {
    const here = fileURLToPath(new URL(".", import.meta.url));
    const chat = readFileSync(join(here, "../components/AgentChat.tsx"), "utf8");
    expect(chat).not.toMatch(/outcome\s*[!=]==\s*"drop"/);
    expect(chat.match(/resendsAnswer\(outcome\)/g)?.length).toBe(4);
    expect(chat).toContain('code: "answered_once"');
  });
});

describe("the route", () => {
  beforeEach(() => {
    vi.resetModules();
    vi.unstubAllGlobals();
  });

  // Mutation: delete the refusal from the route, and this fails.
  it("refuses before the gateway, so the other org never hears the answer", async () => {
    vi.doMock("@/auth", () => ({ auth: async () => ({ user: { email: "bob@b.test" } }) }));
    vi.doMock("@/lib/gateway", () => ({
      GATEWAY_URL: "http://gw.test",
      requireIdentity: async () => ({ email: "bob@b.test", role: "employee" }),
      gatewayHeaders: async () => ({}),
      gatewayFetch: (input: RequestInfo | URL, init?: RequestInit) => fetch(input, init),
    }));
    const gateway = vi.fn(async () => new Response("{}", { status: 200 }));
    vi.stubGlobal("fetch", gateway);
    const { POST } = await import("@/app/api/agent/respond-input/route");
    const req = new Request("http://app.test/api/agent/respond-input", {
      method: "POST",
      body: JSON.stringify({ ...BODY, as: "alice@a.test" }),
    });
    const res = await POST(req as never);
    expect(res.status).toBe(409);
    expect(otherAccountOf(await res.text())).toBe("alice@a.test");
    expect(gateway).not.toHaveBeenCalled();
  });

  it("forwards the answer when the account is the one that drew the card", async () => {
    vi.doMock("@/auth", () => ({ auth: async () => ({ user: { email: "alice@a.test" } }) }));
    vi.doMock("@/lib/gateway", () => ({
      GATEWAY_URL: "http://gw.test",
      requireIdentity: async () => ({ email: "alice@a.test", role: "employee" }),
      gatewayHeaders: async () => ({}),
      gatewayFetch: (input: RequestInfo | URL, init?: RequestInit) => fetch(input, init),
    }));
    const gateway = vi.fn(async () => new Response('{"ok":true}', { status: 200 }));
    vi.stubGlobal("fetch", gateway);
    const { POST } = await import("@/app/api/agent/respond-input/route");
    const req = new Request("http://app.test/api/agent/respond-input", {
      method: "POST",
      body: JSON.stringify({ ...BODY, as: "alice@a.test" }),
    });
    const res = await POST(req as never);
    expect(res.status).toBe(200);
    expect(gateway).toHaveBeenCalledTimes(1);
  });
});
