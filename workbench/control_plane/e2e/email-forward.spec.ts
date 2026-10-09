import { expect, test, type Page, type Route } from "@playwright/test";

/**
 * The Forward of the reading pane keeps the files (follow-up 4 of #766).
 *
 * Owner report, 2026-10-09: the pane built the forward in the browser,
 * cleared every file, and sent a new mail. The pane now sends
 * `POST /email/forward` with the files the member kept. Three behaviours,
 * each one a way the obvious wrong build fails:
 *
 *   1. The files of the email show as chips, kept, and the request asks for
 *      every file. The old path sent `POST /email/send` with no file.
 *   2. On an Outlook mailbox, a chip taken out says that Outlook forwards
 *      all files or none, and nothing goes to the route.
 *   3. A 413 shows its reason, and "Forward without files" sends again with
 *      no file.
 *
 * `src/app/email/lib/forward.test.ts` holds the request and the words.
 */

const OUTLOOK = {
  id: "7c9e6679-7425-40de-944b-e07fc1f90ae7",
  provider: "microsoft",
  email_address: "me@example.com",
  label: "Work",
  unread_count: 0,
  sync_enabled: true,
  sync_status: "idle",
  initial_sync_done: true,
};
const GMAIL = { ...OUTLOOK, id: "9a1f6c2e-1111-4b2b-8c3d-4e5f6a7b8c9d", provider: "gmail" };

const MAIL = "0f8fad5b-d9cb-469f-a165-70867728950e";
const PDF = "3d6f4c1a-0000-4000-8000-000000000001";
const SHEET = "3d6f4c1a-0000-4000-8000-000000000002";

function message(accountId: string) {
  return {
    id: MAIL,
    provider_message_id: "pm-0f8f",
    thread_id: "t-0f8f",
    account_id: accountId,
    from_address: { name: "Ravi", email: "ravi@contoso.test" },
    to_addresses: [{ name: "Me", email: "me@example.com" }],
    subject: "BQ quote for the extruder",
    body_text: "The body of the BQ quote.",
    snippet: "BQ quote",
    folder: "inbox",
    is_read: true,
    received_at: "2026-10-08T10:00:00Z",
    has_attachments: true,
    attachments: [
      { id: PDF, filename: "quote.pdf", mime_type: "application/pdf", size_bytes: 2 * 1024 * 1024 },
      { id: SHEET, filename: "rates.xlsx", mime_type: "application/vnd.ms-excel", size_bytes: 30_000 },
    ],
  };
}

type Forwarded = Array<Record<string, unknown>>;

async function installMocks(page: Page, account: typeof OUTLOOK, answer: (n: number) => { status: number; body: unknown }) {
  const forwarded: Forwarded = [];
  const json = (route: Route, body: unknown, status = 200) =>
    route.fulfill({ status, contentType: "application/json", body: JSON.stringify(body) });
  // The fallback first: Playwright tries the LAST route first.
  await page.route("**/api/**", (r) => json(r, {}));
  await page.route("**/api/health", (r) => json(r, { gateway: "up" }));
  await page.route("**/api/email/accounts", (r) => json(r, [account]));
  await page.route(/.*\/api\/email\/accounts\/[^/]+\/folders.*/, (r) =>
    json(r, [{ provider_folder_id: "inbox", name: "Inbox", type: "system", message_count: 1, unread_count: 0 }]),
  );
  await page.route(/.*\/api\/email\/accounts\/[^/]+\/labels.*/, (r) => json(r, []));
  await page.route(/.*\/api\/email\/messages\/facets.*/, (r) => json(r, { folder: "inbox", total: 0, unread: 0, labels: {} }));
  // The toolbar's Forward acts on a row of the list, so the mail is listed.
  await page.route(/.*\/api\/email\/(messages|search)\?.*/, (r) =>
    json(r, { emails: [message(account.id)], total: 1, page: 1, page_size: 50 }));
  await page.route(new RegExp(`.*/api/email/messages/${MAIL}(\\?.*)?$`), (r) => json(r, message(account.id)));
  await page.route(/.*\/api\/email\/sent-from.*/, (r) => json(r, {}));
  await page.route("**/api/email/drafts", (r) => json(r, { ...message(account.id), id: "draft-1", folder: "drafts" }));
  await page.route("**/api/email/send", (r) => json(r, { detail: "the pane must not send a forward here" }, 500));
  await page.route("**/api/email/forward", (r) => {
    forwarded.push(JSON.parse(r.request().postData() ?? "{}"));
    const { status, body } = answer(forwarded.length);
    return json(r, body, status);
  });
  return forwarded;
}

async function openForward(page: Page, account: typeof OUTLOOK) {
  await page.goto(`/email?email=${MAIL}&account=${account.id}`);
  await expect(page.getByText("The body of the BQ quote.").first()).toBeVisible({ timeout: 60_000 });
  const forward = page.locator('button[title="Forward"]:visible').first();
  await forward.click();
  await expect(page.locator("[data-forward-files]")).toBeVisible({ timeout: 15_000 });
}

const SENT = { status: 200, body: { id: "sent-1", ok: true, subject: "Fwd: BQ quote", attachments: ["quote.pdf", "rates.xlsx"], bytes: 1 } };

test.describe("The reading-pane Forward", () => {
  // The first visit compiles the page on a cold `next dev`.
  test.describe.configure({ timeout: 120_000 });

  test("keeps every file, and sends POST /email/forward with them", async ({ page }) => {
    const forwarded = await installMocks(page, OUTLOOK, () => SENT);
    await openForward(page, OUTLOOK);
    const boxes = page.locator("[data-forward-file] input[type=checkbox]");
    await expect(boxes).toHaveCount(2);
    await expect(boxes.nth(0)).toBeChecked();
    await expect(boxes.nth(1)).toBeChecked();

    await page.getByRole("combobox", { name: "To recipients" }).fill("geo@fracktal.test");
    await page.getByRole("button", { name: "Send", exact: true }).click();
    await expect.poll(() => forwarded.length).toBe(1);
    expect(forwarded[0]).toMatchObject({
      message_id: MAIL,
      account_id: OUTLOOK.id,
      to: ["geo@fracktal.test"],
      include_attachments: true,
    });
    expect(forwarded[0]).not.toHaveProperty("attachment_ids");
    // The pane closes on a sent forward.
    await expect(page.locator("[data-forward-files]")).toBeHidden({ timeout: 10_000 });
  });

  test("on Outlook, a file taken out says all or none, and sends nothing", async ({ page }) => {
    const forwarded = await installMocks(page, OUTLOOK, () => SENT);
    await openForward(page, OUTLOOK);
    await page.getByRole("checkbox", { name: "Forward rates.xlsx" }).uncheck();
    const notice = page.locator("[data-forward-outlook]");
    await expect(notice).toContainText("Outlook forwards all the files of an email, or none of them.");
    await page.getByRole("combobox", { name: "To recipients" }).fill("geo@fracktal.test");
    await page.getByRole("button", { name: "Send", exact: true }).click();
    await expect(page.getByRole("alert").filter({ hasText: "Nothing was sent." })).toBeVisible();
    // One button for each choice: the notice holds them, the error line none.
    await expect(page.locator("[data-forward-offer]")).toHaveCount(0);
    expect(forwarded).toHaveLength(0);

    // "Keep every file" puts the file back, and the notice goes.
    await notice.getByRole("button", { name: "Keep every file" }).click();
    await expect(notice).toBeHidden();
    await expect(page.getByRole("checkbox", { name: "Forward rates.xlsx" })).toBeChecked();
  });

  test("on Gmail, a file taken out sends the kept file by id", async ({ page }) => {
    const forwarded = await installMocks(page, GMAIL, () => SENT);
    await openForward(page, GMAIL);
    await page.getByRole("checkbox", { name: "Forward rates.xlsx" }).uncheck();
    await expect(page.locator("[data-forward-outlook]")).toHaveCount(0);
    await page.getByRole("combobox", { name: "To recipients" }).fill("geo@fracktal.test");
    await page.getByRole("button", { name: "Send", exact: true }).click();
    await expect.poll(() => forwarded.length).toBe(1);
    expect(forwarded[0]).toMatchObject({ include_attachments: true, attachment_ids: [PDF] });
  });

  test("a 413 shows its reason, and Forward without files sends again with no file", async ({ page }) => {
    const tooBig = {
      status: 413,
      body: { detail: "The files of this mail come to 30.0 MB, and a forward carries 25.0 MB at most, so nothing was sent." },
    };
    const forwarded = await installMocks(page, GMAIL, (n) => (n === 1 ? tooBig : SENT));
    await openForward(page, GMAIL);
    await page.getByRole("combobox", { name: "To recipients" }).fill("geo@fracktal.test");
    await page.getByRole("button", { name: "Send", exact: true }).click();
    const alert = page.getByRole("alert").filter({ hasText: "30.0 MB" });
    await expect(alert).toBeVisible();
    await page.locator("[data-forward-offer]").getByRole("button", { name: "Forward without files" }).click();
    await expect.poll(() => forwarded.length).toBe(2);
    expect(forwarded[1]).toMatchObject({ include_attachments: false });
    expect(forwarded[1]).not.toHaveProperty("attachment_ids");
  });
});
