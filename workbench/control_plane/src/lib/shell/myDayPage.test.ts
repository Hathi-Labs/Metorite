/**
 * `/` picks its branch by the flag (NS-3 done-when 1 and 5).
 *
 * Flag off, `/` is the "Welcome back" grid, unchanged. Flag on, it is My Day,
 * with its cards in the order of the question: Needs you, Today, Next
 * actions. A member gets a card only for an app they hold.
 *
 * The runner is `environment: "node"`, so the page renders to a string. That
 * is the SERVER render, which reads the build-time flag only
 * (`myDayOnServer`). The reads have not answered yet, so each card shows its
 * skeleton, and the test asserts the frame and the order.
 *
 * Mutation: make `Home` return `<WelcomeBack />` always, and the second test
 * fails. Return `<MyDay />` always, and the first one fails.
 */
import { readFileSync } from "node:fs";

import { createElement } from "react";
import { renderToStaticMarkup } from "react-dom/server";
import { afterEach, beforeAll, describe, expect, it, vi } from "vitest";

import { NO_ACCESS } from "@/lib/access";

import type { NeedsItem } from "./needs";

const access = { features: ["tasks", "projects", "email"] as string[], is_admin: false };

vi.mock("@/components/AccessProvider", () => ({
  useAccess: () => ({
    access: { ...NO_ACCESS, authenticated: true, features: access.features, is_admin: access.is_admin },
    loading: false,
    stale: false,
    refresh: async () => {},
  }),
}));

vi.mock("next-auth/react", () => ({
  useSession: () => ({ data: { user: { name: "Asha Rao", email: "asha@example.com" } } }),
}));

async function renderHome(): Promise<string> {
  vi.resetModules();
  const { default: Home } = await import("@/app/page");
  return renderToStaticMarkup(createElement(Home));
}

// The page's import graph is large, and its first transform takes seconds.
// Pay it once, outside the budget of each test.
//
// Each render test also resets the modules and imports the page again. That
// takes about 0.5 s alone, and more than 5 s in a full run on a loaded box
// (measured 2026-10-13: 5 to 15 of these timed out at 5 s, a different set
// each run). So each test here gets 30 s.
vi.setConfig({ testTimeout: 30_000 });

beforeAll(async () => {
  await import("@/app/page");
}, 60_000);

afterEach(() => {
  vi.unstubAllEnvs();
  access.features = ["tasks", "projects", "email"];
  access.is_admin = false;
});

describe("/ with the My Day flag", () => {
  it("renders Welcome back, unchanged, with the flag off", async () => {
    vi.stubEnv("NEXT_PUBLIC_MY_DAY", "");
    const html = await renderHome();
    expect(html).toContain("Welcome back");
    expect(html).not.toContain('data-testid="my-day"');
  });

  it("renders My Day with the flag on, cards in the order of the question", async () => {
    vi.stubEnv("NEXT_PUBLIC_MY_DAY", "1");
    const html = await renderHome();
    expect(html).toContain('data-testid="my-day"');
    expect(html).not.toContain("Welcome back");
    const at = (id: string) => html.indexOf(`data-testid="${id}"`);
    expect(at("needs-you")).toBeGreaterThan(-1);
    expect(at("today")).toBeGreaterThan(at("needs-you"));
    expect(at("next-actions")).toBeGreaterThan(at("today"));
    // Each card names its source, and Today hands planning to Calendar.
    expect(html).toContain("Plan my day");
    expect(html).toContain('href="/calendar"');
    expect(html).toContain('href="/tasks?do=capture"');
    // Nothing is read yet: a true miss is a skeleton, never "Loading…".
    expect(html).toContain('aria-busy="true"');
    expect(html).not.toMatch(/Loading…/);
    // The old grid is one tap away.
    expect(html).toContain("All apps");
  });

  it("shows no card for an app the member lacks", async () => {
    vi.stubEnv("NEXT_PUBLIC_MY_DAY", "1");
    access.features = ["email"];
    const html = await renderHome();
    expect(html).toContain('data-testid="needs-you"');
    expect(html).not.toContain('data-testid="today"');
    expect(html).not.toContain('data-testid="next-actions"');
  });

  it("gives a member with My Tasks but not Projects no card that can only fail", async () => {
    // The lens and the feed's task source both need `feature:projects`.
    vi.stubEnv("NEXT_PUBLIC_MY_DAY", "1");
    access.features = ["tasks", "email"];
    const html = await renderHome();
    expect(html).toContain('data-testid="needs-you"');
    expect(html).not.toContain('data-testid="today"');
    expect(html).not.toContain('data-testid="next-actions"');
  });

  it("gives an org admin with only Approvals the Needs you card alone", async () => {
    vi.stubEnv("NEXT_PUBLIC_MY_DAY", "1");
    access.features = ["approvals"];
    access.is_admin = true;
    const html = await renderHome();
    expect(html).toContain('data-testid="needs-you"');
    expect(html).not.toContain('data-testid="today"');
    expect(html).not.toContain('data-testid="next-actions"');
  });

  it("gives a member with only Approvals who is not an admin no Needs you card", async () => {
    // Owner decision, 2026-10-10: only an org admin gets approval rows.
    vi.stubEnv("NEXT_PUBLIC_MY_DAY", "1");
    access.features = ["approvals"];
    const html = await renderHome();
    expect(html).not.toContain('data-testid="needs-you"');
    expect(html).toContain("Nothing is enabled for your account yet");
  });

  it("explains itself to a member who holds nothing", async () => {
    vi.stubEnv("NEXT_PUBLIC_MY_DAY", "1");
    access.features = [];
    const html = await renderHome();
    expect(html).toContain("Nothing is enabled for your account yet");
    expect(html).not.toContain('data-testid="needs-you"');
  });

  it("renders the SERVER branch from the build-time flag only, never the dev override", async () => {
    // The hydration rule. A browser's `cc-my-day` override must not reach
    // the server render, or the page hydrates a different tree. A probe that
    // swaps `myDayOn` in as the server snapshot reads the override here.
    vi.stubEnv("NEXT_PUBLIC_MY_DAY", "");
    vi.stubGlobal("localStorage", { getItem: (k: string) => (k === "cc-my-day" ? "1" : null) });
    try {
      const html = await renderHome();
      expect(html).toContain("Welcome back");
      expect(html).not.toContain('data-testid="my-day"');
    } finally {
      vi.unstubAllGlobals();
    }
    const { readFileSync } = await import("node:fs");
    const page = readFileSync(new URL("../../app/page.tsx", import.meta.url), "utf8");
    expect(page).toMatch(/useSyncExternalStore\(noSubscribe, myDayOn, myDayOnServer\)/);
  });

  it("draws an approval under its own label, opening Approvals, with no act", async () => {
    // NS-3 slice C. The row is the "Waiting for you" card of §4.5, as a
    // group of Needs you. It comes after Overdue and before Due today.
    const { default: NeedsYouCard } = await import("./NeedsYouCard");
    const item = (id: string, kind: NeedsItem["kind"], app: NeedsItem["app"], href: string): NeedsItem => ({
      id,
      app,
      kind,
      title: `Row ${id}`,
      detail: kind === "approval" ? "Proposed by the email assistant agent" : null,
      href,
      at: null,
      act: kind === "approval" ? null : "done",
      act_ref: kind === "approval" ? null : id,
    });
    const items = [
      item("tasks:o", "overdue", "tasks", "/projects?task=o"),
      item("approvals:pa1", "approval", "approvals", "/approvals"),
      item("tasks:d", "due_today", "tasks", "/projects?task=d"),
    ];
    const html = renderToStaticMarkup(
      createElement(NeedsYouCard, {
        now: null,
        needs: {
          items,
          count: items.length,
          total: items.length,
          sources: { tasks: "ok", approvals: "ok" },
          byApp: { tasks: 2, approvals: 1 },
          floors: new Set<never>(),
          loading: false,
          error: null,
          refresh: () => {},
          errors: {},
          act: () => {},
        },
      }),
    );
    const at = (text: string) => html.indexOf(text);
    expect(at("Waiting for your approval")).toBeGreaterThan(at("Overdue"));
    expect(at("Due today")).toBeGreaterThan(at("Waiting for your approval"));
    expect(html).toContain('href="/approvals"');
    expect(html).toContain("Proposed by the email assistant agent");
    // The two task rows keep their Done. The approval row has no act.
    expect(html.match(/data-row-act=""/g)).toHaveLength(2);
  });

  it("never reads the notification list, the bell's read (seams.test.ts)", async () => {
    const { readFileSync } = await import("node:fs");
    for (const file of ["./MyDayPage.tsx", "./NeedsYouCard.tsx", "./needs.ts", "./feed.ts", "./FeedCard.tsx"]) {
      const src = readFileSync(new URL(file, import.meta.url), "utf8");
      expect(src, file).not.toMatch(/notificationsApi\.list\(/);
    }
  });
});

/**
 * The live feed (`my_day_feed.md` §14.1, WS-58 MD-1b done-when 1). With
 * `NEXT_PUBLIC_MY_DAY_FEED` on, My Day is one column of feed cards with
 * Today on the `xl` rail, and Needs you and Next actions are gone. Off, the
 * page above, unchanged.
 *
 * Mutation, run by hand (2026-10-13): make `feedOn` read `myDayFeedOn` as
 * the server snapshot, and the hydration test fails.
 */
describe("/ with the My Day feed flag", () => {
  /** A card of §14.1a, in a group, with its own id. */
  const card = (id: string, group: string) => ({
    signal_id: `tasks:${id}`,
    app: "tasks",
    kind: "overdue",
    folded: [],
    group,
    score: 50,
    title: `Card ${id}`,
    why: `Why ${id}`,
    at: null,
    href: `/projects?task=${id}`,
    acts: [{ id: "done", label: "Done", class: "A", way: "tap", act: "done", act_ref: id }],
  });

  /** Render `/` with this feed answer already in the cache (a revisit). */
  async function renderWithFeed(answer: unknown): Promise<string> {
    vi.resetModules();
    const { put } = await import("@/lib/dataCache");
    const { feedKey, readFeedAnswer } = await import("./feed");
    put(feedKey(), readFeedAnswer(answer));
    const { default: Home } = await import("@/app/page");
    return renderToStaticMarkup(createElement(Home));
  }

  it("renders the feed column, Today on the xl rail, and no Needs you or Next actions", async () => {
    vi.stubEnv("NEXT_PUBLIC_MY_DAY", "1");
    vi.stubEnv("NEXT_PUBLIC_MY_DAY_FEED", "1");
    const html = await renderHome();
    expect(html).toContain('data-testid="my-day-feed"');
    expect(html).toContain('data-testid="today"');
    expect(html).not.toContain('data-testid="needs-you"');
    expect(html).not.toContain('data-testid="next-actions"');
    // Today keeps the right rail from `xl`, beside the column.
    expect(html).toMatch(/data-testid="today"[^>]*class="[^"]*xl:col-start-3/);
    expect(html).toMatch(/data-testid="my-day-feed"[^>]*class="[^"]*xl:col-span-2/);
    // Below `xl` the page is one column, and Today comes FIRST, so "You are
    // caught up" ends the page (visual review, item 6). From `xl` the grid
    // places each one by its start classes, whatever the order.
    expect(html.indexOf('data-testid="today"')).toBeLessThan(html.indexOf('data-testid="my-day-feed"'));
    expect(html).toMatch(/data-testid="my-day-feed"[^>]*class="[^"]*xl:col-start-1 xl:row-start-1/);
    expect(html).toMatch(/data-testid="today"[^>]*class="[^"]*xl:row-start-1/);
    // A true miss is a skeleton, never "Loading…".
    expect(html).toContain('aria-busy="true"');
    expect(html).not.toMatch(/Loading…/);
    // The greeting's frame and the old grid's door stay.
    expect(html).toContain("All apps");
  });

  it("draws the groups in the order of `groups`, and the server's order inside each", async () => {
    vi.stubEnv("NEXT_PUBLIC_MY_DAY", "1");
    vi.stubEnv("NEXT_PUBLIC_MY_DAY_FEED", "1");
    const html = await renderWithFeed({
      cards: [card("a", "needs_you"), card("b", "now"), card("c", "needs_you"), card("d", "fyi")],
      groups: [
        { id: "now", count: 1 },
        { id: "needs_you", count: 2 },
        { id: "fyi", count: 1 },
      ],
      sources: { tasks: "ok" },
    });
    const at = (text: string) => html.indexOf(text);
    expect(at('data-feed-group="now"')).toBeGreaterThan(-1);
    expect(at('data-feed-group="needs_you"')).toBeGreaterThan(at('data-feed-group="now"'));
    expect(at('data-feed-group="fyi"')).toBeGreaterThan(at('data-feed-group="needs_you"'));
    expect(at("Card b")).toBeLessThan(at("Card a"));
    expect(at("Card a")).toBeLessThan(at("Card c"));
    expect(at("Card c")).toBeLessThan(at("Card d"));
    expect(html).toContain("You are caught up.");
  });

  it("shows 20 cards from one answer, and offers the next ones", async () => {
    vi.stubEnv("NEXT_PUBLIC_MY_DAY", "1");
    vi.stubEnv("NEXT_PUBLIC_MY_DAY_FEED", "1");
    const cards = Array.from({ length: 40 }, (_, i) => card(`c${i}`, "needs_you"));
    const html = await renderWithFeed({ cards, groups: [{ id: "needs_you", count: 40 }], sources: { tasks: "ok" } });
    expect(html.match(/data-testid="feed-card-/g)).toHaveLength(20);
    expect(html).toContain("Show 20 more");
    expect(html).not.toContain("You are caught up.");
  });

  it("asks for the feed once, at the default limit", async () => {
    const src = readFileSync(new URL("./FeedCard.tsx", import.meta.url), "utf8");
    expect(src).toMatch(/useCachedResource<FeedAnswer>\(enabled \? feedKey\(\) : null, \(\) => fetchFeed\(\)\)/);
    const page = readFileSync(new URL("./MyDayPage.tsx", import.meta.url), "utf8");
    expect(page.match(/useFeed\(/g)).toHaveLength(1);
  });

  it("renders the page of NS-3, unchanged, with the feed flag off", async () => {
    vi.stubEnv("NEXT_PUBLIC_MY_DAY", "1");
    vi.stubEnv("NEXT_PUBLIC_MY_DAY_FEED", "");
    const html = await renderHome();
    expect(html).not.toContain('data-testid="my-day-feed"');
    expect(html).toContain('data-testid="needs-you"');
    expect(html).toContain('data-testid="next-actions"');
  });

  it("needs My Day: the feed flag alone leaves / as Welcome back", async () => {
    vi.stubEnv("NEXT_PUBLIC_MY_DAY", "");
    vi.stubEnv("NEXT_PUBLIC_MY_DAY_FEED", "1");
    const html = await renderHome();
    expect(html).toContain("Welcome back");
    expect(html).not.toContain('data-testid="my-day-feed"');
  });

  it("renders the SERVER branch from the build-time flag only, never the dev override", async () => {
    vi.stubEnv("NEXT_PUBLIC_MY_DAY", "1");
    vi.stubEnv("NEXT_PUBLIC_MY_DAY_FEED", "");
    vi.stubGlobal("localStorage", { getItem: (k: string) => (k === "cc-my-day-feed" ? "1" : null) });
    try {
      const html = await renderHome();
      expect(html).toContain('data-testid="my-day"');
      expect(html).not.toContain('data-testid="my-day-feed"');
    } finally {
      vi.unstubAllGlobals();
    }
    const page = readFileSync(new URL("./MyDayPage.tsx", import.meta.url), "utf8");
    expect(page).toMatch(/useSyncExternalStore\(noSubscribe, myDayFeedOn, myDayFeedOnServer\)/);
    const flag = readFileSync(new URL("./myDayFlag.ts", import.meta.url), "utf8");
    expect(flag).toContain('process.env.NEXT_PUBLIC_MY_DAY_FEED === "1"');
    expect(flag).toContain('localStorage.getItem("cc-my-day-feed") === "1"');
    // The file keeps no imports, so the sidebar can read it.
    expect(flag).not.toMatch(/^import /m);
  });
});
