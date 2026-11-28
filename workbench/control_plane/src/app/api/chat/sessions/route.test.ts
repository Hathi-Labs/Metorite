// Task 3564, chat sidebar fault 2: `GET /api/chat/sessions` passes the
// gateway's `isBrief` on to the browser as it came, with every other field.
//
// Mutation: map the rows to a list of named fields that leaves `isBrief` out,
// and the test fails.
import { afterEach, describe, expect, it, vi } from "vitest";

vi.mock("@/lib/gateway", () => ({
  GATEWAY_URL: "http://gw.test",
  requireIdentity: async () => ({ email: "a@example.com" }),
  gatewayHeaders: async () => ({ "X-User-Email": "a@example.com" }),
  gatewayFetch: (input: RequestInfo | URL, init?: RequestInit) => fetch(input, init),
}));

import { GET } from "./route";

const ROWS = [
  { id: "c1", agentName: "orchestrator", title: "Your day, Mon 16 Nov", messageCount: 1,
    hasUserMessage: false, isBrief: true, archivedAt: null },
  { id: "c2", agentName: "orchestrator", title: "Pricing", messageCount: 4,
    hasUserMessage: true, isBrief: false, archivedAt: null },
];

afterEach(() => {
  vi.unstubAllGlobals();
});

describe("GET /api/chat/sessions", () => {
  it("passes the brief mark through", async () => {
    const asked: string[] = [];
    vi.stubGlobal("fetch", vi.fn(async (input: RequestInfo | URL) => {
      asked.push(String(input));
      return new Response(JSON.stringify(ROWS), { status: 200 });
    }));
    const res = await GET();
    expect(res.status).toBe(200);
    expect(await res.json()).toEqual(ROWS);
    expect(asked).toEqual(["http://gw.test/chat/sessions"]);
  });
});
