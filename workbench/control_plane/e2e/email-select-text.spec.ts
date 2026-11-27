import { expect, test, type Page } from "@playwright/test";

import { ACCOUNT, IDS, installEmailMocks } from "./email-message-fixtures";

/**
 * A member can select and copy the text of a received mail (task 3392, owner
 * report 2026-11-15). The page root is `select-none`, so the subject, the
 * sender and a plain-text body needed `select-text`. The list row and the
 * buttons must stay unselectable. The captures go to `E2E_SELECT_SHOTS`.
 */
const SHOTS = process.env.E2E_SELECT_SHOTS ?? "";

async function open(page: Page, id: string) {
  await installEmailMocks(page, [id]);
  await page.goto(`/email?email=${id}&account=${ACCOUNT.id}`);
  const actions = page.locator(`[data-message-actions="${id}"]`);
  // On the phone the list shows alone: tap the row to open the mail.
  const row = page.locator(`[data-email-row="${id}"] button.select-none`).first();
  await expect(actions.or(row).first()).toBeVisible({ timeout: 60_000 });
  if (!(await actions.isVisible())) await row.click();
  await expect(actions).toBeVisible({ timeout: 60_000 });
  // The captures are in light mode.
  await page.evaluate(() => document.documentElement.classList.add("light"));
}

const selected = (page: Page) => page.evaluate(() => window.getSelection()?.toString() ?? "");

async function shot(page: Page, name: string) {
  if (SHOTS) await page.screenshot({ path: `${SHOTS}/${name}.png` });
}

for (const [label, size] of [
  ["desktop", { width: 1440, height: 900 }],
  ["phone", { width: 390, height: 844 }],
] as const) {
  test.describe(`Select text, ${label}`, () => {
    test.describe.configure({ timeout: 120_000 });
    test.use({ viewport: size });

    test("plain-text body, subject and sender select", async ({ page }) => {
      await open(page, IDS.text);
      const body = page.locator("[data-email-body]").first();
      await body.getByText("This is a plain text mail").dblclick({ position: { x: 12, y: 8 } });
      expect((await selected(page)).trim().length).toBeGreaterThan(0);
      await page.getByRole("heading", { name: "Plain text note" }).click({ clickCount: 3 });
      expect(await selected(page)).toContain("Plain text note");
      await page.getByText(/<.+@.+>/).first().click({ clickCount: 3 });
      expect(await selected(page)).toContain("@");
      // The first line of the body: a triple click selects that line.
      await body.getByText("This is a plain text mail").click({ position: { x: 12, y: 8 }, clickCount: 3 });
      expect(await selected(page)).toContain("Hi,");
      await shot(page, `${label}-text-light`);
    });

    test("HTML body selects inside its frame", async ({ page }) => {
      await open(page, IDS.plain);
      const frame = page.locator('iframe[title="Email content"]').first();
      await expect(frame).toBeVisible();
      const el = await frame.evaluateHandle((f: HTMLIFrameElement) => {
        const doc = f.contentDocument!;
        const walker = doc.createTreeWalker(doc.body, NodeFilter.SHOW_TEXT);
        let n: Node | null;
        while ((n = walker.nextNode())) if ((n.textContent ?? "").trim().length > 3) return n.parentElement;
        return null;
      });
      const box = await (el.asElement() as NonNullable<ReturnType<typeof el.asElement>>).boundingBox();
      const fbox = (await frame.boundingBox())!;
      expect(box).not.toBeNull();
      await page.mouse.click(fbox.x + box!.x - fbox.x + 10, fbox.y + (box!.y - fbox.y) + 5, { clickCount: 3 });
      const text = await frame.evaluate((f: HTMLIFrameElement) => f.contentWindow!.getSelection()!.toString());
      expect(text.trim().length).toBeGreaterThan(3);
      await shot(page, `${label}-html-light`);
    });

    test("the list row and the buttons stay unselectable", async ({ page }) => {
      await open(page, IDS.text);
      const noSelect = await page.evaluate(() => {
        const row = document.querySelector("button.select-none, [class*='select-none']");
        return {
          root: getComputedStyle(document.querySelector("div.select-none")!).userSelect,
          row: row ? getComputedStyle(row).userSelect : "none",
        };
      });
      expect(noSelect.root).toBe("none");
      expect(noSelect.row).toBe("none");
    });
  });
}
