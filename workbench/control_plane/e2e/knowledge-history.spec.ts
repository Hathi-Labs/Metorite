import { expect, test } from "@playwright/test";

import { PAGE_ID, mockHistory, openHistory, setupHistory } from "./knowledgeHistoryMock";

/**
 * Page history (WS-61 KB-2g, `knowledge_base.md` §21.7).
 *
 * The BFF at `/api/knowledge/**` is a mock that keeps state. A page has five
 * versions. A member opens Page history, compares version 2 with version 5,
 * sees the words that changed marked in place, turns the marks off and on,
 * and restores version 2. The restore posts to the revert route, and the
 * page then reads the body of version 2. A reader sees no Restore. A version
 * the gateway refuses with 404 says so in words.
 *
 * The real gateway half is `tests/unit/test_kb_history_r8.py`.
 */

test.describe("page history", () => {
  test("a member compares version 2 with version 5 and restores version 2", async ({ page }) => {
    test.setTimeout(120_000);
    await page.setViewportSize({ width: 1440, height: 900 });
    await setupHistory(page);
    const kb = await mockHistory(page);
    const dialog = await openHistory(page);

    // The list: five versions, newest first, each with its author.
    const rows = dialog.getByRole("list", { name: "Versions" }).getByRole("listitem");
    await expect(rows).toHaveCount(5);
    await expect(rows.first()).toContainText("meera");
    await expect(rows.first()).toContainText("Edited, v5");

    // The newest version reads against version 4, with its marks.
    await expect(dialog.getByRole("heading", { level: 3, name: "Refund policy" })).toBeVisible();
    await expect(dialog.locator(".kb-diff-added").first()).toBeVisible();

    // Compare version 5 with version 2.
    await dialog.getByRole("button", { name: "Compare version 5 with another" }).click();
    await dialog.getByRole("checkbox", { name: /Pick version 2/ }).check();
    await dialog.getByRole("button", { name: "Compare", exact: true }).click();
    await expect(dialog.getByText(/Comparing version 5 .* with version 2/)).toBeVisible();

    // "seven" went out and "ten" came in, marked in place. So did the new line.
    await expect(dialog.locator(".kb-diff-removed")).toHaveText(["seven"]);
    await expect(dialog.locator(".kb-diff-added")).toHaveText(["ten", "Keep the receipt."]);
    await expect(dialog.getByText("1 of 2 changes")).toBeVisible();
    await dialog.getByRole("button", { name: "The next change" }).click();
    await expect(dialog.getByText("2 of 2 changes")).toBeVisible();
    await expect(dialog.locator(".kb-diff-current")).toHaveText(["Keep the receipt."]);

    // Highlight changes off draws the version with no marks.
    await dialog.getByRole("checkbox", { name: "Highlight changes" }).uncheck();
    await expect(dialog.locator(".kb-diff-added, .kb-diff-removed")).toHaveCount(0);
    await dialog.getByRole("checkbox", { name: "Highlight changes" }).check();
    await expect(dialog.locator(".kb-diff-removed")).toHaveText(["seven"]);

    // Stop the compare, then restore version 2 from its row. It asks first.
    await dialog.getByRole("button", { name: "Stop the compare" }).click();
    await dialog.getByRole("button", { name: "Restore version 2" }).first().click();
    const confirm = page.getByRole("alertdialog").or(page.getByRole("dialog", { name: "Restore version 2?" }));
    await expect(confirm).toBeVisible();
    await confirm.getByRole("button", { name: "Restore version 2" }).click();

    await expect(page.getByRole("dialog", { name: "Page history" })).toBeHidden();
    expect(kb.writes).toEqual([{ method: "POST", path: `pages/${PAGE_ID}/revert`, body: { version: 2 } }]);
    await expect(page.getByText("Refunds take seven days.")).toBeVisible();
  });

  test("a reader compares and sees no Restore", async ({ page }) => {
    test.setTimeout(90_000);
    await page.setViewportSize({ width: 1440, height: 900 });
    await setupHistory(page);
    const kb = await mockHistory(page, { level: "read" });
    const dialog = await openHistory(page);
    await expect(dialog.getByRole("list", { name: "Versions" }).getByRole("listitem")).toHaveCount(5);
    await expect(dialog.getByRole("button", { name: /Restore/ })).toHaveCount(0);
    await dialog.getByRole("list", { name: "Versions" }).getByRole("listitem").last().getByRole("button").first().click();
    await expect(dialog.getByRole("heading", { level: 3, name: "Refunds" })).toBeVisible();
    expect(kb.writes).toEqual([]);
  });

  test("a version the gateway refuses says so in words", async ({ page }) => {
    test.setTimeout(90_000);
    await page.setViewportSize({ width: 1440, height: 900 });
    await setupHistory(page);
    await mockHistory(page, { missing: [1] });
    const dialog = await openHistory(page);
    await dialog.getByRole("list", { name: "Versions" }).getByRole("listitem").last().getByRole("button").first().click();
    await expect(dialog.getByText("This version is not there for you.")).toBeVisible();
  });

  test("on a phone, two fields take the place of the list", async ({ page }) => {
    test.setTimeout(90_000);
    await page.setViewportSize({ width: 390, height: 844 });
    await setupHistory(page);
    await mockHistory(page);
    await page.goto(`/knowledge/pages/${PAGE_ID}`);
    await page.getByRole("button", { name: "Page history", exact: true }).click({ timeout: 30_000 });
    const dialog = page.getByRole("dialog", { name: "Page history" });
    await expect(dialog.getByRole("button", { name: /^Version/ })).toBeVisible();
    await expect(dialog.getByRole("button", { name: /^Compare with/ })).toBeVisible();
    await expect(dialog.getByRole("list", { name: "Versions" })).toBeHidden();
    await expect(dialog.locator(".kb-diff-added").first()).toBeVisible();
  });
});
