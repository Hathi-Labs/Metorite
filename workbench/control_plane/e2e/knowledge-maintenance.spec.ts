import { expect, test, type Page } from "@playwright/test";

import { COMPANY, OPS, chromePages, mockKnowledge, member, signIn, type Level, type MockPage } from "./knowledge-chrome.mock";

/**
 * The verified pages of Knowledge (WS-61 KB-AI-4, `knowledge_base.md` §22.3).
 *
 * The side panel of a page shows its owner and its review date, with an
 * "Overdue" badge when the date has passed, and a "Verify" button for a
 * writer. The page menu has "Set owner" and "Review every...". A manager of
 * Knowledge gets the Maintenance row, which lists each page once with its
 * reasons as chips. The BFF is the stateful mock of `knowledge-chrome.mock.ts`,
 * so a verify changes the list. The real gateway half is
 * `tests/unit/test_kb_verified_r8.py`.
 */

const DAY = 86_400_000;
const past = (days: number) => new Date(Date.now() - days * DAY).toISOString();

function carePages(): MockPage[] {
  const v = (title: string) => [{ version: 1, title, reason: "create", author: "asha@example.com", ago: 600 }];
  const base = { space_id: COMPANY, parent_id: null, state: "published" as const, author: "asha@example.com" };
  return [
    // Overdue, with an owner: one reason.
    { ...base, id: "kp-exp", title: "Expense policy", body_md: "Claims.", owner: "ravi@example.com", review_at: past(30), versions: v("Expense policy") },
    // Overdue, no owner and stale: three reasons on ONE row.
    { ...base, id: "kp-all", title: "Travel rules", body_md: "Trips.", owner: null, review_at: past(90), stale: true, versions: v("Travel rules") },
    // In good care: not on the list.
    { ...base, id: "kp-ok", title: "Holiday list", body_md: "Days off.", owner: "ravi@example.com", versions: v("Holiday list") },
    ...chromePages().filter((p) => p.id === "kp-ops").map((p) => ({ ...p, owner: "ravi@example.com" })),
  ];
}

async function setup(page: Page, opts: { manager?: boolean; level?: Level } = {}) {
  const manager = opts.manager ?? true;
  await signIn(page);
  const kb = await mockKnowledge(page, {
    levels: { [COMPANY]: opts.level ?? "write", [OPS]: "read" },
    pages: carePages(),
    members: ["ravi@example.com", "meera@example.com"],
  });
  await page.route("**/api/auth/me", (route) =>
    route.fulfill({ json: { ...member(), capabilities: manager ? ["knowledge:manage"] : [] } }),
  );
  await page.route("**/api/people**", (route) =>
    route.fulfill({
      json: {
        rows: [
          { id: "pe-1", name: "Meera Iyer", email: "meera@example.com", status: "active" },
          { id: "pe-2", name: "Outside Person", email: "stranger@elsewhere.com", status: "active" },
        ],
        total: 2,
        hr_visible: false,
        can_manage: false,
      },
    }),
  );
  return kb;
}

const railRow = (page: Page) =>
  page.getByRole("navigation", { name: "Knowledge spaces" }).getByRole("button", { name: /^Maintenance/ });
const menuButton = (page: Page) => page.getByRole("button", { name: "Page actions" });

test.describe("Knowledge maintenance", () => {
  test.beforeEach(async ({ page }) => {
    await page.setViewportSize({ width: 1440, height: 900 });
  });

  test("the list names each page once, and Verify clears Overdue and the row", async ({ page }) => {
    test.setTimeout(120_000);
    const kb = await setup(page);
    await page.goto("/knowledge");
    await expect(railRow(page)).toBeVisible({ timeout: 60_000 });
    await expect(railRow(page)).toContainText("2");
    await railRow(page).click();
    await expect(page).toHaveURL(/\/knowledge\/maintenance$/, { timeout: 60_000 });
    await expect(page.getByRole("heading", { level: 2, name: "Maintenance" })).toBeVisible({ timeout: 60_000 });

    // Each page once, the reasons as chips on that one row.
    await expect(page.locator("[data-maintenance-id]")).toHaveCount(2);
    const all = page.locator('[data-maintenance-id="kp-all"]');
    await expect(all).toHaveCount(1);
    await expect(all.getByText("Overdue", { exact: true })).toBeVisible();
    await expect(all.getByText("No owner", { exact: true }).first()).toBeVisible();
    await expect(all.getByText("Not edited for a year", { exact: true })).toBeVisible();
    await expect(page.locator('[data-maintenance-id="kp-ok"]')).toHaveCount(0);

    // Open the owned page, where the review date is past.
    await page.locator('[data-maintenance-id="kp-exp"]').getByRole("link", { name: "Expense policy" }).click();
    await expect(page).toHaveURL(/\/knowledge\/pages\/kp-exp$/, { timeout: 30_000 });
    const panel = page.getByRole("region", { name: "Page outline and details" });
    await expect(panel.getByText("Overdue", { exact: true })).toBeVisible({ timeout: 30_000 });
    await expect(panel.getByText("Owner")).toBeVisible();
    await panel.getByRole("button", { name: "Verify" }).click();
    await expect(panel.getByText("Overdue", { exact: true })).toHaveCount(0, { timeout: 15_000 });
    expect(kb.writes.map((w) => `${w.method} ${w.path}`)).toEqual(["POST pages/kp-exp/verify"]);

    // The list no longer holds the page, and the count drops.
    await railRow(page).click();
    await expect(page.locator('[data-maintenance-id="kp-exp"]')).toHaveCount(0, { timeout: 15_000 });
    await expect(page.locator("[data-maintenance-id]")).toHaveCount(1);
    await expect(railRow(page)).toContainText("1");
  });

  test("a writer sets the owner from the menu, and an outside address shows the refusal inline", async ({ page }) => {
    test.setTimeout(120_000);
    const kb = await setup(page);
    await page.goto("/knowledge/pages/kp-all");
    await expect(page.getByRole("heading", { level: 2 }).first()).toBeVisible({ timeout: 60_000 });
    await menuButton(page).click();
    await page.getByRole("menu").getByText("Set owner", { exact: true }).click();
    const dialog = page.getByRole("dialog", { name: "Set owner" });
    await dialog.getByRole("textbox").fill("m");
    await dialog.getByRole("button", { name: /Outside Person/ }).click({ timeout: 15_000 });
    await dialog.getByRole("button", { name: "Set owner" }).click();
    await expect(dialog.getByRole("alert")).toHaveText("The owner must be a member of your organization.");

    await dialog.getByRole("button", { name: /Meera Iyer/ }).click();
    await dialog.getByRole("button", { name: "Set owner" }).click();
    await expect(dialog).toHaveCount(0, { timeout: 15_000 });
    expect(kb.writes.filter((w) => w.method === "PATCH").map((w) => w.body)).toEqual([
      { owner_email: "stranger@elsewhere.com" },
      { owner_email: "meera@example.com" },
    ]);
  });

  test("a writer sets the review interval from the menu", async ({ page }) => {
    test.setTimeout(120_000);
    const kb = await setup(page);
    await page.goto("/knowledge/pages/kp-ok");
    await expect(page.getByRole("heading", { level: 2 }).first()).toBeVisible({ timeout: 60_000 });
    await menuButton(page).click();
    await page.getByRole("menu").getByText("Review every…", { exact: true }).click();
    const dialog = page.getByRole("dialog", { name: "Review every" });
    await dialog.getByRole("button", { name: "Review every", exact: true }).click();
    await page.getByRole("option", { name: "90 days" }).click();
    await dialog.getByRole("button", { name: "Save" }).click();
    await expect(dialog).toHaveCount(0, { timeout: 15_000 });
    expect(kb.writes.filter((w) => w.method === "PATCH").map((w) => w.body)).toEqual([{ review_every_days: 90 }]);
  });

  test("a reader sees no Verify, no Set owner and no Review every", async ({ page }) => {
    await setup(page, { manager: false, level: "read" });
    await page.goto("/knowledge/pages/kp-all");
    const panel = page.getByRole("region", { name: "Page outline and details" });
    await expect(panel.getByText("Overdue", { exact: true })).toBeVisible({ timeout: 60_000 });
    await expect(panel.getByRole("button", { name: "Verify" })).toHaveCount(0);
    await menuButton(page).click();
    const items = (await page.getByRole("menu").locator('[role="menuitem"], [role="menuitemcheckbox"]').allTextContents()).map((t) => t.trim());
    expect(items).not.toContain("Set owner");
    expect(items).not.toContain("Review every…");
    await page.keyboard.press("Escape");
    // A member without manage has no Maintenance row.
    await expect(page.getByRole("navigation", { name: "Knowledge spaces" }).getByRole("button", { name: /^Spaces/ })).toBeVisible();
    await expect(railRow(page)).toHaveCount(0);
  });
});
