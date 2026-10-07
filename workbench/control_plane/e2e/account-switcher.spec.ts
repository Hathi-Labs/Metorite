import { expect, test, type Page, type Route } from "@playwright/test";

/**
 * The account switcher (MT-1k slice A2) in a browser. The server half has its
 * own tests (`app/api/accounts/route.test.ts`). These stub `/api/accounts` and
 * check what a member sees and what the page asks the server to do.
 *
 *   1. Flag off: the footer is the old one, with its Sign out button.
 *   2. Desktop: the footer button opens the menu, with each organization.
 *   3. Picking an account POSTs the switch for ITS slot, then loads `/`.
 *   4. The folded rail keeps an avatar that opens the same menu.
 *   5. Phone: the drawer's account row unfolds the other accounts.
 */

const SESSION = {
  user: { email: "vjvarada@hathilabs.com", name: "Vijay Varada" },
  expires: "2099-01-01T00:00:00.000Z",
};

const ACCOUNTS = {
  enabled: true,
  active: { email: "vjvarada@hathilabs.com", name: "Vijay Varada" },
  others: [
    { slot: 2, email: "vjvarada@fracktal.in", name: "Vijay Raghav Varada", organization: "Fracktal Works" },
    { slot: 0, email: "ops@hathilabs.com", name: null, organization: null },
  ],
};

async function stub(page: Page, accounts: unknown, calls: string[] = []) {
  const json = (r: Route, body: unknown) =>
    r.fulfill({ status: 200, contentType: "application/json", body: JSON.stringify(body) });
  await page.route("**/api/health", (r) => json(r, { gateway: "up" }));
  await page.route("**/api/auth/session", (r) => json(r, SESSION));
  await page.route(/\/api\/accounts(\?.*)?$/, (r) => json(r, accounts));
  await page.route(/\/api\/accounts\/[a-z-]+$/, async (r) => {
    calls.push(`${new URL(r.request().url()).pathname} ${r.request().postData() ?? ""}`);
    return json(r, { ok: true, email: "vjvarada@fracktal.in" });
  });
}

const footerButton = (page: Page) => page.getByRole("button", { name: /^Accounts: signed in as/ });

test.describe("desktop", () => {
  test.use({ viewport: { width: 1280, height: 800 } });

  test("flag off: the old footer, with Sign out", async ({ page }) => {
    await stub(page, { enabled: false });
    await page.goto("/settings/appearance");
    await expect(page.locator("aside").getByTitle("Sign out")).toBeVisible();
    await expect(footerButton(page)).toHaveCount(0);
  });

  test("the footer opens the menu, with each account and its organization", async ({ page }) => {
    await stub(page, ACCOUNTS);
    await page.goto("/settings/appearance");
    await expect(footerButton(page)).toHaveAccessibleName(/2 more/);
    await footerButton(page).click();
    const menu = page.getByRole("dialog", { name: "Accounts" });
    await expect(menu).toBeVisible();
    await expect(menu).toContainText("vjvarada@hathilabs.com");
    await expect(menu.getByRole("listitem")).toHaveCount(2);
    await expect(menu).toContainText("Fracktal Works · vjvarada@fracktal.in");
    await expect(menu.getByRole("button", { name: "Add another account" })).toBeVisible();
    await expect(menu.getByRole("button", { name: "Sign out of all accounts" })).toBeVisible();
    await page.keyboard.press("Escape");
    await expect(menu).toHaveCount(0);
  });

  test("picking an account switches ITS slot, then loads /", async ({ page }) => {
    const calls: string[] = [];
    await stub(page, ACCOUNTS, calls);
    await page.goto("/settings/appearance");
    await footerButton(page).click();
    await page.getByRole("dialog", { name: "Accounts" }).getByRole("button", { name: /Vijay Raghav Varada/ }).click();
    await page.waitForURL((u) => u.pathname === "/");
    expect(calls).toEqual(['/api/accounts/switch {"slot":2}']);
  });

  test("the folded rail keeps an avatar that opens the same menu", async ({ page }) => {
    await page.addInitScript(() => localStorage.setItem("cc-sidebar-collapsed", "1"));
    await stub(page, ACCOUNTS);
    await page.goto("/settings/appearance");
    await expect(page.locator('aside[data-collapsed="true"]')).toBeVisible();
    // From the keyboard. In the dev build, Next's own "N" badge sits over the
    // foot of the rail and takes a mouse click. Production has no badge.
    await footerButton(page).focus();
    await page.keyboard.press("Enter");
    await expect(page.getByRole("dialog", { name: "Accounts" }).getByRole("listitem")).toHaveCount(2);
  });
});

test.describe("phone", () => {
  test.use({ viewport: { width: 390, height: 844 }, hasTouch: true, isMobile: true });

  test("the drawer's account row unfolds the other accounts", async ({ page }) => {
    await stub(page, ACCOUNTS);
    await page.goto("/settings/appearance");
    await page.getByRole("button", { name: "Menu" }).click();
    const row = page.getByRole("button", { name: /Vijay Varada/ });
    await expect(row).toBeVisible();
    await expect(row).toContainText("2 more");
    await row.click();
    const list = page.getByRole("list", { name: "Other accounts" });
    await expect(list.getByRole("listitem")).toHaveCount(2);
    await expect(page.getByRole("button", { name: "Add another account" })).toBeVisible();
  });
});
