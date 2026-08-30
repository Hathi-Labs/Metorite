// Dual-mode — WS-31.
//
// ⚠️ **The subject is the SEAM, not the palette.** Two callers must agree: the
// inline boot script in `layout.tsx`, which runs before hydration and cannot
// import anything, and `ThemeToggle`, which writes the value. If they disagree
// about the storage key the theme silently stops persisting, and nothing fails
// — the toggle still works for the session, so it looks fine to whoever tests
// it by clicking once.
//
// The colours themselves are unverifiable here. `DESIGN_SYSTEM.md` §8's gate is
// switching the theme and LOOKING, and this suite has no browser.

import { describe, expect, it } from "vitest";
import { readFileSync } from "node:fs";
import { join } from "node:path";

import {
  DEFAULT_THEME,
  STORAGE_KEY,
  bootScript,
  isTheme,
} from "./theme";

const SRC = join(__dirname, "..");
const LAYOUT = readFileSync(join(SRC, "app", "layout.tsx"), "utf8");
const TOGGLE = readFileSync(join(SRC, "app", "ThemeToggle.tsx"), "utf8");
const CSS = readFileSync(join(SRC, "app", "globals.css"), "utf8");

/** ⚠️ **Assert against RULES, never the file.** `globals.css` explains in prose
 *  why it does not use `prefers-color-scheme` and why the dark accent `#5b93f7`
 *  is unusable on white — so a naive `not.toContain` on either string fails on
 *  the comment that documents the very property being checked.
 *
 *  Both directions bite. A `toContain` satisfied by a comment certifies the
 *  documentation rather than the stylesheet, which is the same defect the
 *  Python deploy fences carry `_executable_lines` to avoid. This is the seventh
 *  time this repo has hit a fence matching its own prose. */
const RULES = CSS.replace(/\/\*[\s\S]*?\*\//g, " ");

describe("the boot script", () => {
  it("carries the SAME key the toggle writes", () => {
    // 🔴 The whole seam. Typed twice, they drift, and the theme stops
    // persisting without anything failing.
    expect(bootScript()).toContain(JSON.stringify(STORAGE_KEY));
  });

  it("is wrapped in try/catch, because localStorage THROWS", () => {
    // ⚠️ A browser set to block site data throws rather than returning null.
    // This runs before React exists, so an uncaught throw leaves a blank page.
    expect(bootScript()).toContain("try{");
    expect(bootScript()).toContain("catch");
  });

  it("only ever writes a value it recognises", () => {
    // A junk value in storage must not reach the DOM as a data-theme.
    expect(bootScript()).toContain('"light"');
    expect(bootScript()).toContain('"dark"');
  });

  it("is self-contained — it cannot import anything", () => {
    expect(bootScript()).not.toContain("import");
    expect(bootScript()).not.toContain("require");
  });
});

describe("the layout wires it", () => {
  it("runs the script rather than reimplementing it", () => {
    // ⚠️ A second hand-written copy in the JSX is the drift this seam exists
    // to prevent, so the layout must CALL bootScript.
    expect(LAYOUT).toContain("bootScript()");
  });

  it("suppresses the hydration warning it knowingly causes", () => {
    // 🔴 Required, not decorative. The script rewrites data-theme before React
    // hydrates, so server and client legitimately differ on that attribute.
    expect(LAYOUT).toContain("suppressHydrationWarning");
  });

  it("stamps the default theme server-side", () => {
    expect(LAYOUT).toContain("DEFAULT_THEME");
  });
});

describe("only one place names the storage key", () => {
  it("the toggle imports it rather than typing it", () => {
    // The literal appearing twice in the tree is exactly how the two halves
    // come apart.
    expect(TOGGLE).toContain("STORAGE_KEY");
    expect(TOGGLE).not.toContain(`"${STORAGE_KEY}"`);
    expect(LAYOUT).not.toContain(`"${STORAGE_KEY}"`);
  });

  it("guards every storage access", () => {
    expect(TOGGLE).toContain("try {");
    expect(TOGGLE).toContain("catch");
  });
});

describe("the default", () => {
  it("is dark, and NOT prefers-color-scheme", () => {
    // ⚠️ Deliberate. The light palette has never been looked at by a human;
    // following the OS would hand it to every operator whose machine is light,
    // which is most of them. This flips once somebody has seen it.
    expect(DEFAULT_THEME).toBe("dark");
    expect(RULES).not.toContain("prefers-color-scheme");
  });

  it("recognises only the two themes", () => {
    expect(isTheme("light")).toBe(true);
    expect(isTheme("dark")).toBe(true);
    expect(isTheme("system")).toBe(false);
    expect(isTheme(null)).toBe(false);
  });
});

describe("the light palette exists and is not an inversion", () => {
  it("redefines the surfaces under an explicit selector", () => {
    expect(RULES).toContain('[data-theme="light"]');
  });

  it("re-chooses the accent rather than reusing the dark one", () => {
    // 🔴 Parity is the hard half of dual-mode. The dark accent (#5b93f7)
    // measures ~2.6:1 on white and fails every contrast floor for text, so a
    // palette that merely swaps backgrounds ships unreadable links.
    const light = RULES.slice(RULES.indexOf('[data-theme="light"]'));
    expect(light).toContain("--accent:");
    expect(light).not.toContain("#5b93f7");
  });

  it("sets color-scheme in both modes, so form controls follow", () => {
    // Without this the browser draws native scrollbars, date pickers and
    // autofill in the wrong mode — the one part of the page CSS cannot reach.
    expect(RULES).toContain("color-scheme: dark");
    expect(RULES).toContain("color-scheme: light");
  });
});

describe("the chrome (mockup adoption, 2026-08-30)", () => {
  it("draws the body glow from tokens, in both themes by construction", () => {
    // One definition: the glow reads var(--accent-soft), so the light
    // block re-colours it by re-colouring the accent — no second copy.
    expect(RULES).toContain("--bg-glow:");
    expect(RULES).toMatch(/body\s*\{[^}]*var\(--bg-glow\)/);
  });

  it("builds the brand gradient FROM the accent pair, never a third hue", () => {
    expect(RULES).toMatch(/--brand-grad:[^;]*var\(--accent\)/);
    expect(RULES).toMatch(/--brand-grad:[^;]*var\(--accent-2\)/);
  });

  it("🔴 goes still when the OS asks for reduced motion", () => {
    expect(RULES).toContain("prefers-reduced-motion");
  });
});
