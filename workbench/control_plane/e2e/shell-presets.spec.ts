import { expect, test, type Page, type Route } from "@playwright/test";

/**
 * NS-7 in a browser: presets, stars and the first sign-in question
 * (`navigation_shell.md` §8, and the star model of 2026-10-11, §3.2).
 *
 *   1. A first visit asks "What will you do most here?". An answer opens
 *      step 2, "Choose the apps for your sidebar", with the defaults ticked.
 *      "Done" saves the answer and the stars in ONE write, and each category
 *      of the sidebar then lists only its ticked apps.
 *   2. "Skip for now" is stored, so a reload does not ask again. Every held
 *      app then shows.
 *   3. The star in All apps shows an app under its category, or hides it.
 *      A category with nothing starred is gone.
 *   4. "Add apps" at the end of the sidebar opens All apps with a hint.
 *   5. "Change my layout" in the account menu asks again.
 *   6. A founder's `?welcome=new-org` goes question, apps, invite, one
 *      dialog at a time.
 *   7. The phone drawer follows the same model.
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

const EMPTY = { preset: null, answered: null, pins: null, known: null, cardOrder: null, newOrder: null };

/** Every app MEMBER may star, in nav.ts order: the `known` of each save. */
const KNOWN = ["/tasks", "/calendar", "/email", "/projects", "/people", "/chat", "/approvals"];

/** The Engineer's defaults for MEMBER: Personal Center, and the preset's apps. */
const ENGINEER_STARS = ["/tasks", "/calendar", "/email", "/projects", "/chat"];

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
const appsStep = (page: Page) => page.getByRole("dialog", { name: "Choose the apps for your sidebar" });
const sidebar = (page: Page) => page.locator("aside[data-collapsed]");

/** The sidebar's categories, in order, each with its app names. */
async function shape(page: Page, root = sidebar(page)): Promise<[string, string[]][]> {
  return root.locator("[data-nav-section]").evaluateAll((els) =>
    els.map((el) => [
      el.getAttribute("data-nav-section") ?? "",
      [...el.querySelectorAll("a")].map((a) => a.textContent?.trim() ?? ""),
    ]),
  );
}

const tick = (page: Page, name: string) => appsStep(page).getByRole("checkbox", { name });

test.use({ viewport: { width: 1280, height: 860 } });

test("a first visit asks, an answer ticks the defaults, and Done saves the ticks in one write", async ({ page }) => {
  const store = await stub(page);
  await page.goto("/settings/appearance");
  await expect(question(page)).toBeVisible();
  // An answer has the focus, never Close: Enter there would skip.
  await expect(question(page).getByRole("button", { name: /Run the company/ })).toBeFocused();
  await question(page).getByRole("button", { name: /Build and ship the work/ }).click();

  // Step 2, in the same dialog, with the Engineer's defaults ticked.
  await expect(appsStep(page)).toBeVisible();
  await expect(question(page)).toHaveCount(0);
  expect(store.puts).toEqual([]);
  for (const name of ["My Tasks", "Calendar", "My Email", "Projects", "Chat"]) await expect(tick(page, name)).toBeChecked();
  for (const name of ["People", "Approvals"]) await expect(tick(page, name)).not.toBeChecked();
  // The settings pages are not apps, so they are not in the list.
  await expect(tick(page, "My Profile")).toHaveCount(0);
  await expect(tick(page, "Appearance")).toHaveCount(0);
  // It says where to change this later, and names the real controls.
  const later = appsStep(page).getByTestId("layout-apps-later");
  await expect(later).toContainText("All apps");
  await expect(later).toContainText("+ Add apps");
  await expect(later).toContainText("Change my layout");

  // Untick one, tick another.
  await tick(page, "My Email").click();
  await tick(page, "People").click();
  await appsStep(page).getByRole("button", { name: "Done" }).click();
  await expect(appsStep(page)).toHaveCount(0);
  expect(store.puts).toEqual([
    {
      ...EMPTY,
      preset: "engineer",
      answered: "answered",
      pins: ["/tasks", "/calendar", "/projects", "/people", "/chat"],
      known: KNOWN,
    },
  ]);
  // Each category lists only its ticked apps. Admin has none, so it is gone.
  await expect.poll(() => shape(page)).toEqual([
    ["personal", ["My Tasks", "Calendar"]],
    ["across", ["Projects", "People"]],
    ["studio", ["Chat"]],
  ]);
});

test("Back on step 2 returns to the question, and saves nothing", async ({ page }) => {
  const store = await stub(page);
  await page.goto("/settings/appearance");
  await question(page).getByRole("button", { name: /Build and ship the work/ }).click();
  await appsStep(page).getByRole("button", { name: "Back" }).click();
  await expect(question(page)).toBeVisible();
  expect(store.puts).toEqual([]);
});

test("the X on step 2 keeps the answer, with the ticks it started with", async ({ page }) => {
  const store = await stub(page);
  await page.goto("/settings/appearance");
  await question(page).getByRole("button", { name: /Build and ship the work/ }).click();
  await tick(page, "People").click();
  await appsStep(page).getByRole("button", { name: "Close" }).click();
  await expect(appsStep(page)).toHaveCount(0);
  expect(store.puts).toEqual([
    { ...EMPTY, preset: "engineer", answered: "answered", pins: ENGINEER_STARS, known: KNOWN },
  ]);
});

test("Skip for now is stored, a reload does not ask again, and every app shows", async ({ page }) => {
  const store = await stub(page);
  await page.goto("/settings/appearance");
  await expect(question(page)).toBeVisible();
  await question(page).getByRole("button", { name: "Skip for now" }).click();
  await expect(question(page)).toHaveCount(0);
  await expect(appsStep(page)).toHaveCount(0);
  expect(store.puts).toEqual([{ ...EMPTY, answered: "skipped" }]);

  const read = page.waitForResponse((r) => r.url().endsWith("/api/auth/me/shell"));
  await page.reload();
  await read;
  await expect(sidebar(page)).toBeVisible();
  // Personal Center first, and every category the member holds an app in.
  await expect.poll(() => shape(page)).toEqual([
    ["personal", ["My Tasks", "Calendar", "My Email"]],
    ["across", ["Projects", "People"]],
    ["studio", ["Chat"]],
    ["admin", ["Approvals"]],
  ]);
  await expect(question(page)).toHaveCount(0);
  expect(store.puts).toHaveLength(1);
});

test("the star in All apps shows an app under its category, and hides it", async ({ page }) => {
  const store = await stub(page, { ...EMPTY, preset: "engineer", answered: "answered", pins: ENGINEER_STARS, known: KNOWN });
  await page.goto("/settings/appearance");
  await expect.poll(() => shape(page)).toEqual([
    ["personal", ["My Tasks", "Calendar", "My Email"]],
    ["across", ["Projects"]],
    ["studio", ["Chat"]],
  ]);
  await expect(question(page)).toHaveCount(0);

  await sidebar(page).getByRole("button", { name: "All apps" }).click();
  const launcher = page.getByRole("dialog", { name: "All apps" });
  // Opened from the foot, it shows no hint.
  await expect(launcher.getByTestId("launcher-hint")).toHaveCount(0);
  const star = launcher.getByRole("button", { name: "Show People in sidebar" });
  await expect(star).toHaveAttribute("aria-pressed", "false");
  await expect(star).toHaveAttribute("title", "Show in sidebar");
  // The star sits in its tile's top right corner. `.cc-control` once moved
  // it under the tile, where it read as the next tile's.
  const tile = await launcher.getByRole("link", { name: /^People/ }).boundingBox();
  const at = await star.boundingBox();
  expect(at!.x).toBeGreaterThan(tile!.x + tile!.width / 2);
  expect(at!.x + at!.width).toBeLessThanOrEqual(tile!.x + tile!.width);
  expect(at!.y).toBeGreaterThanOrEqual(tile!.y);
  expect(at!.y + at!.height).toBeLessThan(tile!.y + tile!.height / 2);
  // A personal app has a star too: it is starred, and it can be hidden.
  await expect(launcher.getByRole("button", { name: "Hide My Email from sidebar" })).toHaveAttribute("aria-pressed", "true");
  await star.click();
  const unstar = launcher.getByRole("button", { name: "Hide People from sidebar" });
  await expect(unstar).toHaveAttribute("aria-pressed", "true");
  await expect(unstar).toHaveAttribute("title", "Shown in sidebar");
  expect(store.puts.at(-1)).toMatchObject({ pins: ["/tasks", "/calendar", "/email", "/projects", "/people", "/chat"], known: KNOWN });
  await page.keyboard.press("Escape");
  // People shows under its own category, in nav.ts order: never moved.
  await expect.poll(() => shape(page)).toEqual([
    ["personal", ["My Tasks", "Calendar", "My Email"]],
    ["across", ["Projects", "People"]],
    ["studio", ["Chat"]],
  ]);

  // Hide People again, and hide Chat: AI Studio has nothing left, so it goes.
  await sidebar(page).getByRole("button", { name: "All apps" }).click();
  await launcher.getByRole("button", { name: "Hide People from sidebar" }).click();
  await launcher.getByRole("button", { name: "Hide Chat from sidebar" }).click();
  expect(store.puts.at(-1)).toMatchObject({ pins: ["/tasks", "/calendar", "/email", "/projects"] });
  await page.keyboard.press("Escape");
  await expect.poll(() => shape(page)).toEqual([
    ["personal", ["My Tasks", "Calendar", "My Email"]],
    ["across", ["Projects"]],
  ]);
});

test("Add apps ends the sidebar, opens All apps with the hint, and lines up with the rows", async ({ page }) => {
  await stub(page, { ...EMPTY, preset: "engineer", answered: "answered", pins: [], known: KNOWN });
  await page.goto("/settings/appearance");
  // Nothing starred: no category at all, and Add apps still shows.
  await expect.poll(() => shape(page)).toEqual([]);
  const add = sidebar(page).getByRole("button", { name: "Add apps" });
  await expect(add).toBeVisible();
  await add.click();
  const launcher = page.getByRole("dialog", { name: "All apps" });
  await expect(launcher.getByTestId("launcher-hint")).toHaveText("Star the apps you want in your sidebar.");
  await launcher.getByRole("button", { name: "Show Projects in sidebar" }).click();
  await page.keyboard.press("Escape");
  await expect.poll(() => shape(page)).toEqual([["across", ["Projects"]]]);

  // Its icon sits on the same centre line as the row above it, open and folded.
  const centre = async (el: ReturnType<Page["locator"]>) => {
    const box = await el.locator("svg").first().boundingBox();
    return box!.x + box!.width / 2;
  };
  const rowAbove = sidebar(page).locator('[data-nav-section="across"] a').last();
  expect(Math.abs((await centre(add)) - (await centre(rowAbove)))).toBeLessThanOrEqual(1);
  await page.getByRole("button", { name: "Collapse sidebar" }).first().click();
  await expect(sidebar(page)).toHaveAttribute("data-collapsed", "true");
  const folded = sidebar(page).getByRole("button", { name: "Add apps" });
  await expect(folded).toHaveAttribute("title", "Add apps");
  await expect.poll(async () => Math.abs((await centre(folded)) - (await centre(rowAbove)))).toBeLessThanOrEqual(1);
});

test("Change my layout asks again: the same answer keeps the stars, a new one gives its defaults", async ({ page }) => {
  const store = await stub(page, { ...EMPTY, preset: "engineer", answered: "answered", pins: ["/projects"], known: KNOWN });
  await page.goto("/settings/appearance");
  await expect.poll(() => shape(page)).toEqual([["across", ["Projects"]]]);

  const openMenu = async () => {
    // From the keyboard: in the dev build, Next's own badge sits over the foot.
    const foot = page.getByRole("button", { name: /^Accounts: signed in as/ });
    await foot.focus();
    await page.keyboard.press("Enter");
    await page.getByRole("button", { name: "Change my layout" }).click();
  };
  await openMenu();
  await expect(question(page)).toBeVisible();
  // The way out keeps the layout as it is.
  await expect(question(page).getByRole("button", { name: "Keep my layout" })).toBeVisible();
  // The same answer: the member's current stars, ticked.
  await question(page).getByRole("button", { name: /Build and ship the work/ }).click();
  await expect(tick(page, "Projects")).toBeChecked();
  await expect(tick(page, "My Tasks")).not.toBeChecked();
  // Back, and a different answer: its defaults replace them.
  await appsStep(page).getByRole("button", { name: "Back" }).click();
  await question(page).getByRole("button", { name: /Run the company/ }).click();
  for (const name of ["My Tasks", "Calendar", "My Email", "Projects", "Chat", "Approvals"]) await expect(tick(page, name)).toBeChecked();
  await expect(tick(page, "People")).not.toBeChecked();
  await appsStep(page).getByRole("button", { name: "Done" }).click();
  expect(store.puts.at(-1)).toEqual({
    ...EMPTY,
    preset: "founder",
    answered: "answered",
    pins: ["/tasks", "/calendar", "/email", "/projects", "/chat", "/approvals"],
    known: KNOWN,
  });
  await expect.poll(() => shape(page)).toEqual([
    ["personal", ["My Tasks", "Calendar", "My Email"]],
    ["across", ["Projects"]],
    ["studio", ["Chat"]],
    ["admin", ["Approvals"]],
  ]);
});

test("a founder's welcome follows the question and the apps, one dialog at a time", async ({ page }) => {
  await stub(page, EMPTY, { me: { ...MEMBER, is_admin: true, roles: ["owner"] } });
  await page.goto("/settings/appearance?welcome=new-org");
  await expect(question(page)).toBeVisible();
  await expect(page.getByRole("dialog")).toHaveCount(1);
  await expect(question(page)).toContainText("Your organization is ready");
  await question(page).getByRole("button", { name: /Run the company/ }).click();
  await expect(appsStep(page)).toBeVisible();
  await expect(page.getByRole("dialog")).toHaveCount(1);
  await appsStep(page).getByRole("button", { name: "Done" }).click();
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

  const appsWays: Array<[string, (page: Page) => Promise<void>]> = [
    ["Done", (page) => appsStep(page).getByRole("button", { name: "Done" }).click()],
    ["the header's X", (page) => appsStep(page).getByRole("button", { name: "Close" }).click()],
    ["Escape", (page) => page.keyboard.press("Escape")],
  ];
  for (const [name, leave] of appsWays) {
    test(`step 2's ${name} closes it, says so once, and the answer applies for this page`, async ({ page }) => {
      const store = await stub(page, EMPTY, { putStatus: 503 });
      await page.goto("/settings/appearance");
      await question(page).getByRole("button", { name: /Build and ship the work/ }).click();
      await expect(appsStep(page)).toBeVisible();
      await leave(page);
      await expect(appsStep(page)).toHaveCount(0);
      await expect(question(page)).toHaveCount(0);
      await expect(page.getByLabel("Notifications").getByText(SAVE_FAILED)).toHaveCount(1);
      expect(store.puts).toHaveLength(1);
      await expect.poll(() => shape(page)).toEqual([
        ["personal", ["My Tasks", "Calendar", "My Email"]],
        ["across", ["Projects"]],
        ["studio", ["Chat"]],
      ]);
    });
  }
});

// ── Round 2: the first frame draws the last known layout ───────────────────

test.describe("a reload draws the last known layout on its first frame", () => {
  const FOUNDER = { ...EMPTY, preset: "founder", answered: "answered", pins: ["/approvals"], known: KNOWN };
  const ENGINEER = { ...EMPTY, preset: "engineer", answered: "answered", pins: ["/projects"], known: KNOWN };

  test("with a copy, the stars paint before the read answers, then the read wins", async ({ page }) => {
    const store = await stub(page, ENGINEER, { cached: FOUNDER, holdGet: true });
    await page.goto("/settings/appearance");
    await expect.poll(() => shape(page)).toEqual([["admin", ["Approvals"]]]);
    expect(store.gets).toBe(0);
    await expect(question(page)).toHaveCount(0);
    store.release();
    await expect.poll(() => shape(page)).toEqual([["across", ["Projects"]]]);
    // The copy now holds what the server said.
    const kept = await page.evaluate((k) => localStorage.getItem(k), CACHE_KEY);
    expect(JSON.parse(kept ?? "{}")).toMatchObject({ preset: "engineer", pins: ["/projects"], known: KNOWN });
  });

  test("with no copy, every held app shows until the read answers", async ({ page }) => {
    const store = await stub(page, ENGINEER, { holdGet: true });
    await page.goto("/settings/appearance");
    await expect(sidebar(page).getByRole("link", { name: "People" })).toBeVisible();
    store.release();
    await expect.poll(() => shape(page)).toEqual([["across", ["Projects"]]]);
  });

  test("a refused write is never kept in the copy", async ({ page }) => {
    await stub(page, ENGINEER, { putStatus: 503 });
    await page.goto("/settings/appearance");
    await expect.poll(() => shape(page)).toEqual([["across", ["Projects"]]]);
    await sidebar(page).getByRole("button", { name: "All apps" }).click();
    await page.getByRole("dialog", { name: "All apps" }).getByRole("button", { name: "Show People in sidebar" }).click();
    await expect(page.getByText("Your sidebar was not saved. Try again.").first()).toBeVisible();
    // The star went back.
    await expect(
      page.getByRole("dialog", { name: "All apps" }).getByRole("button", { name: "Show People in sidebar" }),
    ).toHaveAttribute("aria-pressed", "false");
    const kept = await page.evaluate((k) => localStorage.getItem(k), CACHE_KEY);
    expect(JSON.parse(kept ?? "{}").pins).toEqual(["/projects"]);
  });
});

// ── The phone drawer follows the same model ────────────────────────────────

test.describe("phone", () => {
  test.use({ viewport: { width: 390, height: 844 }, hasTouch: true, isMobile: true });

  test("the drawer lists only the starred apps, Personal Center first, and ends with Add apps", async ({ page }) => {
    await stub(page, { ...EMPTY, preset: "engineer", answered: "answered", pins: ["/email", "/chat"], known: KNOWN });
    await page.goto("/settings/appearance");
    await page.getByRole("button", { name: "Menu" }).click();
    const drawer = page.locator("aside:not([data-collapsed])");
    await expect.poll(() => shape(page, drawer)).toEqual([
      ["personal", ["My Email"]],
      ["studio", ["Chat"]],
    ]);
    await drawer.getByRole("button", { name: "Add apps" }).click();
    const launcher = page.getByRole("dialog", { name: "All apps" });
    await expect(launcher.getByTestId("launcher-hint")).toHaveText("Star the apps you want in your sidebar.");
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
