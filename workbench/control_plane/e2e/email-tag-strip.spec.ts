import { expect, test, type Page, type Route } from "@playwright/test";

/**
 * The Email quick-filter row: one line, no scrollbar, every chip reachable.
 * Owner report, 2026-10-06: the row ran off the page, and a mouse could not
 * reach the chips past the edge. `src/lib/scrollStrip.ts` holds the rules,
 * `scrollStrip.test.ts` the arithmetic. This file holds the behaviour.
 *
 * Each test is written so that the obvious wrong build fails it:
 *
 *   1. One line, and no scrollbar. A row that wraps, or that shows the
 *      platform scrollbar, fails.
 *   2. The arrows page to the last chip and back, and each edge's arrow shows
 *      only while chips hide behind that edge.
 *   3. A vertical wheel over the row moves it sideways.
 *   4. "All filters" shows every chip at once, and a chip turned on there
 *      scrolls into view in the row.
 *   5. With two chips on, turning on the second keeps the row on it. A build
 *      that reveals the first chip on scrolls back to the start.
 *   6. On a phone, there are no arrows. The row swipes, and "All filters" is
 *      still there.
 */

const ACCOUNT = {
  id: "acc1",
  provider: "microsoft",
  email_address: "me@example.com",
  label: "Work",
  avatar_color: "#6366f1",
  unread_count: 31,
  sync_enabled: true,
  sync_status: "idle",
};

const FOLDERS = [
  { provider_folder_id: "inbox", name: "Inbox", type: "system", message_count: 302, unread_count: 31 },
];

/** Every curated chip, plus six custom rule labels: far wider than the row. */
const FACETS = {
  folder: "inbox",
  total: 302,
  unread: 31,
  uncategorized: 12,
  labels: {
    "needs reply": 3,
    "awaiting reply": 40,
    "follow-up": 5,
    fyi: 118,
    done: 9,
    newsletter: 14,
    marketing: 41,
    receipt: 2,
    calendar: 23,
    notification: 28,
    "cold email": 1,
    "vendor quotes": 7,
    "board updates": 6,
    "hiring pipeline": 5,
    "customer escalations": 4,
    "legal review": 3,
    "travel bookings": 2,
  },
};

async function installMocks(page: Page) {
  const json = (route: Route, body: unknown) =>
    route.fulfill({ status: 200, contentType: "application/json", body: JSON.stringify(body) });
  // Sign-in and access are left to the dev bypass, as in `email-search.spec.ts`.
  await page.route("**/api/health", (r) => json(r, { gateway: "up" }));
  await page.route("**/api/email/accounts", (r) => json(r, [ACCOUNT]));
  await page.route(/.*\/api\/email\/accounts\/[^/]+\/folders.*/, (r) => json(r, FOLDERS));
  await page.route(/.*\/api\/email\/accounts\/[^/]+\/labels.*/, (r) => json(r, []));
  await page.route(/.*\/api\/email\/messages\/facets.*/, (r) => json(r, FACETS));
  await page.route(/.*\/api\/email\/(messages|search)\?.*/, (r) =>
    json(r, { emails: [], total: 0, page: 1, page_size: 50 }),
  );
}

const strip = (page: Page) => page.getByRole("group", { name: "Quick filters" });
const arrow = (page: Page, edge: "start" | "end") => page.locator(`[data-strip-arrow="${edge}"]`);

/** The scroller's own numbers. */
const metrics = (page: Page) =>
  strip(page).evaluate((el) => ({
    left: el.scrollLeft,
    max: el.scrollWidth - el.clientWidth,
    barHeight: (el as HTMLElement).offsetHeight - el.clientHeight,
  }));

test.describe("desktop", () => {
  test.use({ viewport: { width: 1280, height: 800 } });

  test.beforeEach(async ({ page }) => {
    await installMocks(page);
    await page.goto("/email");
    // 13 built-in chips and the 6 custom labels, once the facets land.
    await expect(strip(page).locator(":scope > button")).toHaveCount(19);
  });

  test("one line, and no scrollbar", async ({ page }) => {
    const tops = await strip(page)
      .locator(":scope > button")
      .evaluateAll((els) => els.map((e) => Math.round(e.getBoundingClientRect().top)));
    expect(new Set(tops).size).toBe(1);
    const m = await metrics(page);
    expect(m.max).toBeGreaterThan(0);
    expect(m.barHeight).toBe(0);
  });

  test("the arrows page to the last chip and back", async ({ page }) => {
    await expect(arrow(page, "start")).toHaveCount(0);
    await expect(arrow(page, "end")).toBeVisible();

    for (let i = 0; i < 12 && (await arrow(page, "end").count()) > 0; i++) {
      const before = (await metrics(page)).left;
      await arrow(page, "end").click();
      await expect.poll(async () => (await metrics(page)).left).toBeGreaterThan(before);
      // Let the smooth scroll settle. The arrow leaves at the end, and a click
      // aimed at it mid-scroll waits for a button that is gone.
      await page.waitForTimeout(450);
    }
    await expect(arrow(page, "end")).toHaveCount(0);
    await expect(arrow(page, "start")).toBeVisible();
    const m = await metrics(page);
    expect(m.left).toBeGreaterThanOrEqual(m.max - 1);

    // The last chip is whole, inside the row.
    const row = (await strip(page).boundingBox())!;
    const last = (await strip(page).locator(":scope > button").last().boundingBox())!;
    expect(last.x + last.width).toBeLessThanOrEqual(row.x + row.width + 1);

    for (let i = 0; i < 12 && (await arrow(page, "start").count()) > 0; i++) {
      await arrow(page, "start").click();
      await page.waitForTimeout(450);
    }
    expect((await metrics(page)).left).toBeLessThanOrEqual(1);
    await expect(arrow(page, "start")).toHaveCount(0);
  });

  test("a vertical wheel over the row moves it sideways", async ({ page }) => {
    const box = (await strip(page).boundingBox())!;
    await page.mouse.move(box.x + box.width / 2, box.y + box.height / 2);
    await page.mouse.wheel(0, 300);
    await expect.poll(async () => (await metrics(page)).left).toBeGreaterThan(100);
  });

  test("All filters shows every chip, and a chip turned on there comes into view", async ({ page }) => {
    await page.getByRole("button", { name: /^All filters/ }).click();
    const panel = page.getByRole("dialog", { name: "All filters" });
    await expect(panel).toBeVisible();
    const rowCount = await strip(page).locator(":scope > button").count();
    await expect(panel.getByRole("button")).toHaveCount(rowCount);

    // The last chip is hidden past the end of the row. Turn it on here.
    await panel.getByRole("button", { name: /^Travel Bookings/ }).click();
    await expect(panel.getByRole("button", { name: /^Travel Bookings/ })).toHaveAttribute("aria-pressed", "true");
    await page.keyboard.press("Escape");
    await expect(panel).toHaveCount(0);

    const chip = strip(page).getByRole("button", { name: /^Travel Bookings/ });
    await expect(chip).toHaveAttribute("aria-pressed", "true");
    await expect
      .poll(async () => {
        const row = (await strip(page).boundingBox())!;
        const c = (await chip.boundingBox())!;
        return c.x >= row.x - 1 && c.x + c.width <= row.x + row.width + 1;
      })
      .toBe(true);
    // The badge counts it.
    await expect(page.getByRole("button", { name: /^All filters/ })).toContainText("1");
  });
});

test.describe("two filters on", () => {
  test.use({ viewport: { width: 1280, height: 800 } });

  test("turning on a second chip keeps the row on that chip, not on the first", async ({ page }) => {
    await installMocks(page);
    await page.goto("/email");
    await expect(strip(page).locator(":scope > button")).toHaveCount(19);

    await strip(page).getByRole("button", { name: /^Unread/ }).click();
    await expect(strip(page).getByRole("button", { name: /^Unread/ })).toHaveAttribute("aria-pressed", "true");

    // Page to the end, and turn on the last chip.
    for (let i = 0; i < 12 && (await arrow(page, "end").count()) > 0; i++) {
      await arrow(page, "end").click();
      await page.waitForTimeout(450);
    }
    const last = strip(page).getByRole("button", { name: /^Travel Bookings/ });
    await last.click();
    await expect(last).toHaveAttribute("aria-pressed", "true");
    await page.waitForTimeout(600);

    // The row stays at the end. A build that reveals the FIRST chip on goes back to 0.
    const m = await metrics(page);
    expect(m.left).toBeGreaterThan(m.max / 2);
    const row = (await strip(page).boundingBox())!;
    const c = (await last.boundingBox())!;
    expect(c.x).toBeGreaterThanOrEqual(row.x - 1);
  });
});

test.describe("phone", () => {
  test.use({ viewport: { width: 390, height: 844 }, hasTouch: true, isMobile: true });

  test("no arrows; the row swipes, and All filters is there", async ({ page }) => {
    await installMocks(page);
    await page.goto("/email");
    await expect(strip(page).getByRole("button", { name: /^Unread/ })).toBeVisible();
    expect((await metrics(page)).max).toBeGreaterThan(0);
    await expect(arrow(page, "end")).toBeHidden();
    await expect(page.getByRole("button", { name: /^All filters/ })).toBeVisible();
  });
});
