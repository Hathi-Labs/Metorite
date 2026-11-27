/**
 * WS-55 EA-4a and EA-19d (`external_agent_access.md` §10.5, §10.9): the
 * consent page and the Integrations page may never sit in a frame.
 */
import { describe, expect, it } from "vitest";

import nextConfig from "../../next.config";

describe("the frame headers", () => {
  it.each(["/connect/:path*", "/integrations/:path*"])("%s carries frame-ancestors 'none' and DENY", async (source) => {
    const rules = (await nextConfig.headers?.()) ?? [];
    const rule = rules.find((r) => r.source === source);
    expect(rule).toBeDefined();
    const byKey = Object.fromEntries(rule!.headers.map((h) => [h.key, h.value]));
    expect(byKey["Content-Security-Policy"]).toContain("frame-ancestors 'none'");
    expect(byKey["X-Frame-Options"]).toBe("DENY");
  });
});
