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

/**
 * The drafts the pane saved and deleted, and when the forward answered. A
 * save counts when its answer goes back, after `putDelayMs`.
 */
type DraftLog = {
  saved: Array<{ id: string; at: number }>;
  deleted: Array<{ id: string; at: number }>;
  answeredAt: number;
  putDelayMs?: number;
};

type Answer = { status: number; body: unknown; delayMs?: number };

async function installMocks(
  page: Page,
  account: typeof OUTLOOK,
  answer: (n: number) => Answer,
  log: DraftLog = { saved: [], deleted: [], answeredAt: 0 },
) {
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
  await page.route(/.*\/api\/email\/contacts\/suggest.*/, (r) => json(r, []));
  await page.route("**/api/email/drafts", async (r) => {
    const sent = JSON.parse(r.request().postData() ?? "{}");
    const id = String(sent.draft_id ?? `draft-${log.saved.length + 1}`);
    if (log.putDelayMs) await new Promise((done) => setTimeout(done, log.putDelayMs));
    log.saved.push({ id, at: Date.now() });
    // A forward draft holds no file (the route carries the files), so the
    // autosave keeps its short wait and never the Gmail wait for files.
    return json(r, { ...message(account.id), id, folder: "drafts", has_attachments: false, attachments: [] });
  });
  await page.route(/.*\/api\/email\/messages\/draft-\d+$/, (r) => {
    if (r.request().method() === "DELETE") {
      log.deleted.push({ id: r.request().url().split("/").pop() ?? "", at: Date.now() });
    }
    return json(r, {});
  });
  await page.route("**/api/email/send", (r) => json(r, { detail: "the pane must not send a forward here" }, 500));
  await page.route("**/api/email/forward", async (r) => {
    forwarded.push(JSON.parse(r.request().postData() ?? "{}"));
    const { status, body, delayMs } = answer(forwarded.length);
    if (delayMs) await new Promise((done) => setTimeout(done, delayMs));
    log.answeredAt = Date.now();
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

  // Review round 1, P2. The Next proxy answers 502 with no detail when its
  // wait ends, and the gateway can still send the mail. Mutation caught:
  // the pane said "nothing was sent", and the member sent it twice.
  test("a 502 with no detail says the mail can still go out, and offers Sent, not a retry", async ({ page }) => {
    const forwarded = await installMocks(page, GMAIL, () => ({ status: 502, body: { error: "aborted" } }));
    await openForward(page, GMAIL);
    await page.getByRole("combobox", { name: "To recipients" }).fill("geo@fracktal.test");
    await page.getByRole("button", { name: "Send", exact: true }).click();
    const alert = page.getByRole("alert").filter({ hasText: "The mail can still go out" });
    await expect(alert).toBeVisible();
    await expect(alert).not.toContainText(/nothing was sent/i);
    await expect(page.locator("[data-forward-unsure]").getByRole("button", { name: "Open Sent" })).toBeVisible();
    await expect(page.locator("[data-forward-offer]")).toHaveCount(0);
    expect(forwarded).toHaveLength(1);
  });

  // Verifier F3. Mutation caught: Send stayed on after an unsure answer, so
  // one more click sent the mail twice.
  test("after an unsure answer, Send waits until the member checked Sent", async ({ page }) => {
    const forwarded = await installMocks(page, GMAIL, (n) =>
      n === 1 ? { status: 502, body: { error: "aborted" } } : SENT);
    await openForward(page, GMAIL);
    await page.getByRole("combobox", { name: "To recipients" }).fill("geo@fracktal.test");
    const send = page.getByRole("button", { name: "Send", exact: true });
    await send.click();
    const unsure = page.locator("[data-forward-unsure]");
    await expect(unsure.getByRole("button", { name: "Open Sent" })).toBeVisible();
    await expect(send).toBeDisabled();
    // Ctrl+Enter in the note waits too.
    await page.getByRole("textbox", { name: /Write your forward/ }).press("Control+Enter");
    await page.waitForTimeout(500);
    expect(forwarded).toHaveLength(1);
    await unsure.getByRole("button", { name: "I checked Sent, send again" }).click();
    await expect(unsure).toHaveCount(0);
    await expect(send).toBeEnabled();
    await send.click();
    await expect.poll(() => forwarded.length).toBe(2);
  });

  // Review round 1, P3-a, and verifier F1 of round 2. The autosave keeps a
  // "Fwd:" draft of the forward. A sent forward must delete it, and no save
  // may land after the forward. So the test waits for a real saved draft,
  // starts a second save that is still running when Send is pressed, and
  // types while the forward is in flight.
  // The save takes 4.5 s, so it is still running when the forward answers
  // unless a drain waits for it.
  // Mutations caught: no deleteEmail loop after draftsToDiscard (the saved
  // draft stays), and no drain at all (the running save lands after the
  // delete, and its draft stays). The send drain and the finish drain each
  // cover this alone, so the source fence in forward.test.ts pins the
  // finish drain.
  test("typing during the send leaves no stray draft", async ({ page }) => {
    const log: DraftLog = { saved: [], deleted: [], answeredAt: 0, putDelayMs: 4500 };
    await installMocks(page, GMAIL, () => ({ ...SENT, delayMs: 3000 }), log);
    await openForward(page, GMAIL);
    const note = page.getByRole("textbox", { name: /Write your forward/ });
    await note.fill("See the quote.");
    await page.getByRole("combobox", { name: "To recipients" }).fill("geo@fracktal.test");
    // A real draft exists before the send.
    await expect.poll(() => log.saved.length, { timeout: 15_000 }).toBeGreaterThan(0);
    const first = log.saved.length;
    // A second save starts, and it is still running when Send is pressed.
    await note.press("End");
    await page.keyboard.type(" Thanks.");
    await page.waitForTimeout(1600);
    expect(log.saved.length, "the second save is still running").toBe(first);
    await page.getByRole("button", { name: "Send", exact: true }).click();
    // In flight: the fields are read-only, so the typing changes nothing.
    await expect(note).toHaveAttribute("readonly", "");
    await note.press("End");
    await page.keyboard.type(" More words.");
    await expect(note).toHaveValue(/Thanks\.\s*$/);
    // Pop out is off while the forward keeps the files of the email.
    await expect(page.getByRole("button", { name: "Pop out to full composer" })).toBeDisabled();
    await expect(page.locator("[data-forward-files]")).toBeHidden({ timeout: 15_000 });
    // Longer than the autosave wait and the save, so a late save would land.
    await page.waitForTimeout(6000);
    expect(log.answeredAt, "the forward answered").toBeGreaterThan(0);
    expect(log.saved.length, "the second save landed").toBeGreaterThan(first);
    // No stray draft: each saved draft is deleted AFTER its last save.
    for (const { id } of log.saved) {
      const lastSave = Math.max(...log.saved.filter((x) => x.id === id).map((x) => x.at));
      const deletedAfter = log.deleted.some((d) => d.id === id && d.at >= lastSave);
      expect(deletedAfter, `draft ${id} stays after the forward`).toBe(true);
    }
  });
});
