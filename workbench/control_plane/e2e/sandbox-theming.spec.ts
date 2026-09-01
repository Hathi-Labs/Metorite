/**
 * The theming engine at the sandbox boundary, in a real browser.
 *
 * Custom Apps, generative-UI cards and React artifacts run in an
 * opaque-origin iframe that inherits *nothing* from us — not our stylesheet,
 * not `data-theme`, not one custom property. Whatever design system they get,
 * we hand across explicitly as the `--cc-*` contract.
 *
 * Unit tests check what `appTokens()` returns. They cannot check the thing that
 * actually decides whether an app looks themed: whether those declarations
 * survive the frame's CSP, resolve against real CSS, and can be *changed* on a
 * document that is already running. This drives the real `buildSrcDoc` and the
 * real `BRIDGE` — importing them rather than mirroring them, because a copy of
 * the frame would happily pass while the frame itself was broken.
 *
 * The regression it exists for is specific and was live until this landed: the
 * `--cc-*` values were hand-written RapidTool literals switching only on
 * light/dark, so every app ever built stayed RapidTool-blue while the shell
 * around it turned Fluent or Material. Nothing errored. It just quietly did not
 * theme.
 */
import { expect, test, type FrameLocator, type Page } from "@playwright/test";

import { BRIDGE, buildSrcDoc } from "../src/lib/theme/sandbox-frame";
import { THEME } from "../src/lib/theme/themes";
import { appTokenMap } from "../src/lib/theme/app-tokens";
import type { ThemeMode } from "../src/lib/theme/types";

/** An app styled the way the agent instructions tell agents to style one. */
const APP = `
<div class="cc-card" id="panel" style="padding:12px">
  <h1 id="title">Orders</h1>
  <p id="hint" style="color:var(--cc-muted)">3 pending</p>
  <span id="chip" style="background:var(--cc-warning);color:var(--cc-warning-fg)">late</span>
  <button class="cc-btn cc-primary" id="go">Go</button>
  <code id="mono" style="font-family:var(--cc-mono)">42</code>
  <span data-cc-icon="Plus" id="ico"></span>
</div>`;

/** Host page with the real frame inside it, reachable as `#f`. */
async function mount(page: Page, mode: ThemeMode = "dark") {
  const srcDoc = buildSrcDoc(APP, THEME, mode, {
    Plus: '<svg data-pack="lucide"></svg>',
  });
  await page.setContent(
    `<!doctype html><html><body><iframe id="f" sandbox="allow-scripts" ` +
      `style="width:600px;height:400px;border:0" srcdoc="${srcDoc.replace(/"/g, "&quot;")}"></iframe>` +
      `</body></html>`,
  );
  return page.frameLocator("#f");
}

/**
 * Computed value of a `--cc-*` variable, read from INSIDE the frame.
 *
 * It has to be read from inside: the frame is `allow-scripts` without
 * `allow-same-origin`, so its origin is opaque and `contentDocument` is null to
 * us. That is the security property the whole sandbox rests on, and it is worth
 * bumping into here — it is exactly why the tokens have to be handed across
 * rather than inherited.
 */
const readVar = (frame: FrameLocator, name: string) =>
  frame
    .locator("#panel")
    .evaluate(
      (_el, n) => getComputedStyle(document.documentElement).getPropertyValue(n).trim(),
      name,
    );

test.describe("tokens cross the boundary", () => {
  test("an app styled with tokens renders against the real values", async ({ page }) => {
    const frame = await mount(page);
    const t = THEME.colors.dark;

    // The declarations survived the CSP and resolved — the baseline claim
    // everything else here depends on.
    await expect(frame.locator("#title")).toHaveText("Orders");
    expect(await readVar(frame, "--cc-primary")).toBe(t.primary);
    expect(await readVar(frame, "--cc-card")).toBe(t.card);
  });

  test("control personality crosses, not just colour", async ({ page }) => {
    // A generated app picks up the shell's button shape, not only its
    // palette. Colour alone would make it "our colours on somebody else's
    // controls", which is not the same product.
    const frame = await mount(page);
    expect(await readVar(frame, "--cc-button-radius")).toBe(THEME.shape.radius);
    expect(await readVar(frame, "--cc-control-label-transform")).toBe(
      THEME.controls.labelTransform,
    );
  });

  test("ink on a coloured fill is legible in both modes", async ({ page }) => {
    // Was hardcoded `hsl(20 14% 12%)`, i.e. near-black, which is only legible
    // over a YELLOW warning. A mode with a dark warning rendered black on dark.
    for (const mode of ["dark", "light"] as const) {
      const frame = await mount(page, mode);
      const fill = await readVar(frame, "--cc-warning");
      const ink = await readVar(frame, "--cc-warning-fg");
      expect(ink, mode).toBeTruthy();
      expect(ink, mode).not.toBe(fill);
    }
  });

  test("the font stack resolves rather than collapsing to nothing", async ({ page }) => {
    // The manifest writes `var(--font-geist-sans), …` — a next/font handle
    // registered on OUR <html> and undefined in here. An unresolvable var()
    // invalidates the whole font-family, so exporting it verbatim gave apps no
    // font at all, silently.
    const frame = await mount(page);
    const font = await frame.locator("#mono").evaluate((el) => getComputedStyle(el).fontFamily);
    expect(font).not.toBe("");
    expect(font).not.toContain("var(");
    // The handle is stripped and the platform tail survives it.
    expect(font.toLowerCase()).toMatch(/ui-monospace|sfmono|menlo|consolas/);
  });

  test("light mode swaps colour without disturbing the shape", async ({ page }) => {
    const dark = await mount(page, "dark");
    const darkBg = await readVar(dark, "--cc-bg");
    const radius = await readVar(dark, "--cc-button-radius");
    const light = await mount(page, "light");
    expect(await readVar(light, "--cc-bg")).not.toBe(darkBg);
    expect(await readVar(light, "--cc-button-radius")).toBe(radius);
  });
});

test.describe("live appearance changes", () => {
  test("a running app restyles without being remounted", async ({ page }) => {
    // Why this must be a patch and not a srcDoc rebuild: a rebuild remounts the
    // document, and a published app would throw away whatever the person had
    // typed into it every time somebody changed the look.
    //
    // The axis is MODE since 2026-08-31 — the theme axis this originally
    // patched along is gone, and dark→light is the change that still happens
    // at runtime while an app is holding state.
    const frame = await mount(page, "dark");
    const before = await readVar(frame, "--cc-primary");

    // Stand in for user state the app is holding.
    await frame.locator("#title").evaluate((el) => {
      el.setAttribute("data-user-state", "typed-but-unsaved");
    });

    await page.evaluate(
      ([vars, icons]) => {
        const win = (document.getElementById("f") as HTMLIFrameElement).contentWindow!;
        win.postMessage({ __cc: true, kind: "theme", mode: "light", vars, icons }, "*");
      },
      [appTokenMap(THEME, "light"), { Plus: '<svg data-pack="lucide"></svg>' }] as const,
    );

    await expect
      .poll(() => readVar(frame, "--cc-primary"))
      .toBe(THEME.colors.light.primary);
    expect(await readVar(frame, "--cc-primary")).not.toBe(before);

    // The document was never rebuilt, so the state is still there.
    await expect(frame.locator("#title")).toHaveAttribute(
      "data-user-state",
      "typed-but-unsaved",
    );
  });

  test("icon placeholders re-resolve to the new pack", async ({ page }) => {
    const frame = await mount(page);
    await expect(frame.locator("#ico svg")).toHaveAttribute("data-pack", "lucide");

    await page.evaluate(() => {
      const win = (document.getElementById("f") as HTMLIFrameElement).contentWindow!;
      win.postMessage(
        {
          __cc: true,
          kind: "theme",
          mode: "dark",
          vars: {},
          icons: { Plus: '<svg data-pack="material"></svg>' },
        },
        "*",
      );
    });

    await expect(frame.locator("#ico svg")).toHaveAttribute("data-pack", "material");
  });

  test("the patch cannot write outside the --cc-* namespace", async ({ page }) => {
    // A sibling frame that got a handle to this window should at worst be able
    // to recolour it — not reach other properties, and not break out of the
    // declaration (setProperty is a value API, so a ';' is rejected, not parsed).
    const frame = await mount(page);
    await page.evaluate(() => {
      const win = (document.getElementById("f") as HTMLIFrameElement).contentWindow!;
      win.postMessage(
        {
          __cc: true,
          kind: "theme",
          vars: { "--evil": "red", "--cc-primary": "rgb(1, 2, 3); --evil2: red" },
        },
        "*",
      );
    });

    await expect.poll(() => readVar(frame, "--evil")).toBe("");
    expect(await readVar(frame, "--evil2")).toBe("");
  });

  test("a message that is not ours is ignored", async ({ page }) => {
    const frame = await mount(page);
    const before = await readVar(frame, "--cc-primary");
    await page.evaluate(() => {
      const win = (document.getElementById("f") as HTMLIFrameElement).contentWindow!;
      win.postMessage({ kind: "theme", vars: { "--cc-primary": "red" } }, "*");
      win.postMessage("theme", "*");
    });
    expect(await readVar(frame, "--cc-primary")).toBe(before);
  });
});

test("the bridge ships the theme listener", () => {
  // Cheap guard on the thing every test above depends on: if the listener is
  // ever dropped from BRIDGE, the frame stops responding to theme changes and
  // the only symptom is an app that needs a reload to restyle.
  expect(BRIDGE).toContain('d.kind !== "theme"');
});
