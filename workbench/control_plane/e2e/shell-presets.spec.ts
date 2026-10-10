import { expect, test, type Page, type Route } from "@playwright/test";

/**
 * NS-7 in a browser: presets, pins and the first sign-in question
 * (`navigation_shell.md` §8, the owner's table of 2026-10-10).
 *
 *   1. A first visit asks "What will you do most here?". An answer sets the
 *      preset, and the sidebar's "My apps" shows its pins.
 *   2. "Skip for now" is stored, so a reload does not ask again. The role's
 *      preset then fills "My apps".
 *   3. The star in All apps pins an app and unpins it.
 *   4. "Change my layout" in the account menu asks again.
 *   5. A founder's `?welcome=new-org` asks the question first, then shows the
 *      welcome, one dialog at a time.
 *
 * No gateway runs here, so `/api/auth/me/shell` is an in-memory store that
 * answers as the gateway does.
 *
 * Mutation, seen to fail first: make "Skip for now" close the dialog without
 * a write, and case 2 fails, because the reload asks again.
 */

const SESSION = {
  user: { email: "priya@example.com", name: "Priya Shah" },
  expires: "2099-01-01T00:00:00.000Z",
};

const MEMBER = {
  authenticated: true,
  email: "priya@example.com",
  is_active: true,
  is_admin: false,
  features: ["tasks", "projects", "email", "chat", "people", "approvals"],
  permissions: [],
  capabilities: [],
  roles: ["member"],
  organization: { id: "org1", slug: "acme", display_name: "Acme" },
};

const EMPTY = { preset: null, answered: null, pins: null, cardOrder: null, newOrder: null };

type Layout = typeof EMPTY | Record<string, unknown>;

interface Store {
  layout: Layout;
  puts: unknown[];
  gets: number;
  /** Lets a held GET answer. A no-op when the GET is not held. */
  release: () => void;
}

interface StubOptions {
  me?: unknown;
  /** The status every PUT answers with. 503 is a write fault. */
  putStatus?: number;
  /** Hold every GET of the layout until `release()`. */
  holdGet?: boolean;
  /** The browser's copy of the layout, written before the first load. */
  cached?: Layout;
  /** Turn My Day on at `/`. */
  myDay?: boolean;
}

/** The browser copy's key for MEMBER (`shellCache.ts`). */
const CACHE_KEY = "cc-shell-prefs:priya@example.com|org1";

async function stub(page: Page, start: Layout = EMPTY, opts: StubOptions = {}): Promise<Store> {
  let release = () => {};
  const held = new Promise<void>((resolve) => {
    release = resolve;
  });
  const store: Store = { layout: start, puts: [], gets: 0, release: () => release() };
  await page.addInitScript(
    ({ cached, key, myDay }) => {
      localStorage.setItem("cc-shell-bar", "1");
      localStorage.setItem("cc-shell-nav", "1");
      localStorage.removeItem("cc-sidebar-collapsed");
      if (myDay) localStorage.setItem("cc-my-day", "1");
      // Only before the first load: a reload must read what the page wrote.
      if (cached && !sessionStorage.getItem("ns7-seeded")) {
        localStorage.setItem(key, JSON.stringify(cached));
        sessionStorage.setItem("ns7-seeded", "1");
      }
    },
    { cached: opts.cached ?? null, key: CACHE_KEY, myDay: !!opts.myDay },
  );
  // One router, on purpose (`org-branding.spec.ts` says why).
  await page.route("**/api/**", async (route: Route) => {
    const req = route.request();
    const path = new URL(req.url()).pathname;
    const json = (body: unknown, status = 200) =>
      route.fulfill({ status, contentType: "application/json", body: JSON.stringify(body) });
    if (path === "/api/auth/me/shell") {
      if (req.method() === "PUT") {
        const body = req.postDataJSON() as Record<string, unknown> | null;
        store.puts.push(body);
        if (opts.putStatus && opts.putStatus >= 400) return json({ error: "shell_unavailable" }, opts.putStatus);
        store.layout = body === null ? EMPTY : { ...EMPTY, ...body };
        return json(store.layout);
      }
      if (opts.holdGet) await held;
      store.gets += 1;
      return json(store.layout);
    }
    if (path === "/api/auth/me") return json(opts.me ?? MEMBER);
    if (path === "/api/auth/session") return json(SESSION);
    if (path === "/api/health") return json({ gateway: "up" });
    if (path === "/api/accounts") return json({ enabled: false });
    if (path === "/api/settings/branding") return json({ logo: null, updatedBy: "", updatedAt: "" });
    if (path === "/api/shell/needs") return json({ count: 0, total: 0, items: [], sources: {} });
    if (path.startsWith("/api/projects/my/")) return json({ rows: [], total: 0 });
    return json([]);
  });
  return store;
}

const question = (page: Page) => page.getByRole("dialog", { name: "What will you do most here?" });
const myApps = (page: Page) => page.locator('aside [data-nav-section="my-apps"] a');
const sidebar = (page: Page) => page.locator("aside[data-collapsed]");

test.use({ viewport: { width: 1280, height: 860 } });

test("a first visit asks the question, and the answer sets the pins", async ({ page }) => {
  const store = await stub(page);
  await page.goto("/settings/appearance");
  await expect(question(page)).toBeVisible();
  // An answer has the focus, never Close: Enter there would skip.
  await expect(question(page).getByRole("button", { name: /Run the company/ })).toBeFocused();
  await question(page).getByRole("button", { name: /Build and ship the work/ }).click();
  await expect(question(page)).toHaveCount(0);
  expect(store.puts).toEqual([{ ...EMPTY, preset: "engineer", answered: "answered" }]);
  // The Engineer's pins, in the table's order, first in the sidebar.
  await expect(myApps(page)).toHaveText(["My Tasks", "Projects", "Calendar", "Chat"]);
  // A pinned app moves into My apps. It keeps one door.
  await expect(sidebar(page).getByRole("link", { name: "Projects" })).toHaveCount(1);
});

test("Skip for now is stored, and a reload does not ask again", async ({ page }) => {
  const store = await stub(page);
  await page.goto("/settings/appearance");
  await expect(question(page)).toBeVisible();
  await question(page).getByRole("button", { name: "Skip for now" }).click();
  await expect(question(page)).toHaveCount(0);
  expect(store.puts).toEqual([{ ...EMPTY, answered: "skipped" }]);

  const read = page.waitForResponse((r) => r.url().endsWith("/api/auth/me/shell"));
  await page.reload();
  await read;
  await expect(sidebar(page)).toBeVisible();
  // The role picks: a member gets New hire.
  await expect(myApps(page)).toHaveText(["My Tasks", "People", "Chat"]);
  await expect(question(page)).toHaveCount(0);
  expect(store.puts).toHaveLength(1);
});

test("the star in All apps pins an app, and unpins it", async ({ page }) => {
  const store = await stub(page, { ...EMPTY, preset: "engineer", answered: "answered" });
  await page.goto("/settings/appearance");
  await expect(myApps(page)).toHaveCount(4);
  await expect(question(page)).toHaveCount(0);

  await sidebar(page).getByRole("button", { name: "All apps" }).click();
  const launcher = page.getByRole("dialog", { name: "All apps" });
  const star = launcher.getByRole("button", { name: "Pin My Email to My apps" });
  await expect(star).toHaveAttribute("aria-pressed", "false");
  // The star sits in its tile's top right corner. `.cc-control` once moved
  // it under the tile, where it read as the next tile's.
  const tile = await launcher.getByRole("link", { name: /^My Email/ }).boundingBox();
  const at = await star.boundingBox();
  expect(at!.x).toBeGreaterThan(tile!.x + tile!.width / 2);
  expect(at!.x + at!.width).toBeLessThanOrEqual(tile!.x + tile!.width);
  expect(at!.y).toBeGreaterThanOrEqual(tile!.y);
  expect(at!.y + at!.height).toBeLessThan(tile!.y + tile!.height / 2);
  await star.click();
  const unstar = launcher.getByRole("button", { name: "Unpin My Email from My apps" });
  await expect(unstar).toHaveAttribute("aria-pressed", "true");
  expect(store.puts.at(-1)).toMatchObject({ pins: ["/tasks", "/projects", "/calendar", "/chat", "/email"] });
  await page.keyboard.press("Escape");
  await expect(myApps(page)).toHaveText(["My Tasks", "Projects", "Calendar", "Chat", "My Email"]);

  await sidebar(page).getByRole("button", { name: "All apps" }).click();
  await launcher.getByRole("button", { name: "Unpin My Email from My apps" }).click();
  expect(store.puts.at(-1)).toMatchObject({ pins: ["/tasks", "/projects", "/calendar", "/chat"] });
  await page.keyboard.press("Escape");
  await expect(myApps(page)).toHaveCount(4);
});

test("Change my layout in the account menu asks again", async ({ page }) => {
  const store = await stub(page, { ...EMPTY, preset: "engineer", answered: "answered" });
  await page.goto("/settings/appearance");
  await expect(myApps(page)).toHaveCount(4);
  await expect(question(page)).toHaveCount(0);

  // From the keyboard: in the dev build, Next's own badge sits over the foot.
  const foot = page.getByRole("button", { name: /^Accounts: signed in as/ });
  await foot.focus();
  await page.keyboard.press("Enter");
  await page.getByRole("button", { name: "Change my layout" }).click();
  await expect(question(page)).toBeVisible();
  // The way out keeps the layout as it is.
  await expect(question(page).getByRole("button", { name: "Keep my layout" })).toBeVisible();
  await question(page).getByRole("button", { name: /Run the company/ }).click();
  await expect(question(page)).toHaveCount(0);
  expect(store.puts.at(-1)).toEqual({ ...EMPTY, preset: "founder", answered: "answered" });
  // Founder pins Approvals too. A member who holds it sees it.
  await expect(myApps(page)).toHaveText(["Projects", "Approvals", "My Email", "Chat"]);
});

test("a founder's welcome follows the question, one dialog at a time", async ({ page }) => {
  await stub(page, EMPTY, { me: { ...MEMBER, is_admin: true, roles: ["owner"] } });
  await page.goto("/settings/appearance?welcome=new-org");
  await expect(question(page)).toBeVisible();
  await expect(page.getByRole("dialog")).toHaveCount(1);
  await expect(question(page)).toContainText("Your organization is ready");
  await question(page).getByRole("button", { name: /Run the company/ }).click();
  const welcome = page.getByRole("dialog", { name: "Your organization is ready" });
  await expect(welcome).toBeVisible();
  await expect(page.getByRole("dialog")).toHaveCount(1);
  await welcome.getByRole("button", { name: "Explore on my own first" }).click();
  await expect(page.getByRole("dialog")).toHaveCount(0);
  await expect(page).toHaveURL(/\/settings\/appearance$/);
});

// ── Round 2: a write fault never traps the member ───────────────────────────

const SAVE_FAILED = "Couldn't save that. We'll ask again next time.";

test.describe("a failed save never traps the member in the question", () => {
  const ways: Array<[string, (page: Page) => Promise<void>]> = [
    ["Skip for now", (page) => question(page).getByRole("button", { name: "Skip for now" }).click()],
    ["Escape", (page) => page.keyboard.press("Escape")],
    ["the header's X", (page) => question(page).getByRole("button", { name: "Close" }).click()],
  ];
  for (const [name, leave] of ways) {
    test(`${name} closes it, says so once, and does not ask again in this page`, async ({ page }) => {
      const store = await stub(page, EMPTY, { putStatus: 503 });
      await page.goto("/settings/appearance");
      await expect(question(page)).toBeVisible();
      await leave(page);
      await expect(question(page)).toHaveCount(0);
      await expect(page.getByLabel("Notifications").getByText(SAVE_FAILED)).toHaveCount(1);
      expect(store.puts).toHaveLength(1);
      // Another page in the same document: the question stays closed.
      await sidebar(page).getByRole("link", { name: "People" }).click();
      await page.waitForURL("**/people**");
      await expect(question(page)).toHaveCount(0);
      // A later visit asks again, because the server still says "never asked".
      await page.reload();
      await expect(question(page)).toBeVisible();
    });
  }

  test("an answer closes it, and its preset applies for this page", async ({ page }) => {
    await stub(page, EMPTY, { putStatus: 503 });
    await page.goto("/settings/appearance");
    await question(page).getByRole("button", { name: /Build and ship the work/ }).click();
    await expect(question(page)).toHaveCount(0);
    await expect(page.getByLabel("Notifications").getByText(SAVE_FAILED)).toHaveCount(1);
    await expect(myApps(page)).toHaveText(["My Tasks", "Projects", "Calendar", "Chat"]);
  });
});

// ── Round 2: the first frame draws the last known layout ───────────────────

test.describe("a reload draws the last known layout on its first frame", () => {
  const FOUNDER = { ...EMPTY, preset: "founder", answered: "answered" };
  const ENGINEER = { ...EMPTY, preset: "engineer", answered: "answered" };

  test("with a copy, My apps paints before the read answers, then the read wins", async ({ page }) => {
    const store = await stub(page, ENGINEER, { cached: FOUNDER, holdGet: true });
    await page.goto("/settings/appearance");
    await expect(myApps(page)).toHaveText(["Projects", "Approvals", "My Email", "Chat"]);
    expect(store.gets).toBe(0);
    await expect(question(page)).toHaveCount(0);
    store.release();
    await expect(myApps(page)).toHaveText(["My Tasks", "Projects", "Calendar", "Chat"]);
    // The copy now holds what the server said.
    const kept = await page.evaluate((k) => localStorage.getItem(k), CACHE_KEY);
    expect(JSON.parse(kept ?? "{}")).toMatchObject({ preset: "engineer" });
  });

  test("with no copy, there is no My apps until the read answers", async ({ page }) => {
    const store = await stub(page, ENGINEER, { holdGet: true });
    await page.goto("/settings/appearance");
    await expect(sidebar(page).getByRole("link", { name: "People" })).toBeVisible();
    await expect(myApps(page)).toHaveCount(0);
    store.release();
    await expect(myApps(page)).toHaveText(["My Tasks", "Projects", "Calendar", "Chat"]);
  });

  test("a refused write is never kept in the copy", async ({ page }) => {
    await stub(page, ENGINEER, { putStatus: 503 });
    await page.goto("/settings/appearance");
    await expect(myApps(page)).toHaveText(["My Tasks", "Projects", "Calendar", "Chat"]);
    await sidebar(page).getByRole("button", { name: "All apps" }).click();
    await page.getByRole("dialog", { name: "All apps" }).getByRole("button", { name: "Pin My Email to My apps" }).click();
    await expect(page.getByText("Your pin was not saved. Try again.").first()).toBeVisible();
    const kept = await page.evaluate((k) => localStorage.getItem(k), CACHE_KEY);
    expect(JSON.parse(kept ?? "{}").pins ?? null).toBeNull();
  });
});

// ── Round 2: My Day keeps one card order ────────────────────────────────────

test.describe("My Day draws its cards in one order", () => {
  const ENGINEER = { ...EMPTY, preset: "engineer", answered: "answered" };
  const order = (page: Page) =>
    page
      .locator('[data-testid="my-day"] :is([data-testid="needs-you"], [data-testid="today"], [data-testid="next-actions"])')
      .evaluateAll((els) => els.map((e) => e.getAttribute("data-testid")));

  test("with a copy, the member's order on the first frame", async ({ page }) => {
    const store = await stub(page, ENGINEER, { cached: ENGINEER, holdGet: true, myDay: true });
    await page.goto("/");
    await expect(page.getByTestId("next-actions")).toBeVisible();
    expect(await order(page)).toEqual(["next-actions", "today", "needs-you"]);
    expect(store.gets).toBe(0);
    store.release();
  });

  test("with no copy, the cards wait for the layout, then draw the member's order", async ({ page }) => {
    const store = await stub(page, ENGINEER, { holdGet: true, myDay: true });
    await page.goto("/");
    await expect(page.getByTestId("my-day")).toBeVisible();
    await page.waitForTimeout(500);
    expect(await order(page)).toEqual([]);
    store.release();
    await expect(page.getByTestId("next-actions")).toBeVisible();
    expect(await order(page)).toEqual(["next-actions", "today", "needs-you"]);
  });
});
