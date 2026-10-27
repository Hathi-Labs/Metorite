import { expect, test, type Page } from "@playwright/test";

import { ME_EMAIL, SPACE_ID, busyPages, setupHome } from "./knowledgeHomeMock";

/**
 * The page icon, the favourites, the home and the space switcher (WS-61
 * KB-2f, `knowledge_base.md` §21.7).
 *
 * The BFF is the stateful mock of `knowledgeHomeMock.ts`. A writer sets an
 * icon from the picker, and the icon shows in the title, the tree, the home
 * and the search hits. The PATCH carries the icon alone. A star in the title
 * row and in the tree menu adds the page to Favourites. The three home tabs
 * each list their pages or say why they are empty, and Load more reads the
 * next page. The switcher at the top of the rail finds a space by name.
 *
 * The real gateway half is `tests/unit/test_kb_home_r8.py`.
 */

const row = (page: Page, id: string) => page.locator(`[data-page-id="${id}"]`);

test.describe("Knowledge home, icon and favourites", () => {
  test("a writer sets an icon, and it shows in the title, the tree, the home and search", async ({ page }) => {
    test.setTimeout(120_000);
    await page.setViewportSize({ width: 1440, height: 900 });
    const kb = await setupHome(page, { level: "write", pages: busyPages() });

    await page.goto("/knowledge/pages/kp-10");
    await expect(page.getByRole("heading", { level: 2, name: "Code of conduct" })).toBeVisible({ timeout: 30_000 });
    await page.getByRole("heading", { level: 2, name: "Code of conduct" }).hover();
    await page.getByRole("button", { name: "Add icon" }).click();
    const picker = page.getByRole("dialog", { name: "Page icon" });
    await expect(picker.getByText("Loading emoji…")).toHaveCount(0, { timeout: 15_000 });
    await picker.getByRole("combobox", { name: "Search emoji" }).fill("rocket");
    await expect(picker.getByRole("option").first()).toHaveAttribute("aria-label", "rocket");
    await page.keyboard.press("Enter");
    await expect(picker).toHaveCount(0);

    // The PATCH carries the icon alone, so the gateway writes no version.
    expect(kb.writes).toEqual([{ method: "PATCH", path: "pages/kp-10", body: { icon: "🚀" } }]);
    await expect(page.getByRole("button", { name: "Change the page icon, now 🚀" })).toBeVisible();
    // The tree.
    await expect(row(page, "kp-10").locator("[data-page-icon]")).toHaveText("🚀");
    // The home list.
    await page.goto("/knowledge");
    await expect(page.getByRole("tab", { name: "Recently updated" })).toBeVisible({ timeout: 30_000 });
    await expect(page.getByRole("link", { name: /Code of conduct/ }).locator("[data-page-icon]")).toHaveText("🚀");
    // The search hits.
    await page.goto("/knowledge/search?q=code");
    await expect(page.getByRole("link", { name: /Code of conduct/ }).locator("[data-page-icon]")).toHaveText("🚀", {
      timeout: 30_000,
    });

    // Remove takes the icon away.
    await page.goto("/knowledge/pages/kp-10");
    await page.getByRole("button", { name: "Change the page icon, now 🚀" }).click();
    await page.getByRole("dialog", { name: "Page icon" }).getByRole("button", { name: "Remove" }).click();
    await expect(page.getByRole("button", { name: "Add icon" })).toBeAttached();
    expect(kb.writes.at(-1)).toEqual({ method: "PATCH", path: "pages/kp-10", body: { icon: "" } });
  });

  test("a reader sees the icon and the star, and no Add icon", async ({ page }) => {
    await setupHome(page, { level: "read", pages: busyPages() });
    await page.goto("/knowledge/pages/kp-0");
    await expect(page.getByRole("heading", { level: 2, name: "Refund policy" })).toBeVisible({ timeout: 30_000 });
    await expect(page.getByRole("img", { name: "Page icon 💸" })).toBeVisible();
    await expect(page.getByRole("button", { name: /page icon/i })).toHaveCount(0);
    await expect(page.getByRole("button", { name: "Add to favourites: Refund policy" })).toBeVisible();
    await page.goto("/knowledge/pages/kp-1");
    await expect(page.getByRole("heading", { level: 2, name: "Holidays" })).toBeVisible({ timeout: 30_000 });
    await expect(page.getByRole("button", { name: "Add icon" })).toHaveCount(0);
  });

  test("the star of the title row and of the tree menu fills Favourites", async ({ page }) => {
    test.setTimeout(90_000);
    const kb = await setupHome(page, { level: "write", pages: busyPages() });
    await page.goto("/knowledge");
    await page.getByRole("tab", { name: "Favourites" }).click({ timeout: 30_000 });
    await expect(page.getByText("No favourites yet.")).toBeVisible();

    await page.goto("/knowledge/pages/kp-2");
    const star = page.getByRole("button", { name: "Add to favourites: Travel" });
    await star.click({ timeout: 30_000 });
    await expect(page.getByRole("button", { name: "Remove from favourites: Travel" })).toHaveAttribute("aria-pressed", "true");

    // The tree menu of another row.
    await row(page, "kp-5").hover();
    await page.getByRole("button", { name: "Actions for Expense claims" }).click();
    await page.getByRole("menu").getByRole("menuitem", { name: "Add to favourites" }).click();
    expect(kb.writes.map((w) => `${w.method} ${w.path}`)).toEqual([
      "POST pages/kp-2/favorite",
      "POST pages/kp-5/favorite",
    ]);
    await row(page, "kp-2").hover();
    await page.getByRole("button", { name: "Actions for Travel" }).click();
    await expect(page.getByRole("menu").getByRole("menuitem", { name: "Remove from favourites" })).toBeVisible();
    await page.keyboard.press("Escape");

    await page.goto("/knowledge");
    await page.getByRole("tab", { name: "Favourites" }).click({ timeout: 30_000 });
    const panel = page.getByRole("tabpanel");
    await expect(panel.getByRole("link")).toHaveText([/Expense claims/, /Travel/]);
  });

  test("Recently updated pages with Load more, and Created by me keeps the member's own", async ({ page }) => {
    await setupHome(page, { level: "read", pages: busyPages() });
    await page.goto("/knowledge");
    const panel = page.getByRole("tabpanel");
    await expect(panel.getByRole("link")).toHaveCount(20, { timeout: 30_000 });
    await expect(panel.getByRole("link").first()).toContainText("Refund policy");
    await panel.getByRole("button", { name: "Load more" }).click();
    await expect(panel.getByRole("link")).toHaveCount(23);
    await expect(panel.getByRole("button", { name: "Load more" })).toHaveCount(0);

    await page.getByRole("tab", { name: "Created by me" }).click();
    const mine = busyPages().filter((p) => p.author === ME_EMAIL);
    await expect(panel.getByRole("link")).toHaveCount(mine.length);
    await expect(panel.getByText("Draft")).toBeVisible();
  });

  test("an empty home says so, with one next act", async ({ page }) => {
    await setupHome(page, { level: "write", pages: [] });
    await page.goto("/knowledge");
    await expect(page.getByText("No pages yet.")).toBeVisible({ timeout: 30_000 });
    await page.getByRole("tab", { name: "Created by me" }).click();
    await expect(page.getByText("You have not written a page yet.")).toBeVisible();
    await page.getByRole("tabpanel").getByRole("button", { name: "New page" }).click();
    await expect(page.getByRole("dialog")).toBeVisible();
  });

  test("the switcher at the top of the rail finds a space by name", async ({ page }) => {
    await page.setViewportSize({ width: 1280, height: 800 });
    await setupHome(page, { level: "read", pages: busyPages() });
    await page.goto("/knowledge");
    const nav = page.getByRole("navigation", { name: "Knowledge spaces" });
    await nav.getByRole("button", { name: "Switch space" }).click({ timeout: 30_000 });
    await page.getByRole("textbox").last().fill("oper");
    await expect(page.getByRole("option")).toHaveText([/Operations/]);
    await page.getByRole("option", { name: /Operations/ }).click();
    await expect(page).toHaveURL(/\/knowledge\?space=ks-ops/);
    await expect(nav.getByRole("button", { name: "Switch space" })).toContainText("Operations");
    await nav.getByRole("button", { name: "Switch space" }).click();
    await page.getByRole("option", { name: /Company/ }).click();
    await expect(page).toHaveURL(new RegExp(`space=${SPACE_ID}`));
  });
});
