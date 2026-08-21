import { expect, test, type Page } from "@playwright/test";

/**
 * Renaming a project, in a real browser (WS-27bg slice 2 remainder, D-PM-21).
 *
 * Why a browser and not a unit test: this tree has no DOM test environment at
 * all (`vitest.config.ts` is `environment: "node"` and collects only
 * `src/**` + `.test.ts`), so every rendering assertion below is unreachable
 * from vitest by construction. `projectMenu.test.ts` can prove the menu OFFERS
 * Rename; only a browser can prove the field opens, takes focus, and that the
 * Save button is actually clickable.
 *
 * That last one is the case worth the spec. The obvious implementation cancels
 * the edit on blur — and clicking Save blurs the field first, so a blur-cancel
 * closes the form before the click lands and the button is silently dead while
 * Enter still works. No unit test can see it; a reviewer reading the diff would
 * have to simulate the event order in their head.
 */

const TREE = {
  rows: [
    {
      id: "p-root",
      name: "Delivery",
      status: "active",
      children: [
        { id: "p-child", name: "Firmware", status: "active", children: [] },
      ],
    },
  ],
  total: 2,
};

/** PATCH bodies the page sent, in order. Empty means nothing was written. */
type Sent = { id: string; body: Record<string, unknown> }[];

async function openTree(page: Page): Promise<Sent> {
  const sent: Sent = [];
  let currentName = "Delivery";

  await page.goto("/");
  await page.evaluate(() => {
    localStorage.setItem("cc-theme", "fluent");
    localStorage.setItem("theme", "dark");
  });

  await page.route("**/api/projects/tree", (route) =>
    route.fulfill({
      json: {
        ...TREE,
        rows: [{ ...TREE.rows[0], name: currentName }],
      },
    }),
  );
  await page.route("**/api/projects/nodes/*/grants", (route) =>
    route.fulfill({ json: { rows: [], total: 0 } }),
  );
  await page.route("**/api/projects/nodes/*", async (route, request) => {
    if (request.method() !== "PATCH") return route.fallback();
    const body = request.postDataJSON() as Record<string, unknown>;
    const id = new URL(request.url()).pathname.split("/").pop() ?? "";
    sent.push({ id, body });
    // The refetch after a rename must see the NEW name, or the assertion that
    // the row updated would be testing the stub rather than the page.
    if (typeof body.name === "string") currentName = body.name;
    return route.fulfill({ json: { ...TREE.rows[0], name: currentName } });
  });

  await page.goto("/projects");
  await expect(page.getByText("Delivery").first()).toBeVisible({
    timeout: 15000,
  });
  return sent;
}

/** Right-click the row and choose Rename. */
async function beginRename(page: Page, rowLabel: string) {
  await page.getByText(rowLabel, { exact: true }).first().click({ button: "right" });
  await page.getByText("Rename", { exact: true }).click();
}

test.describe("project rename", () => {
  test("the field opens on the row, focused, carrying the current name", async ({
    page,
  }) => {
    await openTree(page);
    await beginRename(page, "Delivery");

    const field = page.getByLabel("Rename Delivery");
    await expect(field).toBeVisible();
    // autoFocus is a prop; that the browser HONOURED it is not.
    await expect(field).toBeFocused();
    await expect(field).toHaveValue("Delivery");
  });

  test("Save is clickable — the click is not eaten by the blur it causes", async ({
    page,
  }) => {
    const sent = await openTree(page);
    await beginRename(page, "Delivery");

    await page.getByLabel("Rename Delivery").fill("Delivery & Install");
    // The whole point: a mousedown on Save blurs the field first.
    await page.getByRole("button", { name: "Save", exact: true }).click();

    await expect
      .poll(() => sent.length, { timeout: 5000 })
      .toBe(1);
    expect(sent[0]).toEqual({ id: "p-root", body: { name: "Delivery & Install" } });
    await expect(page.getByText("Delivery & Install").first()).toBeVisible();
  });

  test("Enter submits too", async ({ page }) => {
    const sent = await openTree(page);
    await beginRename(page, "Delivery");

    await page.getByLabel("Rename Delivery").fill("Delivery EMEA");
    await page.getByLabel("Rename Delivery").press("Enter");

    await expect.poll(() => sent.length, { timeout: 5000 }).toBe(1);
    expect(sent[0].body).toEqual({ name: "Delivery EMEA" });
  });

  test("Escape cancels the rename without writing and without closing the app", async ({
    page,
  }) => {
    const sent = await openTree(page);
    await beginRename(page, "Delivery");

    await page.getByLabel("Rename Delivery").fill("Never saved");
    await page.getByLabel("Rename Delivery").press("Escape");

    await expect(page.getByLabel("Rename Delivery")).toHaveCount(0);
    await expect(page.getByText("Delivery", { exact: true }).first()).toBeVisible();
    expect(sent).toHaveLength(0);
    // The row is still there and still selectable: Escape left the FIELD, it
    // did not dismiss the tree or navigate away.
    await expect(page.getByText("Firmware", { exact: true })).toBeVisible();
  });

  test("an unchanged name writes nothing", async ({ page }) => {
    const sent = await openTree(page);
    await beginRename(page, "Delivery");

    await page.getByRole("button", { name: "Save", exact: true }).click();

    await expect(page.getByLabel("Rename Delivery")).toHaveCount(0);
    expect(sent).toHaveLength(0);
  });

  test("a whitespace-only name writes nothing", async ({ page }) => {
    const sent = await openTree(page);
    await beginRename(page, "Delivery");

    await page.getByLabel("Rename Delivery").fill("   ");
    await page.getByRole("button", { name: "Save", exact: true }).click();

    expect(sent).toHaveLength(0);
    // And the row keeps the name it had, rather than going blank.
    await expect(page.getByText("Delivery", { exact: true }).first()).toBeVisible();
  });

  test("the rest of the page stops showing the old name, not just the tree", async ({
    page,
  }) => {
    // `selected` is a snapshot and the resync effect only checks that its id is
    // still PRESENT, so the tree can redraw correctly while every consumer of
    // `selected.name` keeps the old value. The quick-add placeholder is the
    // cheapest reachable one; `projectName={selected.name}` feeds two more.
    await openTree(page);
    const composer = page.getByLabel("New task title");
    await expect(composer).toHaveAttribute("placeholder", /Delivery/);

    await beginRename(page, "Delivery");
    await page.getByLabel("Rename Delivery").fill("Delivery & Install");
    await page.getByRole("button", { name: "Save", exact: true }).click();

    await expect(composer).toHaveAttribute(
      "placeholder",
      /Delivery & Install/,
      { timeout: 5000 },
    );
  });

  test("a subproject renames from its own row, not its parent's", async ({
    page,
  }) => {
    const sent = await openTree(page);
    await beginRename(page, "Firmware");

    await page.getByLabel("Rename Firmware").fill("Firmware v2");
    await page.getByRole("button", { name: "Save", exact: true }).click();

    await expect.poll(() => sent.length, { timeout: 5000 }).toBe(1);
    // The id is the CHILD's. A menu built from a stale closure would send the
    // root's, and the rename would land on the wrong project.
    expect(sent[0].id).toBe("p-child");
  });
});
