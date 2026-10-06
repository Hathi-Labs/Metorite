import { expect, test, type Page } from "@playwright/test";

/**
 * The sidebar folds while you work (`src/lib/sidebarFold.ts`,
 * `navigation_shell.md` §3.2). Owner directive, 2026-10-06.
 *
 * `sidebarFold.test.ts` fences the rules as pure functions. This file fences
 * the behaviour those rules must produce in a browser, because every part that
 * can go wrong is a DOM fact: which element the click landed on, whether the
 * rail is narrow, whether the tip is on screen.
 *
 * Each test is written so that the obvious wrong build fails it:
 *
 *   1. A sidebar link, then a click in the app, folds the rail and shows the tip.
 *   2. A click in the app with NO sidebar link first does nothing. A build that
 *      folds on every click passes test 1 and fails this one.
 *   3. A member who opens the rail again keeps it open on the next click.
 *   4. "Keep it open" stops the fold, and the choice survives a reload.
 *   5. The folded state survives a reload.
 *   6. A first click that opens a menu does not fold until the menu closes.
 *      Several menus measure their trigger once, and the fold moves it.
 *   7. "Keep it open" on the Appearance page keeps it open. The fold listener
 *      runs before the choice saves, so a fold that read the setting at once
 *      would fold the sidebar the member just asked to keep open.
 */

const ADMIN = {
  authenticated: true,
  email: "admin@example.com",
  is_admin: true,
  features: [],
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

const rail = (page: Page) => page.locator("aside[data-collapsed]");
const tip = (page: Page) => page.getByTestId("sidebar-fold-tip");

/** Open an app from the sidebar, the way a member does. */
async function openFromSidebar(page: Page, label: string) {
  await rail(page).getByRole("link", { name: label }).first().click();
}

/** A click inside the app that hits nothing interactive. */
async function workInApp(page: Page) {
  await page.locator("main").click({ position: { x: 600, y: 8 } });
}

test.beforeEach(async ({ page }) => {
  await stubApi(page);
  await page.goto("/settings/appearance");
  await expect(rail(page)).toHaveAttribute("data-collapsed", "false");
});

test("a sidebar link, then work in the app, folds the rail and shows the tip", async ({ page }) => {
  await openFromSidebar(page, "Organisation");
  await page.waitForURL("**/settings/organization**");
  await expect(rail(page)).toHaveAttribute("data-collapsed", "false");

  await workInApp(page);

  await expect(rail(page)).toHaveAttribute("data-collapsed", "true");
  await expect.poll(async () => (await rail(page).boundingBox())?.width).toBeLessThan(60);
  await expect(tip(page)).toBeVisible();
  await expect(page.getByRole("button", { name: "Expand sidebar" })).toBeVisible();

  await tip(page).getByRole("button", { name: "Got it" }).click();
  await expect(tip(page)).toHaveCount(0);
});

test("work in the app with no sidebar link first does not fold", async ({ page }) => {
  await workInApp(page);
  await workInApp(page);
  await expect(rail(page)).toHaveAttribute("data-collapsed", "false");
});

test("a member who opens the rail again keeps it open", async ({ page }) => {
  await openFromSidebar(page, "Organisation");
  await page.waitForURL("**/settings/organization**");
  await workInApp(page);
  await expect(rail(page)).toHaveAttribute("data-collapsed", "true");

  await page.getByRole("button", { name: "Expand sidebar" }).click();
  await expect(rail(page)).toHaveAttribute("data-collapsed", "false");
  await workInApp(page);
  await workInApp(page);
  await expect(rail(page)).toHaveAttribute("data-collapsed", "false");
});

test("Keep it open stops the fold, and the choice survives a reload", async ({ page }) => {
  await openFromSidebar(page, "Organisation");
  await page.waitForURL("**/settings/organization**");
  await workInApp(page);
  await tip(page).getByRole("button", { name: "Keep it open" }).click();
  await expect(rail(page)).toHaveAttribute("data-collapsed", "false");

  await page.reload();
  await expect(rail(page)).toHaveAttribute("data-collapsed", "false");
  await openFromSidebar(page, "Organisation");
  await workInApp(page);
  await expect(rail(page)).toHaveAttribute("data-collapsed", "false");
});

test("the folded rail survives a reload", async ({ page }) => {
  await openFromSidebar(page, "Organisation");
  await page.waitForURL("**/settings/organization**");
  await workInApp(page);
  await expect(rail(page)).toHaveAttribute("data-collapsed", "true");

  await page.reload();
  await expect(rail(page)).toHaveAttribute("data-collapsed", "true");
});

test("a first click that opens a menu waits for the menu to close", async ({ page }) => {
  await openFromSidebar(page, "Organisation");
  await page.waitForURL("**/settings/organization**");
  // A stand-in menu trigger in the app: the click sets aria-expanded, as
  // every menu trigger in the product does.
  await page.evaluate(() => {
    const b = document.createElement("button");
    b.textContent = "Fake menu";
    b.setAttribute("aria-expanded", "false");
    b.onclick = () => b.setAttribute("aria-expanded", "true");
    document.querySelector("main")!.prepend(b);
  });
  await page.getByRole("button", { name: "Fake menu" }).click();
  await page.waitForTimeout(400);
  await expect(rail(page)).toHaveAttribute("data-collapsed", "false");

  // The menu closes. The next work folds.
  await page.evaluate(() =>
    document.querySelector('main [aria-expanded="true"]')!.setAttribute("aria-expanded", "false"),
  );
  await workInApp(page);
  await expect(rail(page)).toHaveAttribute("data-collapsed", "true");
});

test("Keep it open on the Appearance page keeps the sidebar open", async ({ page }) => {
  await openFromSidebar(page, "Appearance");
  const keep = page.locator("main").getByRole("button", { name: "Keep it open" });
  await keep.click();
  await page.waitForTimeout(400);
  await expect(rail(page)).toHaveAttribute("data-collapsed", "false");
  await expect(tip(page)).toHaveCount(0);
  await expect(keep).toHaveClass(/border-primary/);
});
