import { expect, test, type Page, type Route } from "@playwright/test";

/**
 * WS-17 EM-T17 PR-A2, fix round 2: a mail switch ends the AI round.
 *
 * The reading pane stays mounted when the member opens another mail, so the
 * hook of "Draft with AI" stays mounted too. Before the fix, the round of
 * mail A kept its stream. Its draft then landed in the reply of mail B, and
 * autosave kept it as a draft in B's thread. `busy` also stayed on, so B's
 * panel showed Stop.
 *
 * The test holds A's stream, switches to B, opens B's reply and the AI
 * panel, and only then lets A's stream answer.
 * Mutation caught: no `ai.reset()` in the switch effect of `EmailDetail`.
 */

const ACCOUNT = {
  id: "7c9e6679-7425-40de-944b-e07fc1f90ae7",
  provider: "microsoft",
  email_address: "me@example.com",
  label: "Work",
  unread_count: 0,
  sync_enabled: true,
  sync_status: "idle",
  initial_sync_done: true,
};

const MAIL_A = "0f8fad5b-d9cb-469f-a165-70867728950a";
const MAIL_B = "0f8fad5b-d9cb-469f-a165-70867728950b";

function message(id: string, tag: "A" | "B") {
  return {
    id,
    provider_message_id: `pm-${tag}`,
    thread_id: `t-${tag}`,
    account_id: ACCOUNT.id,
    from_address: { name: `Sender ${tag}`, email: `sender-${tag.toLowerCase()}@contoso.test` },
    to_addresses: [{ name: "Me", email: "me@example.com" }],
    subject: `Subject of mail ${tag}`,
    body_text: `The body of mail ${tag}.`,
    snippet: `Mail ${tag}`,
    folder: "inbox",
    is_read: true,
    received_at: tag === "A" ? "2026-10-08T10:00:00Z" : "2026-10-08T09:00:00Z",
    has_attachments: false,
    attachments: [],
  };
}

const sse = (events: unknown[]) => events.map((e) => `data: ${JSON.stringify(e)}\n\n`).join("");

async function installMocks(page: Page) {
  const json = (route: Route, body: unknown, status = 200) =>
    route.fulfill({ status, contentType: "application/json", body: JSON.stringify(body) });
  let releaseA!: () => void;
  const aReleased = new Promise<void>((done) => {
    releaseA = done;
  });
  const streams: string[] = [];

  // The fallback first: Playwright tries the LAST route first.
  await page.route("**/api/**", (r) => json(r, {}));
  await page.route("**/api/health", (r) => json(r, { gateway: "up" }));
  await page.route("**/api/email/accounts", (r) => json(r, [ACCOUNT]));
  await page.route(/.*\/api\/email\/accounts\/[^/]+\/folders.*/, (r) =>
    json(r, [{ provider_folder_id: "inbox", name: "Inbox", type: "system", message_count: 2, unread_count: 0 }]),
  );
  await page.route(/.*\/api\/email\/accounts\/[^/]+\/labels.*/, (r) => json(r, []));
  await page.route(/.*\/api\/email\/messages\/facets.*/, (r) => json(r, { folder: "inbox", total: 0, unread: 0, labels: {} }));
  await page.route(/.*\/api\/email\/(messages|search)\?.*/, (r) => {
    const thread = new URL(r.request().url()).searchParams.get("thread_id");
    const all = [message(MAIL_A, "A"), message(MAIL_B, "B")];
    const emails = thread ? all.filter((m) => m.thread_id === thread) : all;
    return json(r, { emails, total: emails.length, page: 1, page_size: 50 });
  });
  await page.route(new RegExp(`.*/api/email/messages/${MAIL_A}(\\?.*)?$`), (r) => json(r, message(MAIL_A, "A")));
  await page.route(new RegExp(`.*/api/email/messages/${MAIL_B}(\\?.*)?$`), (r) => json(r, message(MAIL_B, "B")));
  await page.route(/.*\/api\/email\/sent-from.*/, (r) => json(r, {}));
  await page.route(/.*\/api\/email\/contacts\/suggest.*/, (r) => json(r, []));
  await page.route("**/api/email/compose-assist/stream", async (r) => {
    const sent = JSON.parse(r.request().postData() ?? "{}");
    streams.push(String(sent.message_id ?? ""));
    if (sent.message_id === MAIL_A) await aReleased;
    const draft = sent.message_id === MAIL_A ? "Draft for A" : "Draft for B";
    // An aborted request refuses the answer. That is the fixed behaviour.
    await r
      .fulfill({
        status: 200,
        contentType: "text/event-stream",
        body: sse([
          { type: "delta", kind: "content", text: draft },
          { type: "done", draft },
        ]),
      })
      .catch(() => undefined);
  });
  await page.route("**/api/email/compose-assist", (r) => json(r, { draft: "From the JSON route" }));
  return { releaseA, streams };
}

async function openReplyAndDraft(page: Page) {
  await page.locator('button[title="Reply"]:visible').first().click();
  const box = page.getByPlaceholder("Write your reply…");
  await expect(box).toBeVisible({ timeout: 15_000 });
  await page.getByRole("button", { name: "Draft with AI" }).first().click();
  return box;
}

test.describe("A mail switch ends the AI round of the mail before", () => {
  // The first visit compiles the page on a cold `next dev`.
  test.describe.configure({ timeout: 120_000 });

  test("mail B's reply never gets mail A's draft, and B's panel is not busy", async ({ page }) => {
    const { releaseA, streams } = await installMocks(page);
    await page.goto(`/email?email=${MAIL_A}&account=${ACCOUNT.id}`);
    await expect(page.getByText("The body of mail A.").first()).toBeVisible({ timeout: 60_000 });

    // A round starts for mail A, and its stream holds.
    await openReplyAndDraft(page);
    await page.getByRole("button", { name: "Draft", exact: true }).click();
    await expect(page.getByRole("button", { name: "Stop drafting" })).toBeVisible();
    await expect.poll(() => streams).toEqual([MAIL_A]);

    // The member opens mail B and starts its reply.
    await page.locator(`[data-email-row="${MAIL_B}"]`).first().click();
    await expect(page.getByText("The body of mail B.").first()).toBeVisible({ timeout: 15_000 });
    const boxB = await openReplyAndDraft(page);

    // `busy` cleared: B's panel offers Draft, not Stop.
    await expect(page.getByRole("button", { name: "Draft", exact: true })).toBeVisible();
    await expect(page.getByRole("button", { name: "Stop drafting" })).toHaveCount(0);

    // A's stream answers late. Nothing of it reaches B's reply.
    releaseA();
    await page.waitForTimeout(1500);
    await expect(boxB).not.toHaveValue(/Draft for A/);

    // B's own round still works.
    await page.getByRole("button", { name: "Draft", exact: true }).click();
    await expect(boxB).toHaveValue(/Draft for B/, { timeout: 15_000 });
    await expect(boxB).not.toHaveValue(/Draft for A/);
  });
});
