import { expect, test, type Page } from "@playwright/test";

/**
 * The project run-state indicator, in a real browser (WS-27bg slice 2, D-PM-21).
 *
 * What only a browser can answer, and why the unit tests are not enough:
 *
 * * **Do the five states actually render as five different colours?** The unit
 *   tests assert a hue NAME (`green`, `amber`). Whether `text-success` and
 *   `text-warning` resolve to different paint depends on the active theme's
 *   token set and on the cascade — which is exactly what `statusAccent`'s own
 *   history warns about: a colour column existed for months and every lane drew
 *   the same grey, and no unit test could see it.
 * * **Does it survive a colour-mode switch?** The owner retired the theming
 *   engine on 2026-08-31 (`cc-theme` and the Fluent, Material and Graphite
 *   looks are gone), so the axis that is left is dark and light mode. Two
 *   modes × the same tree is the cheapest honest version of that check.
 * * **Is the inherited state visibly weaker than a chosen one?** That is an
 *   opacity on a live element, not a prop.
 *
 * The tree is served from a routed fixture rather than a live gateway: this
 * asserts the RENDERING of a state, and coupling it to a database would make it
 * a flake with a second job. The states themselves are proven end to end in
 * `tests/live/live_ws27bg.py`.
 */

/**
 * A department paused, with a child that inherits it, plus every other state.
 *
 * ⚠️ **The roots of the tree are SPACES, and a space owns no run state**
 * (`hasRunState`, owner directive 2026-08-31). The projects sit one level
 * down, so the fixture wraps them in one space. Without the wrapper the page
 * drew five space icons, and the spec measured the five colours of the
 * categorical ramp (3 distinct) instead of the five run states.
 */
const PROJECTS = [
    {
      id: "p-active",
      name: "Delivery",
      status: "active",
      children: [{ id: "p-child", name: "Firmware", status: "active", children: [] }],
    },
    {
      id: "p-paused",
      name: "Research",
      status: "on_hold",
      children: [
        // Its own column says `active`; it must draw PAUSED and weaker.
        { id: "p-inherit", name: "Optics", status: "active", children: [] },
      ],
    },
    { id: "p-stopped", name: "Cancelled line", status: "stopped", children: [] },
    { id: "p-queued", name: "Next quarter", status: "queued", children: [] },
    { id: "p-done", name: "Shipped", status: "done", children: [] },
];

const TREE = {
  rows: [
    { id: "p-space", name: "Studio", status: "active", children: PROJECTS },
  ],
  total: 8,
};

/**
 * Open the tree down a path of row names, and keep it open. A pick opens a row
 * that holds others, but two things can undo it: a pick that lands before the
 * page has hydrated (a cold dev compile) opens nothing, and the fold store
 * closes every row when the member identity binds late (`resumed`). So each
 * pass re-picks whatever is hidden from the top, then waits a beat and checks
 * that the last row stayed.
 */
async function reveal(page: Page, path: string[]) {
  const row = (name: string) => page.getByText(name, { exact: true }).first();
  await expect(async () => {
    for (let i = 1; i < path.length; i++) {
      if (!(await row(path[i]).isVisible())) {
        await row(path[i - 1]).click({ timeout: 3000 });
        await expect(row(path[i])).toBeVisible({ timeout: 3000 });
      }
    }
    await page.waitForTimeout(1500);
    await expect(row(path[path.length - 1])).toBeVisible({ timeout: 1000 });
  }).toPass({ timeout: 60000 });
}

async function openTree(page: Page, mode: "dark" | "light") {
  await page.goto("/");
  await page.evaluate((m) => {
    localStorage.setItem("theme", m);
  }, mode);
  // Only the tree and its grants are stubbed; everything else 404s as usual and
  // the page's own error handling deals with it, which is the point — the tree
  // must render without the rest of the app being healthy.
  await page.route("**/api/projects/tree", (route) =>
    route.fulfill({ json: TREE }),
  );
  await page.route("**/api/projects/nodes/*/grants", (route) =>
    route.fulfill({ json: { rows: [], total: 0 } }),
  );
  await page.goto("/projects");
  // Folds start CLOSED. Picking a row that holds others opens it, so pick the
  // space, then the paused department that holds the inheriting child.
  await reveal(page, ["Studio", "Delivery"]);
  await reveal(page, ["Studio", "Research", "Optics"]);
}

/** The rendered colour of the indicator on the row naming `project`. */
async function dotColour(page: Page, label: string): Promise<string> {
  return page.evaluate((name) => {
    const svg = [...document.querySelectorAll("svg[aria-label]")].find((el) =>
      (el.getAttribute("aria-label") ?? "").includes(name),
    );
    if (!svg) throw new Error(`no indicator for ${name}`);
    return getComputedStyle(svg).color;
  }, label);
}

for (const mode of ["dark", "light"] as const) {
  test.describe(`project run state — ${mode}`, () => {
    test("every state draws a different colour", async ({ page }) => {
      await openTree(page, mode);
      const colours = await Promise.all([
        dotColour(page, "Delivery"),
        dotColour(page, "Research"),
        dotColour(page, "Cancelled line"),
        dotColour(page, "Next quarter"),
        dotColour(page, "Shipped"),
      ]);
      // Five states, five distinct paints. The failure this catches is the one
      // this repo has already shipped once: every row the same grey.
      expect(new Set(colours).size).toBe(5);
      // And none of them is transparent or unset, which is how a token that
      // does not exist in a theme renders.
      for (const c of colours) expect(c).not.toMatch(/rgba\(0, 0, 0, 0\)/);
    });

    test("an inherited state draws weaker than a chosen one", async ({ page }) => {
      await openTree(page, mode);
      const opacity = await page.evaluate(() => {
        const el = [...document.querySelectorAll("svg[aria-label]")].find((s) =>
          (s.getAttribute("aria-label") ?? "").includes("inherited"),
        );
        return el ? getComputedStyle(el).opacity : null;
      });
      expect(opacity).not.toBeNull();
      expect(Number(opacity)).toBeLessThan(1);
    });

    test("a child inherits its parent's pause without its own column changing", async ({
      page,
    }) => {
      await openTree(page, mode);
      // `Optics` is stored `active`; it must announce itself as Paused.
      const label = await page.evaluate(() => {
        const el = [...document.querySelectorAll("svg[aria-label]")].find((s) =>
          (s.getAttribute("aria-label") ?? "").includes("inherited"),
        );
        return el?.getAttribute("aria-label") ?? "";
      });
      expect(label).toContain("Paused");
    });
  });
}

test("the indicator is a glyph, not colour alone", async ({ page }) => {
  // D-PM-27's accessibility floor: amber and green are the pair most commonly
  // confused, and this is a dense list. Distinct glyph PATHS, not just distinct
  // colours — a reader who cannot separate the hues still gets the state.
  await openTree(page, "dark");
  const shapes = await page.evaluate(() =>
    ["Delivery", "Research", "Cancelled line", "Next quarter", "Shipped"].map(
      (name) => {
        const svg = [...document.querySelectorAll("svg[aria-label]")].find((el) =>
          (el.getAttribute("aria-label") ?? "").includes(name),
        );
        return svg?.innerHTML ?? "";
      },
    ),
  );
  expect(new Set(shapes).size).toBe(5);
});
