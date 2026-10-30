import { expect, test, type Page, type Route } from "@playwright/test";

/**
 * WS-BRIEF BR-2c-2, the brief card of My Day (`core_brief.md` §5 item 1).
 *
 * The flags are build-time values, so a dev build lets one browser turn them
 * on for itself through `localStorage`, as `e2e/my-day-feed.spec.ts` does.
 * The gateway is stubbed at the Next route, `/api/brief/today`.
 *
 * Three claims:
 *   1. With a brief, the card shows the lead and the claims in order, each an
 *      in-app link.
 *   2. With a 404, the page draws no brief card and no error.
 *   3. The brief of the day before wears its label.
 *
 * Set `BRIEF_SHOTS=<dir>` to also save screenshots of the card (the visual
 * review of `DESIGN_SYSTEM.md` §11).
 */

const MEMBER = {
  authenticated: true,
  email: "asha@example.com",
  name: "Asha Rao",
  is_active: true,
  is_admin: false,
  features: ["tasks", "projects", "email", "chat", "calendar", "people"],
  permissions: [],
  capabilities: [],
  roles: ["member"],
  organization: { id: "org1", slug: "acme", display_name: "Acme" },
};
const SESSION = { user: { email: "asha@example.com", name: "Asha Rao" }, expires: "2099-01-01T00:00:00.000Z" };
const KNOWN = ["/tasks", "/calendar", "/email", "/projects", "/people", "/chat"];
const LAYOUT = { preset: "engineer", answered: "answered", pins: KNOWN, known: KNOWN, cardOrder: null, newOrder: null };

/** The look under review: set `BRIEF_THEME`, `BRIEF_DENSITY` and `BRIEF_ACCENT` with `BRIEF_SHOTS`. */
const LOOK = {
  theme: process.env.BRIEF_THEME ?? "",
  density: process.env.BRIEF_DENSITY ?? "",
  accent: process.env.BRIEF_ACCENT ?? "",
  ink: process.env.BRIEF_INK ?? "",
};

const ymd = (d: Date) =>
  `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, "0")}-${String(d.getDate()).padStart(2, "0")}`;

const DAYS = ["Sun", "Mon", "Tue", "Wed", "Thu", "Fri", "Sat"];
const MONTHS = ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"];

/** The template lead of the stored brief, built from the date and the claim count. */
const leadFor = (d: Date, n: number) =>
  `Your day, ${DAYS[d.getDay()]} ${d.getDate()} ${MONTHS[d.getMonth()]}. ${n} things need you.`;

const brief = (day: Date) => ({
  kind: "brief",
  local_date: ymd(day),
  state: "ready",
  lead: leadFor(day, 3),
  lead_source: "template",
  ready_at: new Date().toISOString(),
  claims: [
    { position: 2, section: "needs_you", text: "Reply to Dana about the supplier contract", source_app: "email", source_kind: "thread", href: "/email?email=a&account=acc1" },
    { position: 1, section: "needs_you", text: "Approve the Q3 launch plan", source_app: "projects", source_kind: "task", href: "/projects?task=t1" },
    { position: 3, section: "today", text: "Design review at 15:00", source_app: "calendar", source_kind: "event", href: "/calendar" },
  ],
});

async function setup(page: Page, answer: { status: number; body?: unknown }) {
  await page.addInitScript((look) => {
    if (look.theme) localStorage.setItem("theme", look.theme);
    if (look.density) localStorage.setItem("cc-density", look.density);
    if (look.accent) localStorage.setItem("cc-accent", look.accent);
    if (look.ink) localStorage.setItem("cc-accent-ink", look.ink);
    localStorage.setItem("cc-shell-bar", "1");
    localStorage.setItem("cc-shell-nav", "1");
    localStorage.setItem("cc-my-day", "1");
    localStorage.setItem("cc-brief-card", "1");
    localStorage.removeItem("cc-sidebar-collapsed");
  }, LOOK);
  await page.route("**/api/**", async (route: Route) => {
    const path = new URL(route.request().url()).pathname;
    const json = (body: unknown, status = 200) =>
      route.fulfill({ status, contentType: "application/json", body: JSON.stringify(body) });
    if (path === "/api/brief/today") return json(answer.body ?? { detail: "No brief." }, answer.status);
    if (path === "/api/shell/needs")
      return json({ count: 0, total: 0, items: [], sources: { tasks: "ok", projects: "ok", email: "ok" }, totals: {}, capped: [] });
    if (path === "/api/auth/me/shell") return json(LAYOUT);
    if (path === "/api/auth/me") return json(MEMBER);
    if (path === "/api/auth/session") return json(SESSION);
    if (path === "/api/health") return json({ gateway: "up" });
    if (path === "/api/accounts") return json({ enabled: false });
    if (path === "/api/settings/branding") return json({ logo: null, updatedBy: "", updatedAt: "" });
    if (path.startsWith("/api/projects/notifications")) return json({ rows: [], total: 0, unread: { total: 0, mentions: 0 } });
    if (path === "/api/chat/active-sessions") return json([]);
    if (/^\/api\/(notes|chat|agent|apps|agents|email\/accounts)\b/.test(path)) return json([]);
    return json({ rows: [], items: [], total: 0 });
  });
}

const card = (page: Page) => page.getByTestId("brief-card");

test.describe("The My Day brief card (BR-2c-2)", () => {
  test.use({ viewport: { width: Number(process.env.BRIEF_WIDTH ?? 1440), height: 900 } });

  test("shows the lead and the claims in position order, as in-app links", async ({ page }) => {
    await setup(page, { status: 200, body: brief(new Date()) });
    await page.goto("/");
    await expect(card(page).getByRole("heading", { name: leadFor(new Date(), 3) })).toBeVisible();
    const links = card(page).getByRole("link");
    await expect(links).toHaveText([
      "Approve the Q3 launch plan",
      "Reply to Dana about the supplier contract",
      "Design review at 15:00",
    ]);
    await expect(links.first()).toHaveAttribute("href", "/projects?task=t1");
    await expect(card(page)).not.toContainText("Yesterday");
    const dir = process.env.BRIEF_SHOTS;
    if (dir) await page.screenshot({ path: `${dir}/${process.env.BRIEF_SHOT_NAME ?? "card"}.png`, fullPage: true });
  });

  test("draws no card and no error on a 404", async ({ page }) => {
    await setup(page, { status: 404 });
    await page.goto("/");
    await expect(page.getByTestId("my-day")).toBeVisible();
    await expect(page.getByTestId("my-day-summary")).toBeVisible();
    await expect(card(page)).toHaveCount(0);
    // The route announcer is an empty alert, so look for words.
    await expect(page.getByRole("alert").filter({ hasText: /\S/ })).toHaveCount(0);
  });

  test("labels the brief of the day before", async ({ page }) => {
    const y = new Date();
    y.setDate(y.getDate() - 1);
    await setup(page, { status: 200, body: brief(y) });
    await page.goto("/");
    await expect(card(page)).toContainText(/Yesterday's brief, \d{1,2} \w{3}/);
    await expect(card(page).getByRole("heading", { name: leadFor(y, 3) })).toBeVisible();
    const dir = process.env.BRIEF_SHOTS;
    if (dir && process.env.BRIEF_YESTERDAY_SHOT) await page.screenshot({ path: `${dir}/${process.env.BRIEF_YESTERDAY_SHOT}.png`, fullPage: true });
  });
});
