import { expect, test, type Page } from "@playwright/test";

/**
 * Email through the one assistant, in a real browser (navigation_shell.md
 * §7.1.1 rules 5 and 6).
 *
 * The orchestrator hands the email work to `email-assistant` through its
 * specialist tool `email_assistant`. The sub-run's steps arrive as
 * `sub_agent_tool_*` frames. This spec checks that the page draws the
 * delegated draft as the full draft card, with the account of the step, and
 * that the mailbox mark draws above the turn. The chat page mounts the same
 * `AgentChat` as the dock, with no email context, as the dock does.
 *
 * No gateway runs under this suite. Every request the page makes has a stub
 * here, or fails at once.
 */

type Frame = Record<string, unknown>;

const DRAFT_RESULT =
  "Draft from Work · vj@hathilabs.test (mailbox acct-work):\n\nHello Dr Sagar,\n\nWe accept the quote.";

const FRAMES: Frame[] = [
  { type: "tool_start", id: "t-ea", name: "email_assistant", args: { task: "Reply to Dr Sagar" } },
  {
    type: "sub_agent_tool_start", agentName: "email-assistant", id: "s-draft", name: "draft_reply",
    args: { email_id: "msg-1", account_id: "acct-work" },
  },
  { type: "sub_agent_tool_end", agentName: "email-assistant", id: "s-draft", result: DRAFT_RESULT, success: true },
  { type: "sub_agent_delta", agentName: "email-assistant", delta: "Draft ready." },
  { type: "custom", name: "email_mailbox_mark", value: { account_id: "acct-work", label: "Work" } },
  {
    type: "tool_end", id: "t-ea", name: "email_assistant", success: true,
    args: { task: "Reply to Dr Sagar" }, result: "Draft ready.",
  },
];

async function install(page: Page, frames: Frame[]) {
  await page.addInitScript(
    ({ frames }) => {
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
              for (const f of frames) c.enqueue(frame(f));
              c.enqueue(frame({ type: "delta", content: "Your reply is in Drafts.", messageId: "m-1" }));
              c.enqueue(frame({ type: "done" }));
              c.close();
            },
          });
          return new Response(stream, { status: 200, headers: { "content-type": "text/event-stream" } });
        }
        return orig(input, init);
      };
    },
    { frames },
  );
  await page.route("**/api/agent/list", (r) => r.fulfill({ json: [] }));
  await page.route("**/api/models/all", (r) => r.fulfill({ json: { models: [], source: "mock" } }));
  await page.route(/.*\/api\/integrations\/status.*/, (r) => r.fulfill({ json: [] }));
  await page.route("**/api/memory/**", (r) => r.fulfill({ json: [] }));
}

test.describe("email through the one assistant", () => {
  test.setTimeout(150_000);

  test("a delegated draft draws its card, and the mailbox mark draws above the turn", async ({ page }) => {
    await page.setViewportSize({ width: 1440, height: 900 });
    await install(page, FRAMES);
    await page.goto("/chat");
    const picker = page.getByText("New session", { exact: true });
    const box = page.getByRole("textbox", { name: /^Message / });
    await expect(picker.or(box)).toBeVisible({ timeout: 90_000 });
    if (await picker.isVisible()) {
      await page.getByRole("button", { name: /General-purpose AI company brain/i }).click();
    }
    await expect(box).toBeEnabled({ timeout: 30_000 });
    await box.fill("reply to Dr Sagar");
    await page.getByRole("button", { name: "Send", exact: true }).click();
    await expect(page.getByText("Your reply is in Drafts.").first()).toBeVisible({ timeout: 20_000 });

    // Rule 6: the mark, once, above the turn. It names the mailbox of the
    // work under it, so it agrees with the draft card's "from Work".
    const mark = page.locator("[data-mailbox-mark]");
    await expect(mark).toHaveCount(1);
    await expect(mark).toContainText("Now working on Work");

    // Rule 5: the delegated draft draws the editable draft card.
    await expect.poll(async () => {
      const all = page.locator("textarea");
      for (let i = 0; i < (await all.count()); i++) {
        if ((await all.nth(i).inputValue()).includes("We accept the quote.")) return true;
      }
      return false;
    }, { timeout: 15_000 }).toBe(true);

    // Save sends the draft in the mailbox of the STEP, with no email context.
    await page.getByRole("button", { name: "Save to Drafts" }).click();
    await expect.poll(async () => {
      const calls = await page.evaluate(
        () => (window as unknown as { __calls: { method: string; path: string; body: unknown }[] }).__calls.slice(),
      );
      return calls.find((c) => c.path === "/api/email/drafts/save") ?? null;
    }, { timeout: 15_000 }).toMatchObject({ method: "POST", body: { account_id: "acct-work", message_id: "msg-1" } });
  });
});
