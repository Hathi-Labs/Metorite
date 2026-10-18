/**
 * "Not now" on a card that parks a run (owner request, 2026-10-18).
 *
 * Mutations caught, by test:
 * - the gate lets Submit fire after Not now, or Not now after Submit;
 * - the decline body carries an answer, or forgets `declined`;
 * - a decline from another account reaches the gateway;
 * - a malformed decline (a string, an answer with it) reaches the gateway;
 * - the route drops `declined` on the way to the gateway, or adds it to an
 *   answer;
 * - a decline that no run takes sends the decline as a chat message.
 */
import { beforeEach, describe, expect, it, vi } from "vitest";

import {
  DECLINED_ANSWER,
  DECLINED_LINE,
  NOT_NOW,
  cardExit,
  declineBody,
  declinedNoteFor,
  isDeclinedAnswer,
} from "./declineAnswer";
import { malformedDecline, otherAccountOf, sendRespondInputResult } from "./respondInput";

const RID = "a".repeat(32);

describe("one way out of a card", () => {
  it("lets only the first of Submit and Not now fire", () => {
    const sent: string[] = [];
    const exit = cardExit();
    expect(exit.decline(() => sent.push("decline"))).toBe(true);
    expect(exit.submit(() => sent.push("submit"))).toBe(false);
    expect(exit.decline(() => sent.push("decline"))).toBe(false);
    expect(sent).toEqual(["decline"]);
    expect(exit.taken).toBe("decline");

    const other = cardExit();
    expect(other.submit(() => sent.push("submit"))).toBe(true);
    expect(other.decline(() => sent.push("decline"))).toBe(false);
    expect(sent).toEqual(["decline", "submit"]);
  });
});

describe("the decline envelope", () => {
  it("is declined, with no answer", () => {
    expect(declineBody(RID)).toEqual({ request_id: RID, answer: "", was_freeform: false, declined: true });
  });

  it("says Not now, and Not answered after it, in muted words", () => {
    expect(NOT_NOW).toBe("Not now");
    expect(DECLINED_LINE).toBe("Not answered · You can ask again later.");
  });

  it("holds a declined card's answer as a mark no answer can be", () => {
    expect(isDeclinedAnswer(DECLINED_ANSWER)).toBe(true);
    expect(isDeclinedAnswer("Selected: Billed")).toBe(false);
    expect(isDeclinedAnswer(undefined)).toBe(false);
  });

  it("refuses a malformed decline before the gateway", () => {
    expect(malformedDecline(undefined, "Billed")).toBeNull();
    expect(malformedDecline(false, "Billed")).toBeNull();
    expect(malformedDecline(true, "")).toBeNull();
    expect(malformedDecline(true, "Billed")?.status).toBe(400);
    expect(malformedDecline("yes", "")?.status).toBe(400);
    expect(malformedDecline(1, "")?.status).toBe(400);
  });

  it("is sent in the one POST, and a 409 is a drop that resends nothing", async () => {
    const calls: { body: unknown }[] = [];
    const fetchFn = vi.fn(async (_url: unknown, init?: RequestInit) => {
      calls.push({ body: JSON.parse(String(init?.body)) });
      return new Response('{"detail":"No pending question matches that request_id"}', { status: 409 });
    }) as unknown as typeof fetch;
    const out = await sendRespondInputResult({ ...declineBody(RID), thread_id: "t1" }, fetchFn);
    expect(calls[0].body).toMatchObject({ request_id: RID, answer: "", declined: true, thread_id: "t1" });
    expect(out).toEqual({ outcome: "drop", resend: null });
  });
});

describe("the route", () => {
  beforeEach(() => {
    vi.resetModules();
    vi.unstubAllGlobals();
  });

  const mockAs = (email: string) => {
    vi.doMock("@/auth", () => ({ auth: async () => ({ user: { email } }) }));
    vi.doMock("@/lib/gateway", () => ({
      GATEWAY_URL: "http://gw.test",
      requireIdentity: async () => ({ email, role: "employee" }),
      gatewayHeaders: async () => ({}),
      gatewayFetch: (input: RequestInfo | URL, init?: RequestInit) => fetch(input, init),
    }));
  };
  const post = async (body: unknown) => {
    const { POST } = await import("@/app/api/agent/respond-input/route");
    return POST(new Request("http://app.test/api/agent/respond-input", {
      method: "POST", body: JSON.stringify(body),
    }) as never);
  };

  it("forwards a decline as declined, and an answer exactly as before", async () => {
    mockAs("alice@a.test");
    const gateway = vi.fn(async (_u: unknown, _init?: RequestInit) => new Response('{"ok":true}', { status: 200 }));
    vi.stubGlobal("fetch", gateway);
    expect((await post({ ...declineBody(RID), thread_id: "t1", as: "alice@a.test" })).status).toBe(200);
    expect((await post({ request_id: RID, answer: "Billed", was_freeform: false, thread_id: "t1" })).status).toBe(200);
    const sent = gateway.mock.calls.map((c) => JSON.parse(String(c[1]?.body)));
    expect(sent[0]).toMatchObject({ request_id: RID, answer: "", declined: true });
    expect(sent[1]).not.toHaveProperty("declined");
  });

  it("refuses a decline drawn for another account, like an answer", async () => {
    mockAs("bob@b.test");
    const gateway = vi.fn(async () => new Response("{}", { status: 200 }));
    vi.stubGlobal("fetch", gateway);
    const res = await post({ ...declineBody(RID), thread_id: "t1", as: "alice@a.test" });
    expect(res.status).toBe(409);
    expect(otherAccountOf(await res.text())).toBe("alice@a.test");
    expect(gateway).not.toHaveBeenCalled();
  });

  it("refuses a malformed decline, and the gateway never hears it", async () => {
    mockAs("alice@a.test");
    const gateway = vi.fn(async () => new Response("{}", { status: 200 }));
    vi.stubGlobal("fetch", gateway);
    expect((await post({ request_id: RID, answer: "Billed", declined: true, thread_id: "t1" })).status).toBe(400);
    expect((await post({ request_id: RID, answer: "", declined: "yes", thread_id: "t1" })).status).toBe(400);
    expect(gateway).not.toHaveBeenCalled();
  });
});

describe("the note of a declined question card (review of #1152)", () => {
  it("says what Metorite uses now when the card states a default", () => {
    expect(declinedNoteFor([])).toBe("You can ask again later.");
    expect(declinedNoteFor(["", " "])).toBe("You can ask again later.");
    expect(declinedNoteFor(["Billed, until a CRM admin answers"])).toBe(
      "Metorite uses Billed, until a CRM admin answers for now.",
    );
    expect(declinedNoteFor(["A.", "B"])).toBe("Metorite uses the stated defaults for now.");
    expect(declinedNoteFor(["Keep them open for now"])).toBe("Metorite uses Keep them open for now.");
  });
});
