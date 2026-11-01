import { expect, test } from "@playwright/test";

import { gotoAndSettle, stubApi } from "./visual/harness";

/**
 * The glass rail must not trap its own menus (D143, GL-3 review P1).
 *
 * `backdrop-filter` makes an element the containing block of every
 * `position: fixed` child, and the rail's `overflow: hidden` then clips that
 * child. The account menu is fixed inside the rail and is wider than it. So
 * the glass rail wears `glass-chrome-flat`, which has no filter. A
 * `::before` layer under `isolation: isolate` kept the filter off the rail,
 * but its stacking context put the menu UNDER the page. This test fails on
 * both of those: the menu must reach past the rail, and it must be the
 * topmost element there.
 *
 * Glass is the only look (D149), so the page already carries the attribute.
 * The menu has two frames: the
 * account foot ("Account and settings", with the shell flags of production)
 * and the older footer ("Accounts", the CI default, which needs the account
 * switcher on). Both are fixed inside the rail and wider than it, so the test
 * reads either.
 */

const ADMIN = {
  authenticated: true,
  email: "admin@example.com",
  name: "Asha Rao",
  is_admin: true,
  features: ["tasks", "email", "projects", "people", "chat", "approvals", "crm", "integrations", "artifacts", "knowledge", "calendar"],
  permissions: [],
  capabilities: [],
  roles: ["owner"],
  organization: { id: "org1", slug: "acme", display_name: "Acme Works" },
};

/** The account switcher on, with one other account, as `account-switcher.spec.ts` stubs it. */
const ACCOUNTS = {
  enabled: true,
  active: { email: "admin@example.com", name: "Asha Rao" },
  others: [{ slot: 2, email: "asha@other.example", name: "Asha Rao", organization: "Other Works" }],
};

/** Every app shows and the first sign-in question stays shut, so the rail draws. */
const SHELL = { preset: null, answered: "skipped", pins: null, known: null, cardOrder: null, newOrder: null };

const MENU_BUTTON = /^(Account and settings: admin@example\.com|Accounts: signed in as)/;
const MENU = /^(Account and settings|Accounts)$/;

{
  test("the account menu reaches past the rail and sits on top", async ({ page }) => {
    test.setTimeout(120_000);
    await page.setViewportSize({ width: 1440, height: 900 });
    await stubApi(
      page,
      { "auth/me": ADMIN, "auth/me/shell": SHELL, accounts: ACCOUNTS, health: { gateway: "up" }, agents: [] },
      { arrayPaths: /^(notes|chat|agent|apps|agents)\b/ },
    );
    await gotoAndSettle(page, "/projects", 4000);

    const rail = page.locator("aside.sidebar-rail").first();
    const railBox = await rail.boundingBox();
    expect(railBox).not.toBeNull();

    await page.getByRole("button", { name: MENU_BUTTON }).first().click();
    const menu = page.getByRole("dialog", { name: MENU }).first();
    await expect(menu).toBeVisible();
    const menuBox = await menu.boundingBox();
    expect(menuBox).not.toBeNull();

    const railRight = railBox!.x + railBox!.width;
    expect(menuBox!.x + menuBox!.width).toBeGreaterThan(railRight + 20);
    const onTop = await page.evaluate(
      ([x, y]) =>
        !!document.elementFromPoint(x, y)?.closest('[aria-label="Account and settings"], [aria-label="Accounts"]'),
      [railRight + 10, menuBox!.y + menuBox!.height / 2],
    );
    expect(onTop).toBe(true);
  });
}
