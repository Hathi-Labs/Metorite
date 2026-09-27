// The chat picker under the Console Router.
//
// 🔴 Measured on production, 2026-09-24: a member picked
// `openrouter/qwen/qwen3.7-max` and the Router refused it `tier_unknown`. The
// Router serves TIERS and refuses a raw model id by design (D32.7), so while it
// serves, a raw model in this list is a guaranteed 400.
import { beforeEach, describe, expect, it, vi } from "vitest";

vi.mock("@/lib/gateway", () => ({
  GATEWAY_URL: "http://gw.test",
  gatewayHeaders: async () => ({}),
  requireIdentity: async () => ({ email: "a@example.com" }),
  UNAUTHENTICATED: { error: "Sign in to continue" },
}));

const ENABLED = [
  { id: "openrouter/qwen/qwen3.7-max", label: "Qwen 3.7 Max", provider: "openrouter", group: "x" },
];

function stubGateway(routerServing: boolean | undefined) {
  vi.stubGlobal(
    "fetch",
    vi.fn(async (url: string) => {
      const u = String(url);
      const json = (b: unknown) => new Response(JSON.stringify(b), { status: 200 });
      if (u.endsWith("/settings/llm")) {
        return json({
          providers: [{ id: "openrouter", configured: true }],
          ...(routerServing === undefined ? {} : { router_serving: routerServing }),
        });
      }
      if (u.includes("/settings/llm/enabled-models")) return json(ENABLED);
      return new Response("{}", { status: 404 });
    }),
  );
}

async function ids(): Promise<string[]> {
  const { GET } = await import("./route");
  const res = await GET();
  const body = (await res.json()) as { models: { id: string }[] };
  return body.models.map((m) => m.id);
}

describe("the chat picker under the Router", () => {
  beforeEach(() => {
    vi.unstubAllGlobals();
  });

  it("offers no raw model id while the Router serves", async () => {
    stubGateway(true);
    const got = await ids();
    expect(got).not.toContain("openrouter/qwen/qwen3.7-max");
    // The tiers stay: the gateway maps each wire id to its slate name.
    expect(got).toEqual(
      expect.arrayContaining(["tier1-local-qwen3", "tier2-sonnet", "tier3-opus"]),
    );
  });

  it("still offers an enabled model when the Router is off", async () => {
    stubGateway(false);
    expect(await ids()).toContain("openrouter/qwen/qwen3.7-max");
  });

  it("treats an older gateway that does not say as Router off", async () => {
    stubGateway(undefined);
    expect(await ids()).toContain("openrouter/qwen/qwen3.7-max");
  });
});
