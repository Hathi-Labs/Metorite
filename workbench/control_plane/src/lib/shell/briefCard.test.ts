/**
 * The brief card of My Day (`core_brief.md` §5 item 1, BR-2c-2).
 *
 * Claims:
 *   1. The card draws the lead as its heading and one in-app link for each
 *      claim, in the order of `position`.
 *   2. A 404 gives `null`, and the card draws nothing. A failed read draws
 *      nothing and logs once. A claim with an off-site href is no link.
 *   3. A brief that is not today's is labelled with its date.
 *   4. The flag reads the literal `NEXT_PUBLIC_BRIEF_CARD`.
 *
 * Mutation: sort the claims by text, and claim 1 fails. Draw a card on a
 * null brief, and claim 2 fails.
 */
import { createElement } from "react";
import { renderToStaticMarkup } from "react-dom/server";
import { afterEach, describe, expect, it, vi } from "vitest";

const resource: { data: unknown; error: string | null } = { data: undefined, error: null };
vi.mock("@/lib/useCachedResource", () => ({
  useCachedResource: () => ({ data: resource.data, error: resource.error, loading: false, refresh: () => {} }),
}));

import BriefCard from "./BriefCard";
import { briefCardOn, briefCardOnServer } from "./briefCardFlag";
import { fetchBrief, localDateOf, readBrief, staleLabel } from "./briefCard";

const NOW = new Date(2026, 9, 22, 9, 0, 0);

const body = (extra: Record<string, unknown> = {}) => ({
  kind: "brief",
  local_date: "2026-10-22",
  state: "ready",
  lead: "Your day, Thu 22 Oct. 2 things need you.",
  lead_source: "template",
  ready_at: "2026-10-22T02:00:00Z",
  claims: [
    { position: 2, section: "needs_you", text: "Reply to Dana", source_app: "email", source_kind: "thread", href: "/email?thread=9" },
    { position: 1, section: "needs_you", text: "Approve the Q3 plan", source_app: "projects", source_kind: "task", href: "/projects?task=4" },
  ],
  ...extra,
});

const draw = (now: Date | null = NOW) => renderToStaticMarkup(createElement(BriefCard, { now }));

afterEach(() => {
  resource.data = undefined;
  resource.error = null;
  vi.unstubAllEnvs();
});

describe("the brief card", () => {
  it("draws the lead and the claims as in-app links, in position order", () => {
    resource.data = readBrief(body());
    const html = draw();
    expect(html).toContain("<h2");
    expect(html).toContain("Your day, Thu 22 Oct. 2 things need you.");
    expect(html.indexOf("Approve the Q3 plan")).toBeGreaterThan(-1);
    expect(html.indexOf("Approve the Q3 plan")).toBeLessThan(html.indexOf("Reply to Dana"));
    expect(html).toContain('href="/projects?task=4"');
    expect(html).toContain('href="/email?thread=9"');
    expect(html).toContain("<ul");
    expect(html).not.toContain("Yesterday");
  });

  it("draws nothing with no brief, and nothing on a failed read", () => {
    resource.data = null;
    expect(draw()).toBe("");
    resource.data = undefined;
    resource.error = "The brief could not load (500).";
    expect(draw()).toBe("");
  });

  it("makes no link of an off-site href", () => {
    resource.data = readBrief(
      body({ claims: [{ position: 1, section: "s", text: "Go away", source_app: "", source_kind: "", href: "https://evil.example/x" }] }),
    );
    const html = draw();
    expect(html).toContain("Go away");
    expect(html).not.toContain("evil.example");
  });

  it("labels the brief of the day before with its date", () => {
    resource.data = readBrief(body({ local_date: "2026-10-21" }));
    expect(draw()).toContain("Yesterday&#x27;s brief, 21 Oct");
    expect(staleLabel("2026-10-21", NOW)).toBe("Yesterday's brief, 21 Oct");
    expect(staleLabel("2026-10-22", NOW)).toBeNull();
    expect(staleLabel("2026-10-21", null)).toBeNull();
    expect(staleLabel("2026-10-18", NOW)).toBe("Earlier brief, 18 Oct");
    expect(localDateOf(NOW)).toBe("2026-10-22");
  });
});

describe("the read", () => {
  const reply = (status: number, json?: unknown) =>
    vi.fn(async () => new Response(json === undefined ? null : JSON.stringify(json), { status })) as unknown as typeof fetch;

  it("answers null on a 404, the brief on a 200, and throws on a 5xx", async () => {
    expect(await fetchBrief(reply(404))).toBeNull();
    expect((await fetchBrief(reply(200, body())))?.claims.map((c) => c.position)).toEqual([1, 2]);
    await expect(fetchBrief(reply(502))).rejects.toThrow(/502/);
  });

  it("drops a body with no lead", () => {
    expect(readBrief({ local_date: "2026-10-22", lead: "" })).toBeNull();
    expect(readBrief(null)).toBeNull();
  });
});

describe("the flag", () => {
  it("is off by default and on with the build value", () => {
    vi.stubEnv("NODE_ENV", "production");
    expect(briefCardOn()).toBe(false);
    expect(briefCardOnServer()).toBe(false);
    vi.stubEnv("NEXT_PUBLIC_BRIEF_CARD", "1");
    expect(briefCardOn()).toBe(true);
    expect(briefCardOnServer()).toBe(true);
  });
});
