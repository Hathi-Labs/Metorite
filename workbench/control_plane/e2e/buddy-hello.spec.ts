import { expect, test, type BrowserContext, type Page, type Route } from "@playwright/test";

/**
 * The hello (WS-BUD BUD-1, D177, `onboarding.md` section 13).
 *
 * A new member who lands on `/` sees the assistant open on a NEW chat named
 * "Welcome", with one assistant row and three chips. No gateway runs here.
 * `makeServer` is an in-memory gateway: it merges the layout, answers
 * `guide` as `GUIDE_ORGS` would, and claims `buddy_hello_at` first when
 * `POST /api/chat/sessions/<id>/buddy-hello` arrives, as `chat_brief.py` does
 * (the R8 suite `tests/unit/test_buddy_hello_r8.py` proves the real SQL).
 *
 *   a. A founder, after the welcome dialog, at 1280 px and at 390 px.
 *   b. A joiner, on the real path: the layout question first.
 *   c. One hello only: after a reload, and from a second context.
 *   d. The switch off: no call, no card.
 *   e. A deep link, and a member who has chatted: no hello.
 *   f. Each chip.
 *   g. Two tabs at once: one row.
 *
 * Set `HELLO_SHOTS=<dir>` to save captures (the visual review of
 * `DESIGN_SYSTEM.md` section 11), and `HELLO_THEME`, `HELLO_DENSITY`,
 * `HELLO_ACCENT` for the look under review.
 */

const SESSION = {
  user: { email: "priya@example.com", name: "Priya Shah" },
  expires: "2099-01-01T00:00:00.000Z",
};
const MEMBER = {
  authenticated: true,
  email: "priya@example.com",
  name: "Priya Shah",
  is_active: true,
  is_admin: false,
  features: ["tasks", "projects", "email", "chat", "people", "approvals"],
  permissions: [],
  capabilities: [],
  roles: ["member"],
  organization: { id: "org1", slug: "acme", display_name: "Acme" },
};
const FOUNDER = { ...MEMBER, is_admin: true, roles: ["owner"] };

const NEVER = {
  preset: null,
  answered: null,
  pins: null,
  known: null,
  cardOrder: null,
  newOrder: null,
  chat_first_message_at: null,
  tour_seen_at: null,
  buddy_hello_at: null,
};
const ANSWERED = { ...NEVER, preset: "engineer", answered: "answered" };
const SERVER_TIME = "2026-11-05T12:00:00.000Z";
const HELLO = "Hi Priya, welcome to Metorite. It can look like a lot at first, and that's okay. You don't have to learn it all today. I'm here whenever you need me. Ask me anything, and I'll show you where it is.";
const CHIPS = [
  { id: "tour", label: "Show me around" },
  { id: "ask", label: "What can you do here?" },
  { id: "look", label: "I'll look around" },
];

interface Row {
  id: string;
  role: string;
  content: string;
  timestamp: number;
  customEvents: unknown[];
  authorKind: string;
  authorEmail: string;
}

/** The gateway, in memory: one per member, shared by every page that uses it. */
interface Server {
  layout: Record<string, unknown>;
  guide: boolean;
  sessions: Array<Record<string, unknown>>;
  rows: Record<string, Row[]>;
  titles: Record<string, string>;
  helloCalls: Array<{ id: string; status: number }>;
  puts: Record<string, unknown>[];
}

function makeServer(opts: { layout?: Record<string, unknown>; guide?: boolean; chatted?: boolean } = {}): Server {
  return {
    layout: { ...(opts.layout ?? ANSWERED) },
    guide: opts.guide ?? true,
    sessions: opts.chatted
      ? [{
          id: "old1", agentName: "orchestrator", title: "Q3 plan", lastPreview: "Thanks", messageCount: 2,
          createdAt: "2026-10-01T09:00:00Z", updatedAt: "2026-10-01T09:05:00Z", hasUserMessage: true,
        }]
      : [],
    rows: {},
    titles: {},
    helloCalls: [],
    puts: [],
  };
}

const LOOK = {
  theme: process.env.HELLO_THEME ?? "",
  density: process.env.HELLO_DENSITY ?? "",
  accent: process.env.HELLO_ACCENT ?? "",
};

async function stub(page: Page, server: Server, opts: { me?: unknown } = {}): Promise<void> {
  await page.addInitScript((look) => {
    if (look.theme) localStorage.setItem("theme", look.theme);
    if (look.density) localStorage.setItem("cc-density", look.density);
    if (look.accent) localStorage.setItem("cc-accent", look.accent);
    localStorage.setItem("cc-shell-bar", "1");
    localStorage.setItem("cc-shell-nav", "1");
    localStorage.setItem("cc-shell-chat", "1");
    localStorage.setItem("cc-shell-chat-first", "1");
    localStorage.setItem("cc-my-day", "1");
    localStorage.removeItem("cc-sidebar-collapsed");
  }, LOOK);
  await page.route("**/api/**", async (route: Route) => {
    const req = route.request();
    const path = new URL(req.url()).pathname;
    const json = (body: unknown, status = 200) =>
      route.fulfill({ status, contentType: "application/json", body: JSON.stringify(body) });
    if (path === "/api/auth/me/shell") {
      if (req.method() === "PUT") {
        const body = (req.postDataJSON() ?? {}) as Record<string, unknown>;
        server.puts.push(body);
        // The gateway merges marks alone, and never lets a layout save write the hello mark.
        const { buddy_hello_at: _ignored, ...rest } = body;
        server.layout = { ...server.layout, ...rest, ...(rest.tour_seen_at ? { tour_seen_at: SERVER_TIME } : {}) };
      }
      return json({ ...server.layout, guide: server.guide });
    }
    const hello = path.match(/^\/api\/chat\/sessions\/([^/]+)\/buddy-hello$/);
    if (hello) {
      const id = decodeURIComponent(hello[1]);
      const answer = (status: number) => {
        server.helloCalls.push({ id, status });
        return status === 204 ? route.fulfill({ status }) : json({ detail: "Not Found" }, status);
      };
      if (!server.guide) return answer(404);
      // First write wins: the loser gets 204 and no row.
      if (server.layout.buddy_hello_at) return answer(204);
      server.layout = { ...server.layout, buddy_hello_at: SERVER_TIME };
      server.rows[id] = [{
        id: "buddy-hello", role: "assistant", content: HELLO, timestamp: Date.now(), authorKind: "agent",
        authorEmail: "orchestrator",
        customEvents: [{
          name: "generative_ui",
          value: { type: "template", props: { name: "buddyHello", data: { chips: CHIPS } } },
          segmentCutoff: 0,
        }],
      }];
      server.titles[id] = "Welcome";
      server.helloCalls.push({ id, status: 200 });
      return json({ posted: true, message_id: "buddy-hello", preview: HELLO, title: "Welcome" });
    }
    const msgs = path.match(/^\/api\/chat\/sessions\/([^/]+)\/messages$/);
    if (msgs && req.method() === "GET") return json(server.rows[decodeURIComponent(msgs[1])] ?? []);
    if (path === "/api/chat/sessions" && req.method() === "GET") return json(server.sessions);
    if (path === "/api/auth/me") return json(opts.me ?? MEMBER);
    if (path === "/api/auth/session") return json(SESSION);
    if (path === "/api/health") return json({ gateway: "up" });
    if (path === "/api/accounts") return json({ enabled: false });
    if (path === "/api/settings/branding") return json({ logo: null, updatedBy: "", updatedAt: "" });
    if (path === "/api/shell/needs") return json({ count: 0, total: 0, items: [], sources: {} });
    if (path.startsWith("/api/projects/my/")) return json({ rows: [], total: 0 });
    if (path === "/api/chat/active-sessions") return json([]);
    return json([]);
  });
}

const welcome = (page: Page) => page.getByRole("dialog", { name: "Your organization is ready" });
const picker = (page: Page) => page.getByRole("dialog", { name: "What will you do most here?" });
const panel = (page: Page) => page.locator("[data-assistant]");
const chip = (page: Page, name: string) => page.getByRole("button", { name, exact: true });
const card = (page: Page) => page.locator("[data-guide-card]");
const helloCard = (page: Page) => page.locator("[data-buddy-hello]");

async function shot(page: Page, name: string): Promise<void> {
  const dir = process.env.HELLO_SHOTS;
  if (dir) await page.screenshot({ path: `${dir}/${name}.png` });
}

async function expectHello(page: Page): Promise<void> {
  await expect(page.getByText("Hi Priya, welcome to Metorite.").first()).toBeVisible({ timeout: 20_000 });
  await expect(helloCard(page)).toHaveCount(1);
  for (const c of CHIPS) await expect(chip(page, c.label)).toBeVisible();
}

for (const width of [1280, 390]) {
  test.describe(`at ${width} px`, () => {
    test.use({ viewport: { width, height: width === 390 ? 844 : 860 } });

    test(`a founder: after the welcome dialog, one hello in a chat named Welcome`, async ({ page }) => {
      // The founder has answered the layout question; the welcome dialog is next.
      const server = makeServer({ layout: { ...ANSWERED, answered: "skipped", preset: null } });
      await stub(page, server, { me: FOUNDER });
      await page.goto("/?welcome=new-org");
      await expect(welcome(page)).toBeVisible();
      // Nothing opens under the dialog.
      await page.waitForTimeout(600);
      expect(server.helloCalls).toHaveLength(0);
      await welcome(page).getByRole("button", { name: "Explore on my own first" }).click();
      await expect(welcome(page)).toHaveCount(0, { timeout: 2_000 });
      await expectHello(page);
      expect(server.helloCalls.map((c) => c.status)).toEqual([200]);
      const [id] = Object.keys(server.rows);
      expect(server.titles[id]).toBe("Welcome");
      expect(server.rows[id]).toHaveLength(1);
      await shot(page, `founder-${width}`);
    });
  });
}

test.describe("on the real paths", () => {
  test.use({ viewport: { width: 1280, height: 860 } });

  test("a joiner: the layout question first, then the hello", async ({ page }) => {
    const server = makeServer({ layout: NEVER });
    await stub(page, server);
    await page.goto("/");
    await expect(picker(page)).toBeVisible();
    await page.waitForTimeout(600);
    expect(server.helloCalls).toHaveLength(0);
    await picker(page).getByRole("button", { name: "Skip for now" }).click();
    await expect(picker(page)).toHaveCount(0);
    await expectHello(page);
    expect(server.helloCalls.map((c) => c.status)).toEqual([200]);
  });

  test("one hello only: a reload and a second context show none", async ({ page, browser }) => {
    const server = makeServer();
    await stub(page, server);
    await page.goto("/");
    await expectHello(page);
    await page.reload();
    await expect(page.locator('[data-guide="home.app-list"]')).toBeVisible();
    await page.waitForTimeout(1_500);
    await expect(helloCard(page)).toHaveCount(0);
    // A second device: the server stamp stops it, with or without a call.
    const other: BrowserContext = await browser.newContext({ viewport: { width: 1280, height: 860 } });
    const second = await other.newPage();
    await stub(second, server);
    await second.goto("/");
    await expect(second.locator('[data-guide="home.app-list"]')).toBeVisible();
    await second.waitForTimeout(1_500);
    await expect(helloCard(second)).toHaveCount(0);
    expect(server.helloCalls.filter((c) => c.status === 200)).toHaveLength(1);
    expect(Object.values(server.rows).flat()).toHaveLength(1);
    await other.close();
  });

  test("with the switch off: no call and no card", async ({ page }) => {
    const server = makeServer({ guide: false });
    await stub(page, server);
    await page.goto("/");
    await expect(page.locator('[data-guide="home.app-list"]')).toBeVisible();
    await page.waitForTimeout(1_500);
    expect(server.helloCalls).toHaveLength(0);
    await expect(helloCard(page)).toHaveCount(0);
    await expect(panel(page)).toHaveCount(0);
  });

  test("a deep link gets no hello", async ({ page }) => {
    const server = makeServer();
    await stub(page, server);
    await page.goto("/projects?task=t1");
    await expect(page.locator("aside[data-collapsed]")).toBeVisible();
    await page.waitForTimeout(1_500);
    expect(server.helloCalls).toHaveLength(0);
    await expect(helloCard(page)).toHaveCount(0);
  });

  test("an existing member who has chatted gets no hello", async ({ page }) => {
    const server = makeServer({ chatted: true });
    await stub(page, server);
    await page.goto("/");
    await expect(page.locator('[data-guide="home.app-list"]')).toBeVisible();
    await page.waitForTimeout(1_500);
    expect(server.helloCalls).toHaveLength(0);
    await expect(helloCard(page)).toHaveCount(0);
  });

  test("two tabs at once make one row", async ({ context }) => {
    const server = makeServer();
    const a = await context.newPage();
    const b = await context.newPage();
    await stub(a, server);
    await stub(b, server);
    await Promise.all([a.goto("/"), b.goto("/")]);
    await expect.poll(() => server.helloCalls.length, { timeout: 20_000 }).toBeGreaterThanOrEqual(1);
    await a.waitForTimeout(2_000);
    expect(server.helloCalls.filter((c) => c.status === 200)).toHaveLength(1);
    expect(Object.values(server.rows).flat()).toHaveLength(1);
    // A background tab paints late: look at each in turn.
    let shown = 0;
    for (const tab of [a, b]) {
      await tab.bringToFront();
      await tab.waitForTimeout(500);
      shown += await helloCard(tab).count();
    }
    expect(shown).toBe(1);
  });
});

test.describe("the chips", () => {
  test.use({ viewport: { width: 1280, height: 860 } });

  test("'Show me around' starts the tour on the tap", async ({ page }) => {
    const server = makeServer();
    await stub(page, server);
    await page.goto("/");
    await expectHello(page);
    await expect(card(page)).toHaveCount(0);
    await chip(page, "Show me around").click();
    await expect(card(page)).toContainText("1 of 5");
    // The tap stamped the tour with one key-only write.
    await expect.poll(() => server.puts.filter((p) => "tour_seen_at" in p).length).toBe(1);
  });

  test("'What can you do here?' goes out as the member's own message", async ({ page }) => {
    const server = makeServer();
    await stub(page, server);
    await page.goto("/");
    await expectHello(page);
    await chip(page, "What can you do here?").click();
    await expect(panel(page).getByText("What can you do here?", { exact: true }).first()).toBeVisible();
    await expect(card(page)).toHaveCount(0);
  });

  test("'I'll look around' closes the panel and starts nothing", async ({ page }) => {
    const server = makeServer();
    await stub(page, server);
    await page.goto("/");
    await expectHello(page);
    await chip(page, "I'll look around").click();
    await expect(panel(page)).toBeHidden();
    await expect(card(page)).toHaveCount(0);
    // The nudge stays away too: the hello took its place.
    await page.waitForTimeout(600);
    await expect(page.locator("[data-chat-nudge]")).toHaveCount(0);
  });
});

test.describe("the visual review rig", () => {
  test.skip(!process.env.HELLO_SHOTS, "set HELLO_SHOTS=<dir> to capture");
  test.use({ viewport: { width: Number(process.env.HELLO_WIDTH ?? 1440), height: Number(process.env.HELLO_HEIGHT ?? 900) } });

  test("capture the hello", async ({ page }) => {
    const server = makeServer();
    await stub(page, server);
    await page.goto("/");
    await expectHello(page);
    await page.waitForTimeout(900);
    await shot(page, process.env.HELLO_NAME ?? "hello");
  });
});
