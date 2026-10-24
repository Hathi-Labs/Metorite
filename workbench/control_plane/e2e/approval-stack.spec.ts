import { expect, test, type Page } from "@playwright/test";

/**
 * A stack of approval cards (owner, 2026-10-24): two cards wait at once. The
 * banner counts them, the dots move between them, a swipe on a touch screen
 * moves to the next one, and an email card shows the subject as a row and
 * the mail in a frame of its own. Built on the held-send stubs.
 */

const RID = "6c2e1d3f9a7b4c5d8e0f1a2b3c4d5e6f";
const KNOWN = "kim@contoso.test";
const COLLEAGUE = "geo@acme.test";
const STRANGER = "x@evil.example";

const ROWS = [
  { id: "to-0", label: `To: ${KNOWN}`, hint: "", checked: true, kind: "to", class: "sent" },
  { id: "cc-0", label: `Cc: ${COLLEAGUE}`, hint: "", checked: true, kind: "cc", class: "org" },
  {
    id: "cc-1",
    label: `Cc: ${STRANGER}`,
    hint: "new address — not in your contacts",
    checked: false,
    kind: "cc",
    class: "new",
  },
];

async function install(page: Page) {
  await page.addInitScript(
    ({ rid, rows, known, colleague, stranger }) => {
      window.localStorage.clear();
      const w = window as unknown as Record<string, unknown>;
      const answers: unknown[] = [];
      w.__answers = answers;
      const enc = new TextEncoder();
      const frame = (e: unknown) => enc.encode(`data: ${JSON.stringify(e)}\n\n`);
      const json = (body: unknown, status = 200) =>
        new Response(JSON.stringify(body), { status, headers: { "content-type": "application/json" } });
      let answer: (a: string) => void = () => {};
      const answered = new Promise<string>((r) => { answer = r; });
      const orig = window.fetch.bind(window);
      window.fetch = async (input: RequestInfo | URL, init?: RequestInit) => {
        const url = typeof input === "string" ? input : input instanceof URL ? input.href : input.url;
        const method = (init?.method ?? "GET").toUpperCase();
        if (url.includes("/api/agent/respond-input")) {
          const body = JSON.parse(String(init?.body ?? "{}"));
          answers.push(body);
          answer(String(body.answer));
          return json({ ok: true });
        }
        if (url.includes("/api/chat/active-sessions")) return json([]);
        if (url.includes("/api/auth/me")) {
          return json({
            email: "jane@acme.test", user_id: "jane", authenticated: true, is_active: true,
            organization: { id: "00000000-0000-0000-0000-0000000000e2", slug: "e2e" },
            roles: ["owner"], legacy_role: "executive", features: ["chat", "email"],
            features_denied: [], agents: ["orchestrator", "email-assistant"], permissions: ["*"],
            capabilities: [], denied: [], is_admin: true,
          });
        }
        if (url.includes("/api/health")) return json({ gateway: "up", build: "e2e" });
        if (url.includes("/api/chat/sessions")) {
          if (url.includes("/room")) return json({ detail: "not a room" }, 404);
          return method === "GET" ? json([]) : json({ ok: true, saved: 1, unchanged: [] });
        }
        if (url.includes("/api/agent/chat")) {
          const body = JSON.parse(String(init?.body ?? "{}"));
          if (body.reconnect) return json({}, 404);
          const stream = new ReadableStream<Uint8Array>({
            async start(c) {
              const send = (e: unknown) => {
                try { c.enqueue(frame(e)); } catch { /* closed */ }
              };
              send({ type: "message_start", messageId: "m-1" });
              send({ type: "delta", messageId: "m-1", content: "The draft to Kim is ready.\n\n" });
              send({ type: "tool_start", id: "t-1", name: "email_assistant", args: {} });
              send({
                type: "custom",
                name: "confirmation_requested",
                value: {
                  title: "Send this draft?",
                  detail: "From Work · jane@acme.test · 3 recipient(s). Only a ticked person gets the mail.",
                  context: "Subject: Quote\n\nThe text of the draft:\nHi Kim,\n\nThe quote is attached.",
                  request_id: rid,
                  rows,
                },
              });
              send({
                type: "custom",
                name: "confirmation_requested",
                value: {
                  title: "Send this new email?",
                  detail: "From Work · jane@acme.test · 1 recipient(s), 0 file(s).",
                  context: "Subject: Brochures for Kim\n\nThe text:\nHi Lee,\n\nPlease send Kim the brochures.",
                  request_id: rid + "-2",
                },
              });
              const said = await answered;
              const ids: string[] = said.startsWith("APPROVE ") ? JSON.parse(said.slice(8)).rows : [];
              const cc = [ids.includes("cc-0") ? colleague : "", ids.includes("cc-1") ? stranger : ""]
                .filter(Boolean)
                .join(", ");
              const left = rows.length - ids.length;
              const result = ids.length
                ? `Draft sent: To ${known}${cc ? `; Cc ${cc}` : ""}.` +
                  (left ? ` The member left ${left} row(s) unticked, and those did not go.` : "")
                : "Send cancelled — the draft was not sent.";
              send({
                type: "custom",
                name: "confirmation_resolved",
                value: { request_id: rid, answer: ids.length ? "APPROVE" : "REJECT" },
              });
              send({ type: "tool_end", id: "t-1", name: "email_assistant", success: true, result });
              send({ type: "delta", messageId: "m-1", content: result });
              send({ type: "message_end", messageId: "m-1" });
              send({ type: "done" });
              try { c.close(); } catch { /* closed */ }
            },
          });
          return new Response(stream, { status: 200, headers: { "content-type": "text/event-stream" } });
        }
        return orig(input, init);
      };
    },
    { rid: RID, rows: ROWS, known: KNOWN, colleague: COLLEAGUE, stranger: STRANGER },
  );
  await page.route("**/api/agent/list", (r) => r.fulfill({ json: [] }));
  await page.route("**/api/models/all", (r) => r.fulfill({ json: { models: [], source: "mock" } }));
  await page.route(/.*\/api\/integrations\/status.*/, (r) => r.fulfill({ json: [] }));
  await page.route("**/api/memory/**", (r) => r.fulfill({ json: [] }));
}

async function ask(page: Page, size = { width: 1440, height: 900 }) {
  await page.setViewportSize(size);
  await install(page);
  await page.goto("/chat");
  const picker = page.getByText("New session", { exact: true });
  const box = page.getByRole("textbox", { name: /^Message / });
  await expect(picker.or(box)).toBeVisible({ timeout: 60_000 });
  if (await picker.isVisible()) {
    await page.getByRole("button", { name: /General-purpose AI company brain/i }).click();
  }
  await expect(box).toBeEnabled({ timeout: 30_000 });
  await box.fill("Send the draft to Kim.");
  await page.getByRole("button", { name: "Send", exact: true }).click();
  await expect(page.locator("[data-confirmation-card]")).toBeVisible({ timeout: 15_000 });
}


test.describe("a stack of approval cards", () => {
  test.describe.configure({ timeout: 120_000 });

  test("the banner counts the cards, the dots and the arrows move between them", async ({ page }) => {
    await ask(page);
    const banner = page.locator("[data-approval-stack-banner]");
    await expect(banner).toContainText("2 approvals waiting");
    await expect(page.locator("[data-approval-stack-edge]")).toHaveCount(1);
    await expect(page.locator("[data-confirmation-card]")).toContainText("Send this draft?");
    await banner.getByRole("button", { name: "Show card 2 of 2" }).click();
    await expect(page.locator("[data-confirmation-card]")).toContainText("Send this new email?");
  });

  test("the email shows its subject as a row and the mail in its own frame", async ({ page }) => {
    await ask(page);
    const mail = page.locator("[data-card-email]");
    await expect(mail.locator('[data-card-email-row="Subject"]')).toContainText("Quote");
    await expect(mail.locator("[data-card-text]")).toContainText("Hi Kim,");
    await expect(mail.locator("[data-card-text]")).toContainText("The quote is attached.");
    await expect(mail.locator("[data-card-text]")).not.toContainText("The text of the draft");
  });
});

