import { expect, test, type Page, type Route } from "@playwright/test";

/**
 * The reply body grows, takes a dragged height, and expands (owner request,
 * 2026-10-14). The pure rules have vitest fences in
 * `src/app/email/lib/composerSize.test.ts`. This spec holds the DOM half:
 * the measures, the flex layout, the storage and the scroll.
 *
 * The fixtures follow `email-composer-scroll.spec.ts`: a mocked gateway and
 * a mocked AI stream, with no live stack.
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
// A draft taller than any body, so the body reaches its cap.
const DRAFT = Array.from({ length: 60 }, (_, i) => `Line ${i} of the draft.`).join("\n");
// Enough AI steps to make the steps list tall.
const STEPS = ["context", "contacts", "files", "tone", "facts", "check"];

function message() {
  return {
    id: MAIL,
    provider_message_id: "pm-0f8f",
    thread_id: "t-0f8f",
    account_id: ACCOUNT.id,
    from_address: { name: "Rajendra Kumar Pati", email: "rajendra@contoso.test" },
    to_addresses: [{ name: "Me", email: "me@example.com" }, { name: "Ravi", email: "ravi@contoso.test" }],
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
  // A signed-in member, so the kept height has a member scope.
  await page.route("**/api/auth/me", (r) =>
    json(r, { email: "me@example.com", organization: { id: "org-grow" } }));
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
  const events = [
    ...STEPS.flatMap((id) => [
      { type: "activity", kind: "stage", id, status: "start" },
      { type: "activity", kind: "stage", id, status: "done", detail: `read the ${id}` },
    ]),
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

const body = (page: Page) => page.locator("[data-email-composer] textarea");
const grip = (page: Page) => page.locator("[data-composer-grip]");

/** Open the mail, start a reply, and let the AI fill a long draft. */
async function openReplyWithDraft(page: Page, button: "Reply" | "Reply all" = "Reply") {
  await page.goto(`/email?email=${MAIL}&account=${ACCOUNT.id}`);
  await expect(page.getByText("The body of the BQ quote.").first()).toBeVisible({ timeout: 90_000 });
  await page.locator(`button[title="${button}"]:visible`).first().click();
  await expect(body(page)).toBeVisible();
  await page.getByRole("button", { name: "Draft with AI" }).last().click();
  const ask = page.getByRole("textbox", { name: "Draft with AI" });
  await ask.fill("say yes");
  await ask.press("Enter");
  await expect(page.getByRole("textbox", { name: "Refine the draft" })).toBeVisible({ timeout: 15_000 });
}

/** The body height, rounded. */
async function bodyHeight(page: Page): Promise<number> {
  return body(page).evaluate((el) => Math.round(el.getBoundingClientRect().height));
}

test.describe("The reply body grows", () => {
  // The first visit compiles the page on a cold `next dev`.
  test.describe.configure({ timeout: 180_000 });

  test("Expand fills the pane inside its padding, and shows the bottom border", async ({ page }) => {
    await page.setViewportSize({ width: 1440, height: 900 });
    await installMocks(page);
    await openReplyWithDraft(page);
    await page.locator('[data-email-composer] [data-composer-expand="off"]').click();
    await expect(page.locator('[data-email-composer] [data-composer-expand="on"]')).toBeVisible();
    await expect.poll(async () => page.evaluate(() => {
      const root = document.querySelector<HTMLElement>("[data-email-composer]")!;
      const pane = document.querySelector<HTMLElement>("[data-email-thread]")!;
      const cs = getComputedStyle(pane);
      const inner = pane.clientHeight - parseFloat(cs.paddingTop) - parseFloat(cs.paddingBottom);
      const r = root.getBoundingClientRect();
      const p = pane.getBoundingClientRect();
      return {
        heightIsInner: Math.abs(r.height - inner) <= 1,
        // The whole composer, its bottom border too, is inside the pane.
        topInside: r.top >= p.top + pane.clientTop - 1,
        bottomInside: r.bottom <= p.top + pane.clientTop + pane.clientHeight + 1,
        page: window.scrollY,
      };
    })).toEqual({ heightIsInner: true, topInside: true, bottomInside: true, page: 0 });
    // Send shows, and Collapse goes back.
    await expect(page.getByRole("button", { name: "Send", exact: true })).toBeInViewport();
    await page.locator('[data-email-composer] [data-composer-expand="on"]').click();
    await expect(page.locator('[data-email-composer] [data-composer-expand="off"]')).toBeVisible();
  });

  test("a dragged height survives a reload, and a double-click resets to auto", async ({ page }) => {
    await page.setViewportSize({ width: 1440, height: 900 });
    await installMocks(page);
    await openReplyWithDraft(page);
    const auto = await bodyHeight(page);
    await expect(grip(page)).toHaveAttribute("aria-valuenow", String(auto));

    // Drag the grip up by 120 px: a shorter body than auto.
    const box = (await grip(page).boundingBox())!;
    await page.mouse.move(box.x + box.width / 2, box.y + box.height / 2);
    await page.mouse.down();
    await page.mouse.move(box.x + box.width / 2, box.y + box.height / 2 - 60, { steps: 4 });
    await page.mouse.move(box.x + box.width / 2, box.y + box.height / 2 - 120, { steps: 4 });
    await page.mouse.up();
    const dragged = await bodyHeight(page);
    expect(Math.abs(dragged - (auto - 120))).toBeLessThanOrEqual(2);
    await expect.poll(() => page.evaluate(() =>
      localStorage.getItem("cc-email-composer-height:me@example.com|org-grow"))).toBe(String(dragged));

    // A reload keeps it.
    await page.reload();
    await openReplyWithDraft(page);
    await expect.poll(() => bodyHeight(page)).toBe(dragged);

    // A double-click goes back to auto, and forgets the height.
    await grip(page).dblclick();
    await expect.poll(() => bodyHeight(page)).toBe(auto);
    expect(await page.evaluate(() =>
      localStorage.getItem("cc-email-composer-height:me@example.com|org-grow"))).toBeNull();
  });

  test("a short window: the expanded body never covers the AI panel", async ({ page }) => {
    // Review round 1, P1: a 517 px pane, Reply all with Cc and Bcc, and the
    // AI steps open. The body once shrank under its textarea, and the
    // textarea painted over the AI panel and took its clicks.
    // 640 px tall: the pane is too short for the header, Cc, Bcc, the AI
    // steps, the footer and a body of 4 lines.
    await page.setViewportSize({ width: 1366, height: 640 });
    await installMocks(page);
    await openReplyWithDraft(page, "Reply all");
    const ccBcc = page.locator("[data-email-composer]").getByRole("button", { name: "Cc/Bcc" });
    if (await ccBcc.count()) await ccBcc.click();
    await expect(page.getByRole("combobox", { name: "Bcc recipients" })).toBeVisible();
    await page.getByRole("button", { name: "Show what the AI did" }).click();
    await page.locator('[data-email-composer] [data-composer-expand="off"]').click();

    const facts = await page.evaluate(() => {
      const root = document.querySelector<HTMLElement>("[data-email-composer]")!;
      const ta = root.querySelector("textarea")!;
      // The AI panel: the child of the composer that holds the refine box.
      let ai = document.querySelector<HTMLElement>('input[aria-label="Refine the draft"]')!;
      while (ai.parentElement && ai.parentElement !== root) ai = ai.parentElement;
      return {
        taBottom: Math.round(ta.getBoundingClientRect().bottom),
        aiTop: Math.round(ai.getBoundingClientRect().top),
        taHeight: Math.round(ta.getBoundingClientRect().height),
        rootScrolls: root.scrollHeight > root.clientHeight,
      };
    });
    expect(facts.taBottom).toBeLessThanOrEqual(facts.aiTop);
    // The body keeps its minimum of 4 lines, so the composer scrolls instead.
    expect(facts.taHeight).toBeGreaterThanOrEqual(80);
    expect(facts.rootScrolls).toBe(true);
    // The refine box takes a click, so nothing covers it.
    await page.getByRole("textbox", { name: "Refine the draft" }).click();
    await expect(page.getByRole("textbox", { name: "Refine the draft" })).toBeFocused();
  });
});
