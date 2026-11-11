// WS-MCP SW-31b — the account menu's "Agents" row shows only while guidance
// is on for the org. The read fails closed.
import { describe, expect, it } from "vitest";

import { fetchGuidanceEnabled } from "./useAgentGuidanceEnabled";

const answer = (status: number) =>
  (async () => new Response(JSON.stringify(status === 200 ? { rows: [] } : { detail: "Not Found" }), { status })) as unknown as typeof fetch;

describe("fetchGuidanceEnabled", () => {
  it("is true on a 200 only", async () => {
    expect(await fetchGuidanceEnabled(answer(200))).toBe(true);
  });

  it("is false while the router is dark (404) or Projects is closed (403)", async () => {
    expect(await fetchGuidanceEnabled(answer(404))).toBe(false);
    expect(await fetchGuidanceEnabled(answer(403))).toBe(false);
  });

  it("throws on a server fault, so the cache keeps no wrong yes", async () => {
    await expect(fetchGuidanceEnabled(answer(502))).rejects.toThrow();
  });

  it("asks the org history for one row", async () => {
    let asked = "";
    const spy = (async (url: string) => {
      asked = url;
      return new Response("{}", { status: 200 });
    }) as unknown as typeof fetch;
    await fetchGuidanceEnabled(spy);
    expect(asked).toBe("/api/agent-guidance/history?limit=1");
  });
});
