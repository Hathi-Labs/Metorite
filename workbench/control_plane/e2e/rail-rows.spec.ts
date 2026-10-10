import { expect, test, type Locator, type Page } from "@playwright/test";

import { LONG_NAME, NO_COUNT_NAME, RAIL_TREE, SHORT_NAME } from "./rail-rows-fixture";
import { SAFE_EMPTY } from "./visual/harness";

/**
 * The rail row, in a real browser (owner, 2026-10-10).
 *
 * The owner's report: every Projects row kept a "···" and a "+" on screen,
 * which cut each name about 60 px early, and nothing showed the whole name.
 * `RailRow` and `OverflowTip` are the fix. vitest has no DOM, so only a
 * browser can see the four things this spec checks:
 *
 *   1. At rest a row shows no actions, and they take no width.
 *   2. On hover the actions appear.
 *   3. A cut name shows its whole text in a tip, and a short name shows none.
 *   4. The name keeps the room the actions used to hold.
 *
 * Each case fails on the obvious wrong build: remove the reveal and case 1
 * fails, make the tip show always and the short-name case fails.
 */

async function openProjects(page: Page) {
  await page.setViewportSize({ width: 1440, height: 900 });
  await page.route("**/api/**", (route) => route.fulfill({ json: SAFE_EMPTY }));
  await page.route("**/api/projects/tree", (route) => route.fulfill({ json: RAIL_TREE }));
  await page.goto("/projects");
  await expect(page.getByText("Company Operations").first()).toBeVisible({ timeout: 20_000 });
}

/** The visible row whose label is exactly `name`. */
function row(page: Page, name: string): Locator {
  return page
    .locator("[data-rail-row]")
    .filter({ has: page.getByText(name, { exact: true }) })
    .first();
}

/** The trailing actions zone of a row: its opacity and its width. */
async function actionsBox(target: Locator) {
  return target.locator(".rail-row-actions").evaluate((el) => ({
    opacity: Number(getComputedStyle(el).opacity),
    width: el.getBoundingClientRect().width,
  }));
}

/** The width of a row's label, in px. */
async function labelWidth(target: Locator, name: string) {
  return target
    .getByText(name, { exact: true })
    .evaluate((el) => el.getBoundingClientRect().width);
}

/** Park the pointer away from the rail, so no row is hovered. */
async function rest(page: Page) {
  await page.mouse.move(1200, 600);
}

test.describe("rail rows", () => {
  test.setTimeout(90_000);

  test("at rest a row shows no actions, and on hover they appear", async ({ page }) => {
    await openProjects(page);
    await rest(page);
    const target = row(page, "Finance & Accounts");

    const atRest = await actionsBox(target);
    expect(atRest.opacity).toBe(0);
    expect(atRest.width).toBe(0);

    await target.hover();
    const hovered = await actionsBox(target);
    expect(hovered.opacity).toBe(1);
    expect(hovered.width).toBeGreaterThan(30);
    await expect(target.getByRole("button", { name: "Actions for Finance & Accounts" })).toBeVisible();

    // And it goes away again when the pointer leaves.
    await rest(page);
    expect((await actionsBox(target)).width).toBe(0);
  });

  test("at rest a row shows its open count, and hover swaps it for the actions", async ({ page }) => {
    await openProjects(page);
    await rest(page);
    const target = row(page, "Finance & Accounts");
    // 9 tasks, 3 done: 6 open.
    const meta = target.locator(".rail-row-meta");
    await expect(meta).toBeVisible();
    await expect(meta).toContainText("6");
    await target.hover();
    await expect(meta).toBeHidden();
  });

  test("the name keeps the room the actions used to hold", async ({ page }) => {
    await openProjects(page);
    await rest(page);
    const target = row(page, NO_COUNT_NAME);
    const atRest = await labelWidth(target, NO_COUNT_NAME);
    await target.hover();
    const hovered = await labelWidth(target, NO_COUNT_NAME);
    // The two actions are about 44 px plus a gap. Before this change the
    // label lost that room at all times, so this difference IS the room it
    // got back. ⚠️ It is measured on a row with no count, so the count's own
    // width does not hide part of the difference.
    expect(atRest - hovered).toBeGreaterThanOrEqual(40);
  });

  test("a cut name shows its whole text in a tip after a hover", async ({ page }) => {
    await openProjects(page);
    await rest(page);
    const label = row(page, LONG_NAME).getByText(LONG_NAME, { exact: true });
    const cut = await label.evaluate((el) => el.scrollWidth > el.clientWidth);
    expect(cut, "the fixture name must be long enough to be cut").toBe(true);

    await label.hover();
    const tip = page.getByRole("tooltip");
    await expect(tip).toBeVisible({ timeout: 2_000 });
    await expect(tip).toHaveText(LONG_NAME);
    // No native title: the browser would show the same name a second time.
    expect(await label.getAttribute("title")).toBeNull();

    await rest(page);
    await expect(tip).toHaveCount(0);
  });

  test("a short name shows no tip", async ({ page }) => {
    await openProjects(page);
    await rest(page);
    const label = row(page, SHORT_NAME).getByText(SHORT_NAME, { exact: true });
    await label.hover();
    // Twice the delay, so a tip that was going to show has shown.
    await page.waitForTimeout(900);
    await expect(page.getByRole("tooltip")).toHaveCount(0);
  });

  test("Escape hides the tip", async ({ page }) => {
    await openProjects(page);
    await rest(page);
    await row(page, LONG_NAME).getByText(LONG_NAME, { exact: true }).hover();
    await expect(page.getByRole("tooltip")).toBeVisible({ timeout: 2_000 });
    await page.keyboard.press("Escape");
    await expect(page.getByRole("tooltip")).toHaveCount(0);
  });
});

/** The computed opacity of the first element `selector` finds in a row. */
const opacityOf = (target: Locator, selector: string) =>
  target.locator(selector).first().evaluate((el) => Number(getComputedStyle(el).opacity));

test.describe("the chevron takes the icon's slot", () => {
  test.setTimeout(90_000);

  test("at rest the chevron is hidden, but the toggle is focusable and opens on Enter", async ({ page }) => {
    await openProjects(page);
    await rest(page);
    const space = row(page, "Company Operations");
    const toggle = space.locator("[data-rail-toggle]");
    expect(await opacityOf(space, "[data-rail-toggle]")).toBe(0);
    expect(await opacityOf(space, "[data-rail-icon]")).toBe(1);
    await expect(toggle).toHaveAttribute("aria-label", "Collapse Company Operations");
    await expect(toggle).toHaveAttribute("aria-expanded", "true");

    // A keyboard reaches it: Shift+Tab from the label lands on the toggle,
    // and reaching it by key shows it.
    await space.locator("[data-rail-label]").focus();
    await page.keyboard.press("Shift+Tab");
    await expect(toggle).toBeFocused();
    await expect.poll(() => opacityOf(space, "[data-rail-toggle]")).toBe(1);

    await page.keyboard.press("Enter");
    await expect(toggle).toHaveAttribute("aria-expanded", "false");
    await expect(page.getByText("Finance & Accounts", { exact: true })).toHaveCount(0);
    await page.keyboard.press("Enter");
    await expect(page.getByText("Finance & Accounts", { exact: true }).first()).toBeVisible();
  });

  test("ArrowLeft on the label closes the row, and ArrowRight opens it", async ({ page }) => {
    await openProjects(page);
    const space = row(page, "Fracktal Care");
    const label = space.locator("[data-rail-label]");
    await label.focus();
    await page.keyboard.press("ArrowLeft");
    await expect(space.locator("[data-rail-toggle]")).toHaveAttribute("aria-expanded", "false");
    await page.keyboard.press("ArrowRight");
    await expect(space.locator("[data-rail-toggle]")).toHaveAttribute("aria-expanded", "true");
  });

  test("on hover the chevron shows in the icon's place, and the icon gives way", async ({ page }) => {
    await openProjects(page);
    await rest(page);
    const space = row(page, "Fracktory");
    await space.hover();
    // Polled: the control fades over the house motion duration.
    await expect.poll(() => opacityOf(space, "[data-rail-toggle]")).toBe(1);
    await expect.poll(() => opacityOf(space, "[data-rail-icon]")).toBe(0);
    const [chevron, icon] = await Promise.all(
      ["[data-rail-toggle]", "[data-rail-icon]"].map((s) =>
        space.locator(s).first().evaluate((el) => {
          const r = el.getBoundingClientRect();
          return { x: r.left + r.width / 2, y: r.top + r.height / 2 };
        }),
      ),
    );
    expect(Math.abs(chevron.x - icon.x)).toBeLessThanOrEqual(3);
    expect(Math.abs(chevron.y - icon.y)).toBeLessThanOrEqual(3);
  });

  test("no row keeps a chevron column: the name starts right after the icon", async ({ page }) => {
    await openProjects(page);
    await rest(page);
    // Depth 1: 8 px row padding, one 12 px step, a 16 px icon, an 8 px gap.
    // The old chevron column added 22 px to this.
    for (const name of ["Finance & Accounts", "Knowledge Base"]) {
      const target = row(page, name);
      const offset = await target.evaluate((el, n) => {
        const label = Array.from(el.querySelectorAll("span")).find((s) => s.textContent === n)!;
        return label.getBoundingClientRect().left - el.getBoundingClientRect().left;
      }, name);
      expect(offset, name).toBeLessThanOrEqual(8 + 12 + 16 + 8 + 1);
    }
  });
});

test.describe("rail rows on a touch-capable display", () => {
  // Chromium reports `hover: none` here, which is what hid every
  // `group-hover:` control on a touchscreen laptop (H-131).
  test.use({ hasTouch: true });
  test.setTimeout(90_000);

  test("a mouse hover still reveals the actions", async ({ page }) => {
    await openProjects(page);
    await rest(page);
    const target = row(page, "Finance & Accounts");
    expect((await actionsBox(target)).width).toBe(0);
    await target.hover();
    expect((await actionsBox(target)).opacity).toBe(1);
  });
});
