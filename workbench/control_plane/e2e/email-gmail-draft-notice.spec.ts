import { expect, test, type Page, type Route } from "@playwright/test";

/**
 * WS-17 EM-G3a-f7 PR-C2: the DraftCard of a Gmail-web draft (§12.3.12.5 of
 * `project-docs/specs/email_app_master_plan.md`).
 *
 * The behaviour half of the fences in `src/app/email/lib/draftSave.test.ts`.
 *
 *   1. A Gmail draft with HTML shows the notice above the text box. A save
 *      with the text as it opened sends `keep_html`.
 *   2. The 409 `html_unavailable` of the send stops the send. The card says
 *      that the next press sends the text only, and that press sends
 *      `allow_text_only: true`.
 *   3. A pre-send save that answers `html: "lost"` stops the first press, and
 *      sends nothing. A text edit brings the general words back. The next
 *      press sends the text only.
 *   4. An Outlook draft shows no notice, and its save and its send carry the
 *      bodies of main.
 *
 * Acceptance 23 is a visual review. With `GMAIL_C2_SHOTS` set to a folder,
 * the spec saves a screenshot of each state there: dark, light, compact
 * density with a changed accent, and 390 px.
 */

const SHOTS = process.env.GMAIL_C2_SHOTS;
const NOTICE =
  "This draft has formatting or images from Gmail. If you change the text here, " +
  "Metorite saves the text only, and the formatting and images go.";
const STOPPED =
  "Metorite could not keep the formatting and images of this draft. Press Send again to send the text only.";

const INBOUND = "0f8fad5b-d9cb-469f-a165-70867728951a";
const DRAFT = "0f8fad5b-d9cb-469f-a165-70867728951d";
const THREAD = "t-plan";

function account(provider: "gmail" | "microsoft") {
  return {
    id: "7c9e6679-7425-40de-944b-e07fc1f90ae7",
    provider,
    email_address: "me@example.com",
    label: "Work",
    unread_count: 0,
    sync_enabled: true,
    sync_status: "idle",
    initial_sync_done: true,
  };
}

function inbound(accountId: string) {
  return {
    id: INBOUND,
    provider_message_id: "pm-in",
    thread_id: THREAD,
    account_id: accountId,
    from_address: { name: "Ravi", email: "ravi@contoso.test" },
    to_addresses: [{ name: "Me", email: "me@example.com" }],
    subject: "The plan for the extruder",
    body_text: "Can you send the plan for the extruder?",
    snippet: "Can you send the plan",
    folder: "inbox",
    is_read: true,
    received_at: "2026-10-08T09:00:00Z",
    has_attachments: false,
    attachments: [],
  };
}

/** A draft that the member wrote in Gmail web, with bold text and an inline image. */
function draft(accountId: string) {
  return {
    id: DRAFT,
    provider_message_id: "pm-draft",
    thread_id: THREAD,
    account_id: accountId,
    from_address: { name: "Me", email: "me@example.com" },
    to_addresses: [{ name: "Ravi", email: "ravi@contoso.test" }],
    subject: "Re: The plan for the extruder",
    body_text: "Thanks Ravi. The plan is below.",
    body_html: '<p>Thanks Ravi. <b>The plan</b> is below.</p><img src="cid:ii_plan">',
    snippet: "Thanks Ravi.",
    folder: "drafts",
    is_read: true,
    received_at: "2026-10-08T10:00:00Z",
    has_attachments: false,
    attachments: [],
  };
}

interface Answers {
  /** The `html` answer of each save. */
  saveHtml?: "kept" | "lost" | "none";
  /** The status of the first send. Each later send answers 200. */
  firstSend?: number;
}

async function installMocks(page: Page, provider: "gmail" | "microsoft", answers: Answers = {}) {
  const box = account(provider);
  const json = (route: Route, body: unknown, status = 200) =>
    route.fulfill({ status, contentType: "application/json", body: JSON.stringify(body) });
  const saves: Record<string, unknown>[] = [];
  const sends: Record<string, unknown>[] = [];

  // The fallback first: Playwright tries the LAST route first.
  await page.route("**/api/**", (r) => json(r, {}));
  await page.route("**/api/health", (r) => json(r, { gateway: "up" }));
  await page.route("**/api/email/accounts", (r) => json(r, [box]));
  await page.route(/.*\/api\/email\/accounts\/[^/]+\/folders.*/, (r) =>
    json(r, [{ provider_folder_id: "inbox", name: "Inbox", type: "system", message_count: 1, unread_count: 0 }]),
  );
  await page.route(/.*\/api\/email\/accounts\/[^/]+\/labels.*/, (r) => json(r, []));
  await page.route(/.*\/api\/email\/messages\/facets.*/, (r) => json(r, { folder: "inbox", total: 0, unread: 0, labels: {} }));
  await page.route(/.*\/api\/email\/(messages|search)\?.*/, (r) => {
    const thread = new URL(r.request().url()).searchParams.get("thread_id");
    const emails = thread ? [inbound(box.id), draft(box.id)] : [inbound(box.id)];
    return json(r, { emails, total: emails.length, page: 1, page_size: 50 });
  });
  await page.route(new RegExp(`.*/api/email/messages/${INBOUND}(\\?.*)?$`), (r) => json(r, inbound(box.id)));
  await page.route(/.*\/api\/email\/contacts\/suggest.*/, (r) => json(r, []));
  await page.route("**/api/email/drafts", (r) => {
    if (r.request().method() !== "PUT") return json(r, {});
    saves.push(JSON.parse(r.request().postData() ?? "{}"));
    const answer: Record<string, unknown> = { ...draft(box.id) };
    if (provider === "gmail") answer.html = answers.saveHtml ?? "kept";
    return json(r, answer);
  });
  await page.route("**/api/email/drafts/send", (r) => {
    sends.push(JSON.parse(r.request().postData() ?? "{}"));
    if (sends.length === 1 && answers.firstSend === 409) {
      return json(r, { detail: { reason: "html_unavailable" } }, 409);
    }
    return json(r, { sent: true });
  });
  return { box, saves, sends };
}

async function openThread(page: Page, accountId: string, phone = false) {
  await page.goto(`/email?email=${INBOUND}&account=${accountId}`);
  // A phone shows the list first. A tap on the row opens the thread.
  if (phone) await page.locator(`[data-email-row="${INBOUND}"]`).first().click({ timeout: 60_000 });
  await expect(page.getByPlaceholder("Write your reply…")).toBeVisible({ timeout: 60_000 });
}

async function shot(page: Page, name: string) {
  if (!SHOTS) return;
  const card = page.getByPlaceholder("Write your reply…").locator("xpath=ancestor::div[contains(@class,'border-primary')][1]");
  await card.screenshot({ path: `${SHOTS}/${name}.png` });
  await page.screenshot({ path: `${SHOTS}/${name}-page.png` });
}

const sendButton = (page: Page) => page.getByRole("button", { name: "Send", exact: true });

test.describe("The DraftCard of a Gmail-web draft", () => {
  // The first visit compiles the page on a cold `next dev`.
  test.describe.configure({ timeout: 120_000 });

  test("shows the notice above the text box, and an unchanged save keeps the HTML", async ({ page }) => {
    const { box, saves } = await installMocks(page, "gmail");
    await openThread(page, box.id);

    const notice = page.getByRole("status").filter({ hasText: NOTICE });
    await expect(notice).toBeVisible();
    const textBox = page.getByPlaceholder("Write your reply…");
    const [n, t] = [await notice.boundingBox(), await textBox.boundingBox()];
    expect(n && t && n.y + n.height <= t.y).toBe(true);
    await shot(page, "notice-dark-1280");

    // A Cc edit saves with the text as it opened, so the save keeps the HTML.
    const cc = page.locator('[placeholder="Cc (comma-separated)"]:visible').first();
    if (!(await cc.isVisible())) await page.getByRole("button", { name: "Cc/Bcc" }).click();
    await cc.fill("lena@contoso.test");
    await expect.poll(() => saves.length, { timeout: 10_000 }).toBeGreaterThan(0);
    expect(saves[saves.length - 1].keep_html).toBe(true);

    // A text edit saves the text only.
    await textBox.fill("Thanks Ravi. The new plan is below.");
    await expect.poll(() => saves.length, { timeout: 10_000 }).toBeGreaterThan(1);
    expect("keep_html" in saves[saves.length - 1]).toBe(false);
  });

  test("the 409 of the send shows the notice, and the next press sends the text only", async ({ page }) => {
    const { box, saves, sends } = await installMocks(page, "gmail", { firstSend: 409 });
    await openThread(page, box.id);

    await sendButton(page).click();
    await expect.poll(() => sends.length).toBe(1);
    expect(saves[0].keep_html).toBe(true);
    expect(sends[0].allow_text_only).toBe(false);
    // The card says the stop as an alert, in place of the general words, and
    // no banner names an object.
    await expect(page.getByRole("alert").filter({ hasText: STOPPED })).toBeVisible();
    await expect(page.getByText(NOTICE)).toHaveCount(0);
    await expect(page.getByText("[object Object]")).toHaveCount(0);
    await shot(page, "send-409-dark-1280");

    await sendButton(page).click();
    await expect.poll(() => sends.length).toBe(2);
    expect(sends[1].allow_text_only).toBe(true);
  });

  test("a pre-send save that lost the HTML stops the first press", async ({ page }) => {
    const { box, saves, sends } = await installMocks(page, "gmail", { saveHtml: "lost" });
    await openThread(page, box.id);

    await sendButton(page).click();
    await expect.poll(() => saves.length).toBe(1);
    await expect(page.getByRole("alert").filter({ hasText: STOPPED })).toBeVisible();
    expect(sends.length).toBe(0);

    // A text edit clears the stop, and the general words come back.
    await page.getByPlaceholder("Write your reply…").fill("Thanks Ravi. The plan is below, in text.");
    await expect(page.getByRole("status").filter({ hasText: NOTICE })).toBeVisible();
    await expect(page.getByText(STOPPED)).toHaveCount(0);

    await sendButton(page).click();
    await expect.poll(() => sends.length).toBe(1);
    expect(sends[0].allow_text_only).toBe(true);
  });

  test("an Outlook draft shows no notice, and saves and sends the bodies of main", async ({ page }) => {
    const { box, saves, sends } = await installMocks(page, "microsoft");
    await openThread(page, box.id);

    await expect(page.getByText(NOTICE)).toHaveCount(0);
    await sendButton(page).click();
    await expect.poll(() => sends.length).toBe(1);
    expect("keep_html" in saves[0]).toBe(false);
    expect(sends[0]).toEqual({ account_id: box.id, draft_id: DRAFT });
  });
});

test.describe("The notice in other looks (acceptance 23)", () => {
  test.describe.configure({ timeout: 120_000 });
  test.skip(!SHOTS, "Set GMAIL_C2_SHOTS to a folder to save the visual review.");

  const LOOKS: { name: string; width: number; store: Record<string, string> }[] = [
    { name: "notice-light-1280", width: 1280, store: { theme: "light" } },
    {
      name: "notice-light-compact-accent-1280",
      width: 1280,
      store: { theme: "light", "cc-density": "compact", "cc-accent": "hsl(330 70% 42%)", "cc-accent-ink": "hsl(0 0% 100%)" },
    },
    {
      name: "notice-dark-compact-accent-1280",
      width: 1280,
      store: { theme: "dark", "cc-density": "compact", "cc-accent": "hsl(160 60% 40%)", "cc-accent-ink": "hsl(0 0% 100%)" },
    },
    { name: "notice-light-390", width: 390, store: { theme: "light" } },
  ];

  for (const look of LOOKS) {
    test(look.name, async ({ page }) => {
      await page.setViewportSize({ width: look.width, height: 844 });
      await page.addInitScript((store: Record<string, string>) => {
        for (const [k, v] of Object.entries(store)) localStorage.setItem(k, v);
      }, look.store);
      const { box } = await installMocks(page, "gmail", { firstSend: 409 });
      await openThread(page, box.id, look.width < 768);
      const notice = page.getByText(NOTICE);
      await expect(notice).toBeVisible();
      // The notice fits its card: no word leaves the box at 390 px.
      const fits = await notice.evaluate((el) => {
        const p = el.parentElement as HTMLElement;
        return p.scrollWidth <= p.clientWidth;
      });
      expect(fits).toBe(true);
      await shot(page, look.name);
      await sendButton(page).click();
      const stopped = page.getByRole("alert").filter({ hasText: STOPPED });
      await expect(stopped).toBeVisible();
      expect(await stopped.evaluate((el) => el.scrollWidth <= el.clientWidth)).toBe(true);
      await shot(page, `${look.name}-send-409`);
    });
  }
});
