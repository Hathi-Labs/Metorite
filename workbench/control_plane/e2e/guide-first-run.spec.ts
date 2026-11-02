import { expect, test, type Page, type Route } from "@playwright/test";

/**
 * The first-run tour's entries (WS-OB GD-5a, `assistant_guide.md` §4.1, §7).
 *
 *   a. A founder presses "Show me around" in the welcome step. The dialog
 *      closes at once, the tour runs to "5 of 5", the last stop offers the
 *      apps, and a chip ends the tour and opens its app. One key-only write
 *      stamps `tour_seen_at`.
 *   b. A member opens the account menu and presses "Take the tour". The menu
 *      closes, the tour runs to the end, and End ends it. A reload starts
 *      nothing.
 *   c. A founder who presses "Explore on my own first" gets no tour.
 *   d. With `guide` false, or not in the answer, no entry shows, and
 *      `?guide=home.tour` starts nothing.
 *
 * No gateway runs here. `/api/auth/me/shell` is an in-memory store that
 * merges a key-only write, as `routes/admin/me.py` does, and answers `guide`
 * as the gateway's `GUIDE_ORGS` would.
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
const FOUNDER = { ...MEMBER, is_admin: true, roles: ["owner"] };

const SKIPPED = {
  preset: null,
  answered: "skipped",
  pins: null,
  known: null,
  cardOrder: null,
  newOrder: null,
  chat_first_message_at: null,
  tour_seen_at: null,
};
const SERVER_TIME = "2026-11-02T12:00:00.000Z";

interface Store {
  puts: Record<string, unknown>[];
}

async function stub(page: Page, opts: { me?: unknown; guide?: boolean | "missing" } = {}): Promise<Store> {
  const store: Store = { puts: [] };
  let layout: Record<string, unknown> = { ...SKIPPED };
  const guide = opts.guide ?? true;
  await page.addInitScript(() => {
    localStorage.setItem("cc-shell-bar", "1");
    localStorage.setItem("cc-shell-nav", "1");
    localStorage.setItem("cc-shell-chat", "1");
    localStorage.setItem("cc-shell-chat-first", "1");
    localStorage.setItem("cc-my-day", "1");
    localStorage.removeItem("cc-sidebar-collapsed");
  });
  await page.route("**/api/**", async (route: Route) => {
    const req = route.request();
    const path = new URL(req.url()).pathname;
    const json = (body: unknown, status = 200) =>
      route.fulfill({ status, contentType: "application/json", body: JSON.stringify(body) });
    if (path === "/api/auth/me/shell") {
      if (req.method() === "PUT") {
        const body = (req.postDataJSON() ?? {}) as Record<string, unknown>;
        store.puts.push(body);
        // The gateway merges marks alone, and stamps the tour with its own time.
        layout = { ...layout, ...body, ...(body.tour_seen_at ? { tour_seen_at: SERVER_TIME } : {}) };
      }
      return json(guide === "missing" ? layout : { ...layout, guide });
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

const ring = (page: Page) => page.locator("[data-guide-ring]");
const popover = (page: Page) => page.locator("[data-guide-popover]");
const card = (page: Page) => page.locator("[data-guide-card]");
const stored = (page: Page) => page.evaluate(() => sessionStorage.getItem("metorite.guide.v1"));
const welcome = (page: Page) => page.getByRole("dialog", { name: "Your organization is ready" });
const aside = (page: Page) => page.locator("aside[data-collapsed]");
const accountTrigger = (page: Page) => aside(page).getByRole("button", { name: /^Account and settings: / });
const accountMenu = (page: Page) => page.getByRole("dialog", { name: "Account and settings" });
const tourPuts = (store: Store) => store.puts.filter((p) => "tour_seen_at" in p);

/**
 * The three welcome buttons stand in one column of equal width: no row wraps,
 * so none stands alone (desktop and 390 px).
 */
async function expectCleanButtons(page: Page): Promise<void> {
  const names = ["Explore on my own first", "Show me around", "Invite my team"];
  const boxes = [];
  for (const name of names) boxes.push((await welcome(page).getByRole("button", { name }).boundingBox())!);
  for (let i = 1; i < boxes.length; i++) {
    expect(boxes[i].y).toBeGreaterThan(boxes[i - 1].y + boxes[i - 1].height - 1);
    expect(Math.abs(boxes[i].x - boxes[0].x)).toBeLessThan(2);
    expect(Math.abs(boxes[i].width - boxes[0].width)).toBeLessThan(2);
  }
}

/**
 * Next through the stops, from `from` to 5 of 5. The line shows ONCE. Stops 1
 * and 2 ring a part of the page, and the popover carries the line. From stop 3
 * the side panel is open, so the step card carries it, and the last stop's
 * card also carries the chips. No popover covers either.
 */
async function nextTo5(page: Page, from: number): Promise<void> {
  const line = (i: number) => (i <= 2 ? popover(page) : card(page));
  for (let i = from; i < 5; i++) {
    await expect(line(i)).toContainText(`${i} of 5`);
    await expect(i <= 2 ? card(page) : popover(page)).toHaveCount(0);
    await line(i).getByRole("button", { name: "Next" }).click();
  }
  await expect(card(page)).toContainText("5 of 5");
  await expect(ring(page)).toBeVisible();
  await expect(popover(page)).toHaveCount(0);
}

test.use({ viewport: { width: 1280, height: 860 } });

test("a founder: Show me around closes the welcome, runs to 5 of 5, and a chip opens its app", async ({ page }) => {
  const store = await stub(page, { me: FOUNDER });
  await page.goto("/?welcome=new-org");
  await expect(welcome(page)).toBeVisible();
  // Nothing starts before the founder presses the button (D-GD-1).
  await expect(ring(page)).toHaveCount(0);
  await expectCleanButtons(page);
  await welcome(page).getByRole("button", { name: "Show me around" }).click();

  // The dialog closes at once, by the path of "Explore on my own first".
  await expect(page.getByRole("dialog", { name: "Your organization is ready" })).toHaveCount(0, { timeout: 1_000 });
  await expect(page).toHaveURL(/localhost:\d+\/$/);
  await expect(ring(page)).toBeVisible();
  await expect(popover(page)).toContainText("Your apps live here");

  await nextTo5(page, 1);
  // The last stop: the step card in the side chat offers the member's apps.
  const chips = card(page).locator("[data-guide-chips]");
  await expect(chips).toBeVisible();
  // Every chip is in the window and on top: nothing covers it.
  for (const b of await chips.getByRole("button").all()) {
    const box = await b.boundingBox();
    expect(box).not.toBeNull();
    const top = await page.evaluate(
      ([x, y]) => document.elementFromPoint(x, y)?.closest("[data-guide-chips]") !== null,
      [box!.x + box!.width / 2, box!.y + box!.height / 2],
    );
    expect(top).toBe(true);
  }
  const labels = await chips.getByRole("button").allTextContents();
  expect(labels.length).toBeGreaterThan(0);
  expect(labels.length).toBeLessThanOrEqual(6);
  expect(labels).toContain("Projects");
  // A preview pane, and a pane the member does not hold, give no chip.
  expect(labels).not.toContain("CRM");

  // The tour started once, so one key-only write stamped it.
  expect(tourPuts(store)).toHaveLength(1);
  expect(Object.keys(tourPuts(store)[0])).toEqual(["tour_seen_at"]);

  await chips.getByRole("button", { name: "Projects" }).click();
  // A cold dev server compiles /projects on this first visit.
  await expect(page).toHaveURL(/\/projects$/, { timeout: 30_000 });
  await expect(ring(page)).toHaveCount(0);
  await expect(card(page)).toHaveCount(0);
  expect(await stored(page)).toBeNull();
});

test("a member on another page: Take the tour goes to / and stop 4 rings My Day", async ({ page }) => {
  await stub(page);
  await page.goto("/projects");
  await expect(aside(page)).toBeVisible();
  await accountTrigger(page).click();
  await accountMenu(page).getByRole("button", { name: "Take the tour" }).click();
  await expect(accountMenu(page)).toHaveCount(0);
  // The row navigates to the guide link and does not start the tour in place.
  await expect(page).toHaveURL(/localhost:\d+\/$/);
  await expect(popover(page)).toContainText("1 of 5");

  // Stop 4 rings My Day, which lives on `/`. The tour used to miss it from here.
  await popover(page).getByRole("button", { name: "Next" }).click();
  await popover(page).getByRole("button", { name: "Next" }).click();
  await expect(card(page)).toContainText("3 of 5");
  await card(page).getByRole("button", { name: "Next" }).click();
  await expect(card(page)).toContainText("4 of 5");
  await expect(card(page)).not.toContainText("can't point");
  const target = page.locator('[data-guide="home.my-day"]');
  await expect(target).toBeVisible();
  await expect(ring(page)).toBeVisible();
  const r = (await ring(page).boundingBox())!;
  const t = (await target.boundingBox())!;
  const vp = page.viewportSize()!;
  // The ring is on My Day (its box, held inside the window), and it shows whole.
  expect(Math.abs(r.x - Math.max(4, t.x - 4))).toBeLessThan(3);
  expect(r.x).toBeGreaterThanOrEqual(0);
  expect(r.y).toBeGreaterThanOrEqual(0);
  expect(r.x + r.width).toBeLessThanOrEqual(vp.width);
  expect(r.y + r.height).toBeLessThanOrEqual(vp.height);
});

test("a member: Take the tour in the account menu runs to the end, and a reload starts nothing", async ({ page }) => {
  const store = await stub(page);
  await page.goto("/");
  await expect(page.locator('[data-guide="home.app-list"]')).toBeVisible();
  await accountTrigger(page).click();
  await accountMenu(page).getByRole("button", { name: "Take the tour" }).click();

  // The menu closes, then the tour starts.
  await expect(accountMenu(page)).toHaveCount(0);
  await expect(ring(page)).toBeVisible();
  await nextTo5(page, 1);
  await card(page).getByRole("button", { name: "End tour" }).click();
  await expect(ring(page)).toHaveCount(0);
  expect(await stored(page)).toBeNull();
  expect(tourPuts(store)).toHaveLength(1);

  // A second visit: nothing starts by itself.
  await page.reload();
  await expect(page.locator('[data-guide="home.app-list"]')).toBeVisible();
  await page.waitForTimeout(800);
  await expect(ring(page)).toHaveCount(0);
  expect(await stored(page)).toBeNull();
  // The row stays: the member may take the tour again.
  await accountTrigger(page).click();
  await expect(accountMenu(page).getByRole("button", { name: "Take the tour" })).toBeVisible();
});

test("a founder who explores on their own first gets no tour", async ({ page }) => {
  const store = await stub(page, { me: FOUNDER });
  await page.goto("/?welcome=new-org");
  await expect(welcome(page).getByRole("button", { name: "Show me around" })).toBeVisible();
  await welcome(page).getByRole("button", { name: "Explore on my own first" }).click();
  await expect(page.getByRole("dialog")).toHaveCount(0, { timeout: 1_000 });
  await page.waitForTimeout(800);
  await expect(ring(page)).toHaveCount(0);
  expect(await stored(page)).toBeNull();
  expect(tourPuts(store)).toHaveLength(0);
});

for (const guide of [false, "missing"] as const) {
  test(`with guide ${guide === false ? "false" : "not in the answer"}: no button, no row, and ?guide= starts nothing`, async ({ page }) => {
    const store = await stub(page, { me: FOUNDER, guide });
    await page.goto("/?welcome=new-org");
    await expect(welcome(page)).toBeVisible();
    await expect(welcome(page).getByRole("button", { name: "Explore on my own first" })).toBeVisible();
    await expect(welcome(page).getByRole("button", { name: "Show me around" })).toHaveCount(0);
    await welcome(page).getByRole("button", { name: "Explore on my own first" }).click();

    await accountTrigger(page).click();
    await expect(accountMenu(page).getByRole("button", { name: "Change my layout" })).toBeVisible();
    await expect(accountMenu(page).getByRole("button", { name: "Take the tour" })).toHaveCount(0);
    await page.keyboard.press("Escape");

    await page.goto("/?guide=home.tour");
    await expect(page.locator('[data-guide="home.app-list"]')).toBeVisible();
    await page.waitForTimeout(800);
    await expect(ring(page)).toHaveCount(0);
    // The parameter still leaves the address.
    expect(new URL(page.url()).search).toBe("");
    expect(await stored(page)).toBeNull();
    expect(tourPuts(store)).toHaveLength(0);
  });
}

test.describe("phone", () => {
  test.use({ viewport: { width: 390, height: 844 }, hasTouch: true, isMobile: true });

  test("a founder on a phone runs to 5 of 5, and a chip opens its app with the sheet out of the way", async ({ page }) => {
    await stub(page, { me: FOUNDER });
    await page.goto("/?welcome=new-org");
    await expectCleanButtons(page);
    await welcome(page).getByRole("button", { name: "Show me around" }).click();
    await expect(page.getByRole("dialog", { name: "Your organization is ready" })).toHaveCount(0, { timeout: 1_000 });
    // Stop 1 is under the Menu drawer, stops 2 and 4 use the strip, stop 3 the card.
    const carrier = (i: number) =>
      i === 0 ? popover(page) : i === 1 || i === 3 ? page.locator("[data-guide-strip]") : card(page);
    for (let i = 0; i < 4; i++) {
      await expect(carrier(i)).toContainText(`${i + 1} of 5`);
      await carrier(i).getByRole("button", { name: "Next" }).click();
    }
    await expect(card(page)).toContainText("5 of 5");
    const chips = card(page).locator("[data-guide-chips]");
    await expect(chips).toBeVisible();
    // The strip stays one line: it never draws a chip.
    await expect(page.locator("[data-guide-strip] [data-guide-chips]")).toHaveCount(0);
    await chips.getByRole("button", { name: "Projects" }).click();
    // A cold dev server compiles /projects on this first visit.
    await expect(page).toHaveURL(/\/projects$/, { timeout: 30_000 });
    await expect(ring(page)).toHaveCount(0);
    expect(await page.evaluate(() => document.querySelector('[data-assistant="full"]') === null)).toBe(true);
  });
});
