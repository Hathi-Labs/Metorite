import { expect, test, type Page, type Route } from "@playwright/test";

/**
 * A scroll in the reply composer moves the draft, never the page.
 *
 * Owner report, 2026-10-10. The owner scrolled in an AI draft of a reply, and
 * the whole app moved up and left a blank band at the bottom.
 *
 * The cause, measured with this spec on the code before the fix:
 *
 *   1. After a draft, `DraftAssistant` draws an `sr-only` summary. `sr-only`
 *      is `position: absolute`, and no ancestor of it was positioned. So its
 *      containing block was the initial one, and it escaped each
 *      `overflow: hidden` box of the shell. It sat at its static place in the
 *      long thread (top 2022 px in a 720 px window) and made the document
 *      2023 px tall.
 *   2. A wheel at the end of the textarea went to the thread, then to the
 *      document, which then scrolled by 1303 px. `html` scrolled, and nothing
 *      of the app was left on screen.
 *
 * `src/lib/scrollWithin.test.ts` holds the source half of the fence.
 */

const ACCOUNT = {
  id: "7c9e6679-7425-40de-944b-e07fc1f90ae7",
  provider: "gmail",
  email_address: "me@example.com",
  label: "Work",
  unread_count: 0,
  sync_enabled: true,
  sync_status: "idle",
  initial_sync_done: true,
};
const MAIL = "0f8fad5b-d9cb-469f-a165-70867728950e";
// A thread taller than the window, so the composer opens below the fold.
const THREAD = Array.from({ length: 60 }, (_, i) => `Line ${i} of the thread.`).join("\n");
// A draft taller than the six rows of the composer, so the textarea scrolls.
const DRAFT = Array.from({ length: 40 }, (_, i) => `Line ${i} of the draft.`).join("\n");

function message() {
  return {
    id: MAIL,
    provider_message_id: "pm-0f8f",
    thread_id: "t-0f8f",
    account_id: ACCOUNT.id,
    from_address: { name: "Ravi", email: "ravi@contoso.test" },
    to_addresses: [{ name: "Me", email: "me@example.com" }],
    subject: "BQ quote for the extruder",
    body_text: `The body of the BQ quote.\n${THREAD}`,
    snippet: "BQ quote",
    folder: "inbox",
    is_read: true,
    received_at: "2026-10-08T10:00:00Z",
    has_attachments: false,
    attachments: [],
  };
}

async function installMocks(page: Page) {
  const json = (route: Route, body: unknown) =>
    route.fulfill({ status: 200, contentType: "application/json", body: JSON.stringify(body) });
  // The fallback first: Playwright tries the LAST route first.
  await page.route("**/api/**", (r) => json(r, {}));
  await page.route("**/api/health", (r) => json(r, { gateway: "up" }));
  await page.route("**/api/email/accounts", (r) => json(r, [ACCOUNT]));
  await page.route(/.*\/api\/email\/accounts\/[^/]+\/folders.*/, (r) =>
    json(r, [{ provider_folder_id: "inbox", name: "Inbox", type: "system", message_count: 1, unread_count: 0 }]),
  );
  await page.route(/.*\/api\/email\/accounts\/[^/]+\/labels.*/, (r) => json(r, []));
  await page.route(/.*\/api\/email\/messages\/facets.*/, (r) =>
    json(r, { folder: "inbox", total: 0, unread: 0, labels: {} }));
  await page.route(/.*\/api\/email\/(messages|search)\?.*/, (r) =>
    json(r, { emails: [message()], total: 1, page: 1, page_size: 50 }));
  await page.route(new RegExp(`.*/api/email/messages/${MAIL}([?].*)?$`), (r) => json(r, message()));
  await page.route(/.*\/api\/email\/contacts\/suggest.*/, (r) => json(r, []));
  // One round of the AI draft, with one finished step, as the stream sends it.
  const events = [
    { type: "activity", kind: "stage", id: "context", status: "start" },
    { type: "activity", kind: "stage", id: "context", status: "done", detail: "read the thread" },
    { type: "delta", kind: "content", text: DRAFT },
    { type: "done", draft: DRAFT },
  ];
  await page.route("**/api/email/compose-assist/stream", (r) =>
    r.fulfill({
      status: 200,
      contentType: "text/event-stream",
      body: events.map((e) => `data: ${JSON.stringify(e)}\n\n`).join(""),
    }));
}

/**
 * Each box from the composer up to `html` that scrolled, other than the two
 * that may: the textarea and the thread. Also the height of the document.
 */
async function shifted(page: Page) {
  return page.evaluate(() => {
    const box = document.querySelector<HTMLElement>('textarea[placeholder^="Write your"]');
    const moved: string[] = [];
    for (let el = box?.parentElement ?? null; el; el = el.parentElement) {
      if (el.hasAttribute("data-email-thread")) continue;
      if (el.scrollTop !== 0) moved.push(`${el.tagName}.${String(el.className).slice(0, 60)} scrollTop=${el.scrollTop}`);
    }
    return {
      moved,
      windowY: window.scrollY,
      docOverflow: document.documentElement.scrollHeight - window.innerHeight,
    };
  });
}

test.describe("The reply composer", () => {
  // The first visit compiles the page on a cold `next dev`.
  test.describe.configure({ timeout: 120_000 });

  test("a wheel in the AI draft scrolls the draft, and the page stays", async ({ page }) => {
    await installMocks(page);
    await page.goto(`/email?email=${MAIL}&account=${ACCOUNT.id}`);
    await expect(page.getByText("The body of the BQ quote.").first()).toBeVisible({ timeout: 60_000 });

    await page.locator('button[title="Reply"]:visible').first().click();
    const body = page.getByPlaceholder(/Write your/);
    await expect(body).toBeVisible();
    // The reply opens below a long thread. The thread scrolls to it, and no
    // box above the thread moves.
    await expect.poll(async () => (await shifted(page)).moved).toEqual([]);
    expect(await page.locator("[data-email-thread]").evaluate((el) => el.scrollTop)).toBeGreaterThan(0);

    await page.getByRole("button", { name: "Draft with AI" }).last().click();
    const ask = page.getByRole("textbox", { name: "Draft with AI" });
    await ask.fill("say yes");
    await ask.press("Enter");
    await expect(page.getByRole("textbox", { name: "Refine the draft" })).toBeVisible({ timeout: 15_000 });

    // The finished draft adds nothing below the window.
    expect((await shifted(page)).docOverflow).toBeLessThanOrEqual(0);

    // The root cause, by behaviour: the sr-only summary of the AI panel is
    // held by the panel itself, not by the shell. The shell clip alone also
    // keeps the document short, so the check above cannot see this layer.
    const summary = await page.evaluate(() => {
      const span = [...document.querySelectorAll<HTMLElement>(".sr-only")].find((s) =>
        (s.textContent ?? "").includes("read the thread"));
      const holder = span?.offsetParent ?? null;
      const refine = document.querySelector('input[aria-label="Refine the draft"]');
      return {
        found: !!span,
        inAssistant: !!holder && !!refine && holder.contains(refine),
        isShell: !!holder?.hasAttribute("data-app-shell"),
      };
    });
    expect(summary).toEqual({ found: true, inAssistant: true, isShell: false });

    // Scroll far past the end of the draft.
    await body.hover();
    for (let i = 0; i < 40; i++) {
      await page.mouse.wheel(0, 200);
      await page.waitForTimeout(25);
    }
    await expect.poll(() => body.evaluate((el) => el.scrollTop)).toBeGreaterThan(0);
    await page.waitForTimeout(400);
    const after = await shifted(page);
    expect(after.windowY).toBe(0);
    expect(after.moved).toEqual([]);
    // The shell bar and the sidebar are still at the top of the window.
    expect(await page.locator("[data-app-shell]").evaluate((el) => el.getBoundingClientRect().top)).toBe(0);
  });
});
