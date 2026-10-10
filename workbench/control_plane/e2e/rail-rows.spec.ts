import { expect, test, type Locator, type Page } from "@playwright/test";

import { LONG_NAME, NO_COUNT_NAME, RAIL_TREE, SHORT_NAME } from "./rail-rows-fixture";
import { SAFE_EMPTY } from "./visual/harness";
import { openAllRows } from "./railOpen";

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
  // The rail starts closed (railFold.ts). These specs measure open rows.
  await openAllRows(page);
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
    // Hidden from sight only, so it stays in the accessible name: the box
    // shrinks to the screen-reader clip rather than leaving the tree.
    await expect.poll(() => meta.evaluate((el) => el.getBoundingClientRect().width)).toBeLessThanOrEqual(1);
    await expect(target.locator("[data-rail-label]")).toHaveAccessibleName(/6 open tasks/);
  });

  test("a focused row keeps its count in its accessible name", async ({ page }) => {
    // `display: none` on focus took "6 open tasks" out of the name a screen
    // reader announces. The count now gives way from sight only.
    await openProjects(page);
    const target = row(page, "Finance & Accounts");
    const label = target.locator("[data-rail-label]");
    await expect(label).toHaveAccessibleName(/6 open tasks/);
    // Reach it by KEY, so :focus-visible matches and the swap runs.
    await label.focus();
    await page.keyboard.press("Shift+Tab");
    await page.keyboard.press("Tab");
    await expect(label).toBeFocused();
    await expect.poll(() =>
      target.locator(".rail-row-meta").evaluate((el) => el.getBoundingClientRect().width),
    ).toBeLessThanOrEqual(1);
    await expect(label).toHaveAccessibleName(/6 open tasks/);
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
    // Depth 1: 8 px row padding, one 16 px step, a 16 px icon, an 8 px gap.
    // The old chevron column added 22 px to this.
    for (const name of ["Finance & Accounts", "Knowledge Base"]) {
      const target = row(page, name);
      const offset = await target.evaluate((el, n) => {
        const label = Array.from(el.querySelectorAll("span")).find((s) => s.textContent === n)!;
        return label.getBoundingClientRect().left - el.getBoundingClientRect().left;
      }, name);
      expect(offset, name).toBeLessThanOrEqual(8 + 16 + 16 + 8 + 1);
    }
  });
});

test.describe("rail rows on a touch-capable display", () => {
  // Chromium reports `hover: none` here, which is what hid every
  // `group-hover:` control on a touchscreen laptop (H-131).
  test.use({ hasTouch: true });
  test.setTimeout(90_000);

  test("a tap on a parent's icon selects the row, and a tap on the chevron opens it", async ({ page }) => {
    // ⚠️ The chevron is transparent over the icon at rest. If it took the
    // pointer there, this first tap would toggle the row instead of selecting
    // it, and the tree would jump under the member's finger.
    await openProjects(page);
    const space = row(page, "Fracktal Care");
    const toggle = space.locator("[data-rail-toggle]");
    await expect(toggle).toHaveAttribute("aria-expanded", "true");
    await space.locator("[data-rail-icon]").tap();
    await expect(space).toHaveClass(/bg-primary\/10/);
    await expect(toggle).toHaveAttribute("aria-expanded", "true");
    // The tap leaves the row hovered, so the chevron now shows and takes it.
    await expect.poll(() => opacityOf(space, "[data-rail-toggle]")).toBe(1);
    await toggle.tap();
    await expect(toggle).toHaveAttribute("aria-expanded", "false");
  });

  test("a mouse hover still reveals the actions", async ({ page }) => {
    await openProjects(page);
    await rest(page);
    const target = row(page, "Finance & Accounts");
    expect((await actionsBox(target)).width).toBe(0);
    await target.hover();
    expect((await actionsBox(target)).opacity).toBe(1);
  });
});

test.describe("the phone drawer", () => {
  test.setTimeout(120_000);

  async function openDrawer(page: Page) {
    await page.setViewportSize({ width: 390, height: 844 });
    await page.route("**/api/**", (route) => route.fulfill({ json: SAFE_EMPTY }));
    await page.route("**/api/projects/tree", (route) => route.fulfill({ json: RAIL_TREE }));
    await page.goto("/projects");
    await page.waitForTimeout(4000);
    await page.evaluate(() =>
      window.dispatchEvent(new CustomEvent("cc-mobile-nav", { detail: "projects-tree" })),
    );
    await expect(page.getByText("Company Operations").first()).toBeVisible({ timeout: 20_000 });
    await openAllRows(page);
  }

  test("a row that opens shows its glyph AND its chevron", async ({ page }) => {
    // The swap hid the icon on a phone: a space lost its glyph and a parent
    // project its run-state wheel. The phone has a chevron column instead.
    await openDrawer(page);
    const space = row(page, "Fracktal Care");
    await expect(space.locator("[data-rail-toggle]")).toBeVisible();
    await expect(space.locator("[data-rail-icon]")).toBeVisible();
    expect(await opacityOf(space, "[data-rail-toggle]")).toBe(1);
    expect(await opacityOf(space, "[data-rail-icon]")).toBe(1);
    const [chevron, icon] = await Promise.all(
      ["[data-rail-toggle]", "[data-rail-icon]"].map((sel) =>
        space.locator(sel).first().evaluate((el) => el.getBoundingClientRect()),
      ),
    );
    expect(chevron.right).toBeLessThanOrEqual(icon.left + 1);
  });

  test("an unselected row's ··· shows and opens its menu, and the drawer stays", async ({ page }) => {
    await openDrawer(page);
    const target = row(page, "Issue Escalations");
    await expect(target).not.toHaveClass(/bg-primary\/10/);
    const more = target.getByRole("button", { name: "Actions for Issue Escalations" });
    await expect(more).toBeVisible();
    const box = await more.boundingBox();
    expect(box?.height ?? 0).toBeGreaterThanOrEqual(40);
    await more.click();
    await expect(page.getByText("Rename", { exact: true })).toBeVisible();
    await expect(page.getByText("Company Operations").first()).toBeVisible();
  });

  test("New space beside Spaces shows a focused draft row in the drawer", async ({ page }) => {
    await openDrawer(page);
    // One heading and one door: the drawer drew a second pair before.
    await expect(page.getByText("Spaces", { exact: true })).toHaveCount(1);
    const plus = page.locator('button[aria-label="New space"]:visible');
    await expect(plus).toHaveCount(1);
    await plus.click();
    const field = page.getByRole("textbox", { name: "New space" });
    await expect(field).toBeVisible({ timeout: 10_000 });
    await expect(field).toBeFocused();
    // Still in the drawer, beside the tree it will join.
    await expect(page.getByText("Company Operations").first()).toBeVisible();
  });
});

test.describe("a row with no actions", () => {
  test.setTimeout(90_000);

  test("keeps its count on hover: a WhatsApp triage stream", async ({ page }) => {
    await page.setViewportSize({ width: 1440, height: 900 });
    await page.route("**/api/**", (route) => route.fulfill({ json: SAFE_EMPTY }));
    await page.route(/\/api\/whatsapp\/accounts(\?.*)?$/, (route) =>
      route.fulfill({
        json: [{ id: "wa1", phone_number: "+91 98450 00000", phone_number_id: "pn1", waba_id: null,
          display_name: "Fracktal Works", avatar_color: "", sync_status: "ok", sync_error: null,
          history_import_phase: 0, quality_rating: null, is_default: true }],
      }),
    );
    await page.route(/\/api\/whatsapp\/streams(\?.*)?$/, (route) =>
      route.fulfill({ json: { needs_reply: 7, waiting: 3, groups: 12, all: 48, snoozed: 0 } }),
    );
    await page.route(/\/api\/whatsapp\/(labels|chats)(\?.*)?$/, (route) => route.fulfill({ json: [] }));
    await page.goto("/whatsapp");
    const stream = row(page, "Waiting on them");
    await expect(stream).toBeVisible({ timeout: 20_000 });
    const meta = stream.locator(".rail-row-meta");
    await expect(meta).toHaveText("3");
    await stream.hover();
    await page.waitForTimeout(300);
    expect(await meta.evaluate((el) => el.getBoundingClientRect().width)).toBeGreaterThan(4);
    await expect(meta).toBeVisible();
  });
});
