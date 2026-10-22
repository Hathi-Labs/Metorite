import { expect, test, type Page } from "@playwright/test";

import { COMPANY, OPS, chromePages, mockKnowledge, signIn, type Level } from "./knowledge-chrome.mock";

/**
 * The page chrome of Knowledge (WS-61 KB-2d, `knowledge_base.md` §21.7).
 *
 * One test for each act: the breadcrumbs and their `…`, the byline and its
 * contributors, the Outline and Details tabs, the copy-link anchor of a
 * heading, each entry of the page menu, the Archived view with Restore, and
 * the banner on an archived page. A reader sees no Move, Archive or Restore.
 *
 * The BFF is a stateful mock (`knowledge-chrome.mock.ts`). The real gateway
 * half is `tests/unit/test_kb_pages_r8.py`.
 */

async function open(page: Page, id: string, levels: Partial<Record<string, Level>> = { [COMPANY]: "manage", [OPS]: "write" }) {
  await signIn(page);
  const kb = await mockKnowledge(page, { levels, pages: chromePages() });
  await page.goto(`/knowledge/pages/${id}`);
  await expect(page.getByRole("heading", { level: 2 }).first()).toBeVisible({ timeout: 30_000 });
  return kb;
}

const crumbs = (page: Page) => page.getByRole("navigation", { name: "Breadcrumbs" });
const menuButton = (page: Page) => page.getByRole("button", { name: "Page actions" });

async function menuItems(page: Page): Promise<string[]> {
  await menuButton(page).click();
  const items = await page.getByRole("menu").locator('[role="menuitem"], [role="menuitemcheckbox"]').allTextContents();
  await page.keyboard.press("Escape");
  return items.map((t) => t.trim());
}

async function pick(page: Page, label: string) {
  await menuButton(page).click();
  await page.getByRole("menu").getByText(label, { exact: true }).click();
}

test.describe("Knowledge page chrome", () => {
  test.beforeEach(async ({ page }) => {
    await page.setViewportSize({ width: 1440, height: 900 });
  });

  test("the breadcrumbs fold the middle into a … list", async ({ page }) => {
    await open(page, "kp-r");
    const nav = crumbs(page);
    await expect(nav.getByRole("link", { name: "Company" })).toBeVisible();
    await expect(nav.getByText("Refunds")).toHaveAttribute("aria-current", "page");
    await expect(nav.getByRole("link", { name: "Handbook" })).toHaveCount(0);
    await nav.getByRole("button", { name: "Show 3 hidden pages of the path" }).click();
    const list = page.getByRole("dialog", { name: "The path to this page" });
    await expect(list.getByRole("link")).toHaveText(["Handbook", "Policies", "Travel"]);
    await list.getByRole("link", { name: "Policies" }).click();
    await expect(page).toHaveURL(/\/knowledge\/pages\/kp-p$/);
    await expect(page.getByRole("heading", { level: 2, name: "Policies" })).toBeVisible();
    // Company > Handbook > Policies is three crumbs, so nothing folds.
    await expect(crumbs(page).getByRole("link", { name: "Handbook" })).toBeVisible();
  });

  test("on a phone, every crumb is in the list", async ({ page }) => {
    await page.setViewportSize({ width: 390, height: 844 });
    await open(page, "kp-r");
    await crumbs(page).getByRole("button", { name: "Show the path to this page" }).click();
    const list = page.getByRole("dialog", { name: "The path to this page" });
    await expect(list.getByRole("link")).toHaveText(["Company", "Handbook", "Policies", "Travel", "Refunds"]);
  });

  test("the byline names the last editor, and lists the contributors", async ({ page }) => {
    await open(page, "kp-r");
    const byline = page.locator("[data-page-byline]");
    await expect(byline).toContainText("Edited by");
    await expect(byline).toContainText("meera, 3m ago");
    await byline.getByRole("button", { name: "3 contributors" }).click();
    const list = page.getByRole("dialog", { name: "Contributors to this page" });
    await expect(list.getByRole("listitem")).toHaveCount(3);
    await expect(list.getByRole("listitem").nth(0)).toContainText("meera");
    await expect(list.getByRole("listitem").nth(2)).toContainText("asha");
    await page.keyboard.press("Escape");
    await expect(list).toHaveCount(0);
  });

  test("the outline follows the scroll, and a click goes to the heading", async ({ page }) => {
    await page.setViewportSize({ width: 1440, height: 700 });
    await open(page, "kp-r");
    const outline = page.getByRole("navigation", { name: "Outline" });
    await expect(outline.getByRole("button")).toHaveText(["Scope", "Who may ask", "How to ask"]);
    await expect(outline.getByRole("button", { name: "Scope" })).toHaveAttribute("aria-current", "location");

    await outline.getByRole("button", { name: "How to ask" }).click();
    await expect(outline.getByRole("button", { name: "How to ask" })).toHaveAttribute("aria-current", "location");
    // Scroll back to the top by hand: the mark follows.
    await page.locator("[data-page-scroller]").evaluate((el) => el.scrollTo({ top: 0 }));
    await expect(outline.getByRole("button", { name: "Scope" })).toHaveAttribute("aria-current", "location");

    await outline.getByRole("button", { name: "Who may ask" }).click();
    await expect(outline.getByRole("button", { name: "Who may ask" })).toHaveAttribute("aria-current", "location");
    // The jump is a smooth scroll, so the heading arrives over a few frames.
    const heading = page.getByRole("heading", { name: "Who may ask", exact: true });
    await expect.poll(async () => (await heading.boundingBox())?.y ?? 9999).toBeLessThan(300);
  });

  test("Details shows the author, the last editor, the counts, the review date and the state", async ({ page }) => {
    await open(page, "kp-r");
    await page.getByRole("tab", { name: "Details" }).click();
    const aside = page.getByRole("complementary", { name: "About this page" });
    const fact = (label: string) => aside.locator("dl > div").filter({ has: page.getByText(label, { exact: true }) });
    await expect(fact("Author")).toContainText("asha");
    await expect(fact("Last edited by")).toContainText("meera");
    // The body holds 3 headings and 8 paragraphs of 6 lines of 10 words.
    await expect(fact("Words").locator("dd")).toHaveText("487");
    await expect(fact("Characters").locator("dd")).toHaveText(/^\d{1,2},\d{3}$/);
    await expect(aside.getByText("14 Apr 2027")).toBeVisible();
    await expect(aside.getByText("Published", { exact: true })).toBeVisible();
  });

  test("a page with fewer than two headings shows no outline", async ({ page }) => {
    await open(page, "kp-one");
    await expect(page.getByRole("heading", { name: "Only heading" })).toBeVisible();
    await expect(page.getByRole("tab", { name: "Outline" })).toHaveCount(0);
    await expect(page.getByRole("navigation", { name: "Outline" })).toHaveCount(0);
    await expect(page.getByRole("heading", { level: 3, name: "Details" })).toBeVisible();
  });

  test("a heading's anchor copies its address, and the address opens at the heading", async ({ page, context }) => {
    await context.grantPermissions(["clipboard-read", "clipboard-write"]);
    await page.setViewportSize({ width: 1440, height: 700 });
    await open(page, "kp-r");
    await page.getByRole("heading", { name: "Who may ask", exact: true }).hover();
    const anchor = page.getByRole("button", { name: "Copy the link to the heading Who may ask" });
    await expect(anchor).toHaveCSS("opacity", "1");
    await anchor.click();
    await expect(page.getByText("Link to the heading copied")).toBeVisible();
    const copied = await page.evaluate(() => navigator.clipboard.readText());
    expect(copied).toMatch(/\/knowledge\/pages\/kp-r#who-may-ask$/);

    await page.goto("/knowledge/pages/kp-r#how-to-ask");
    const outline = page.getByRole("navigation", { name: "Outline" });
    await expect(outline.getByRole("button", { name: "How to ask" })).toHaveAttribute("aria-current", "location", {
      timeout: 30_000,
    });
  });

  test("the menu copies the link and the Markdown, and downloads the .md", async ({ page, context }) => {
    await context.grantPermissions(["clipboard-read", "clipboard-write"]);
    await open(page, "kp-one");
    await pick(page, "Copy link");
    await expect(page.getByText("Link copied")).toBeVisible();
    expect(await page.evaluate(() => navigator.clipboard.readText())).toMatch(/\/knowledge\/pages\/kp-one$/);

    await pick(page, "Copy as Markdown");
    await expect(page.getByText("Markdown copied")).toBeVisible();
    expect(await page.evaluate(() => navigator.clipboard.readText())).toBe("# Short note\n\n# Only heading\n\nOne line.\n");

    const download = page.waitForEvent("download");
    await pick(page, "Download as .md");
    const file = await download;
    expect(file.suggestedFilename()).toBe("Short note.md");
    const path = await file.path();
    const fs = await import("node:fs");
    expect(fs.readFileSync(path, "utf-8")).toBe("# Short note\n\n# Only heading\n\nOne line.\n");
  });

  test("Print prints the title and the body in a frame of their own", async ({ page }) => {
    await open(page, "kp-one");
    await page.evaluate(() => {
      new MutationObserver(() => {
        const frame = document.querySelector<HTMLIFrameElement>("iframe[data-kb-print-frame]");
        if (frame?.contentWindow && !(frame as unknown as { stubbed?: boolean }).stubbed) {
          (frame as unknown as { stubbed?: boolean }).stubbed = true;
          frame.contentWindow.print = () => {
            (window as unknown as { printed?: string }).printed = frame.contentDocument?.body.innerText ?? "";
          };
        }
      }).observe(document.body, { childList: true });
    });
    await pick(page, "Print");
    await expect.poll(() => page.evaluate(() => (window as unknown as { printed?: string }).printed ?? "")).toContain(
      "Short note",
    );
    const printed = await page.evaluate(() => (window as unknown as { printed?: string }).printed ?? "");
    expect(printed).toContain("One line.");
    // The frame holds no shell: no rail, no app bar.
    expect(printed).not.toContain("Spaces");
  });

  test("Full width is kept in the browser", async ({ page }) => {
    await open(page, "kp-one");
    const scroller = page.locator("[data-full-width]");
    await expect(scroller).toHaveCount(0);
    await pick(page, "Full width");
    await expect(scroller).toHaveCount(1);
    await page.reload();
    await expect(page.getByRole("heading", { level: 2, name: "Short note" })).toBeVisible({ timeout: 30_000 });
    await expect(page.locator("[data-full-width]")).toHaveCount(1);
    await menuButton(page).click();
    await expect(page.getByRole("menu").getByRole("menuitemcheckbox", { name: "Full width" })).toHaveAttribute(
      "aria-checked",
      "true",
    );
  });

  test("Move sends the page to another space", async ({ page }) => {
    const kb = await open(page, "kp-one");
    await pick(page, "Move");
    const dialog = page.getByRole("dialog", { name: "Move page" });
    await expect(dialog.getByRole("button", { name: "Move page" })).toBeDisabled();
    await dialog.getByRole("button", { name: "Space" }).click();
    await page.getByRole("option", { name: "Operations" }).click();
    await dialog.getByRole("button", { name: "Move page" }).click();
    await expect(page.getByText("Moved to Operations")).toBeVisible();
    expect(kb.writes).toEqual([{ method: "POST", path: "pages/kp-one/move", body: { space_id: OPS, parent_id: null } }]);
    await expect(crumbs(page).getByRole("link", { name: "Operations" })).toBeVisible();
  });

  test("Move puts the page under another parent in its space", async ({ page }) => {
    const kb = await open(page, "kp-one");
    await pick(page, "Move");
    const dialog = page.getByRole("dialog", { name: "Move page" });
    await dialog.getByRole("button", { name: "Parent" }).click();
    await page.getByRole("option", { name: "Policies" }).click();
    await dialog.getByRole("button", { name: "Move page" }).click();
    await expect(page.getByText("Moved to Company")).toBeVisible();
    expect(kb.writes).toEqual([{ method: "POST", path: "pages/kp-one/move", body: { parent_id: "kp-p" } }]);
    // Company > Handbook > Policies > Short note: four crumbs, so the middle folds.
    await crumbs(page).getByRole("button", { name: "Show 2 hidden pages of the path" }).click();
    await expect(page.getByRole("dialog", { name: "The path to this page" }).getByRole("link")).toHaveText([
      "Handbook",
      "Policies",
    ]);
  });

  test("Archive asks first, the page shows a banner, and Restore puts it back", async ({ page }) => {
    const kb = await open(page, "kp-one");
    await pick(page, "Archive");
    const ask = page.getByRole("dialog", { name: "Archive this page?" });
    await ask.getByRole("button", { name: "Archive page" }).click();
    const banner = page.locator("[data-archived-banner]");
    await expect(banner).toBeVisible();
    await expect(banner).toContainText("This page is out of the tree");
    expect(await menuItems(page)).toContain("Restore");

    await pick(page, "Restore");
    await expect(banner).toHaveCount(0);
    await expect(page.getByText("Restored Short note")).toBeVisible();
    expect(kb.writes.map((w) => `${w.method} ${w.path}`)).toEqual([
      "POST pages/kp-one/archive",
      "POST pages/kp-one/restore",
    ]);
  });

  test("the banner on an archived page restores it", async ({ page }) => {
    const kb = await open(page, "kp-old");
    const banner = page.locator("[data-archived-banner]");
    await expect(banner).toBeVisible();
    await expect(page.getByRole("button", { name: "Edit", exact: true })).toHaveCount(0);
    await banner.getByRole("button", { name: "Restore page" }).click();
    await expect(banner).toHaveCount(0);
    expect(kb.writes.map((w) => `${w.method} ${w.path}`)).toEqual(["POST pages/kp-old/restore"]);
  });

  test("a manager opens the Archived view from the space, and restores a page", async ({ page }) => {
    await signIn(page);
    const kb = await mockKnowledge(page, { levels: { [COMPANY]: "manage" }, pages: chromePages() });
    await page.goto(`/knowledge?space=${COMPANY}`);
    await expect(page.getByRole("heading", { level: 2, name: "Company" })).toBeVisible({ timeout: 30_000 });
    // A click before the app has hydrated is lost on the dev server, so retry it.
    await expect(async () => {
      await page.getByRole("link", { name: "Archived pages" }).click();
      await expect(page).toHaveURL(/\/knowledge\?space=ks-company&view=archived$/, { timeout: 3_000 });
    }).toPass({ timeout: 30_000 });
    await expect(page.getByRole("heading", { level: 2, name: "Archived pages" })).toBeVisible({ timeout: 30_000 });
    const row = page.locator('[data-archived-page="kp-old"]');
    await expect(row).toContainText("Old process");
    await row.getByRole("button", { name: "Restore Old process" }).click();
    await expect(row).toHaveCount(0);
    await expect(page.getByText("No archived pages in Company.")).toBeVisible();
    expect(kb.writes.map((w) => `${w.method} ${w.path}`)).toEqual(["POST pages/kp-old/restore"]);
  });

  test("a reader sees no Move, Archive or Restore, and no Archived view", async ({ page }) => {
    await open(page, "kp-one", { [COMPANY]: "read", [OPS]: "read" });
    expect(await menuItems(page)).toEqual(["Copy link", "Copy as Markdown", "Download as .md", "Export page", "Print", "Full width", "Favourite"]);

    await page.goto("/knowledge/pages/kp-old");
    const banner = page.locator("[data-archived-banner]");
    await expect(banner).toContainText("A manager of the space can restore it.", { timeout: 30_000 });
    await expect(banner.getByRole("button")).toHaveCount(0);
    expect(await menuItems(page)).not.toContain("Restore");

    await page.goto(`/knowledge?space=${COMPANY}`);
    await expect(page.getByRole("heading", { level: 2, name: "Company" })).toBeVisible({ timeout: 30_000 });
    await expect(page.getByRole("link", { name: "Archived pages" })).toHaveCount(0);
    await page.goto(`/knowledge?space=${COMPANY}&view=archived`);
    await expect(page.getByText("Only a manager of Company sees its archived pages.")).toBeVisible({ timeout: 30_000 });
  });

  test("a writer gets Move and no Archive", async ({ page }) => {
    await open(page, "kp-one", { [COMPANY]: "write", [OPS]: "write" });
    const items = await menuItems(page);
    expect(items).toContain("Move");
    expect(items).not.toContain("Archive");
    expect(items).not.toContain("Restore");
  });
});
