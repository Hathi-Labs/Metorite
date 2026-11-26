// WS-CRM CRM-M3a: the browser cannot claim a chat change.
//
// The gateway logs `via=chat` only for an `X-Actor-Via: chat:` stamp beside
// the internal bearer (`routes/crm/change_log.via_of`). The bearer is held by
// this proxy and by the agent runtime. So the stamp is true only while this
// proxy never forwards a header of the browser. These cases RUN the handler
// with a forged stamp and read the headers that went upstream.
import { afterAll, beforeAll, beforeEach, describe, expect, it, vi } from "vitest";

// `lib/gateway.ts` reads the token once, when it loads. A known value makes
// the upstream bearer exact, whatever the shell that runs the suite holds.
const TOKEN = vi.hoisted(() => {
  const token = "m3a-proxy-internal-token";
  vi.stubEnv("GATEWAY_INTERNAL_TOKEN", token);
  return token;
});
afterAll(() => vi.unstubAllEnvs());

vi.mock("@/auth", () => ({
  auth: async () => ({ user: { email: "priya@fracktal.in" } }),
  isAuthEnabled: true,
}));

async function relay(method: "POST" | "PATCH" | "GET", path: string[]): Promise<Headers> {
  const seen: RequestInit[] = [];
  vi.stubGlobal(
    "fetch",
    vi.fn(async (_url: string, init?: RequestInit) => {
      seen.push(init ?? {});
      return new Response("{}", { status: 201, headers: { "content-type": "application/json" } });
    })
  );
  const { NextRequest } = await import("next/server");
  const route = await import("./route");
  const req = new NextRequest(`http://localhost:3001/api/crm/${path.join("/")}`, {
    method,
    headers: {
      "Content-Type": "application/json",
      "X-Actor-Via": "chat:crm-assistant",
      "X-User-Email": "boss@fracktal.in",
      Authorization: "Bearer forged",
    },
    ...(method === "GET" ? {} : { body: JSON.stringify({ name: "AMC contracts", stages: [{ name: "Site visit" }] }) }),
  });
  await route[method](req, { params: Promise.resolve({ path }) });
  expect(seen).toHaveLength(1);
  return new Headers(seen[0].headers as HeadersInit);
}

describe("the CRM proxy and a forged chat stamp", () => {
  beforeAll(async () => {
    await import("next/server");
    await import("./route");
  }, 60_000);
  beforeEach(() => vi.unstubAllGlobals());

  it.each([
    ["POST", ["pipelines"]],
    ["GET", ["pipelines", "preview"]],
    ["PATCH", ["pipelines", "8a0f0e1e-0000-4000-8000-000000000001"]],
  ] as const)("%s /%s goes upstream with no stamp and the session's own identity", async (method, path) => {
    const sent = await relay(method, [...path]);
    expect(sent.get("x-actor-via")).toBeNull();
    // The identity is the session's, never the browser's header.
    expect(sent.get("x-user-email")).toBe("priya@fracktal.in");
    // The proxy's own bearer, never the browser's.
    expect(sent.get("authorization")).toBe(`Bearer ${TOKEN}`);
  });
});
