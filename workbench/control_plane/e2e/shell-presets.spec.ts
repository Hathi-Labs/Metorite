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

/** A stored Engineer layout: Personal Center, and the preset's apps. */
const ENGINEER_STARS = ["/tasks", "/calendar", "/email", "/projects", "/chat"];

/**
 * What an Engineer answer ticks for MEMBER: Personal Center, the preset's
 * apps, and Approvals, which MEMBER holds (`ALWAYS_STARRED`).
 */
const ENGINEER_DEFAULTS = [...ENGINEER_STARS, "/approvals"];

type Layout = typeof EMPTY | Record<string, unknown>;

interface Store {
  layout: Layout;
  puts: unknown[];
  gets: number;
  /** Lets a held GET answer. A no-op when the GET is not held. */
  release: () => void;
  /** Lets a held `/api/auth/me` answer. */
  releaseMe: () => void;
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
  /** Hold `/api/auth/me` until `releaseMe()`. */
  holdMe?: boolean;
}

/** The browser copy's key for MEMBER (`shellCache.ts`). */
const CACHE_KEY = "cc-shell-prefs:priya@example.com|org1";

async function stub(page: Page, start: Layout = EMPTY, opts: StubOptions = {}): Promise<Store> {
  let release = () => {};
  const held = new Promise<void>((resolve) => {
    release = resolve;
  });
  let releaseMe = () => {};
  const meHeld = new Promise<void>((resolve) => {
    releaseMe = resolve;
  });
  const store: Store = { layout: start, puts: [], gets: 0, release: () => release(), releaseMe: () => releaseMe() };
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
    if (path === "/api/auth/me") {
      if (opts.holdMe) await meHeld;
      return json(opts.me ?? MEMBER);
    }
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
  // MEMBER holds Approvals, so every answer ticks it (the approver's door).
  await expect(tick(page, "Approvals")).toBeChecked();
  await expect(tick(page, "People")).not.toBeChecked();
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
      pins: ["/tasks", "/calendar", "/projects", "/people", "/chat", "/approvals"],
      known: KNOWN,
    },
  ]);
  // Each category lists only its ticked apps, under its own heading.
  await expect.poll(() => shape(page)).toEqual([
    ["personal", ["My Tasks", "Calendar"]],
    ["across", ["Projects", "People"]],
    ["studio", ["Chat"]],
    ["admin", ["Approvals"]],
  ]);
});

test("Back on step 2 returns to the question, and saves nothing", async ({ page }) => {
  const store = await stub(page);
  await page.goto("/settings/appearance");
  await question(page).getByRole("button", { name: /Build and ship the work/ }).click();
  await appsStep(page).getByRole("button", { name: "Back" }).click();
  await expect(question(page)).toBeVisible();
  // The focus is on the answer the member chose, never on Close.
  await expect(question(page).getByRole("button", { name: /Build and ship the work/ })).toBeFocused();
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
    { ...EMPTY, preset: "engineer", answered: "answered", pins: ENGINEER_DEFAULTS, known: KNOWN },
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
        ["admin", ["Approvals"]],
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

  test("with no copy, the skeleton holds the place until the read answers", async ({ page }) => {
    const store = await stub(page, ENGINEER, { holdGet: true });
    await page.goto("/settings/appearance");
    await expect(sidebar(page).getByTestId("nav-skeleton")).toBeVisible();
    // No app paints first and then leaves: nothing narrows under the eye.
    await page.waitForTimeout(500);
    expect(await shape(page)).toEqual([]);
    await expect(sidebar(page).getByRole("link", { name: "People" })).toHaveCount(0);
    store.release();
    await expect.poll(() => shape(page)).toEqual([["across", ["Projects"]]]);
    await expect(sidebar(page).getByTestId("nav-skeleton")).toHaveCount(0);
  });

  test("with no copy and no answer, every held app shows after the wait", async ({ page }) => {
    test.setTimeout(60_000);
    await stub(page, ENGINEER, { holdGet: true });
    await page.goto("/settings/appearance");
    await expect(sidebar(page).getByTestId("nav-skeleton")).toBeVisible();
    // SHELL_WAIT_MS, the proxy's own timeout. Then the sidebar is complete.
    await expect(sidebar(page).getByRole("link", { name: "People", exact: true })).toBeVisible({ timeout: 15_000 });
    await expect(sidebar(page).getByTestId("nav-skeleton")).toHaveCount(0);
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

// ── The row menu, and the folds (owner, 2026-10-11) ────────────────────────

test.describe("a row's menu hides an app, and Undo brings it back", () => {
  const STARRED = { ...EMPTY, preset: "engineer", answered: "answered", pins: ENGINEER_STARS, known: KNOWN };

  test("the ··· hides the app, and Undo restores it, with one write each way", async ({ page }) => {
    const store = await stub(page, STARRED);
    await page.goto("/settings/appearance");
    await expect.poll(() => shape(page)).toContainEqual(["across", ["Projects"]]);
    const row = sidebar(page).getByRole("link", { name: "Projects", exact: true });
    // The ··· is a sibling: the link keeps its own name.
    await expect(row).toHaveCount(1);
    await row.hover();
    const more = sidebar(page).getByRole("button", { name: "More for Projects" });
    await expect(more).toBeVisible();
    await more.click();
    await expect(page.getByRole("menu")).toBeVisible();
    // The "···" stays in view while its menu is open, with the pointer gone.
    // Read the paint: Playwright calls an element at opacity 0 "visible".
    await page.mouse.move(900, 600);
    await expect
      .poll(() => more.evaluate((el) => getComputedStyle(el.closest(".rail-row-actions")!).opacity))
      .toBe("1");
    await page.getByRole("menuitem", { name: "Hide from sidebar" }).click();
    await expect.poll(() => store.puts.length).toBe(1);
    // Projects was the last of its category, so the focus goes to the next
    // row: Chat.
    await expect(sidebar(page).getByRole("link", { name: "Chat", exact: true })).toBeFocused();
    expect(store.puts[0]).toMatchObject({ pins: ["/tasks", "/calendar", "/email", "/chat"], known: KNOWN });
    await expect.poll(() => shape(page)).toEqual([
      ["personal", ["My Tasks", "Calendar", "My Email"]],
      ["studio", ["Chat"]],
    ]);
    const toast = page.getByLabel("Notifications");
    await expect(toast.getByText("Projects hidden from your sidebar.")).toBeVisible();
    await toast.getByRole("button", { name: "Undo" }).click();
    await expect.poll(() => store.puts.length).toBe(2);
    expect(store.puts[1]).toMatchObject({ pins: ENGINEER_STARS, known: KNOWN });
    await expect.poll(() => shape(page)).toContainEqual(["across", ["Projects"]]);
  });

  test("a right-click opens the same menu, open or folded, and Home has none", async ({ page }) => {
    await stub(page, STARRED);
    await page.goto("/settings/appearance");
    await expect.poll(() => shape(page)).toContainEqual(["studio", ["Chat"]]);
    await sidebar(page).getByRole("link", { name: "Chat", exact: true }).click({ button: "right" });
    await expect(page.getByRole("menuitem", { name: "Hide from sidebar" })).toBeVisible();
    await expect(page.getByRole("menuitem", { name: "Open in new tab" })).toBeVisible();
    await page.keyboard.press("Escape");
    await expect(page.getByRole("menuitem", { name: "Hide from sidebar" })).toHaveCount(0);
    // Home is always there, so it has no Hide.
    await sidebar(page).getByRole("link", { name: /^(Home|My Day)$/ }).click({ button: "right" });
    await expect(page.getByRole("menuitem", { name: "Hide from sidebar" })).toHaveCount(0);
    await expect(sidebar(page).getByRole("button", { name: /^More for (Home|My Day)$/ })).toHaveCount(0);

    // Folded: no ···, and a right-click still opens the menu.
    await page.getByRole("button", { name: "Collapse sidebar" }).first().click();
    await expect(sidebar(page)).toHaveAttribute("data-collapsed", "true");
    await expect(sidebar(page).locator("[data-row-more]")).toHaveCount(0);
    await sidebar(page).locator('a[href="/chat"]').click({ button: "right" });
    await expect(page.getByRole("menuitem", { name: "Hide from sidebar" })).toBeVisible();
  });

  test("from the keyboard: the ··· opens with Enter, and Escape closes it and returns the focus", async ({ page }) => {
    await stub(page, STARRED);
    await page.goto("/settings/appearance");
    await expect.poll(() => shape(page)).toContainEqual(["across", ["Projects"]]);
    await sidebar(page).getByRole("link", { name: "Projects", exact: true }).focus();
    await page.keyboard.press("Tab");
    const more = sidebar(page).getByRole("button", { name: "More for Projects" });
    await expect(more).toBeFocused();
    await expect(more).toBeVisible();
    await page.keyboard.press("Enter");
    await expect(page.getByRole("menuitem", { name: "Hide from sidebar" })).toBeFocused();
    // The arrow keys move through the menu, and wrap.
    await page.keyboard.press("ArrowDown");
    await expect(page.getByRole("menuitem", { name: "Open in new tab" })).toBeFocused();
    await page.keyboard.press("ArrowDown");
    await expect(page.getByRole("menuitem", { name: "Hide from sidebar" })).toBeFocused();
    await page.keyboard.press("Escape");
    await expect(page.getByRole("menuitem", { name: "Hide from sidebar" })).toHaveCount(0);
    await expect(more).toBeFocused();
    await page.keyboard.press("Space");
    await expect(page.getByRole("menuitem", { name: "Hide from sidebar" })).toBeFocused();
  });
});

test.describe("a category folds, per account", () => {
  const STARRED = { ...EMPTY, preset: "engineer", answered: "answered", pins: ENGINEER_STARS, known: KNOWN };
  const heading = (page: Page, id: string) => sidebar(page).locator(`[data-nav-section="${id}"] > button`);

  test("a fold shows the count, survives a reload, and toggles from the keyboard", async ({ page }) => {
    await stub(page, STARRED);
    await page.goto("/settings/appearance");
    await expect.poll(() => shape(page)).toContainEqual(["personal", ["My Tasks", "Calendar", "My Email"]]);
    const personal = heading(page, "personal");
    await expect(personal).toHaveAttribute("aria-expanded", "true");
    await personal.focus();
    await page.keyboard.press("Enter");
    await expect(personal).toHaveAttribute("aria-expanded", "false");
    await expect(personal).toHaveText(/Personal Center \(3\)/);
    await expect.poll(() => shape(page)).toContainEqual(["personal", []]);
    // Kept per account, so a reload keeps it.
    await page.reload();
    await expect(heading(page, "personal")).toHaveAttribute("aria-expanded", "false");
    await expect(heading(page, "personal")).toHaveText(/Personal Center \(3\)/);
    await heading(page, "personal").focus();
    await page.keyboard.press("Space");
    await expect(heading(page, "personal")).toHaveAttribute("aria-expanded", "true");
  });

  test("another account's folds never apply", async ({ page }) => {
    await page.addInitScript(() => {
      localStorage.setItem("cc-sidebar-folds:ben@beta.example|org2", JSON.stringify({ personal: true }));
      localStorage.setItem("cc-sidebar-folds:priya@example.com|other-org", JSON.stringify({ personal: true }));
    });
    await stub(page, STARRED);
    await page.goto("/settings/appearance");
    await expect.poll(() => shape(page)).toContainEqual(["personal", ["My Tasks", "Calendar", "My Email"]]);
    await expect(heading(page, "personal")).toHaveAttribute("aria-expanded", "true");
    // Priya folds AI Studio. Then another member signs in on this browser:
    // Ben sees AI Studio open, because Priya's folds are hers.
    await heading(page, "studio").click();
    await expect(heading(page, "studio")).toHaveAttribute("aria-expanded", "false");
    await stub(page, STARRED, {
      me: { ...MEMBER, email: "ben@beta.example", organization: { id: "org2", slug: "beta", display_name: "Beta" } },
    });
    await page.reload();
    await expect.poll(() => shape(page)).toContainEqual(["studio", ["Chat"]]);
    await expect(heading(page, "studio")).toHaveAttribute("aria-expanded", "true");
  });

  test("the category of the current route opens, whatever was stored", async ({ page }) => {
    // Once per test, not on every load: the page must keep what it writes.
    await page.addInitScript(() => {
      if (sessionStorage.getItem("folds-seeded")) return;
      sessionStorage.setItem("folds-seeded", "1");
      localStorage.setItem("cc-sidebar-folds:priya@example.com|org1", JSON.stringify({ across: true, studio: true }));
    });
    await stub(page, { ...STARRED, pins: [...ENGINEER_STARS, "/people"] });
    await page.goto("/settings/appearance");
    await expect(heading(page, "across")).toHaveAttribute("aria-expanded", "false");
    await page.goto("/people");
    // AI Studio is the witness: once it reads folded, the stored folds have
    // loaded, so an open Apps is the rule and not the first frame.
    await expect(heading(page, "studio")).toHaveAttribute("aria-expanded", "false");
    await expect(heading(page, "across")).toHaveAttribute("aria-expanded", "true");
    await expect(sidebar(page).getByRole("link", { name: "People", exact: true })).toBeVisible();
    // While the member is on one of its apps, its fold is off, so a press
    // never seems to do nothing.
    await expect(heading(page, "across")).toHaveAttribute("aria-disabled", "true");
    // ONE press: two would cancel out, and prove nothing.
    await heading(page, "across").focus();
    await page.keyboard.press("Enter");
    await expect(heading(page, "across")).toHaveAttribute("aria-expanded", "true");
    await expect(heading(page, "studio")).not.toHaveAttribute("aria-disabled", "true");
    // The presses stored nothing: away from People, Apps is still open.
    await page.goto("/settings/appearance");
    await expect(heading(page, "studio")).toHaveAttribute("aria-expanded", "false");
    await expect(heading(page, "across")).toHaveAttribute("aria-expanded", "true");
  });
});

// ── Fix round, 2026-10-11 ───────────────────────────────────────────────────

test("asked again, Escape on step 2 changes nothing and writes nothing", async ({ page }) => {
  const STARS = { ...EMPTY, preset: "engineer", answered: "answered", pins: ["/projects"], known: KNOWN };
  const store = await stub(page, STARS);
  await page.goto("/settings/appearance");
  await expect.poll(() => shape(page)).toEqual([["across", ["Projects"]]]);
  const foot = page.getByRole("button", { name: /^Accounts: signed in as/ });
  await foot.focus();
  await page.keyboard.press("Enter");
  await page.getByRole("button", { name: "Change my layout" }).click();
  // Another answer, whose defaults differ from the stars.
  await question(page).getByRole("button", { name: /Run the company/ }).click();
  await expect(appsStep(page)).toBeVisible();
  await page.keyboard.press("Escape");
  await expect(appsStep(page)).toHaveCount(0);
  await page.waitForTimeout(300);
  expect(store.puts).toEqual([]);
  await expect.poll(() => shape(page)).toEqual([["across", ["Projects"]]]);
});

test("the question waits for access, so step 2 never lists nothing", async ({ page }) => {
  const store = await stub(page, EMPTY, { holdMe: true });
  await page.goto("/settings/appearance");
  // The layout read answered "never asked", and access is still out.
  await expect.poll(() => store.gets).toBeGreaterThan(0);
  await page.waitForTimeout(500);
  await expect(question(page)).toHaveCount(0);
  store.releaseMe();
  await question(page).getByRole("button", { name: /Build and ship the work/ }).click();
  await expect(tick(page, "Projects")).toBeChecked();
});

test("the row's link keeps the whole row at rest", async ({ page }) => {
  await stub(page, { ...EMPTY, preset: "engineer", answered: "answered", pins: ENGINEER_STARS, known: KNOWN });
  await page.goto("/settings/appearance");
  await expect.poll(() => shape(page)).toContainEqual(["across", ["Projects"]]);
  await page.mouse.move(900, 600);
  const row = sidebar(page).locator('[data-nav-row="/projects"]');
  const link = row.getByRole("link", { name: "Projects", exact: true });
  const r = (await row.boundingBox())!;
  const l = (await link.boundingBox())!;
  // The "···" takes no width at rest (`rail-row-actions`).
  expect(Math.abs(r.width - l.width)).toBeLessThanOrEqual(1);
});

// The owner's production screenshot of 2026-10-11: NS-7 stored a starred
// Chat, and the sidebar drew it under "MY APPS".
test("a production NS-7 row with Chat pinned: Chat sits under AI Studio, and every app shows", async ({ page }) => {
  const NS7 = { preset: "founder", answered: "answered", pins: ["/chat"], cardOrder: null, newOrder: null };
  const store = await stub(page, NS7);
  await page.goto("/settings/appearance");
  // No `known`, so every held app shows, each under its own category.
  await expect.poll(() => shape(page)).toEqual([
    ["personal", ["My Tasks", "Calendar", "My Email"]],
    ["across", ["Projects", "People"]],
    ["studio", ["Chat"]],
    ["admin", ["Approvals"]],
  ]);
  await expect(sidebar(page).getByText(/my apps/i)).toHaveCount(0);

  // Hide Chat in All apps: it leaves AI Studio, and nothing else moves.
  await sidebar(page).getByRole("button", { name: "All apps" }).click();
  const launcher = page.getByRole("dialog", { name: "All apps" });
  await launcher.getByRole("button", { name: "Hide Chat from sidebar" }).click();
  expect(store.puts.at(-1)).toMatchObject({
    pins: ["/tasks", "/calendar", "/email", "/projects", "/people", "/approvals"],
    known: KNOWN,
  });
  await page.keyboard.press("Escape");
  await expect.poll(() => shape(page)).toEqual([
    ["personal", ["My Tasks", "Calendar", "My Email"]],
    ["across", ["Projects", "People"]],
    ["admin", ["Approvals"]],
  ]);
  // Show it again: back under AI Studio.
  await sidebar(page).getByRole("button", { name: "All apps" }).click();
  await launcher.getByRole("button", { name: "Show Chat in sidebar" }).click();
  await page.keyboard.press("Escape");
  await expect.poll(() => shape(page)).toContainEqual(["studio", ["Chat"]]);
  await expect(sidebar(page).getByText(/my apps/i)).toHaveCount(0);
});

test("every category's apps can be hidden: Personal Center, AI Studio and Admin too", async ({ page }) => {
  await stub(page, { ...EMPTY, preset: "founder", answered: "answered", pins: KNOWN, known: KNOWN });
  await page.goto("/settings/appearance");
  await sidebar(page).getByRole("button", { name: "All apps" }).click();
  const launcher = page.getByRole("dialog", { name: "All apps" });
  for (const name of ["My Tasks", "Calendar", "My Email", "Chat", "Approvals"]) {
    await launcher.getByRole("button", { name: `Hide ${name} from sidebar` }).click();
  }
  await page.keyboard.press("Escape");
  await expect.poll(() => shape(page)).toEqual([["across", ["Projects", "People"]]]);
});

test("Undo puts back the layout from before the hide, exactly", async ({ page }) => {
  // `known` names an app MEMBER does not hold. A second toggle would write
  // `known` as the held apps only, and so lose it. A restore keeps it.
  const BEFORE = { ...EMPTY, preset: "engineer", answered: "answered", pins: ENGINEER_STARS, known: [...KNOWN, "/crm"] };
  const store = await stub(page, BEFORE);
  await page.goto("/settings/appearance");
  await expect.poll(() => shape(page)).toContainEqual(["across", ["Projects"]]);
  await sidebar(page).getByRole("link", { name: "Projects", exact: true }).click({ button: "right" });
  await page.getByRole("menuitem", { name: "Hide from sidebar" }).click();
  await expect.poll(() => store.puts.length).toBe(1);
  await page.getByLabel("Notifications").getByRole("button", { name: "Undo" }).click();
  await expect.poll(() => store.puts.length).toBe(2);
  expect(store.puts[1]).toEqual(BEFORE);
});
