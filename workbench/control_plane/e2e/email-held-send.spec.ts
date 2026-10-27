import { expect, test, type Page } from "@playwright/test";

/**
 * §21 (`email_app_master_plan.md`): a held chat sends a draft through ONE card
 * that the gateway built and recorded. A person that the member does not know
 * starts unticked, with the mark "new address — not in your contacts".
 *
 * The page is the real `/chat`. No gateway runs. The stream is a `fetch`
 * override in the page, as in `email-chat-reply.spec.ts`. The card arrives as
 * `confirmation_requested` with the gateway's rows (`kind`, `class`), and the
 * stream waits until the page answers through `POST /api/agent/respond-input`.
 * The stream then gives the tool's answer for the ids that the page ticked,
 * in the words of `_held_sent_text`. The server's own audit line and its send
 * of only the ticked rows are fenced in Python
 * (`tests/unit/test_held_send_card_record.py`).
 *
 * What this holds: the new row is unticked and marked; a tick sends it; no
 * tick leaves it out; with no ticked To row, Approve is off.
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

async function ask(page: Page) {
  await page.setViewportSize({ width: 1440, height: 900 });
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

const answers = (page: Page) =>
  page.evaluate(() => (window as unknown as { __answers: Record<string, unknown>[] }).__answers.slice());

test.describe("the held send card in the main chat", () => {
  test.describe.configure({ timeout: 120_000 });

  test("a new address starts unticked and marked, and a tick sends it", async ({ page }) => {
    await ask(page);
    const card = page.locator("[data-confirmation-card]");
    const stranger = card.locator('[data-confirmation-row="cc-1"]');
    await expect(stranger).not.toBeChecked();
    await expect(card.locator('[data-send-row-mark="new"]')).toHaveText(
      "new address — not in your contacts",
    );
    await expect(card.locator('[data-confirmation-row="to-0"]')).toBeChecked();
    await stranger.check();
    await card.locator("[data-confirmation-approve]").click();
    await expect.poll(() => answers(page)).toHaveLength(1);
    expect((await answers(page))[0]).toMatchObject({
      request_id: RID,
      answer: 'APPROVE {"rows":["to-0","cc-0","cc-1"]}',
    });
    await expect(page.getByText(`Draft sent: To ${KNOWN}; Cc ${COLLEAGUE}, ${STRANGER}.`).first())
      .toBeVisible({ timeout: 15_000 });
  });

  test("with no tick, the new address does not go", async ({ page }) => {
    await ask(page);
    const card = page.locator("[data-confirmation-card]");
    await card.locator("[data-confirmation-approve]").click();
    await expect.poll(() => answers(page)).toHaveLength(1);
    expect((await answers(page))[0]).toMatchObject({
      request_id: RID,
      answer: 'APPROVE {"rows":["to-0","cc-0"]}',
    });
    await expect(page.getByText("The member left 1 row(s) unticked, and those did not go.").first())
      .toBeVisible({ timeout: 15_000 });
  });

  test("with no ticked To row, Approve is off", async ({ page }) => {
    await ask(page);
    const card = page.locator("[data-confirmation-card]");
    await card.locator('[data-confirmation-row="to-0"]').uncheck();
    await expect(card.locator("[data-confirmation-approve]")).toBeDisabled();
    await expect(card).toContainText("Tick one To row at least.");
    expect(await answers(page)).toEqual([]);
  });
});
