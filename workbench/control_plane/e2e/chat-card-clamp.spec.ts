import { expect, test, type Page } from "@playwright/test";

/**
 * A long result card opens cut, and the cut never remounts its body (spec
 * `projects_ai_chat.md` §29 rule 2, review of #1160).
 *
 * The first cut swapped the body between `<div>{children}</div>` and bare
 * `children`. React then remounted the card's content at each swap, so a
 * sandbox reloaded and a table lost its sort. A cut body also measured at
 * the cap, uncut itself, grew, and cut again, at frame rate.
 *
 * This spec marks the DOM node of the card's content. A remount makes a new
 * node, and the mark is gone. It also counts every change of the cut state
 * while the card sits cut: a loop changes it many times a second.
 */

const THREAD_TEXT = "Here is the long list you asked for.";

/** A `stack` root draws in the wrapper's frame (`header="frame"`). */
const FRAME_SPEC = {
  type: "stack",
  children: [
    { type: "heading", props: { text: "Every open item" } },
    { type: "markdown", props: { text: Array.from({ length: 60 }, (_, i) => `- Item ${i + 1}: check the vendor contract`).join("\n") } },
  ],
};

/** A template with its own head (`header="own"`). */
const OWN_SPEC = {
  type: "template",
  props: {
    name: "progressTracker",
    data: {
      title: "Rollout steps",
      steps: Array.from({ length: 40 }, (_, i) => ({ label: `Step ${i + 1}`, state: i < 10 ? "done" : i === 10 ? "active" : "pending" })),
    },
  },
};

async function install(page: Page, spec: unknown) {
  await page.addInitScript(
    ({ spec, text }) => {
      window.localStorage.clear();
      const enc = new TextEncoder();
      const frame = (e: unknown) => enc.encode(`data: ${JSON.stringify(e)}\n\n`);
      const json = (body: unknown, status = 200) =>
        new Response(JSON.stringify(body), { status, headers: { "content-type": "application/json" } });
      const orig = window.fetch.bind(window);
      window.fetch = async (input: RequestInfo | URL, init?: RequestInit) => {
        const url = typeof input === "string" ? input : input instanceof URL ? input.href : input.url;
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
          const method = (init?.method ?? "GET").toUpperCase();
          if (url.includes("/room")) return json({ detail: "not a room" }, 404);
          return method === "GET" ? json([]) : json({ ok: true, saved: 1, unchanged: [] });
        }
        if (url.includes("/api/agent/chat")) {
          const body = JSON.parse(String(init?.body ?? "{}"));
          if (body.reconnect) return json({}, 404);
          const stream = new ReadableStream<Uint8Array>({
            start(c) {
              c.enqueue(frame({ type: "message_start", messageId: "m-1" }));
              c.enqueue(frame({ type: "delta", content: text, messageId: "m-1" }));
              c.enqueue(frame({ type: "message_end", messageId: "m-1" }));
              c.enqueue(frame({ type: "tool_start", id: "t-ui", name: "emit_generative_ui", args: {} }));
              c.enqueue(frame({ type: "custom", name: "generative_ui", value: spec }));
              c.enqueue(frame({ type: "tool_end", id: "t-ui", name: "emit_generative_ui", success: true, result: "{\"ok\":true}" }));
              c.enqueue(frame({ type: "done" }));
              c.close();
            },
          });
          return new Response(stream, { status: 200, headers: { "content-type": "text/event-stream" } });
        }
        return orig(input, init);
      };
    },
    { spec, text: THREAD_TEXT },
  );
  await page.route("**/api/agent/list", (r) => r.fulfill({ json: [] }));
  await page.route("**/api/models/all", (r) => r.fulfill({ json: { models: [], source: "mock" } }));
  await page.route(/.*\/api\/integrations\/status.*/, (r) => r.fulfill({ json: [] }));
  await page.route("**/api/memory/**", (r) => r.fulfill({ json: [] }));
}

async function startTurn(page: Page) {
  await page.setViewportSize({ width: 1440, height: 900 });
  await page.goto("/chat");
  const picker = page.getByText("New session", { exact: true });
  const box = page.getByRole("textbox", { name: /^Message / });
  await expect(picker.or(box)).toBeVisible({ timeout: 60_000 });
  if (await picker.isVisible()) {
    await page.getByRole("button", { name: /General-purpose AI company brain/i }).click();
  }
  await expect(box).toBeEnabled({ timeout: 30_000 });
  await box.fill("Show me the long list");
  await page.getByRole("button", { name: "Send", exact: true }).click();
}

for (const [name, spec] of [["a frame card", FRAME_SPEC], ["a card with its own head", OWN_SPEC]] as const) {
  test(`${name}: the cut holds, and neither the cut nor Show all remounts the body`, async ({ page }) => {
    test.setTimeout(120_000);
    await install(page, spec);
    await startTurn(page);
    const wrap = page.locator('[data-rollup-cut="on"]').first();
    await expect(wrap).toBeVisible({ timeout: 20_000 });
    // Mark the content's DOM node, and count every change of the cut state.
    await wrap.evaluate((el) => {
      (el.firstElementChild as unknown as Record<string, unknown>).__mark = "kept";
      const w = window as unknown as Record<string, number>;
      w.__cutChanges = 0;
      new MutationObserver(() => { w.__cutChanges += 1; })
        .observe(el, { attributes: true, attributeFilter: ["data-rollup-cut", "style"] });
    });
    await page.waitForTimeout(1500);
    await expect(wrap).toHaveAttribute("data-rollup-cut", "on");
    expect(await page.evaluate(() => (window as unknown as Record<string, number>).__cutChanges)).toBe(0);
    expect(await wrap.evaluate((el) => (el.firstElementChild as unknown as Record<string, unknown>).__mark)).toBe("kept");

    await page.getByRole("button", { name: "Show all" }).click();
    const opened = page.locator("[data-rollup-cut]").first();
    await expect(opened).toHaveAttribute("data-rollup-cut", "off");
    await expect(page.getByRole("button", { name: "Show all" })).toHaveCount(0);
    expect(await opened.evaluate((el) => (el.firstElementChild as unknown as Record<string, unknown>).__mark)).toBe("kept");
  });
}
