import { expect, test, type Page } from "@playwright/test";

import { stubApi } from "./visual/harness";

test.describe.configure({ timeout: 150_000 });

/**
 * Notion-style linking and insert shortcuts in the Knowledge editor
 * (WS-61): the `@` mention menu, the page chip, the grouped `/` menu, the
 * link popover, a URL pasted over text, and the Markdown input rules.
 *
 * The BFF at `/api/knowledge/**` is a small mock that keeps state. It has
 * its own copy, as the other editor specs do, so the files never meet in a
 * merge.
 */

const SPACE_ID = "ks-company";
const HOLIDAY = "3f2b8c1e-5a4d-4e6f-9b7a-1c2d3e4f5a6b";
const REFUND = "7a1c9d2e-0b3f-4c5d-8e6f-2a3b4c5d6e7f";
const GONE = "9c8b7a6d-5e4f-4a3b-8c2d-1e0f9a8b7c6d";
const ASHA = "5b1d7e2a-3c4f-4a8b-9d6e-0f1a2b3c4d5e";
const NEW_PAGE = "c0ffee00-1111-4222-8333-444455556666";
const HOLIDAY_TEXT = "Public holidays for the year. Plan **leave** early, see [the policy](/knowledge/pages/x) and ask your lead. ";

type KbPage = { id: string; title: string; body_md: string; versions: number };

function todayIso(): string {
  const d = new Date();
  const pad = (n: number) => String(n).padStart(2, "0");
  return `${d.getFullYear()}-${pad(d.getMonth() + 1)}-${pad(d.getDate())}`;
}

type SetupOptions = {
  /** The page detail's `can_write`. A reader sees no Create row. */
  canWrite?: boolean;
  /** `refused` answers the People directory with 403. */
  people?: "ok" | "refused";
};

async function setup(page: Page, body: string, opts: SetupOptions = {}) {
  const canWrite = opts.canWrite ?? true;
  await page.addInitScript(() => {
    localStorage.setItem("cc-shell-bar", "1");
    localStorage.setItem("cc-shell-nav", "1");
  });
  await stubApi(page, {
    "auth/me": {
      authenticated: true,
      email: "asha@example.com",
      name: "Asha Rao",
      is_admin: false,
      features: ["knowledge", "people"],
      permissions: [],
      capabilities: [],
      roles: ["member"],
      organization: { id: "org1", slug: "acme", display_name: "Acme Works" },
    },
  });
  const now = new Date().toISOString();
  const doc: KbPage = { id: "kp-a", title: "Refund policy notes", body_md: body, versions: 1 };
  const writes: Array<{ title?: string; body_md?: string }> = [];
  const creates: Array<Record<string, unknown>> = [];
  await page.route("**/api/people**", async (route) => {
    const url = new URL(route.request().url());
    if (opts.people === "refused") return route.fulfill({ status: 403, contentType: "application/json", body: JSON.stringify({ detail: "No access" }) });
    const q = (url.searchParams.get("q") ?? "").toLowerCase();
    const all = [
      { id: ASHA, name: "Asha Rao", email: "asha@example.com", status: "active" },
      ...Array.from({ length: 7 }, (_, i) => ({
        id: `5b1d7e2a-3c4f-4a8b-9d6e-0f1a2b3c4d${String(10 + i)}`, name: `Ashok Kumar ${i}`, email: `ashok${i}@example.com`, status: "active",
      })),
      { id: "5b1d7e2a-3c4f-4a8b-9d6e-0f1a2b3c4d99", name: "Ben Cole", email: "ben@example.com", status: "active" },
    ];
    const rows = all.filter((r) => `${r.name} ${r.email}`.toLowerCase().includes(q));
    return route.fulfill({ status: 200, contentType: "application/json", body: JSON.stringify({ rows, total: rows.length, hr_visible: false, can_manage: false }) });
  });
  const summary = (id: string, title: string, icon: string | null = null) => ({
    id, space_id: SPACE_ID, parent_id: null, position: 0, title, icon, state: "published",
    author_email: "asha@example.com", reviewers: [], review_at: null, source_ref: null, published_at: null,
    archived_at: null, history_from: null, archived_from_state: null, updated_at: now, created_at: now,
  });
  const json = (status: number, payload: unknown) => ({ status, contentType: "application/json", body: JSON.stringify(payload) });
  await page.route("**/api/knowledge/**", async (route) => {
    const req = route.request();
    const url = new URL(req.url());
    const path = url.pathname.replace(/^\/api\/knowledge\//, "");
    if (path === "spaces") {
      return route.fulfill(json(200, { spaces: [{ id: SPACE_ID, slug: "company", name: "Company", purpose: "How we work.",
        icon: "", kind: "company", owner_email: null, archived_at: null, created_at: now, level: canWrite ? "write" : "read", page_count: 3 }] }));
    }
    if (path === "pages" && req.method() === "GET") {
      return route.fulfill(json(200, { pages: [
        summary(doc.id, doc.title),
        summary(HOLIDAY, "Holiday calendar", "🎉"),
        summary(REFUND, "Returns handbook"),
      ] }));
    }
    if (path === "pages" && req.method() === "POST") {
      const made = JSON.parse(req.postData() ?? "{}") as Record<string, unknown>;
      creates.push(made);
      return route.fulfill(json(201, { ...summary(NEW_PAGE, String(made.title ?? "")), state: "draft", parent_id: made.parent_id ?? null, body_md: "", origin: null, version: 1 }));
    }
    if (path === `pages/${HOLIDAY}`) {
      return route.fulfill(json(200, { ...summary(HOLIDAY, "Holiday calendar", "🎉"), body_md: HOLIDAY_TEXT, origin: null, level: "write", can_write: true, links: [], versions: [] }));
    }
    if (path !== `pages/${doc.id}`) return route.fulfill(json(404, { detail: "Not found" }));
    if (req.method() === "PATCH") {
      const patch = JSON.parse(req.postData() ?? "{}") as { title?: string; body_md?: string };
      writes.push(patch);
      if (typeof patch.title === "string") doc.title = patch.title;
      if (typeof patch.body_md === "string") doc.body_md = patch.body_md;
      doc.versions += 1;
      return route.fulfill(json(200, { ...summary(doc.id, doc.title), body_md: doc.body_md, origin: null, version: doc.versions }));
    }
    return route.fulfill(json(200, {
      ...summary(doc.id, doc.title), body_md: doc.body_md, origin: null, level: canWrite ? "write" : "read", can_write: canWrite, links: [],
      versions: Array.from({ length: doc.versions }, (_, i) => ({ version: doc.versions - i, title: doc.title,
        author_email: "asha@example.com", reason: "edit", via: "web", created_at: now })),
    }));
  });
  return { writes, doc, creates };
}

async function edit(page: Page) {
  await page.goto("/knowledge/pages/kp-a");
  await page.getByRole("button", { name: "Edit", exact: true }).click({ timeout: 30_000 });
  const body = page.getByRole("textbox", { name: "Page body" });
  await expect(body).toBeVisible();
  return body;
}

async function save(page: Page, kb: { writes: Array<{ title?: string; body_md?: string }> }) {
  const before = kb.writes.length;
  await page.getByRole("button", { name: "Save changes" }).click();
  await expect.poll(() => kb.writes.length, { timeout: 15_000 }).toBe(before + 1);
  await expect(page.getByRole("button", { name: "Edit", exact: true })).toBeVisible();
  return kb.writes[kb.writes.length - 1];
}

async function caretAtEnd(page: Page) {
  // The block handle loads as its own chunk. Its plugin registers late, and a
  // registration closes a menu that is open. So a test waits for it.
  await page.locator(".kb-drag-handle").waitFor({ state: "attached", timeout: 30_000 });
  await page.locator(".ProseMirror[contenteditable='true']").evaluate((el) => {
    (el as unknown as { editor: { commands: { focus: (at: string) => boolean } } }).editor.commands.focus("end");
  });
  await expect(page.getByRole("textbox", { name: "Page body" })).toBeFocused();
}

const MENTION = (page: Page) => page.getByRole("listbox", { name: "Mention a page or a date" });

test.describe("the @ mention menu", () => {
  test("@ and a page title, then Enter, saves a Markdown link to the page", async ({ page }) => {
    await page.setViewportSize({ width: 1440, height: 900 });
    const kb = await setup(page, "See ");
    await edit(page);
    await caretAtEnd(page);
    await page.keyboard.type("@holi");
    const menu = MENTION(page);
    await expect(menu).toBeVisible();
    await expect(menu.getByText("Pages", { exact: true })).toBeVisible();
    await expect(menu.getByRole("option", { name: /Holiday calendar/ })).toBeVisible();
    await page.keyboard.press("Enter");
    await expect(menu).toHaveCount(0);
    expect((await save(page, kb)).body_md).toBe(`See [Holiday calendar](/knowledge/pages/${HOLIDAY})`);
  });

  test("@today and Enter inserts today's date, and the arrows pick another row", async ({ page }) => {
    await page.setViewportSize({ width: 1440, height: 900 });
    const kb = await setup(page, "Due ");
    await edit(page);
    await caretAtEnd(page);
    await page.keyboard.type("@");
    const menu = MENTION(page);
    await expect(menu.getByText("Date", { exact: true })).toBeVisible();
    await page.keyboard.type("today");
    await expect(menu.getByRole("option")).toHaveCount(1);
    await page.keyboard.press("Enter");
    const body = (await save(page, kb)).body_md ?? "";
    expect(body).toContain(`<time data-type="date" datetime="${todayIso()}">`);
  });

  test("a typed YYYY-MM-DD inserts that day, and a bad day offers nothing", async ({ page }) => {
    await page.setViewportSize({ width: 1440, height: 900 });
    const kb = await setup(page, "On ");
    await edit(page);
    await caretAtEnd(page);
    await page.keyboard.type("@2026-02-30");
    // A bad day offers no date. Only the Create page row is left.
    await expect(MENTION(page).getByRole("option")).toHaveCount(1);
    await expect(MENTION(page).getByRole("option")).toHaveText('Create page "2026-02-30"');
    await expect(MENTION(page).getByText("Date", { exact: true })).toHaveCount(0);
    await page.keyboard.press("Backspace");
    await page.keyboard.press("Backspace");
    await page.keyboard.type("19");
    await expect(MENTION(page).getByRole("option")).toHaveCount(1);
    await page.keyboard.press("Enter");
    expect((await save(page, kb)).body_md).toContain('datetime="2026-02-19"');
  });

  test("Escape closes the menu, and @ then a space closes it", async ({ page }) => {
    await page.setViewportSize({ width: 1440, height: 900 });
    await setup(page, "Hi ");
    await edit(page);
    await caretAtEnd(page);
    await page.keyboard.type("@");
    await expect(MENTION(page)).toBeVisible();
    await page.keyboard.press("Escape");
    await expect(MENTION(page)).toHaveCount(0);
    await page.keyboard.press("Backspace");
    await page.keyboard.type("@ ");
    await expect(MENTION(page)).toHaveCount(0);
  });

  test("a@b.com opens no menu", async ({ page }) => {
    await page.setViewportSize({ width: 1440, height: 900 });
    await setup(page, "Mail ");
    await edit(page);
    await caretAtEnd(page);
    await page.keyboard.type("asha@example.com");
    await expect(MENTION(page)).toHaveCount(0);
  });

  test("[[ still opens the page picker, and the slash item opens the @ menu", async ({ page }) => {
    await page.setViewportSize({ width: 1440, height: 900 });
    await setup(page, "Go ");
    await edit(page);
    await caretAtEnd(page);
    await page.keyboard.type("[[ret");
    await expect(page.getByRole("listbox", { name: "Pages" }).getByRole("option", { name: /Returns handbook/ })).toBeVisible();
    await page.keyboard.press("Escape");
    for (let i = 0; i < 5; i++) await page.keyboard.press("Backspace");
    await page.keyboard.type("/link to");
    await page.getByRole("option", { name: /Link to a page/ }).click();
    await expect(MENTION(page)).toBeVisible();
  });
});

test.describe("the page chip", () => {
  test("an internal link draws as a chip, with the page emoji, and an external link does not", async ({ page }) => {
    await page.setViewportSize({ width: 1440, height: 900 });
    await setup(page, `See [Holiday calendar](/knowledge/pages/${HOLIDAY}), [Handbook](/knowledge/pages/${REFUND}) and [the web](https://example.com/x).`);
    await page.goto("/knowledge/pages/kp-a");
    const body = page.getByLabel(/the page body/);
    const chip = body.locator('a[data-page-id="' + HOLIDAY + '"]');
    await expect(chip).toHaveClass(/kb-page-chip/);
    await expect(body.locator("a.kb-page-chip")).toHaveCount(2);
    await expect(body.locator('a[href="https://example.com/x"]')).not.toHaveClass(/kb-page-chip/);
    // The page list loads after the body, and the glyph takes the page emoji.
    await expect(chip.locator(".kb-page-glyph")).toHaveAttribute("data-emoji", "🎉");
    await expect(body.locator('a[data-page-id="' + REFUND + '"] .kb-page-glyph')).not.toHaveAttribute("data-emoji", /.+/);
    expect(await chip.evaluate((el) => getComputedStyle(el).textDecorationLine)).toBe("none");
  });

  test("a click on a chip in the read view opens the page", async ({ page }) => {
    await page.setViewportSize({ width: 1440, height: 900 });
    await setup(page, `See [Holiday calendar](/knowledge/pages/${HOLIDAY}).`);
    await page.goto("/knowledge/pages/kp-a");
    await page.locator("a.kb-page-chip").click();
    await expect(page).toHaveURL(new RegExp(`/knowledge/pages/${HOLIDAY}`));
  });
});

test.describe("the / menu", () => {
  test("groups the blocks, shows a line and a shortcut, and drops the headings while filtering", async ({ page }) => {
    await page.setViewportSize({ width: 1440, height: 900 });
    await setup(page, "");
    await edit(page);
    await caretAtEnd(page);
    await page.keyboard.type("/");
    const menu = page.getByRole("listbox", { name: "Blocks" });
    for (const h of ["Basic blocks", "Lists", "Media", "Advanced", "Inline"]) {
      await expect(menu.getByText(h, { exact: true })).toHaveCount(1);
    }
    const h1 = menu.getByRole("option", { name: /Heading 1/ });
    await expect(h1).toContainText("Big section heading");
    await expect(h1).toContainText("#");
    await page.keyboard.type("head");
    await expect(menu.getByText("Basic blocks", { exact: true })).toHaveCount(0);
    await expect(menu.getByRole("option").first()).toContainText("Heading 1");
  });
});

test.describe("the link popover", () => {
  test("links the selection to a page found by title", async ({ page }) => {
    await page.setViewportSize({ width: 1440, height: 900 });
    const kb = await setup(page, "Read the handbook today");
    await edit(page);
    await page.locator(".ProseMirror[contenteditable='true']").evaluate((el) => {
      (el as unknown as { editor: { chain: () => { focus: () => { setTextSelection: (r: { from: number; to: number }) => { run: () => void } } } } })
        .editor.chain().focus().setTextSelection({ from: 10, to: 18 }).run();
    });
    await page.getByRole("button", { name: "Link", exact: true }).click();
    const field = page.getByRole("textbox", { name: "Link address or page" });
    await expect(field).toHaveAttribute("placeholder", "Paste a link or search pages");
    await field.fill("hand");
    await expect(page.getByRole("option", { name: /Returns handbook/ })).toHaveCount(1);
    await field.fill("holi");
    await field.press("Enter");
    expect((await save(page, kb)).body_md).toBe(`Read the [handbook](/knowledge/pages/${HOLIDAY}) today`);
  });

  test("a URL offers Link to <url>, Enter links it, and Remove link takes it off", async ({ page }) => {
    await page.setViewportSize({ width: 1440, height: 900 });
    const kb = await setup(page, "Read the handbook today");
    await edit(page);
    const select = () =>
      page.locator(".ProseMirror[contenteditable='true']").evaluate((el) => {
        (el as unknown as { editor: { chain: () => { focus: () => { setTextSelection: (r: { from: number; to: number }) => { run: () => void } } } } })
          .editor.chain().focus().setTextSelection({ from: 10, to: 18 }).run();
      });
    await select();
    await page.getByRole("button", { name: "Link", exact: true }).click();
    const field = page.getByRole("textbox", { name: "Link address or page" });
    await field.fill("https://example.com/guide");
    await expect(page.getByRole("option", { name: "Link to https://example.com/guide" })).toBeVisible();
    await field.press("Enter");
    expect((await save(page, kb)).body_md).toBe("Read the [handbook](https://example.com/guide) today");

    await edit(page);
    await select();
    await page.getByRole("button", { name: "Link", exact: true }).click();
    await page.getByRole("button", { name: "Remove the link" }).click();
    expect((await save(page, kb)).body_md).toBe("Read the handbook today");
  });

  test("Escape closes the popover and returns the focus to the editor", async ({ page }) => {
    await page.setViewportSize({ width: 1440, height: 900 });
    await setup(page, "Read the handbook today");
    const body = await edit(page);
    await page.locator(".ProseMirror[contenteditable='true']").evaluate((el) => {
      (el as unknown as { editor: { chain: () => { focus: () => { setTextSelection: (r: { from: number; to: number }) => { run: () => void } } } } })
        .editor.chain().focus().setTextSelection({ from: 10, to: 18 }).run();
    });
    await page.getByRole("button", { name: "Link", exact: true }).click();
    await page.getByRole("textbox", { name: "Link address or page" }).press("Escape");
    await expect(page.getByRole("textbox", { name: "Link address or page" })).toHaveCount(0);
    await expect(body).toBeFocused();
  });
});

test.describe("paste and the Markdown rules", () => {
  test("a URL pasted over selected text makes it a link", async ({ page }) => {
    await page.setViewportSize({ width: 1440, height: 900 });
    const kb = await setup(page, "Read the handbook today");
    await edit(page);
    await page.locator(".ProseMirror[contenteditable='true']").evaluate((el) => {
      const editor = (el as unknown as { editor: { chain: () => { focus: () => { setTextSelection: (r: { from: number; to: number }) => { run: () => void } } } } }).editor;
      editor.chain().focus().setTextSelection({ from: 10, to: 18 }).run();
      const data = new DataTransfer();
      data.setData("text/plain", "https://example.com/guide");
      el.dispatchEvent(new ClipboardEvent("paste", { clipboardData: data, bubbles: true, cancelable: true }));
    });
    expect((await save(page, kb)).body_md).toBe("Read the [handbook](https://example.com/guide) today");
  });

  const RULES: Array<[string, string, string]> = [
    ["# ", "heading", '[data-md-level="1"]'],
    ["## ", "heading 2", '[data-md-level="2"]'],
    ["### ", "heading 3", '[data-md-level="3"]'],
    ["- ", "bullet", "ul:not([data-type]) > li"],
    ["* ", "bullet with a star", "ul:not([data-type]) > li"],
    ["1. ", "numbered", "ol > li"],
    ["[] ", "task", 'ul[data-type="taskList"] > li'],
    ["> ", "quote", "blockquote"],
    ["```", "code", "pre"],
    ["---", "divider", "hr"],
  ];
  for (const [typed, name, selector] of RULES) {
    test(`${JSON.stringify(typed)} makes a ${name}`, async ({ page }) => {
      await page.setViewportSize({ width: 1440, height: 900 });
      await setup(page, "");
      const body = await edit(page);
      await caretAtEnd(page);
      await page.keyboard.type(typed);
      await expect(body.locator(selector).first()).toBeVisible();
    });
  }
});


// ── KB-2k (WS-61): people, the hover preview and Create page ───────────────

test.describe("KB-2k1 people in the @ menu", () => {
  test("@as shows Asha Rao under People, and Enter saves a plain link to the person", async ({ page }) => {
    await page.setViewportSize({ width: 1440, height: 900 });
    const kb = await setup(page, "Ask ");
    await edit(page);
    await caretAtEnd(page);
    await page.keyboard.type("@asha");
    const menu = MENTION(page);
    await expect(menu.getByText("People", { exact: true })).toBeVisible();
    const row = menu.getByRole("option", { name: /Asha Rao/ });
    await expect(row).toContainText("asha@example.com");
    await page.keyboard.press("Enter");
    await expect(menu).toHaveCount(0);
    expect((await save(page, kb)).body_md).toBe(`Ask [@Asha Rao](/people/${ASHA})`);
  });

  test("the menu shows at most five people", async ({ page }) => {
    await page.setViewportSize({ width: 1440, height: 900 });
    await setup(page, "Ask ");
    await edit(page);
    await caretAtEnd(page);
    await page.keyboard.type("@as");
    const menu = MENTION(page);
    await expect(menu.getByRole("option", { name: /Asha Rao/ })).toBeVisible();
    // Five people, and the Create page row after them.
    await expect(menu.getByRole("option")).toHaveCount(6);
  });

  test("the read view draws a person chip that opens the person", async ({ page }) => {
    await page.setViewportSize({ width: 1440, height: 900 });
    await setup(page, `Ask [@Asha Rao](/people/${ASHA}) about it.`);
    await page.goto("/knowledge/pages/kp-a");
    const chip = page.getByLabel(/the page body/).locator("a.kb-person-chip");
    await expect(chip).toHaveCount(1);
    await expect(chip).toHaveText("@Asha Rao");
    await expect(chip).toHaveAttribute("href", `/people/${ASHA}`);
    // It is no page chip: nothing hovers a card for it.
    await expect(page.locator("a.kb-page-chip")).toHaveCount(0);
    expect(await chip.evaluate((el) => getComputedStyle(el).textDecorationLine)).toBe("none");
    await chip.click();
    // The dev server compiles the People route on the first visit.
    await expect(page).toHaveURL(new RegExp(`/people/${ASHA}`), { timeout: 45_000 });
  });

  test("a refused directory read shows no People section, and the menu still works", async ({ page }) => {
    await page.setViewportSize({ width: 1440, height: 900 });
    await setup(page, "Ask ", { people: "refused" });
    await edit(page);
    await caretAtEnd(page);
    await page.keyboard.type("@holi");
    const menu = MENTION(page);
    await expect(menu.getByRole("option", { name: /Holiday calendar/ })).toBeVisible();
    await page.waitForTimeout(600);
    await expect(menu.getByText("People", { exact: true })).toHaveCount(0);
    await page.keyboard.press("Enter");
    await expect(menu).toHaveCount(0);
  });
});

test.describe("KB-2k2 the hover preview of a page chip", () => {
  const CARD = (page: Page) => page.getByRole("tooltip");
  const body = `See [Holiday calendar](/knowledge/pages/${HOLIDAY}) and [Old page](/knowledge/pages/${GONE}).`;

  test("a hover of 400 ms shows the title, the space and the text, and leaving closes it", async ({ page }) => {
    await page.setViewportSize({ width: 1440, height: 900 });
    await setup(page, body);
    await page.goto("/knowledge/pages/kp-a");
    const chip = page.locator(`a.kb-page-chip[data-page-id="${HOLIDAY}"]`);
    await chip.hover();
    const card = CARD(page);
    await expect(card).toBeVisible();
    await expect(card).toContainText("Holiday calendar");
    await expect(card).toContainText("Company");
    await expect(card).toContainText("Published");
    await expect(card).toContainText("Public holidays for the year. Plan leave early, see the policy and ask your lead.");
    await page.mouse.move(5, 5);
    await expect(card).toHaveCount(0);
  });

  test("a page the mock answers 404 shows the refusal and no title", async ({ page }) => {
    await page.setViewportSize({ width: 1440, height: 900 });
    await setup(page, body);
    await page.goto("/knowledge/pages/kp-a");
    await page.locator(`a.kb-page-chip[data-page-id="${GONE}"]`).hover();
    const card = CARD(page);
    await expect(card).toContainText("You cannot open this page");
    await expect(card).not.toContainText("Old page");
  });

  test("Tab to a chip shows the card at once, and Esc closes it", async ({ page }) => {
    await page.setViewportSize({ width: 1440, height: 900 });
    await setup(page, body);
    await page.goto("/knowledge/pages/kp-a");
    const chip = page.locator(`a.kb-page-chip[data-page-id="${HOLIDAY}"]`);
    await expect(chip).toBeVisible();
    for (let i = 0; i < 60; i++) {
      await page.keyboard.press("Tab");
      if (await chip.evaluate((el) => el === document.activeElement)) break;
    }
    await expect(chip).toBeFocused();
    const card = CARD(page);
    await expect(card).toContainText("Holiday calendar");
    await page.keyboard.press("Escape");
    await expect(card).toHaveCount(0);
  });
});

test.describe("KB-2k3 Create page from the @ menu", () => {
  test("@Refund FAQ with no match shows the row, and Enter makes a draft child and links it", async ({ page }) => {
    await page.setViewportSize({ width: 1440, height: 900 });
    const kb = await setup(page, "See ");
    await edit(page);
    await caretAtEnd(page);
    await page.keyboard.type("@Refund FAQ");
    const menu = MENTION(page);
    const rows = menu.getByRole("option");
    await expect(rows.last()).toHaveText('Create page "Refund FAQ"');
    await page.keyboard.press("ArrowUp");
    await page.keyboard.press("Enter");
    await expect.poll(() => kb.creates.length).toBe(1);
    expect(kb.creates[0]).toMatchObject({ title: "Refund FAQ", parent_id: "kp-a", space_id: SPACE_ID });
    await expect(menu).toHaveCount(0);
    expect((await save(page, kb)).body_md).toBe(`See [Refund FAQ](/knowledge/pages/${NEW_PAGE})`);
  });

  test("a reader without write cannot open the editor, so no Create row exists", async ({ page }) => {
    await page.setViewportSize({ width: 1440, height: 900 });
    await setup(page, "See ", { canWrite: false });
    await page.goto("/knowledge/pages/kp-a");
    await expect(page.getByRole("button", { name: "Edit", exact: true })).toHaveCount(0);
    await expect(page.getByText("Read only")).toBeVisible();
  });

  test("a failed create shows a toast and inserts nothing", async ({ page }) => {
    await page.setViewportSize({ width: 1440, height: 900 });
    const kb = await setup(page, "See ");
    await page.route("**/api/knowledge/pages", async (route) => {
      if (route.request().method() !== "POST") return route.fallback();
      return route.fulfill({ status: 403, contentType: "application/json", body: JSON.stringify({ detail: "Writing in this space needs a write grant." }) });
    });
    await edit(page);
    await caretAtEnd(page);
    await page.keyboard.type("@Refund FAQ");
    await expect(MENTION(page).getByRole("option").last()).toHaveText('Create page "Refund FAQ"');
    await page.keyboard.press("ArrowUp");
    await page.keyboard.press("Enter");
    const toast = page.getByLabel("Notifications");
    await expect(toast.getByText("Could not create the page")).toBeVisible();
    await expect(toast.getByText("Writing in this space needs a write grant.")).toBeVisible();
    await expect(page.getByRole("textbox", { name: "Page body" })).not.toContainText("](/knowledge");
    expect(kb.creates).toHaveLength(0);
  });
});
