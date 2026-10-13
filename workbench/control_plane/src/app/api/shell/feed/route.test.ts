// `/api/shell/feed` passes the feed on, caps `limit`, and fails LOUD
// (`my_day_feed.md` §14.1a, MD-1b).
//
// An empty list here would draw "You are caught up", which is a claim about
// the member's day. So an unreachable gateway must be an error the column can
// show, never a 200 of nothing.
//
// Mutation, run by hand (2026-10-13): make the catch answer `{ cards: [] }`
// with a 200, and the second test fails.
import { beforeEach, describe, expect, it, vi } from "vitest";
import { NextRequest } from "next/server";

const identity = { value: { email: "a@example.com" } as unknown };

vi.mock("@/lib/gateway", async () => {
  const { NextResponse } = await import("next/server");
  return {
    GATEWAY_URL: "http://gw.test",
    requireIdentity: async () =>
      identity.value ?? NextResponse.json({ error: "Sign in to continue" }, { status: 401 }),
    gatewayHeaders: async () => ({ "X-User-Email": "a@example.com" }),
    gatewayFetch: (input: RequestInfo | URL, init?: RequestInit) => fetch(input, init),
  };
});

const asked: string[] = [];

function stubGateway(reply: () => Response) {
  asked.length = 0;
  vi.stubGlobal(
    "fetch",
    vi.fn(async (input: RequestInfo | URL) => {
      asked.push(String(input));
      return reply();
    }),
  );
}

const get = async (query = "") => {
  const { GET } = await import("./route");
  return GET(new NextRequest(`http://app.test/api/shell/feed${query}`));
};

/** The answer of §14.1a, as the gateway sends it. */
const ANSWER = {
  cards: [
    {
      signal_id: "tasks:9b2e",
      app: "tasks",
      kind: "overdue",
      folded: ["block_soon"],
      group: "needs_you",
      score: 87,
      entity: { type: "task", id: "9b2e", label: "Send the Q3 BOM" },
      title: "Send the Q3 BOM to purchase",
      why: "Due Monday. 2 days late.",
      why_key: "days_late",
      facts: { due_at: "2026-10-12T17:00:00+05:30", days_late: 2 },
      fingerprint: "a41f09c2",
      urgent_at: null,
      expires_at: null,
      at: "2026-10-12T17:00:00+05:30",
      href: "/projects?task=9b2e",
      acts: [
        { id: "done", label: "Done", class: "A", way: "tap", act: "done", act_ref: "9b2e" },
        { id: "open-day", label: "Open the day", class: "A", way: "open", href: "/calendar" },
      ],
    },
  ],
  groups: [{ id: "needs_you", count: 1 }],
  needs: { count: 1, total: 1, items: [], sources: {}, totals: {}, capped: [] },
  sources: { tasks: "ok", projects: "ok", email: "absent", approvals: "absent", calendar: "ok" },
  capped: [],
};

describe("/api/shell/feed", () => {
  beforeEach(() => {
    vi.unstubAllGlobals();
    identity.value = { email: "a@example.com" };
  });

  it("forwards the gateway's feed unchanged, at the default limit of 40", async () => {
    stubGateway(() => new Response(JSON.stringify(ANSWER), { status: 200 }));
    const res = await get();
    expect(res.status).toBe(200);
    expect(await res.json()).toEqual(ANSWER);
    expect(asked[0]).toBe("http://gw.test/shell/feed?limit=40");
  });

  it("answers 502 when the gateway cannot be reached, never an empty feed", async () => {
    stubGateway(() => {
      throw new TypeError("fetch failed");
    });
    const res = await get();
    expect(res.status).toBe(502);
    expect(await res.json()).not.toHaveProperty("cards");
  });

  it("keeps a refusal's status", async () => {
    stubGateway(() => new Response("{}", { status: 403 }));
    expect((await get()).status).toBe(403);
  });

  it("caps limit at 80, and passes nothing else on", async () => {
    stubGateway(() => new Response("{}", { status: 200 }));
    await get("?limit=9999&org=other&email=b@example.com");
    expect(asked[0]).toBe("http://gw.test/shell/feed?limit=80");
    await get("?limit=nope");
    expect(asked.at(-1)).toBe("http://gw.test/shell/feed?limit=40");
  });

  it("asks the gateway nothing without a session", async () => {
    identity.value = null;
    stubGateway(() => new Response(JSON.stringify(ANSWER), { status: 200 }));
    const res = await get();
    expect(res.status).toBe(401);
    expect(asked).toEqual([]);
  });

  it("exports only its handler and its route config", async () => {
    const mod = await import("./route");
    expect(Object.keys(mod).sort()).toEqual(["GET", "dynamic"]);
  });
});
