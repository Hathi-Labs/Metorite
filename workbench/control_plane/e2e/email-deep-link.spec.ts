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

/**
 * The email chat answers with a link to mail A. The stream is a `fetch`
 * override inside the page, as in `genui-option-picker.spec.ts`, and the
 * session store answers empty, so the page needs no gateway.
 */
async function installChat(page: Page) {
  await page.addInitScript((mail) => {
    const json = (body: unknown, status = 200) =>
      new Response(JSON.stringify(body), { status, headers: { "content-type": "application/json" } });
    const enc = new TextEncoder();
    const frame = (e: unknown) => enc.encode(`data: ${JSON.stringify(e)}\n\n`);
    const orig = window.fetch.bind(window);
    window.fetch = async (input: RequestInfo | URL, init?: RequestInit) => {
      const url = typeof input === "string" ? input : input instanceof URL ? input.href : input.url;
      if (url.includes("/api/chat/active-sessions")) return json([]);
      if (url.includes("/api/chat/sessions")) {
        // "Unknown" to the restored-chat probe (`probeSession`): neither open
        // nor refused, so reopening the chat keeps the same conversation.
        if (url.includes("/room")) return json({ detail: "no gateway in this suite" }, 503);
        return (init?.method ?? "GET").toUpperCase() === "GET" ? json([]) : json({ ok: true });
      }
      if (url.includes("/api/auth/me")) {
        return json({
          email: "dev@e2e.test", user_id: "dev", authenticated: true, is_active: true,
          organization: { id: "00000000-0000-0000-0000-0000000000e2", slug: "e2e" },
          roles: ["owner"], legacy_role: "executive", features: ["chat", "email"],
          features_denied: [], agents: ["email-assistant"], permissions: ["*"],
          capabilities: [], denied: [], is_admin: true,
        });
      }
      if (url.includes("/api/agent/chat")) {
        const body = JSON.parse(String(init?.body ?? "{}"));
        if (body.reconnect) return json({}, 404);
        const stream = new ReadableStream<Uint8Array>({
          start(controller) {
            controller.enqueue(frame({ type: "message_start", messageId: "m-1" }));
            controller.enqueue(frame({ type: "delta", messageId: "m-1",
              content: `Here it is: [BQ quote for the extruder](/email?email=${mail}).` }));
            controller.enqueue(frame({ type: "message_end", messageId: "m-1" }));
            controller.enqueue(frame({ type: "done" }));
            controller.close();
          },
        });
        return new Response(stream, { status: 200, headers: { "content-type": "text/event-stream" } });
      }
      return orig(input, init);
    };
  }, MAIL_A);
  await page.route("**/api/agent/list", (r) => r.fulfill({ json: [] }));
  await page.route("**/api/models/all", (r) => r.fulfill({ json: { models: [], source: "mock" } }));
  await page.route(/.*\/api\/integrations\/status.*/, (r) => r.fulfill({ json: [] }));
  await page.route("**/api/memory/**", (r) => r.fulfill({ json: [] }));
}

test.describe("A link in the email chat", () => {
  test.describe.configure({ timeout: 120_000 });

  test("opens its email on the first click AND on a second click of the same link", async ({ page }) => {
    // Review round 1, P2-b: the second click pushed the URL the page already
    // showed, so nothing changed and the chat stayed over the mail.
    await installMocks(page);
    await installChat(page);
    await page.goto("/email");
    const chatButton = page.getByRole("button", { name: "Chat", exact: true }).first();
    await expect(chatButton).toBeVisible({ timeout: 60_000 });
    await chatButton.click();

    const box = page.getByRole("textbox", { name: /^Message / });
    await expect(box).toBeEnabled({ timeout: 30_000 });
    await box.fill("Find the BQ quote");
    await page.getByRole("button", { name: "Send", exact: true }).click();
    const link = page.getByRole("link", { name: "BQ quote for the extruder" });
    await expect(link).toBeVisible({ timeout: 15_000 });

    const body = page.getByText("The body of BQ quote for the extruder.").first();
    await link.click();
    await expect(body).toBeVisible({ timeout: 15_000 });
    await expect(link).toBeHidden();

    // The chat again, and the SAME link again.
    await chatButton.click();
    await expect(link).toBeVisible({ timeout: 15_000 });
    await link.click();
    await expect(link).toBeHidden({ timeout: 10_000 });
    await expect(body).toBeVisible();
  });
});
