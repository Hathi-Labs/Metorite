import type { Page } from "@playwright/test";

/**
 * Open every row of the Projects rail, the way a member does: by its toggle.
 *
 * The rail starts CLOSED since 2026-10-10 (`app/projects/lib/treeFold.ts`,
 * owner ask). A spec that needs a child row on screen opens the tree first,
 * rather than the app keeping a test-only default.
 *
 * Opens the first "Expand …" toggle until none is left, so a row that only
 * appears once its parent opens is reached too. The cap stops a loop if a
 * toggle ever fails to open its row.
 *
 * ⚠️ By the KEYBOARD, not a click. At rest the chevron is transparent and the
 * row's label sits over it, on purpose: a tap on the icon selects the row
 * (`rail-rows.spec.ts`). A click waits on that cover until the test times
 * out. Focus and Enter is the path the rail promises to keep.
 */
export async function openAllRows(page: Page, cap = 60): Promise<void> {
  const toggles = page.locator('[data-rail-row] button[aria-label^="Expand "] >> visible=true');
  for (let i = 0; i < cap; i += 1) {
    if ((await toggles.count()) === 0) return;
    await toggles.first().focus();
    await page.keyboard.press("Enter");
  }
}
