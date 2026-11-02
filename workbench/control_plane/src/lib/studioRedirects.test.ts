/**
 * AI Studio A1 + E1 (`ai_studio.md` §14): the old panes redirect to `/studio`.
 * The run page and the Workshop under `/build/apps/[slug]` must NOT redirect.
 */
import { describe, expect, it } from "vitest";

import nextConfig, { STUDIO_REDIRECTS } from "../../next.config";

describe("the Studio redirects", () => {
  it("holds the four rules, all permanent", async () => {
    expect(await nextConfig.redirects?.()).toEqual(STUDIO_REDIRECTS);
    expect(
      STUDIO_REDIRECTS.map((r) => [r.source, r.destination, r.permanent]),
    ).toEqual([
      ["/workflows", "/studio", true],
      ["/workflows/:id", "/studio/workflows/:id", true],
      ["/build/apps", "/studio", true],
      ["/build/agents", "/studio", true],
    ]);
  });

  it("leaves the run page, the Workshop and the API alone", () => {
    const sources = STUDIO_REDIRECTS.map((r) => r.source);
    expect(sources).not.toContain("/build/apps/:path*");
    expect(sources).not.toContain("/build/apps/:slug");
    expect(sources.some((s) => s.startsWith("/api"))).toBe(false);
  });
});
