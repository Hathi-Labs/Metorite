import { expect, test, type Page } from "@playwright/test";

/**
 * The owner's request of 2026-10-10, in a real browser against the real chat
 * page: long cards roll up when a newer card arrives, and an approval card
 * that arrives at the bottom of the transcript draws in view with no pin.
 *
 * The turn is the owner's screenshot. A "Nothing was created." receipt, a
 * tall "Tasks created (5)" receipt, then a newer receipt, then the approval
 * "Create 5 tasks in MCP & External". The stream is held inside the page (a
 * `fetch` override) and goes on one step at a time, so each moment can be
 * measured.
 *
 * What this replays, before the fix: at the bottom (distance 0), the
 * approval card drew below the fold, the transcript did not move, and the
 * pin "Waiting for your approval · Show" showed for a card the member had
 * never scrolled away from. The vitest twins are `src/lib/stickToBottom.test.ts`
 * and `src/lib/cardRollup.test.ts`.
 *
 * No gateway runs under this suite. Every request the page makes either has
 * a stub here, or fails at once (`GATEWAY_RETRY_DEADLINE_MS=0`).
 */

const uuid = (n: number) => `00000000-0000-4000-8000-${String(n).padStart(12, "0")}`;

const RECEIPT = [
  "Created 5 tasks in «MCP & External».",
  ...[141, 142, 143, 144, 145].flatMap((n, i) => [
    `- #${n} «Wire the MCP connector step ${i + 1}» · Backlog`,
    `  full_id: ${uuid(n)}`,
  ]),
].join("\n");

const UPDATED = `Updated #146 «Write the docs».\n  full_id: ${uuid(146)}`;

/** Text long enough that the transcript overflows a 900 px window. */
const TEXT = Array(9)
  .fill("This paragraph is long so that the transcript overflows the window. ".repeat(6))
  .join("\n\n");

const THREAD = "div.flex-1.overflow-y-auto.relative";
const RECEIPT_CARD = '[data-rollup-card="tool:t-batch"] > [data-rollup]';

async function install(page: Page) {
  await page.addInitScript(
    ({ receipt, updated, text }) => {
      window.localStorage.clear();
      const w = window as unknown as Record<string, unknown>;
      const gates: (() => void)[] = [];
      const waits = [0, 1, 2].map(() => new Promise<void>((r) => gates.push(r)));
      let step = 0;
      w.__release = () => gates[step++]?.();
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
            async start(c) {
              c.enqueue(frame({ type: "message_start", messageId: "m-1" }));
              c.enqueue(frame({ type: "delta", content: text, messageId: "m-1" }));
              c.enqueue(frame({ type: "tool_start", id: "t-none", name: "create_tasks", args: {} }));
              c.enqueue(frame({ type: "tool_end", id: "t-none", name: "create_tasks", success: true, result: "Nothing was created." }));
              c.enqueue(frame({ type: "tool_start", id: "t-batch", name: "create_tasks", args: {} }));
              c.enqueue(frame({ type: "tool_end", id: "t-batch", name: "create_tasks", success: true, result: receipt }));
              await waits[0];
              c.enqueue(frame({ type: "tool_start", id: "t-upd", name: "update_task", args: {} }));
              c.enqueue(frame({ type: "tool_end", id: "t-upd", name: "update_task", success: true, result: updated }));
              await waits[1];
              c.enqueue(frame({
                type: "custom",
                name: "confirmation_requested",
                value: { request_id: "req-c1", title: "Create 5 tasks in MCP & External" },
              }));
              await waits[2];
              c.enqueue(frame({ type: "done" }));
              c.close();
            },
          });
          return new Response(stream, { status: 200, headers: { "content-type": "text/event-stream" } });
        }
        return orig(input, init);
      };
    },
    { receipt: RECEIPT, updated: UPDATED, text: TEXT },
  );
  await page.route("**/api/agent/list", (r) => r.fulfill({ json: [] }));
  await page.route("**/api/models/all", (r) => r.fulfill({ json: { models: [], source: "mock" } }));
  await page.route(/.*\/api\/integrations\/status.*/, (r) => r.fulfill({ json: [] }));
  await page.route("**/api/memory/**", (r) => r.fulfill({ json: [] }));
}

async function startTurn(page: Page) {
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
  await box.fill("Create the five MCP tasks");
  await page.getByRole("button", { name: "Send", exact: true }).click();
  await expect(page.getByText("#141").first()).toBeVisible({ timeout: 15_000 });
}

const release = (page: Page) =>
  page.evaluate(() => (window as unknown as { __release: () => void }).__release());

/** Distance of the thread from its bottom, in px. */
const distance = (page: Page) =>
  page.locator(THREAD).evaluate((el) => el.scrollHeight - el.scrollTop - el.clientHeight);

test.describe("chat cards roll up, and an approval at the bottom draws in view", () => {
  test.setTimeout(120_000);

  test("the receipt rolls up when a newer card arrives, and the approval lands in view with no pin", async ({ page }) => {
    await startTurn(page);
    // The newest card is open, and five rows make it long: it has a toggle.
    await expect(page.locator(RECEIPT_CARD)).toHaveAttribute("data-rollup", "open");
    await expect(page.locator(`${RECEIPT_CARD} [data-rollup-toggle]`)).toHaveAttribute("aria-expanded", "true");
    // The short "Nothing was created." receipt has no toggle.
    await expect(page.locator('[data-rollup-card="tool:t-none"] [data-rollup-toggle]')).toHaveCount(0);

    await release(page); // the newer card
    await expect(page.locator(RECEIPT_CARD)).toHaveAttribute("data-rollup", "closed");
    await expect.poll(() => distance(page)).toBeLessThan(80);

    // The approval arrives at the bottom. Watch for a second: the pin must
    // never draw, and the thread must stay at its bottom.
    //
    // ⚠️ The distance is read in a ResizeObserver callback, not in a frame
    // callback. A frame callback runs BEFORE the frame's resize callbacks,
    // so it reads the content grown and not yet pinned: a state the member
    // never sees painted. This observer is made after the chat's own, so
    // its callback runs after the chat's pin, before the paint.
    await page.evaluate(() => {
      const w = window as unknown as Record<string, unknown>;
      const thread = document.querySelector<HTMLElement>("div.flex-1.overflow-y-auto.relative")!;
      const frames: { pin: boolean; dist: number }[] = [];
      w.__frames = frames;
      const sample = () =>
        frames.push({
          pin: !!document.querySelector("[data-ask-pin]"),
          dist: thread.scrollHeight - thread.scrollTop - thread.clientHeight,
        });
      const ro = new ResizeObserver(sample);
      ro.observe(thread);
      ro.observe(thread.querySelector(":scope > div.max-w-3xl")!);
      let n = 0;
      const tick = () => {
        sample();
        if (++n < 60) requestAnimationFrame(() => setTimeout(tick, 0));
        else {
          ro.disconnect();
          w.__done = true;
        }
      };
      requestAnimationFrame(() => setTimeout(tick, 0));
      (w.__release as () => void)();
    });
    const card = page.locator('[data-chat-ask="hitl"]');
    await expect(card).toBeVisible();
    await expect.poll(() => page.evaluate(() => (window as unknown as { __done?: boolean }).__done === true), { timeout: 10_000 }).toBe(true);
    const frames = await page.evaluate(() => (window as unknown as { __frames: { pin: boolean; dist: number }[] }).__frames);
    expect(frames.filter((f) => f.pin), "the pin drew while the card was in view").toHaveLength(0);
    expect(Math.max(...frames.map((f) => f.dist)), "the transcript left its bottom").toBeLessThan(80);

    // The card is wholly inside the thread's viewport.
    const box = await card.boundingBox();
    const view = await page.locator(THREAD).boundingBox();
    expect(box && view).toBeTruthy();
    expect(box!.y).toBeGreaterThanOrEqual(view!.y - 1);
    expect(box!.y + box!.height).toBeLessThanOrEqual(view!.y + view!.height + 1);
  });

  test("scrolled up, nothing moves and the pin offers Show", async ({ page }) => {
    await startTurn(page);
    await release(page); // the newer card
    await expect(page.locator(RECEIPT_CARD)).toHaveAttribute("data-rollup", "closed");
    await page.locator(THREAD).evaluate((el) => {
      el.scrollTop = 0;
    });
    await expect.poll(() => page.locator(THREAD).evaluate((el) => el.scrollTop)).toBe(0);

    await release(page); // the approval
    await expect(page.locator("[data-ask-pin]")).toBeVisible();
    await expect(page.locator("[data-ask-pin]")).toContainText("Waiting for your approval");
    expect(await page.locator(THREAD).evaluate((el) => el.scrollTop)).toBe(0);
  });

  test("a receipt opened by hand stays open when the approval arrives", async ({ page }) => {
    await startTurn(page);
    await release(page); // the newer card
    const toggle = page.locator(`${RECEIPT_CARD} [data-rollup-toggle]`);
    await expect(toggle).toHaveAttribute("aria-expanded", "false");
    await toggle.click();
    await expect(page.locator(RECEIPT_CARD)).toHaveAttribute("data-rollup", "open");

    await release(page); // the approval: the newest card now, and it waits
    await expect(page.locator('[data-chat-ask="hitl"]')).toBeVisible();
    await expect(page.locator(RECEIPT_CARD)).toHaveAttribute("data-rollup", "open");
  });
});

/**
 * The owner's feedback of 2026-10-10: "the option to roll up is on the
 * right-hand side, and the option to open the accordion is on the left-hand
 * side". There is one toggle now. It is the card's own title row, inside its
 * border, and its chevron stays in one place in both states. The vitest twin
 * is "one toggle, one place, inside the card" in `src/lib/cardRollup.test.ts`.
 */
test.describe("one toggle, one place, inside the card", () => {
  test.setTimeout(120_000);

  const CARD = '[data-rollup-card="tool:t-batch"]';
  const TOGGLE = `${CARD} [data-rollup-toggle]`;
  const CHEVRON = `${TOGGLE} [data-rollup-chevron]`;

  test("the title row toggles the card, and the chevron does not move", async ({ page }) => {
    await startTurn(page);
    await release(page); // the newer card: the receipt rolls up
    await expect(page.locator(RECEIPT_CARD)).toHaveAttribute("data-rollup", "closed");

    // One toggle per card, and no "Roll up" control anywhere.
    await expect(page.locator(TOGGLE)).toHaveCount(1);
    await expect(page.locator(`${CARD} [aria-expanded]`)).toHaveCount(1);
    await expect(page.getByText("Roll up", { exact: true })).toHaveCount(0);

    // The toggle is the first row inside the receipt's own border.
    const inside = () =>
      page.locator(TOGGLE).evaluate((btn) => {
        const frame = btn.parentElement!;
        const b = btn.getBoundingClientRect();
        const f = frame.getBoundingClientRect();
        return {
          first: frame.firstElementChild === btn,
          bordered: getComputedStyle(frame).borderTopWidth !== "0px",
          within: b.left >= f.left && b.right <= f.right && b.top >= f.top && b.bottom <= f.bottom,
        };
      });
    expect(await inside()).toEqual({ first: true, bordered: true, within: true });

    // The chevron is ONE element: keep a handle to it across both states.
    const chevron = await page.locator(CHEVRON).elementHandle();
    const at = () =>
      chevron!.evaluate((el) => {
        // `offsetLeft` ignores the turn, so a turn still under way does not
        // read as a move.
        const h = el as HTMLElement;
        const left = h.offsetLeft + (h.offsetParent?.getBoundingClientRect().left ?? 0);
        return { left: Math.round(left), first: el.parentElement?.firstElementChild === el, live: el.isConnected };
      });
    const shut = await at();
    expect(shut.first && shut.live).toBe(true);

    // A click on the header opens the card. The chevron is the same node,
    // at the same x.
    await page.locator(TOGGLE).click();
    await expect(page.locator(RECEIPT_CARD)).toHaveAttribute("data-rollup", "open");
    await expect(page.locator(TOGGLE)).toHaveAttribute("aria-expanded", "true");
    expect(await page.locator(CHEVRON).evaluate((el, h) => el === h, chevron)).toBe(true);
    const open = await at();
    expect(open).toEqual(shut);
    expect(await inside()).toEqual({ first: true, bordered: true, within: true });
    await expect(page.locator(TOGGLE)).toHaveCount(1);

    // The same click closes it again.
    await page.locator(TOGGLE).click();
    await expect(page.locator(RECEIPT_CARD)).toHaveAttribute("data-rollup", "closed");
    expect(await at()).toEqual(shut);

    // The keyboard: Enter and Space toggle it.
    await page.locator(TOGGLE).focus();
    await page.keyboard.press("Enter");
    await expect(page.locator(RECEIPT_CARD)).toHaveAttribute("data-rollup", "open");
    await page.keyboard.press("Space");
    await expect(page.locator(RECEIPT_CARD)).toHaveAttribute("data-rollup", "closed");

    // The toggle names its panel.
    const controls = await page.locator(TOGGLE).getAttribute("aria-controls");
    expect(controls).toBeTruthy();
    await expect(page.locator(`[id="${controls}"]`)).toHaveCount(1);
  });

  test("a press on the shut summary opens the card by hand, and it stays open when the focus leaves", async ({ page }) => {
    // Measured in the visual review, 2026-10-10: the focus opened a shut
    // card at the press, the summary under the pointer left the page, the
    // click never landed, and the card shut again when the focus moved on.
    await startTurn(page);
    await release(page);
    await expect(page.locator(RECEIPT_CARD)).toHaveAttribute("data-rollup", "closed");
    const summary = page.locator(`${TOGGLE} > span`).last();
    await expect(summary).toContainText("#141");
    await summary.click();
    await expect(page.locator(RECEIPT_CARD)).toHaveAttribute("data-rollup", "open");
    // The focus leaves the card.
    await page.getByRole("textbox", { name: /^Message / }).focus();
    await expect(page.locator(RECEIPT_CARD)).toHaveAttribute("data-rollup", "open");
    // A Tab onto a shut header does not open it: only Enter does.
    await page.locator(TOGGLE).click();
    await expect(page.locator(RECEIPT_CARD)).toHaveAttribute("data-rollup", "closed");
    await page.getByRole("textbox", { name: /^Message / }).focus();
    await page.locator(TOGGLE).focus();
    await expect(page.locator(RECEIPT_CARD)).toHaveAttribute("data-rollup", "closed");
  });

  test("a row's open-link icon opens the task, and the card does not toggle", async ({ page }) => {
    await startTurn(page);
    // Newest, the receipt is open. Hold the task page's request, so the
    // page stays to be measured.
    const held = page.waitForRequest((r) => r.url().includes("/projects?task="));
    await page.route(/\/projects\?task=/, () => {});
    await expect(page.locator(RECEIPT_CARD)).toHaveAttribute("data-rollup", "open");
    await page.evaluate((sel) => {
      const w = window as unknown as Record<string, unknown>;
      const flips: string[] = [];
      w.__flips = flips;
      const root = document.querySelector(sel)!;
      new MutationObserver(() => flips.push(root.getAttribute("data-rollup") ?? "")).observe(root, {
        attributes: true,
        attributeFilter: ["data-rollup"],
      });
    }, RECEIPT_CARD);
    await page.locator(`${CARD} [aria-label="Open in Projects"]`).first().click();
    await held; // the link works
    expect(await page.evaluate(() => (window as unknown as { __flips: string[] }).__flips)).toEqual([]);
    await expect(page.locator(RECEIPT_CARD)).toHaveAttribute("data-rollup", "open");
  });
});
