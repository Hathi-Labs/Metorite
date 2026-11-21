import { expect, test, type Page } from "@playwright/test";

/**
 * The row of the chat list, in a real browser (task 3564, owner 2026-11-16).
 *
 * The fault: on hover the Rename and Archive icons drew over the end of the
 * title, the run or unread dot and the age. A row now keeps its acts IN the
 * row. At rest they take no width. On hover or on keyboard focus the title
 * gives up their width, and the dot and the age fold away with it.
 *
 * Each case measures boxes with getBoundingClientRect, for two icons and for
 * three. A third icon joins the same strip as one more child Button (CO-11
 * adds Pin there). Fence of the source: `lib/shell/sessionPickerStrip.test.ts`.
 */

const LONG = "A very long chat title about the quarterly vendor contract review and renewal terms for next year";
const NOW = Date.now();
const ago = (ms: number) => new Date(NOW - ms).toISOString();

const SESSIONS = [
  {
    id: "s-long", agentName: "orchestrator", title: LONG, messageCount: 4,
    createdAt: ago(3 * 86_400_000), updatedAt: ago(3 * 86_400_000),
    visibility: "private", isOwner: true, participantCount: 1, hasUserMessage: true, archivedAt: null,
  },
  {
    id: "s-short", agentName: "orchestrator", title: "Short one", messageCount: 2,
    createdAt: ago(3_600_000), updatedAt: ago(3_600_000),
    visibility: "private", isOwner: true, participantCount: 1, hasUserMessage: true, archivedAt: null,
  },
];

async function open(page: Page, width = 1440) {
  await page.setViewportSize({ width, height: 900 });
  await page.route("**/api/auth/me", (r) =>
    r.fulfill({
      json: {
        email: "dev@e2e.test", user_id: "dev", authenticated: true, is_active: true,
        organization: { id: "00000000-0000-0000-0000-0000000000e2", slug: "e2e" },
        roles: ["owner"], legacy_role: "executive", features: ["chat", "artifacts"],
        features_denied: [], agents: ["orchestrator"], permissions: ["*"],
        capabilities: [], denied: [], is_admin: true,
      },
    }),
  );
  await page.route("**/api/chat/active-sessions", (r) => r.fulfill({ json: [] }));
  await page.route("**/api/chat/sessions", (r) => r.fulfill({ json: SESSIONS }));
  await page.route("**/api/chat/sessions/**", (r) => r.fulfill({ json: [] }));
  await page.goto("/artifacts");
  await expect(page.locator('[data-session-row="s-long"]')).toBeVisible({ timeout: 60_000 });
}

/** An unread dot on the long row, the way the app draws one. */
async function addDot(page: Page) {
  await page.evaluate(() => {
    const meta = document.querySelector('[data-session-row="s-long"] .rail-row-meta');
    const dot = document.createElement("span");
    dot.setAttribute("data-test-dot", "");
    dot.className = "h-1.5 w-1.5 shrink-0 rounded-full bg-info";
    meta?.prepend(dot);
  });
}

/** Make the strip hold `n` icons. The third is one more child Button. */
async function icons(page: Page, n: number) {
  await page.evaluate((n) => {
    const strip = document.querySelector('[data-session-row="s-long"] [data-session-acts]');
    if (!strip) throw new Error("no strip");
    while (strip.children.length < n) strip.appendChild(strip.children[0].cloneNode(true));
  }, n);
}

type Box = { l: number; r: number; t: number; b: number; w: number };

async function boxes(page: Page) {
  return page.evaluate(() => {
    const row = document.querySelector('[data-session-row="s-long"]')!;
    const rect = (el: Element | null): Box | null => {
      if (!el) return null;
      const b = el.getBoundingClientRect();
      return { l: b.left, r: b.right, t: b.top, b: b.bottom, w: b.width };
    };
    const title = row.querySelector("button span.truncate");
    const range = document.createRange();
    range.selectNodeContents(title!);
    const r = range.getBoundingClientRect();
    const meta = row.querySelector(".rail-row-meta")!;
    return {
      strip: rect(row.querySelector("[data-session-acts]")),
      title: rect(title),
      text: { l: r.left, r: r.right, t: r.top, b: r.bottom, w: r.width } as Box,
      dot: rect(row.querySelector("[data-test-dot]")),
      age: rect(meta.lastElementChild),
      row: rect(row),
      metaShown: meta.getBoundingClientRect().width > 1,
      titleClipped: title!.scrollWidth > title!.clientWidth,
    };
  });
}

const meets = (a: Box, b: Box) => a.l < b.r && b.l < a.r && a.t < b.b && b.t < a.b;

for (const n of [2, 3]) {
  for (const how of ["hover", "keyboard"] as const) {
    test(`${n} icons on ${how}: the strip covers no title, dot or age`, async ({ page }) => {
      await open(page);
      await addDot(page);
      await icons(page, n);
      const row = page.locator('[data-session-row="s-long"]');
      // At rest the strip takes no width: the title keeps its room.
      await page.mouse.move(1300, 800);
      const rest = await boxes(page);
      expect(rest.strip!.w).toBe(0);
      expect(rest.metaShown).toBe(true);
      expect(rest.dot).not.toBeNull();

      if (how === "hover") {
        await row.hover();
      } else {
        // A key press sets keyboard modality, so the focus below is a
        // `:focus-visible` one, as a Tab onto the row would give.
        await page.keyboard.press("Tab");
        await row.locator("> button").first().focus();
        await expect(row.locator("> button").first()).toBeFocused();
      }
      const on = await boxes(page);
      expect(on.strip!.w).toBeGreaterThan(20 * n);
      // The strip sits inside the row, after the title.
      expect(on.strip!.r).toBeLessThanOrEqual(on.row!.r + 0.5);
      expect(meets(on.strip!, on.title!)).toBe(false);
      // The hover background is the ROW's, so the pill spans the title AND the
      // icons (owner, 2026-11-16: the icons sat outside the pill).
      const pill = await row.evaluate((li) => {
        const cs = getComputedStyle(li);
        const b = li.getBoundingClientRect();
        return { bg: cs.backgroundColor, radius: parseFloat(cs.borderTopLeftRadius), r: b.right, l: b.left };
      });
      expect(pill.bg).not.toMatch(/rgba\(0, 0, 0, 0\)|transparent/);
      expect(pill.radius).toBeGreaterThan(0);
      expect(on.strip!.r).toBeLessThanOrEqual(pill.r + 0.5);
      expect(on.strip!.l).toBeGreaterThan(pill.l);
      // The dot and the age fold away together with the strip showing.
      expect(on.metaShown).toBe(false);
      expect(on.titleClipped).toBe(true);
      expect(on.title!.w).toBeGreaterThan(rest.title!.w - rest.strip!.w - 1 - (rest.age?.w ?? 0) - (rest.dot?.w ?? 0) - 40);
      // Every icon is on screen inside the row, and none meets another.
      const icon = await row.locator("[data-session-acts] button").evaluateAll((els) =>
        els.map((e) => { const b = e.getBoundingClientRect(); return { l: b.left, r: b.right, t: b.top, b: b.bottom, w: b.width }; }),
      );
      expect(icon).toHaveLength(n);
      for (let i = 1; i < icon.length; i++) expect(icon[i].l).toBeGreaterThanOrEqual(icon[i - 1].r - 0.5);
      for (const b of icon) expect(meets(b, on.title!)).toBe(false);
    });
  }
}

test("the icons stay reachable by Tab from the row", async ({ page }) => {
  await open(page);
  const row = page.locator('[data-session-row="s-long"]');
  await page.keyboard.press("Tab");
  await row.locator("> button").first().focus();
  await page.keyboard.press("Tab");
  await expect(row.getByRole("button", { name: /^Rename / })).toBeFocused();
  await page.keyboard.press("Tab");
  await expect(row.getByRole("button", { name: /^Archive / })).toBeFocused();
});

test("at 390 px the strip still covers nothing", async ({ page }) => {
  await open(page, 390);
  await addDot(page);
  await page.locator('[data-session-row="s-long"]').hover();
  const on = await boxes(page);
  expect(meets(on.strip!, on.title!)).toBe(false);
  expect(on.metaShown).toBe(false);
});

// ---- Daily briefs (task 3564): the briefs fold into one closed group at the end. ----

const BRIEF = (id: string, over: Record<string, unknown> = {}) => ({
  id, agentName: "orchestrator", title: `Your day, ${id}`, messageCount: 2,
  createdAt: ago(7_200_000), updatedAt: ago(7_200_000),
  visibility: "private", isOwner: true, participantCount: 1,
  hasUserMessage: false, isBrief: true, archivedAt: null, ...over,
});

// The briefs are older than "Short one", so the page opens on a chat outside the group.
const BRIEF_SESSIONS = [
  ...SESSIONS,
  BRIEF("brief-a"),
  BRIEF("brief-b", { updatedAt: ago(2 * 86_400_000) }),
  // The member typed this title: the server does not mark it a brief.
  BRIEF("typed", { title: "Your day, my own plan", isBrief: false, hasUserMessage: true }),
  { ...SESSIONS[1], id: "s-old", title: "Put away", archivedAt: ago(1000) },
];

async function openBriefs(page: Page, width = 1440) {
  await page.setViewportSize({ width, height: 900 });
  await page.route("**/api/auth/me", (r) =>
    r.fulfill({
      json: {
        email: "dev@e2e.test", user_id: "dev", authenticated: true, is_active: true,
        organization: { id: "00000000-0000-0000-0000-0000000000e2", slug: "e2e" },
        roles: ["owner"], legacy_role: "executive", features: ["chat", "artifacts"],
        features_denied: [], agents: ["orchestrator"], permissions: ["*"],
        capabilities: [], denied: [], is_admin: true,
      },
    }),
  );
  await page.route("**/api/chat/active-sessions", (r) => r.fulfill({ json: [] }));
  await page.route("**/api/chat/sessions", (r) => r.fulfill({ json: BRIEF_SESSIONS }));
  await page.route("**/api/chat/sessions/**", (r) => r.fulfill({ json: [] }));
  await page.goto("/artifacts");
  await expect(page.locator('[data-session-row="s-long"]')).toBeVisible({ timeout: 60_000 });
}

test("the daily briefs fold into one closed group at the end, with the count", async ({ page }) => {
  await openBriefs(page);
  const group = page.locator('[data-chat-group="Daily briefs"]');
  await expect(group).toBeVisible();
  const toggle = group.getByRole("button", { name: /Daily briefs/ });
  await expect(toggle).toHaveAttribute("aria-expanded", "false");
  await expect(toggle).toContainText("2");
  await expect(page.locator('[data-session-row="brief-a"]')).toHaveCount(0);
  // The group sits below the last day group and above Archived.
  const y = async (sel: string) => (await page.locator(sel).first().boundingBox())!.y;
  expect(await y('[data-chat-group="Daily briefs"]')).toBeGreaterThan(await y('[data-chat-group="Today"]'));
  const archived = page.getByRole("button", { name: /Archived chats/ });
  expect((await archived.boundingBox())!.y).toBeGreaterThan((await toggle.boundingBox())!.y);
  // The member's own 'Your day' chat is not folded.
  await expect(page.locator('[data-session-row="typed"]')).toBeVisible();
  // A click opens it, and a second click closes it.
  await toggle.click();
  await expect(toggle).toHaveAttribute("aria-expanded", "true");
  await expect(page.locator('[data-session-row="brief-a"]')).toBeVisible();
  await expect(page.locator('[data-session-row="brief-b"]')).toBeVisible();
  await toggle.click();
  await expect(page.locator('[data-session-row="brief-a"]')).toHaveCount(0);
});

test("a search shows a matching brief in the one list", async ({ page }) => {
  await openBriefs(page);
  await page.getByLabel("Search chats").fill("brief-b");
  await expect(page.locator('[data-session-row="brief-b"]')).toBeVisible();
  await expect(page.locator('[data-chat-group="Daily briefs"]')).toHaveCount(0);
});

test("the group opens when the chat on screen is a brief and the list arrives late", async ({ page }) => {
  await page.setViewportSize({ width: 1440, height: 900 });
  await page.route("**/api/auth/me", (r) =>
    r.fulfill({
      json: {
        email: "dev@e2e.test", user_id: "dev", authenticated: true, is_active: true,
        organization: { id: "00000000-0000-0000-0000-0000000000e2", slug: "e2e" },
        roles: ["owner"], legacy_role: "executive", features: ["chat", "artifacts"],
        features_denied: [], agents: ["orchestrator"], permissions: ["*"],
        capabilities: [], denied: [], is_admin: true,
      },
    }),
  );
  await page.route("**/api/chat/active-sessions", (r) => r.fulfill({ json: [] }));
  await page.route("**/api/chat/sessions", async (r) => {
    await new Promise((done) => setTimeout(done, 1500));
    await r.fulfill({ json: BRIEF_SESSIONS });
  });
  await page.route("**/api/chat/sessions/**", (r) => r.fulfill({ json: [] }));
  await page.goto("/artifacts?session=brief-a");
  const toggle = page.locator('[data-chat-group="Daily briefs"]').getByRole("button", { name: /Daily briefs/ });
  await expect(toggle).toBeVisible({ timeout: 60_000 });
  await expect(toggle).toHaveAttribute("aria-expanded", "true");
  await expect(page.locator('[data-session-row="brief-a"]')).toBeVisible();
  // The member closes it: it stays closed for the same chat on screen.
  await toggle.click();
  await expect(toggle).toHaveAttribute("aria-expanded", "false");
});
