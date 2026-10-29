import { expect, test, type Page } from "@playwright/test";

/**
 * The folded rail keeps its icons at full size when it scrolls (Metorite task
 * 998, owner report 2026-10-29).
 *
 * The folded rail is 56px wide. When its app list is taller than the window,
 * the nav scrolls, and a real scrollbar takes its width out of those 56px. The
 * icon is a flex item, so the browser shrank it to fit what was left: about
 * 11px against the 18px of "All apps", which sits outside the scroller.
 *
 * ⚠️ **This file must see a real scrollbar.** Playwright starts headless
 * Chromium with `--hide-scrollbars`, and a hidden bar takes no width, so the
 * bug cannot happen there. `ignoreDefaultArgs` puts the bar back, and the
 * short viewport makes the list overflow. The first test asserts the overflow,
 * so a rail that stops scrolling cannot pass it by accident.
 */

const ADMIN = {
  authenticated: true,
  email: "admin@example.com",
  is_admin: true,
  // The live apps, so the rail holds as many rows as a real member's does.
  features: ["tasks", "email", "projects", "crm", "people", "knowledge", "chat", "approvals", "dashboard"],
  permissions: ["*"],
  roles: ["admin"],
  organization: { slug: "acme", name: "Acme" },
};

/** One router, on purpose. `org-branding.spec.ts` says why. */
async function stubApi(page: Page) {
  await page.route("**/api/**", async (route) => {
    const path = new URL(route.request().url()).pathname;
    const json = (body: unknown) =>
      route.fulfill({ status: 200, contentType: "application/json", body: JSON.stringify(body) });
    if (path === "/api/auth/me") return json(ADMIN);
    if (path === "/api/health") return json({ gateway: "up" });
    if (path === "/api/settings/branding") return json({ logo: null, updatedBy: "", updatedAt: "" });
    return json([]);
  });
}

test.use({
  viewport: { width: 1280, height: 420 },
  launchOptions: {
    ...(process.env.PLAYWRIGHT_EXECUTABLE_PATH ? { executablePath: process.env.PLAYWRIGHT_EXECUTABLE_PATH } : {}),
    ignoreDefaultArgs: ["--hide-scrollbars"],
  },
});

const rail = (page: Page) => page.locator("aside[data-collapsed]");
const nav = (page: Page) => rail(page).locator("nav");
const bar = (page: Page) => page.locator("[data-shell-bar]");

test.beforeEach(async ({ page }) => {
  await stubApi(page);
  // The frame the owner uses: the full-width bar and the shell nav.
  await page.addInitScript(() => {
    localStorage.setItem("cc-shell-bar", "1");
    localStorage.setItem("cc-shell-nav", "1");
  });
  await page.goto("/settings/appearance");
  await expect(rail(page)).toHaveAttribute("data-collapsed", "false");
  await bar(page).getByRole("button", { name: "Collapse sidebar" }).click();
  await expect(rail(page)).toHaveAttribute("data-collapsed", "true");
  await expect.poll(async () => (await rail(page).boundingBox())?.width).toBeLessThan(60);
});

test("every app icon in the folded rail keeps 18px while the rail scrolls", async ({ page }) => {
  // The case under test: the app list is taller than the window.
  expect(await nav(page).evaluate((el) => el.scrollHeight > el.clientHeight)).toBe(true);
  // The folded rail's bar takes no width, so the icons sit in its centre.
  expect(await nav(page).evaluate((el) => el.offsetWidth - el.clientWidth)).toBe(0);

  const icons = nav(page).locator("a[data-nav-row] > svg, [data-nav-add-apps] button > svg");
  expect(await icons.count()).toBeGreaterThan(3);
  const widths = await icons.evaluateAll((els) => els.map((el) => el.getBoundingClientRect().width));
  for (const width of widths) expect(width).toBeCloseTo(18, 0);
});

test("an icon keeps 18px even when a bar takes the rail's width", async ({ page }) => {
  // The second half of the fix, `shrink-0` on the icon. Put the thin bar back
  // on the folded rail: it takes width again, and the icons must not shrink.
  // A non-auto `scrollbar-width` also overrides the `::-webkit-scrollbar` rule.
  await nav(page).evaluate((el) => {
    el.style.scrollbarWidth = "thin";
  });
  expect(await nav(page).evaluate((el) => el.offsetWidth - el.clientWidth)).toBeGreaterThan(0);

  const icons = nav(page).locator("a[data-nav-row] > svg, [data-nav-add-apps] button > svg");
  const widths = await icons.evaluateAll((els) => els.map((el) => el.getBoundingClientRect().width));
  for (const width of widths) expect(width).toBeCloseTo(18, 0);
});

test("the folded rail still scrolls to its last app", async ({ page }) => {
  const last = nav(page).locator("a[data-nav-row], [data-nav-add-apps] button").last();
  await last.scrollIntoViewIfNeeded();
  await expect(last).toBeInViewport();
  expect(await nav(page).evaluate((el) => el.scrollTop)).toBeGreaterThan(0);
});

test("the open rail keeps its thin scrollbar", async ({ page }) => {
  await bar(page).getByRole("button", { name: "Expand sidebar" }).click();
  await expect(rail(page)).toHaveAttribute("data-collapsed", "false");
  expect(await nav(page).evaluate((el) => getComputedStyle(el).scrollbarWidth)).toBe("thin");
});
