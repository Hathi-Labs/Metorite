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
 *   6. Two tabs: a tab that loads as another account sends the old tab to /.
 *      Without it, the old tab writes as an account it does not show.
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

async function stub(page: Page, accounts: unknown, calls: string[] = [], session: unknown = SESSION) {
  const json = (r: Route, body: unknown) =>
    r.fulfill({ status: 200, contentType: "application/json", body: JSON.stringify(body) });
  await page.route("**/api/health", (r) => json(r, { gateway: "up" }));
  await page.route("**/api/auth/session", (r) => json(r, session));
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

test.describe("two tabs", () => {
  test.use({ viewport: { width: 1280, height: 800 } });

  test("a tab that loads as another account sends the old tab to /", async ({ context }) => {
    const tabA = await context.newPage();
    await stub(tabA, ACCOUNTS);
    await tabA.goto("/settings/appearance");
    await expect(footerButton(tabA)).toBeVisible();

    const tabB = await context.newPage();
    await stub(tabB, ACCOUNTS, [], { ...SESSION, user: { email: "vjvarada@fracktal.in", name: "Vijay" } });
    await tabB.goto("/settings/appearance");

    await tabA.waitForURL((u) => u.pathname === "/");
    // ⚠️ Close both now. Each stub answers its own session, so the reloaded
    // tab A announces A again and the two would reload each other. In the
    // product one cookie jar answers both, and they settle.
    await tabB.close();
    await tabA.close();
  });
});

test.describe("phone", () => {
  test.use({ viewport: { width: 390, height: 844 }, hasTouch: true, isMobile: true });

  // The organization the active account is in. Owner request, 2026-10-09: the
  // phone must say which organization is open and which account is signed in,
  // and switch in two taps.
  const ME = {
    authenticated: true,
    email: "vjvarada@hathilabs.com",
    is_admin: false,
    features: ["tasks", "email", "chat"],
    permissions: [],
    roles: ["employee"],
    organization: { id: "org1", slug: "hathi", display_name: "Hathi Labs LLP" },
  };
  async function phone(page: Page, calls: string[] = []) {
    await stub(page, ACCOUNTS, calls);
    await page.route("**/api/auth/me", (r) =>
      r.fulfill({ status: 200, contentType: "application/json", body: JSON.stringify(ME) }),
    );
    await page.goto("/settings/appearance");
  }

  // The header is the account surface: the mark and the organization, the
  // signed-in address under it, and a tap opens the rest (owner, 2026-10-09).
  const header = (page: Page) => page.getByTestId("drawer-org").getByRole("button", { name: /^Account, / });

  test("the menu header names the organization and the account, and stays one line", async ({ page }) => {
    await phone(page);
    await page.getByRole("button", { name: "Menu" }).click();
    const top = page.getByTestId("drawer-org");
    await expect(top).toContainText("Hathi Labs LLP");
    await expect(top).toContainText("vjvarada@hathilabs.com");
    await expect(header(page)).toHaveAccessibleName(
      "Account, signed in as vjvarada@hathilabs.com, in Hathi Labs LLP, 2 more",
    );
    // Folded until tapped: no switch list, no actions, no second account row.
    await expect(header(page)).toHaveAttribute("aria-expanded", "false");
    await expect(top.getByRole("list", { name: "Switch organization" })).toHaveCount(0);
    await expect(page.getByRole("list", { name: "Other accounts" })).toHaveCount(0);
    await expect(page.getByTestId("account-tab")).toHaveCount(0);
  });

  test("a tap on the header opens the other accounts and the actions", async ({ page }) => {
    await phone(page);
    await page.getByRole("button", { name: "Menu" }).click();
    await header(page).click();
    const top = page.getByTestId("drawer-org");
    const list = top.getByRole("list", { name: "Switch organization" });
    await expect(list.getByRole("listitem")).toHaveCount(2);
    await expect(list).toContainText("Fracktal Works");
    await expect(top.getByRole("button", { name: "Add account" })).toBeVisible();
    await expect(top.getByRole("button", { name: "Sign out of all" })).toBeVisible();
  });

  test("one tap in the open header switches to that account", async ({ page }) => {
    const calls: string[] = [];
    await phone(page, calls);
    await page.getByRole("button", { name: "Menu" }).click();
    await header(page).click();
    await page.getByTestId("drawer-org").getByRole("button", { name: /Fracktal Works/ }).click();
    await page.waitForURL((u) => u.pathname === "/");
    expect(calls).toEqual(['/api/accounts/switch {"slot":2}']);
  });

  test("a row drops ONE account from this browser", async ({ page }) => {
    const calls: string[] = [];
    await phone(page, calls);
    await page.getByRole("button", { name: "Menu" }).click();
    await header(page).click();
    await page
      .getByTestId("drawer-org")
      .getByRole("button", { name: "Remove vjvarada@fracktal.in from this browser" })
      .click();
    await expect.poll(() => calls).toEqual(['/api/accounts/remove {"slot":2}']);
  });
});
