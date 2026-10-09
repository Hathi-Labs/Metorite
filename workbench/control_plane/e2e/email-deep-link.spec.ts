import { expect, test, type Page, type Route } from "@playwright/test";

/**
 * The link to one email: `/email?email=<id>` (owner report, 2026-10-09).
 *
 * The email assistant now cites each email as that link, and a chat card's
 * "Open in inbox" pushes it. Two behaviours, each one a way the obvious wrong
 * build fails:
 *
 *   1. A fresh visit to the link opens that email. The reader that this
 *      change replaced did this, and it must still work.
 *   2. A second link, pushed while the email page is open, opens the second
 *      email. A link in the email chat does this, and the old reader ran
 *      once per mailbox, so it opened nothing.
 *
 * `src/app/email/lib/emailLink.test.ts` holds the shape of the link.
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

const MAIL_A = "0f8fad5b-d9cb-469f-a165-70867728950e";
const MAIL_B = "1b4e28ba-2fa1-41d2-883f-0016d3cca427";

function message(id: string, subject: string) {
  return {
    id,
    provider_message_id: `pm-${id.slice(0, 4)}`,
    thread_id: `t-${id.slice(0, 4)}`,
    account_id: ACCOUNT.id,
    from_address: { name: "Ravi", email: "ravi@contoso.test" },
    to_addresses: [{ name: "Me", email: "me@example.com" }],
    subject,
    body_text: `The body of ${subject}.`,
    snippet: subject,
    folder: "inbox",
    is_read: true,
    received_at: "2026-10-08T10:00:00Z",
  };
}

async function installMocks(page: Page) {
  const json = (route: Route, body: unknown) =>
    route.fulfill({ status: 200, contentType: "application/json", body: JSON.stringify(body) });
  await page.route("**/api/health", (r) => json(r, { gateway: "up" }));
  await page.route("**/api/email/accounts", (r) => json(r, [ACCOUNT]));
  await page.route(/.*\/api\/email\/accounts\/[^/]+\/folders.*/, (r) =>
    json(r, [{ provider_folder_id: "inbox", name: "Inbox", type: "system", message_count: 0, unread_count: 0 }]),
  );
  await page.route(/.*\/api\/email\/accounts\/[^/]+\/labels.*/, (r) => json(r, []));
  await page.route(/.*\/api\/email\/messages\/facets.*/, (r) => json(r, { folder: "inbox", total: 0, unread: 0, labels: {} }));
  await page.route(/.*\/api\/email\/(messages|search)\?.*/, (r) =>
    json(r, { emails: [], total: 0, page: 1, page_size: 50 }),
  );
  await page.route(new RegExp(`.*/api/email/messages/${MAIL_A}(\\?.*)?$`), (r) => json(r, message(MAIL_A, "BQ quote for the extruder")));
  await page.route(new RegExp(`.*/api/email/messages/${MAIL_B}(\\?.*)?$`), (r) => json(r, message(MAIL_B, "Second mail about the PDF")));
}

test.describe("The link to one email", () => {
  // The first visit compiles the page on a cold `next dev`, which can pass
  // the suite's 45 s default by itself (CI starts cold).
  test.describe.configure({ timeout: 120_000 });

  test("a fresh visit to /email?email=<id> opens that email", async ({ page }) => {
    await installMocks(page);
    await page.goto(`/email?email=${MAIL_A}&account=${ACCOUNT.id}`);
    await expect(page.getByText("The body of BQ quote for the extruder.").first()).toBeVisible({ timeout: 60_000 });
  });

  test("a second link pushed while the page is open opens the second email", async ({ page }) => {
    await installMocks(page);
    await page.goto(`/email?email=${MAIL_A}`);
    await expect(page.getByText("The body of BQ quote for the extruder.").first()).toBeVisible({ timeout: 60_000 });

    // An in-app link, as the chat renders it: `ControlLink` routes a plain
    // click through the Next router, so the page stays open. A reload would
    // hide the defect, because the old reader ran on each mount.
    const pushed = await page.evaluate((href) => {
      const w = window as unknown as { next?: { router?: { push: (h: string) => void } } };
      if (!w.next?.router?.push) return false;
      w.next.router.push(href);
      return true;
    }, `/email?email=${MAIL_B}`);
    test.skip(!pushed, "this Next build exposes no router on window.next");
    // A cold dev server compiles the route on the push, so the wait is long.
    await expect(page).toHaveURL(new RegExp(`email=${MAIL_B}`), { timeout: 15_000 });
    await expect(page.getByText("The body of Second mail about the PDF.").first()).toBeVisible({ timeout: 15_000 });
  });
});
