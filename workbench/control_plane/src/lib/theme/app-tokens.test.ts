/**
 * The `--cc-*` contract.
 *
 * This is a PUBLISHED interface — it is written into `agent-app-builder`'s
 * instructions, into `acb_skills/design.md`, and into every Custom App already
 * built and published, none of which we can go back and edit. So the tests here
 * are about the promises we made to code we do not control:
 *
 *   1. every token resolves to a real CSS value inside an iframe that inherits
 *      nothing from us,
 *   2. switching theme actually changes them — the failure this whole module
 *      exists to fix was values that were *present and frozen*, which no type
 *      or lint could have caught,
 *   3. the two consumers (the srcDoc `<style>` and the live postMessage patch)
 *      never disagree.
 */

import { describe, expect, it } from "vitest";

import { CC_TOKEN_NAMES, appTokenCss, appTokenMap, appTokens } from "./app-tokens";
import { THEME } from "./themes";
import type { ThemeMode } from "./types";

const MODES: ThemeMode[] = ["dark", "light"];

describe("appTokens", () => {
  it("defines every token in both modes", () => {
    for (const mode of MODES) {
      const map = appTokenMap(THEME, mode);
      expect(Object.keys(map).sort()).toEqual([...CC_TOKEN_NAMES].sort());
      for (const [name, value] of Object.entries(map)) {
        expect(value, `${mode} ${name}`).toBeTruthy();
        expect(String(value).trim(), `${mode} ${name}`).not.toBe("");
      }
    }
  });

  it("resolves var() references that only exist in OUR document", () => {
    // `controls.buttonRadius` is `var(--radius)`. Inside the sandbox's opaque
    // origin `--radius` does not exist, and an unresolvable var() invalidates
    // the whole declaration at computed-value time — so the button silently
    // loses its radius rather than erroring anywhere visible.
    for (const mode of MODES) {
      for (const [name, value] of appTokens(THEME, mode)) {
        expect(value, `${mode} ${name}`).not.toMatch(/var\(\s*--radius\b/);
      }
    }
  });

  it("leaves no reference to a token the sandbox does not define", () => {
    // Any var() that survives must point at something inside the --cc-*
    // namespace, which the frame does have. This is the general form of the
    // --radius case above and it caught a second, subtler one: the font stack
    // starts with a `next/font` handle (`var(--font-geist-sans)`) that exists
    // only on our <html>, so exporting stacks verbatim gave sandboxed apps an
    // invalid font-family and therefore NO font.
    const defined = new Set(CC_TOKEN_NAMES);
    for (const [name, value] of appTokens(THEME, "dark")) {
      for (const [, ref] of String(value).matchAll(/var\(\s*(--[a-z-]+)/gi)) {
        expect(defined.has(ref), `${name} → ${ref}`).toBe(true);
      }
    }
  });

  it("keeps a real stack when it strips the font handles", () => {
    // Stripping the webfont handle must leave the platform families behind,
    // not flatten to a bare generic: they are the half that survives the
    // sandbox boundary (the frame's CSP is `font-src data:`, so a self-hosted
    // face cannot follow). A bare "sans-serif" here means a sandboxed app
    // renders in Times while the shell around it renders in Geist.
    for (const key of ["--cc-font", "--cc-mono"] as const) {
      const value = appTokenMap(THEME, "dark")[key];
      expect(value, key).not.toBe("sans-serif");
      expect(value, key).not.toBe("monospace");
      expect(String(value).split(",").length, `${key} kept a stack`).toBeGreaterThan(1);
    }
  });

  it("maps --cc-muted to the muted FOREGROUND, not the muted fill", () => {
    // The published name is a wart: an app writing `color: var(--cc-muted)` on
    // helper text wants ink, and every app already assumes it. Mapping it to
    // `colors.muted` would be defensible from the name alone and would render
    // grey-on-grey in every one of them.
    const map = appTokenMap(THEME, "dark");
    expect(map["--cc-muted"]).toBe(THEME.colors.dark.mutedForeground);
    expect(map["--cc-muted"]).not.toBe(THEME.colors.dark.muted);
  });

  it("carries control PERSONALITY across, not just colour", () => {
    // The point of handing these over: a generated app picks up the shell's
    // button radius and label treatment, rather than being "our palette on
    // somebody else's controls".
    const map = appTokenMap(THEME, "dark");
    // The RESOLVED radius, not the manifest's `var(--radius)` — that variable
    // does not exist inside the frame, which is what `resolveVars` is for.
    expect(map["--cc-button-radius"]).toBe(THEME.shape.radius);
    expect(map["--cc-control-label-transform"]).toBe(THEME.controls.labelTransform);
    expect(map["--cc-control-state-layer"]).toBe(THEME.controls.stateLayerOpacity);
  });

  it("differs between modes — the frozen-values regression", () => {
    // The bug this module fixes was not a missing token, it was a token that
    // existed and never changed: the sandbox stayed frozen while the shell
    // around it moved. The theme axis that used to prove this is gone
    // (2026-08-31), so MODE is the axis that carries the guard now — and it
    // is the one that still moves at runtime.
    expect(appTokenMap(THEME, "dark")["--cc-bg"]).not.toBe(
      appTokenMap(THEME, "light")["--cc-bg"],
    );
    expect(appTokenCss(THEME, "dark")).not.toBe(appTokenCss(THEME, "light"));
  });
});

describe("appTokenCss", () => {
  it("agrees exactly with the postMessage map", () => {
    // The srcDoc <style> and the live patch are two paths to the same screen.
    // If they disagree, a mode switch changes the app's appearance and a
    // reload changes it back — the worst kind of bug to be told about.
    const css = appTokenCss(THEME, "light");
    for (const [name, value] of Object.entries(appTokenMap(THEME, "light"))) {
      expect(css).toContain(`${name}: ${value};`);
    }
  });

  it("emits one declaration per line, safe to paste inside a rule", () => {
    const css = appTokenCss(THEME, "dark");
    const lines = css.split("\n").filter((l) => l.trim());
    expect(lines).toHaveLength(CC_TOKEN_NAMES.length);
    for (const line of lines) {
      expect(line.trim()).toMatch(/^--cc-[a-z-]+:\s*.+;$/);
    }
    // Nothing that could terminate the enclosing rule or open a comment.
    expect(css).not.toContain("}");
    expect(css).not.toContain("/*");
  });
});
