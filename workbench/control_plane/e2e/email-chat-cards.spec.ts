import { expect, test, type Locator, type Page } from "@playwright/test";

/**
 * The Email chat cards, in a real browser against the real chat page
 * (follow-up of #945, "chat cards: one look").
 *
 * Two halves.
 *
 * 1. **The payload.** Each act of a card sends the same request as before
 *    the cards moved onto the shared header: Save and Send of a draft, and
 *    Disable and Delete of a rule. The spec records every `/api/email/*`
 *    call the page makes, and compares the method, the path and the body by
 *    value. This half ran green on `main` BEFORE the change, so it is the
 *    snapshot of the payload, not of the markup.
 * 2. **The look.** The draft card waits on the member, so it draws the
 *    shared header and no fold toggle while it waits, even after newer
 *    cards arrive. Once the draft is saved, it may fold.
 *
 * No gateway runs under this suite. Every request the page makes either has
 * a stub here, or fails at once (`GATEWAY_RETRY_DEADLINE_MS=0`).
 */

const RULE_ID = "aaaaaaaa-1111-4000-8000-000000000001";
const RULE = {
  id: RULE_ID,
  accountId: "acct-1",
  name: "Invoices",
  enabled: true,
  automated: false,
  instructions: "Invoices from vendors",
  actions: [{ type: "LABEL", label: "Invoices" }],
};

type Frame = Record<string, unknown>;

const tool = (id: string, name: string, args: Record<string, unknown>, result: string): Frame[] => [
  { type: "tool_start", id, name, args },
  { type: "tool_end", id, name, success: true, args, result },
];

const DRAFT = tool(
  "t-draft",
  "draft_reply",
  { email_id: "msg-1", account_id: "acct-model" },
  "Draft from Ops <ops@example.com> (mailbox acct-1):\n\nHello there,\n\nThe quote is attached.",
);
const RULE_EVENT = tool(
  "t-rule",
  "create_rule",
  {
    account_id: "acct-1",
    name: "Invoices",
    instructions: "Invoices from vendors",
    action_type: "LABEL",
    label: "Invoices",
    automated: false,
  },
  `Created rule 'Invoices' (id=${RULE_ID}).`,
);
const SETTINGS = tool(
  "t-settings",
  "update_assistant_settings",
  { account_id: "acct-1", auto_run: true, signature: "Best, Ops" },
  "Settings updated.",
);
const ARCHIVE = tool(
  "t-archive",
  "manage_inbox",
  { action: "archive", message_ids: ["msg-2", "msg-3"] },
  "Archived 2 emails.",
);
/** A newer receipt with a long body, so an older long card would fold. */
const LONG = tool(
  "t-long",
  "add_knowledge",
  {},
  Array.from({ length: 30 }, (_, i) => `Line ${i + 1} of the knowledge entry.`).join("\n"),
);

interface Call {
  method: string;
  path: string;
  body: unknown;
}

async function install(page: Page, frames: Frame[]) {
  await page.addInitScript(
    ({ frames, rule }) => {
      window.localStorage.clear();
      const w = window as unknown as Record<string, unknown>;
      const calls: { method: string; path: string; body: unknown }[] = [];
      w.__calls = calls;
      const enc = new TextEncoder();
      const frame = (e: unknown) => enc.encode(`data: ${JSON.stringify(e)}\n\n`);
      const json = (body: unknown, status = 200) =>
        new Response(JSON.stringify(body), { status, headers: { "content-type": "application/json" } });
      const orig = window.fetch.bind(window);
      window.fetch = async (input: RequestInfo | URL, init?: RequestInit) => {
        const url = typeof input === "string" ? input : input instanceof URL ? input.href : input.url;
        const method = (init?.method ?? "GET").toUpperCase();
        const path = new URL(url, window.location.href);
        if (path.pathname.startsWith("/api/email/")) {
          const raw = init?.body ? String(init.body) : "";
          calls.push({ method, path: path.pathname + path.search, body: raw ? JSON.parse(raw) : null });
          if (path.pathname === "/api/email/drafts/save") return json({ created: true, id: "draft-1" });
          if (path.pathname === "/api/email/drafts/send") return json({ sent: true });
          if (path.pathname === "/api/email/rules" && method === "GET") return json({ rules: [rule] });
          if (path.pathname.startsWith("/api/email/rules/")) {
            return method === "DELETE" ? new Response(null, { status: 204 }) : json(rule);
          }
          return json({});
        }
        if (url.includes("/api/chat/active-sessions")) return json([]);
        if (url.includes("/api/auth/me")) {
          return json({
            email: "dev@e2e.test", user_id: "dev", authenticated: true, is_active: true,
            organization: { id: "00000000-0000-0000-0000-0000000000e2", slug: "e2e" },
            roles: ["owner"], legacy_role: "executive", features: ["chat"],
            features_denied: [], agents: ["orchestrator"], permissions: ["*"],
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
            start(c) {
              c.enqueue(frame({ type: "message_start", messageId: "m-1" }));
              c.enqueue(frame({ type: "delta", content: "Here is what I did.", messageId: "m-1" }));
              for (const f of frames) c.enqueue(frame(f));
              c.enqueue(frame({ type: "done" }));
              c.close();
            },
          });
          return new Response(stream, { status: 200, headers: { "content-type": "text/event-stream" } });
        }
        return orig(input, init);
      };
    },
    { frames, rule: RULE },
  );
  await page.route("**/api/agent/list", (r) => r.fulfill({ json: [] }));
  await page.route("**/api/models/all", (r) => r.fulfill({ json: { models: [], source: "mock" } }));
  await page.route(/.*\/api\/integrations\/status.*/, (r) => r.fulfill({ json: [] }));
  await page.route("**/api/memory/**", (r) => r.fulfill({ json: [] }));
}

async function startTurn(page: Page, frames: Frame[]) {
  await page.setViewportSize({ width: 1440, height: 900 });
  await install(page, frames);
  await page.goto("/chat");
  const picker = page.getByText("New session", { exact: true });
  const box = page.getByRole("textbox", { name: /^Message / });
  await expect(picker.or(box)).toBeVisible({ timeout: 60_000 });
  if (await picker.isVisible()) {
    await page.getByRole("button", { name: /General-purpose AI company brain/i }).click();
  }
  await expect(box).toBeEnabled({ timeout: 30_000 });
  await box.fill("Draft the reply and make the rule");
  await page.getByRole("button", { name: "Send", exact: true }).click();
  await expect(page.getByText("Here is what I did.").first()).toBeVisible({ timeout: 15_000 });
}

const calls = (page: Page) =>
  page.evaluate(() => (window as unknown as { __calls: Call[] }).__calls.slice());

/** The draft's text box: the one textarea that holds the draft. */
async function draftBox(page: Page): Promise<Locator> {
  const all = page.locator("textarea");
  await expect.poll(async () => {
    for (let i = 0; i < (await all.count()); i++) {
      if ((await all.nth(i).inputValue()).includes("Hello there")) return i;
    }
    return -1;
  }).toBeGreaterThan(-1);
  for (let i = 0; i < (await all.count()); i++) {
    if ((await all.nth(i).inputValue()).includes("Hello there")) return all.nth(i);
  }
  throw new Error("no draft box");
}

/** The innermost element that holds the text: the card itself. */
const cardWith = (page: Page, text: string) =>
  page.locator("[data-rollup-card], div.rounded-lg").filter({ hasText: text }).last();

test.describe("the Email chat cards send the same payload", () => {
  test.setTimeout(120_000);

  test("Save to Drafts saves the edited text in the mailbox of the draft", async ({ page }) => {
    await startTurn(page, DRAFT);
    const box = await draftBox(page);
    await box.fill("Edited body");
    await page.getByRole("button", { name: "Save to Drafts" }).click();
    await expect(page.getByText("Saved to Drafts")).toBeVisible();
    expect(await calls(page)).toEqual([
      { method: "POST", path: "/api/email/drafts/save", body: { account_id: "acct-1", message_id: "msg-1", body: "Edited body" } },
    ]);
  });

  test("Send asks first, then saves and sends that draft", async ({ page }) => {
    await startTurn(page, DRAFT);
    const asked: string[] = [];
    page.on("dialog", (d) => {
      asked.push(d.message());
      void d.accept();
    });
    await cardWith(page, "Draft reply").getByRole("button", { name: "Send", exact: true }).click();
    await expect(page.getByText("Sent", { exact: true })).toBeVisible();
    expect(asked).toEqual(["Send this reply from Ops <ops@example.com>?"]);
    expect(await calls(page)).toEqual([
      {
        method: "POST",
        path: "/api/email/drafts/save",
        body: { account_id: "acct-1", message_id: "msg-1", body: "Hello there,\n\nThe quote is attached." },
      },
      { method: "POST", path: "/api/email/drafts/send", body: { account_id: "acct-1", draft_id: "draft-1" } },
    ]);
  });

  test("Disable reads the rule, then writes it back with enabled off", async ({ page }) => {
    await startTurn(page, RULE_EVENT);
    const card = cardWith(page, "Created rule: Invoices");
    await card.getByRole("button", { name: "Disable", exact: true }).click();
    await expect(card.getByText("Disabled.")).toBeVisible();
    expect(await calls(page)).toEqual([
      { method: "GET", path: "/api/email/rules?account_id=acct-1", body: null },
      { method: "PATCH", path: `/api/email/rules/${RULE_ID}`, body: { ...RULE, enabled: false } },
    ]);
  });

  test("Delete asks first, then deletes that rule", async ({ page }) => {
    await startTurn(page, RULE_EVENT);
    const asked: string[] = [];
    page.on("dialog", (d) => {
      asked.push(d.message());
      void d.accept();
    });
    const card = cardWith(page, "Created rule: Invoices");
    await card.getByRole("button", { name: "Delete", exact: true }).click();
    await expect(card.getByText("Deleted.")).toBeVisible();
    expect(asked).toEqual(['Delete rule "Invoices"?']);
    expect(await calls(page)).toEqual([{ method: "DELETE", path: `/api/email/rules/${RULE_ID}`, body: null }]);
  });

  test("the rule and settings cards show what the tool wrote", async ({ page }) => {
    await startTurn(page, [...RULE_EVENT, ...SETTINGS]);
    const rule = cardWith(page, "Created rule: Invoices");
    await expect(rule).toContainText("AI: Invoices from vendors");
    await expect(rule).toContainText("LABEL: Invoices");
    await expect(rule).toContainText("Proposes for approval (not auto-applied)");
    const settings = cardWith(page, "Settings updated");
    await expect(settings).toContainText("Auto-run rules: on");
    await expect(settings).toContainText("Signature: Best, Ops");
    // Showing a card writes nothing.
    expect(await calls(page)).toEqual([]);
  });
});
