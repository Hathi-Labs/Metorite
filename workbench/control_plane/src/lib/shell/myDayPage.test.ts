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
import { createElement } from "react";
import { renderToStaticMarkup } from "react-dom/server";
import { afterEach, beforeAll, describe, expect, it, vi } from "vitest";

import { NO_ACCESS } from "@/lib/access";

const access = { features: ["tasks", "projects", "email"] as string[] };

vi.mock("@/components/AccessProvider", () => ({
  useAccess: () => ({
    access: { ...NO_ACCESS, authenticated: true, features: access.features },
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
// Pay it once, outside the five-second budget of each test.
beforeAll(async () => {
  await import("@/app/page");
}, 60_000);

afterEach(() => {
  vi.unstubAllEnvs();
  access.features = ["tasks", "projects", "email"];
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

  it("never reads the notification list, the bell's read (seams.test.ts)", async () => {
    const { readFileSync } = await import("node:fs");
    for (const file of ["./MyDayPage.tsx", "./NeedsYouCard.tsx", "./needs.ts"]) {
      const src = readFileSync(new URL(file, import.meta.url), "utf8");
      expect(src, file).not.toMatch(/notificationsApi\.list\(/);
    }
  });
});
