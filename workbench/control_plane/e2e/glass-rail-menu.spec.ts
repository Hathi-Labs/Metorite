import { expect, test } from "@playwright/test";

import { gotoAndSettle, setLook, stubApi } from "./visual/harness";

/**
 * The glass rail must not trap its own menus (D142, GL-3 review P1).
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
 * The glass CSS ships in every build, so the test turns glass on with the
 * attribute and needs no `NEXT_PUBLIC_UI_LOOK`.
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

for (const look of ["glass", "classic"] as const) {
  test(`the account menu reaches past the rail and sits on top (${look})`, async ({ page }) => {
    test.setTimeout(120_000);
    await page.setViewportSize({ width: 1440, height: 900 });
    await stubApi(page, { "auth/me": ADMIN, health: { gateway: "up" }, agents: [] }, { arrayPaths: /^(notes|chat|agent|apps|agents)\b/ });
    await gotoAndSettle(page, "/projects", 4000);
    await setLook(page, look);

    const rail = page.locator("aside.sidebar-rail").first();
    const railBox = await rail.boundingBox();
    expect(railBox).not.toBeNull();

    await page.getByRole("button", { name: /Account and settings: admin@example\.com/ }).first().click();
    const menu = page.getByRole("dialog", { name: "Account and settings" }).first();
    await expect(menu).toBeVisible();
    const menuBox = await menu.boundingBox();
    expect(menuBox).not.toBeNull();

    const railRight = railBox!.x + railBox!.width;
    expect(menuBox!.x + menuBox!.width).toBeGreaterThan(railRight + 20);
    const onTop = await page.evaluate(
      ([x, y]) => !!document.elementFromPoint(x, y)?.closest('[aria-label="Account and settings"]'),
      [railRight + 10, menuBox!.y + menuBox!.height / 2],
    );
    expect(onTop).toBe(true);
  });
}
