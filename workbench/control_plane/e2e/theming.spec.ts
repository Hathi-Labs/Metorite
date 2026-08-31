import { expect, test, type Page } from "@playwright/test";

import { THEME } from "../src/lib/theme/themes";

/**
 * The look, end to end.
 *
 * ⚠️ This file used to test the THEMING ENGINE — four themes, a `data-theme`
 * scope, and the cascade relationship between `globals.css` and a generated
 * stylesheet. The owner retired that engine on 2026-08-31, and the tests that
 * only existed to prove a theme SWITCH worked were deleted with it rather
 * than rewritten into assertions about nothing.
 *
 * What is left is what still has a subject: the tokens resolve, the mode
 * switch works, the personal preferences apply, and a malformed one is
 * refused. These read computed style in a real browser, so they see what the
 * user sees — which is exactly the half that unit tests cannot reach.
 */

const readVar = (page: Page, prop: string) =>
  page.evaluate(
    (p) => getComputedStyle(document.documentElement).getPropertyValue(p).trim(),
    prop,
  );

/** Load the app in a given colour mode, as a returning member would. */
async function loadInMode(page: Page, mode: "dark" | "light" = "dark") {
  await page.goto("/");
  await page.evaluate((m) => localStorage.setItem("theme", m), mode);
  await page.goto("/");
}

test.describe("tokens", () => {
  test("the server renders the tokens before any script runs", async ({ page }) => {
    await page.goto("/");
    // `globals.css` IS the source now — no boot script writes a scope, so
    // this is also the with-JavaScript-disabled case.
    expect(await readVar(page, "--primary")).toBe(THEME.colors.dark.primary);
    expect(await readVar(page, "--radius")).toBe(THEME.shape.radius);
  });

  test("no theme scope survives on the document", async ({ page }) => {
    // A leftover `data-theme` would out-specify `:root` and pin the app to a
    // scope no stylesheet defines any more, leaving it unstyled.
    await page.goto("/");
    await expect(page.locator("html")).not.toHaveAttribute("data-theme", /.*/);
  });

  test("the font stack reaches the body, fallbacks included", async ({ page }) => {
    await page.goto("/");
    const font = await page.evaluate(() => getComputedStyle(document.body).fontFamily);
    // The webfont handle resolves to a real family name, and the platform
    // fallback is still behind it — that tail is what renders if Geist 404s.
    expect(font).toMatch(/Geist/i);
    expect(font).toMatch(/system-ui|-apple-system/);
  });

  test("the whole Tailwind radius scale follows --radius", async ({ page }) => {
    await page.goto("/");
    const radii = await page.evaluate(() => {
      const el = document.createElement("div");
      document.body.appendChild(el);
      const at = (cls: string) => {
        el.className = cls;
        return getComputedStyle(el).borderRadius;
      };
      const out = { sm: at("rounded-sm"), lg: at("rounded-lg"), xl: at("rounded-xl") };
      el.remove();
      return out;
    });
    // --radius is 0.75rem = 12px; `lg` and `xl` both equal it (AGENTS.md
    // rule 6), and `sm` steps down by 4px.
    expect(radii.lg).toBe("12px");
    expect(radii.xl).toBe("12px");
    expect(radii.sm).toBe("8px");
  });
});

test.describe("colour mode", () => {
  test("light mode swaps colours but keeps the structure", async ({ page }) => {
    await loadInMode(page, "light");
    expect(await readVar(page, "--primary")).toBe(THEME.colors.light.primary);
    expect(await readVar(page, "--background")).toBe(THEME.colors.light.background);
    // Structural tokens live only on `:root`; `.light` inherits them.
    expect(await readVar(page, "--radius")).toBe(THEME.shape.radius);

    await loadInMode(page, "dark");
    expect(await readVar(page, "--primary")).toBe(THEME.colors.dark.primary);
  });
});

test.describe("icons", () => {
  test("every glyph is drawn by Lucide", async ({ page }) => {
    // One pack since 2026-08-31. A glyph that is NOT Lucide means something
    // slipped past `<Icon>` — the rule conformance rule 2 enforces statically,
    // checked here against the rendered document.
    await page.goto("/");
    const counts = await page.evaluate(() => {
      const svgs = [...document.querySelectorAll("svg")];
      return {
        lucide: svgs.filter((s) => s.getAttribute("class")?.includes("lucide")).length,
        other: svgs.filter((s) => !s.getAttribute("class")?.includes("lucide")).length,
      };
    });
    expect(counts.lucide).toBeGreaterThan(0);
    expect(counts.other).toBe(0);
  });
});

test.describe("user preferences", () => {
  test("density scales the root font size", async ({ page }) => {
    await page.goto("/");
    await page.evaluate(() => localStorage.setItem("cc-density", "compact"));
    await page.goto("/");
    // 16px browser default × the compact scale of 0.92.
    expect(await page.evaluate(() => getComputedStyle(document.documentElement).fontSize)).toBe(
      "14.72px",
    );
  });

  test("an accent override replaces the primary", async ({ page }) => {
    await page.goto("/");
    await page.evaluate(() => localStorage.setItem("cc-accent", "rgb(255, 0, 0)"));
    await page.goto("/");
    expect(await readVar(page, "--primary")).toBe("rgb(255, 0, 0)");
  });

  test("an accent override brings ink that is legible ON it", async ({ page }) => {
    // Reported 2026-08-07: the sidebar logo mark rendered a near-black glyph
    // on a red accent badge. The accent replaced `--primary` but not
    // `--primary-foreground`, so the ink stayed the one the theme chose for
    // its OWN primary. Every primary-filled surface had it, not just the logo.
    await page.goto("/");
    await page.evaluate(() => {
      localStorage.setItem("theme", "dark");
      localStorage.setItem("cc-accent", "hsl(347 77% 50%)");
      localStorage.setItem("cc-accent-ink", "#ffffff");
    });
    await page.goto("/");
    expect(await readVar(page, "--primary")).toBe("hsl(347 77% 50%)");
    expect(await readVar(page, "--primary-foreground")).toBe("#ffffff");
    // The glyph on the primary-filled logo badge, in a real browser.
    const glyph = await page
      .locator("svg.lucide-command")
      .first()
      .evaluate((el) => getComputedStyle(el).color);
    expect(glyph).toBe("rgb(255, 255, 255)");
  });

  test("clearing the accent restores the app's own pairing", async ({ page }) => {
    // The ink must not outlive the accent that justified it.
    await loadInMode(page, "dark");
    expect(await readVar(page, "--primary-foreground")).toBe(
      THEME.colors.dark.primaryForeground,
    );
  });

  test("a malformed stored accent is ignored rather than applied", async ({ page }) => {
    await page.goto("/");
    await page.evaluate(() =>
      localStorage.setItem("cc-accent", "red; background: url(https://evil.test/x)"),
    );
    await page.goto("/");
    // The boot script writes through setProperty, so the CSSOM rejects the
    // value outright; nothing is injected into the stylesheet.
    expect(await readVar(page, "--primary")).toBe(THEME.colors.dark.primary);
  });
});

test.describe("control personality", () => {
  /**
   * The shared primitives carry shape and weight, not just colour. This reads
   * computed style off a real button, because none of it is visible to a
   * type-checker and only some of it is expressible as a class.
   *
   * ⚠️ Three of the four tests here compared themes (Material's pills,
   * Fluent's stroke, Graphite's uppercase) and went with them. What survives
   * is the one that always mattered most: the shipped look is unchanged by
   * the primitives.
   */
  test("the app's buttons are unchanged by the primitives", async ({ page }) => {
    await page.goto("/");
    const b = await page.evaluate(() => {
      const el = document.createElement("button");
      el.className =
        "cc-control cc-button cc-button-filled bg-primary text-primary-foreground px-3 py-1.5 text-xs";
      document.body.appendChild(el);
      const cs = getComputedStyle(el);
      const out = {
        radius: cs.borderTopLeftRadius,
        filledBorder: cs.borderTopWidth,
        weight: cs.fontWeight,
        transform: cs.textTransform,
      };
      el.remove();
      return out;
    });
    expect(b.radius).toBe("12px");
    expect(b.filledBorder).toBe("0px");
    expect(b.weight).toBe("500");
    expect(b.transform).toBe("none");
  });
});
