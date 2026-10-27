import { expect, test, type Page } from "@playwright/test";

import { stubApi } from "./visual/harness";

// Each test opens the editor more than once, and the dev server compiles on
// the first request. So a test gets more time than the default 45 s.
test.describe.configure({ timeout: 150_000 });

/**
 * The Knowledge editor's first Docmost set (WS-61 KB-2i,
 * `knowledge_base.md` §21.7): the table grips and their menus, footnotes,
 * find and replace on Mod-F, the `:` emoji insert, and the title flow.
 *
 * The BFF at `/api/knowledge/**` is a small mock that keeps state, so each
 * test edits a page, saves it and reads the body that reached the BFF. The
 * full mock of the KB-2a flow lives in `knowledge-pages.spec.ts`. This file
 * keeps its own, so the two files do not meet in a merge.
 */

const SPACE_ID = "ks-company";

type KbPage = { id: string; title: string; body_md: string; versions: number };

async function setup(page: Page, body: string) {
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
      features: ["knowledge"],
      permissions: [],
      capabilities: [],
      roles: ["member"],
      organization: { id: "org1", slug: "acme", display_name: "Acme Works" },
    },
  });
  const now = new Date().toISOString();
  const doc: KbPage = { id: "kp-a", title: "Refund policy", body_md: body, versions: 1 };
  const writes: Array<{ title?: string; body_md?: string }> = [];
  const summary = () => ({
    id: doc.id, space_id: SPACE_ID, parent_id: null, position: 0, title: doc.title, state: "published",
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
        icon: "", kind: "company", owner_email: null, archived_at: null, created_at: now, level: "write", page_count: 1 }] }));
    }
    if (path === "pages" && req.method() === "GET") return route.fulfill(json(200, { pages: [summary()] }));
    if (path !== `pages/${doc.id}`) return route.fulfill(json(404, { detail: "Not found" }));
    if (req.method() === "PATCH") {
      const patch = JSON.parse(req.postData() ?? "{}") as { title?: string; body_md?: string };
      writes.push(patch);
      if (typeof patch.title === "string") doc.title = patch.title;
      if (typeof patch.body_md === "string") doc.body_md = patch.body_md;
      doc.versions += 1;
      return route.fulfill(json(200, { ...summary(), body_md: doc.body_md, origin: null, version: doc.versions }));
    }
    return route.fulfill(json(200, {
      ...summary(), body_md: doc.body_md, origin: null, level: "write", can_write: true, links: [],
      versions: Array.from({ length: doc.versions }, (_, i) => ({ version: doc.versions - i, title: doc.title,
        author_email: "asha@example.com", reason: "edit", via: "web", created_at: now })),
    }));
  });
  return { writes, doc };
}

/** Open the page in the editor. */
async function edit(page: Page) {
  await page.goto("/knowledge/pages/kp-a");
  await page.getByRole("button", { name: "Edit", exact: true }).click({ timeout: 30_000 });
  const body = page.getByRole("textbox", { name: "Page body" });
  await expect(body).toBeVisible();
  return body;
}

/** Save, and wait for the read view to come back. Returns the PATCH body. */
async function save(page: Page, kb: { writes: Array<{ title?: string; body_md?: string }> }) {
  const before = kb.writes.length;
  await page.getByRole("button", { name: "Save changes" }).click();
  await expect.poll(() => kb.writes.length, { timeout: 15_000 }).toBe(before + 1);
  await expect(page.getByRole("button", { name: "Edit", exact: true })).toBeVisible();
  return kb.writes[kb.writes.length - 1];
}

/** Put the caret at the end of the body through the editor, and wait for focus. */
async function caretAtEnd(page: Page) {
  await page.locator(".ProseMirror[contenteditable='true']").evaluate((el) => {
    (el as unknown as { editor: { commands: { focus: (at: string) => boolean } } }).editor.commands.focus("end");
  });
  await expect(page.getByRole("textbox", { name: "Page body" })).toBeFocused();
}

const TABLE = "| Item | Days |\n| --- | --- |\n| Shoes | 30 |\n| Bags | 14 |";

test.describe("the table grips (KB-2i)", () => {
  test("the column menu aligns, adds and moves a column, and the row menu moves a row", async ({ page }) => {
    await page.setViewportSize({ width: 1440, height: 900 });
    const kb = await setup(page, TABLE);
    const body = await edit(page);

    // The grips follow the cell under the pointer.
    await body.locator("td", { hasText: "30" }).hover();
    const colGrip = page.getByRole("button", { name: "Column 2 actions" });
    await expect(colGrip).toBeVisible();
    await colGrip.click();
    const menu = page.getByRole("menu");
    await menu.getByRole("menuitemcheckbox", { name: "Align right" }).click();
    await expect(menu).toHaveCount(0);
    expect((await save(page, kb)).body_md).toBe("| Item | Days |\n| --- | ---: |\n| Shoes | 30 |\n| Bags | 14 |");

    await edit(page);
    await body.locator("td", { hasText: "Bags" }).hover();
    await page.getByRole("button", { name: "Row 3 actions" }).click();
    await menu.getByRole("menuitem", { name: "Move row up" }).click();
    await body.locator("th", { hasText: "Item" }).hover();
    await page.getByRole("button", { name: "Column 1 actions" }).click();
    await menu.getByRole("menuitem", { name: "Move column right" }).click();
    expect((await save(page, kb)).body_md).toBe("| Days | Item |\n| ---: | --- |\n| 14 | Bags |\n| 30 | Shoes |");

    await edit(page);
    await body.locator("td", { hasText: "Bags" }).hover();
    await page.getByRole("button", { name: "Column 2 actions" }).click();
    await menu.getByRole("menuitem", { name: "Add column left" }).click();
    await body.locator("td", { hasText: "Shoes" }).hover();
    await page.getByRole("button", { name: "Row 3 actions" }).click();
    await menu.getByRole("menuitem", { name: "Delete row" }).click();
    expect((await save(page, kb)).body_md).toBe("| Days |  | Item |\n| ---: | --- | --- |\n| 14 |  | Bags |");
  });

  test("the header row turns off and stays off after a save", async ({ page }) => {
    await page.setViewportSize({ width: 1440, height: 900 });
    const kb = await setup(page, TABLE);
    const body = await edit(page);
    await body.locator("th", { hasText: "Item" }).hover();
    await page.getByRole("button", { name: "Row 1 actions" }).click();
    await page.getByRole("menu").getByRole("menuitemcheckbox", { name: "Header row" }).click();
    await expect(body.locator("th")).toHaveCount(0);
    expect((await save(page, kb)).body_md).toBe("|  |  |\n| --- | --- |\n| Item | Days |\n| Shoes | 30 |\n| Bags | 14 |");
    // The read view shows no header, and an edit with no change writes nothing.
    await expect(page.locator("article th")).toHaveCount(0);
    await edit(page);
    // After the editor's first write (300 ms), the draft is still clean.
    await page.waitForTimeout(600);
    await expect(page.getByRole("button", { name: "Save changes" })).toBeDisabled();
  });

  test("a drag of a grip moves the column and the row", async ({ page }) => {
    await page.setViewportSize({ width: 1440, height: 900 });
    const kb = await setup(page, TABLE);
    const body = await edit(page);

    const drag = async (gripName: string, from: string, to: string) => {
      await body.locator("td, th", { hasText: from }).first().hover();
      const grip = page.getByRole("button", { name: gripName });
      await expect(grip).toBeVisible();
      // Rest on the grip first. The pointer's arrival re-renders the grips,
      // and a press in that same frame lands on the old one.
      await grip.hover();
      await expect(grip).toBeVisible();
      const g = (await grip.boundingBox())!;
      const target = (await body.locator("td, th", { hasText: to }).first().boundingBox())!;
      await page.mouse.move(g.x + g.width / 2, g.y + g.height / 2);
      await page.mouse.down();
      await page.mouse.move(g.x + g.width / 2 + 2, g.y + g.height / 2 + 2, { steps: 2 });
      await page.mouse.move(target.x + target.width / 2, target.y + target.height / 2, { steps: 8 });
      await expect(page.locator(".kb-table-drop-line")).toBeVisible();
      await page.mouse.up();
      await expect(page.locator(".kb-table-drop-line")).toHaveCount(0);
    };

    await drag("Column 1 actions", "Item", "Days");
    await expect(body.locator("th").first()).toHaveText("Days");
    await drag("Row 2 actions", "Shoes", "Bags");
    await expect(body.locator("tr").nth(1)).toContainText("Bags");
    expect((await save(page, kb)).body_md).toBe("| Days | Item |\n| --- | --- |\n| 14 | Bags |\n| 30 | Shoes |");
  });
});

test.describe("footnotes (KB-2i)", () => {
  test("/Footnote adds a mark and its note, and a stored footnote keeps its label", async ({ page }) => {
    await page.setViewportSize({ width: 1440, height: 900 });
    const kb = await setup(page, "Thirty days[^terms].\n\n[^terms]: See the sales terms.");
    // The read view draws the mark as its number, and the note under it.
    await page.goto("/knowledge/pages/kp-a");
    await expect(page.locator("article sup.kb-footnote-ref[data-footnote-ref='terms']")).toHaveAttribute("aria-label", "Footnote 1", { timeout: 30_000 });
    await expect(page.locator("article .kb-footnote[data-footnote='terms']")).toContainText("See the sales terms.");

    await edit(page);
    // The caret at the end of the note. Enter adds a line to it, and Enter on
    // that empty line leaves the note, as it leaves a list. (`focus("end")`
    // can land in the editor's own empty last line, so the test clicks.)
    // The editor draws its node views just after it mounts, so a click in
    // that moment can miss. Click again until the caret is in the note.
    const note = page.locator(".kb-footnote-body p", { hasText: "See the sales terms." });
    await expect(async () => {
      await note.click();
      expect(await page.evaluate(() => window.getSelection()?.anchorNode?.textContent ?? "")).toContain(
        "See the sales terms.",
      );
    }).toPass({ timeout: 10_000 });
    await page.keyboard.press("End");
    await page.keyboard.press("Enter");
    await page.keyboard.press("Enter");
    await page.keyboard.type("Ask first /Footnote");
    const blocks = page.getByRole("listbox", { name: "Blocks" });
    await expect(blocks.getByRole("option").first()).toContainText("Footnote");
    await page.keyboard.press("Enter");
    // The caret moves into the new note, so the next keys write it.
    await page.keyboard.type("Call the desk.");
    expect((await save(page, kb)).body_md).toBe(
      "Thirty days[^terms].\n\n[^terms]: See the sales terms.\n\nAsk first [^1]\n\n[^1]: Call the desk.",
    );
    await expect(page.locator("article sup.kb-footnote-ref[data-footnote-ref='1']")).toHaveAttribute("aria-label", "Footnote 2");
  });
});

test.describe("find and replace (KB-2i)", () => {
  test("Mod-F marks each match, steps through them, and replaces them", async ({ page }) => {
    await page.setViewportSize({ width: 1440, height: 900 });
    const kb = await setup(page, "Apple pie.\n\nAn apple tart.\n\nAPPLE juice.");
    await page.goto("/knowledge/pages/kp-a");
    await expect(page.locator("article").getByText("Apple pie.")).toBeVisible({ timeout: 30_000 });

    // The read view finds too, with no replace.
    await page.locator("article").getByText("Apple pie.").click();
    await page.keyboard.press("ControlOrMeta+f");
    const bar = page.getByRole("search", { name: "Find in this page" });
    await expect(bar).toBeVisible();
    await expect(bar.getByRole("button", { name: "Replace" })).toHaveCount(0);
    await bar.getByRole("textbox", { name: "Find" }).fill("apple");
    await expect(bar).toContainText("1 of 3");
    await expect(page.locator(".kb-find-match")).toHaveCount(3);
    await expect(page.locator(".kb-find-match-current")).toHaveText("Apple");
    await page.keyboard.press("Enter");
    await expect(bar).toContainText("2 of 3");
    await expect(page.locator(".kb-find-match-current")).toHaveText("apple");
    await page.keyboard.press("Shift+Enter");
    await expect(bar).toContainText("1 of 3");
    await bar.getByRole("button", { name: "Match case" }).click();
    await expect(bar).toContainText("1 of 1");
    await expect(page.locator(".kb-find-match")).toHaveCount(1);
    // Escape closes the bar from any control in it.
    await page.keyboard.press("Escape");
    await expect(bar).toHaveCount(0);
    await expect(page.locator(".kb-find-match")).toHaveCount(0);

    // In an edit, Replace changes the match in focus, and Replace all the rest.
    await edit(page);
    await caretAtEnd(page);
    await page.keyboard.press("ControlOrMeta+f");
    await expect(bar).toBeVisible();
    await bar.getByRole("textbox", { name: "Find" }).fill("apple");
    await expect(bar).toContainText("1 of 3");
    await bar.getByRole("button", { name: "Replace", exact: true }).click();
    await bar.getByRole("textbox", { name: "Replace with" }).fill("Pear");
    await bar.getByRole("button", { name: "Replace", exact: true }).last().click();
    await expect(bar).toContainText("1 of 2");
    await bar.getByRole("button", { name: "Replace all" }).click();
    await expect(bar).toContainText("No match");
    await bar.getByRole("button", { name: "Close find" }).click();
    expect((await save(page, kb)).body_md).toBe("Pear pie.\n\nAn Pear tart.\n\nPear juice.");
  });

  test("Ctrl+K opens no find bar", async ({ page }) => {
    await page.setViewportSize({ width: 1440, height: 900 });
    await setup(page, "Apple pie.");
    await edit(page);
    await caretAtEnd(page);
    await page.keyboard.press("Control+k");
    await page.keyboard.press("Escape");
    await expect(page.getByRole("search", { name: "Find in this page" })).toHaveCount(0);
  });
});

test.describe("the : emoji insert (KB-2i)", () => {
  test(": and a name insert the emoji, and a : inside a word opens nothing", async ({ page }) => {
    await page.setViewportSize({ width: 1440, height: 900 });
    const kb = await setup(page, "Start.");
    await edit(page);
    await caretAtEnd(page);
    await page.keyboard.press("Enter");
    await page.keyboard.type("Launch :rocke");
    const list = page.getByRole("listbox", { name: "Emoji" });
    await expect(list.getByRole("option").first()).toContainText("rocket");
    await page.keyboard.press("Enter");
    await expect(list).toHaveCount(0);
    await page.keyboard.type("at 10:30");
    await expect(list).toHaveCount(0);
    expect((await save(page, kb)).body_md).toBe("Start.\n\nLaunch 🚀 at 10:30");
  });
});

test.describe("the title flow (KB-2i)", () => {
  test("Enter in the title moves the rest into the body, and the arrows move between them", async ({ page }) => {
    await page.setViewportSize({ width: 1440, height: 900 });
    const kb = await setup(page, "Thirty days.");
    const body = await edit(page);
    const title = page.getByRole("textbox", { name: "Title" });

    // ArrowDown from the title goes to the body, and ArrowUp at its start comes back.
    await title.click();
    await page.keyboard.press("End");
    await page.keyboard.press("ArrowDown");
    await expect(body).toBeFocused();
    await page.keyboard.press("ArrowUp");
    await expect(title).toBeFocused();
    await page.keyboard.press("ArrowRight");
    await expect(body).toBeFocused();
    await page.keyboard.press("ArrowUp");
    await expect(title).toBeFocused();

    // Enter splits the title at the caret.
    for (let i = 0; i < " policy".length; i++) await page.keyboard.press("ArrowLeft");
    await page.keyboard.press("Enter");
    await expect(title).toHaveValue("Refund");
    await expect(body).toBeFocused();
    await expect(body.locator("p").first()).toHaveText("policy");
    await page.keyboard.type("Our ");
    const patch = await save(page, kb);
    expect(patch).toEqual({ title: "Refund", body_md: "Our policy\n\nThirty days." });
  });
});
