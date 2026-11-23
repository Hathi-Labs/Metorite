import { expect, test, type Page } from "@playwright/test";

/**
 * One Escape closes ONE thing (task 2299).
 *
 * Owner report: open the Status or the Type list in the task panel, press
 * Escape, and the list closes AND the whole panel closes. Two handlers closed
 * the panel: its own `onKeyDown` (`panelEscape`) and the page's `window`
 * listener. `claimEscape` in `SelectButton.tsx` now stops the key at the open
 * list. These tests ask a real browser how far that stop reaches, which a
 * node test cannot: React runs a key from the main tree at `document`, a key
 * from a portal at the portal node, and Base UI's dialog listens on
 * `document`.
 *
 * Stubs as `projects-focus-ring.spec.ts`: every `/api/**` call answers from
 * the table below, and no gateway runs.
 *
 *     npx playwright test e2e/projects-select-escape.spec.ts --project=chromium
 */

const ME = "you@example.com";

const TASK = {
  id: "t2",
  project_id: "p2",
  root_project_id: "p1",
  task_number: 2,
  status_id: "s2",
  title: "Per-person credit limits",
  description: "",
  tags: [],
  assignees: [],
  custom_fields: {},
  subtasks: { done: 0, total: 0 },
  blocked_by_count: 0,
};

const STATUSES = [
  { id: "s2", project_id: "p2", name: "To do", color: "blue", position: 20, category: "todo", is_default: true },
  { id: "s3", project_id: "p2", name: "Doing", color: "green", position: 30, category: "in_progress", is_default: false },
];

const TYPES = [{ id: "ty1", project_id: null, name: "Bug", icon: "Bug", is_default: false }];

/** One registered tag, so an empty tag field shows a list. */
const TAGS = [{ id: "tg1", project_id: null, name: "release", color: "green", task_count: 0 }];

const TREE = [{ id: "p1", name: "Hathi Labs", parent_project_id: null, kind: "project", children: [
  { id: "p2", name: "Metorite", parent_project_id: "p1", kind: "project", children: [] },
] }];

/** A live agent connection with the write scope, so "Ask an agent" opens its dialog. */
const CONNECTION = {
  id: "c1",
  member_email: ME,
  client_name: "Claude Code",
  client_origin: "https://claude.ai",
  scopes: ["projects:read", "projects:write"],
  pinned_projects: [],
  include_personal: false,
  created_at: "2026-10-10T09:00:00Z",
  last_used_at: null,
  expires_at: "2999-01-01T00:00:00Z",
};

const EMPTY = { rows: [], items: [], total: 0, links: [], subtasks: [], children: [], buckets: [] };

const FIXTURES: Record<string, unknown> = {
  "auth/me": { email: ME, name: "You", features: ["projects", "tasks"], is_admin: false },
  "projects/tree": { rows: TREE, total: 1 },
  "nodes/p2/statuses": { rows: STATUSES, total: STATUSES.length },
  "nodes/p2/types": { rows: TYPES, total: TYPES.length },
  "nodes/p1/tags": { rows: TAGS, total: TAGS.length },
  "nodes/p2/tags": { rows: TAGS, total: TAGS.length },
  "projects/tasks/t2/agent-runs": { rows: [] },
  "agent-access/connections": { connections: [CONNECTION] },
  "projects/tasks/t2": TASK,
  "projects/tasks": { rows: [TASK], total: 1 },
};

async function openPanel(page: Page) {
  await page.setViewportSize({ width: 1440, height: 900 });
  await page.route("**/api/**", async (route) => {
    if (route.request().method() !== "GET") return route.fulfill({ json: EMPTY });
    const path = new URL(route.request().url()).pathname.replace(/^\/api\//, "");
    const hit = Object.keys(FIXTURES)
      .sort((a, b) => b.length - a.length)
      .find((key) => path.endsWith(key) || path.includes(`${key}?`));
    return route.fulfill({ json: hit ? (FIXTURES[hit] as object) : EMPTY });
  });
  // The first test pays the cold compile of `/projects`. On a busy box it took
  // longer than the 60 s `navigationTimeout` of the config (2026-11-22).
  await page.goto("/projects?task=t2", { timeout: 150_000 });
  // The task panel is the `<aside>` that holds the card's tab bar.
  const panel = page.locator("aside").filter({ has: page.locator("[data-record-tab-bar]") });
  await expect(panel).toBeVisible({ timeout: 60_000 });
  return panel;
}

for (const label of ["Status", "Type"]) {
  test(`Escape on the open ${label} list closes the list, and a second Escape closes the panel`, async ({ page }) => {
    test.setTimeout(180_000);
    const panel = await openPanel(page);
    const trigger = panel.getByRole("button", { name: label, exact: true });
    await expect(trigger).toBeEnabled({ timeout: 20_000 });
    await trigger.click();
    const list = page.getByRole("listbox", { name: label });
    await expect(list).toBeVisible();

    await page.keyboard.press("Escape");
    await expect(list).toHaveCount(0);
    await expect(panel).toBeVisible();
    await expect(trigger).toHaveAttribute("aria-expanded", "false");
    await expect(trigger).toBeFocused();

    await page.keyboard.press("Escape");
    await expect(panel).toHaveCount(0);
  });
}

test("Escape with the focus off an open list closes the list, and the panel stays", async ({ page }) => {
  test.setTimeout(180_000);
  // Safari does not focus a clicked button, so the focus can stay off the
  // control. The document listener of `SelectButton` takes the key then.
  const panel = await openPanel(page);
  const trigger = panel.getByRole("button", { name: "Status", exact: true });
  await expect(trigger).toBeEnabled({ timeout: 20_000 });
  await trigger.click();
  const list = page.getByRole("listbox", { name: "Status" });
  await expect(list).toBeVisible();
  await page.evaluate(() => (document.activeElement as HTMLElement | null)?.blur());

  await page.keyboard.press("Escape");
  await expect(list).toHaveCount(0);
  await expect(panel).toBeVisible();
});

test("Escape on an open list in a dialog closes the list, and the dialog stays", async ({ page }) => {
  test.setTimeout(180_000);
  const panel = await openPanel(page);
  const ask = panel.locator("[data-ask-agent]");
  await expect(ask).toBeVisible({ timeout: 20_000 });
  await ask.click();
  const dialog = page.getByRole("dialog");
  const trigger = dialog.getByRole("button", { name: "Agent", exact: true });
  await expect(trigger).toBeVisible({ timeout: 20_000 });
  await trigger.click();
  const list = page.getByRole("listbox", { name: "Agent" });
  await expect(list).toBeVisible();

  await page.keyboard.press("Escape");
  await expect(list).toHaveCount(0);
  await expect(dialog).toBeVisible();
  await expect(trigger).toBeFocused();

  // The second Escape is the dialog's, and the panel under it stays.
  await page.keyboard.press("Escape");
  await expect(dialog).toHaveCount(0);
  await expect(panel).toBeVisible();
});

test("Escape on the open tag list of an empty field closes the list, and the panel stays", async ({ page }) => {
  test.setTimeout(180_000);
  const panel = await openPanel(page);
  const field = panel.getByRole("textbox", { name: "Add a tag" });
  await field.click();
  const tag = page.getByRole("button", { name: /^release/ });
  await expect(tag).toBeVisible({ timeout: 20_000 });

  await page.keyboard.press("Escape");
  await expect(tag).toHaveCount(0);
  await expect(panel).toBeVisible();
  await expect(field).toBeFocused();

  // The list is shut now, so the next Escape is the panel's.
  await page.keyboard.press("Escape");
  await expect(panel).toHaveCount(0);
});

test("Escape on the open tag list keeps the typed text in the field", async ({ page }) => {
  test.setTimeout(180_000);
  const panel = await openPanel(page);
  const field = panel.getByRole("textbox", { name: "Add a tag" });
  await field.click();
  await field.fill("urgent");
  const create = page.getByRole("button", { name: "Create “urgent”" });
  await expect(create).toBeVisible();

  await page.keyboard.press("Escape");
  await expect(create).toHaveCount(0);
  await expect(panel).toBeVisible();
  await expect(field).toBeFocused();
  await expect(field).toHaveValue("urgent");
});
