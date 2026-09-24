/**
 * WS-27bm S8 fix round 4 — code blocks read in both colour modes.
 *
 * `vscDarkPlus` was a dark palette of hex values, unreadable on a light
 * card. The chat now uses `CODE_THEME`, and every colour in it is a theme
 * token.
 */
import { readFileSync } from "node:fs";
import { fileURLToPath } from "node:url";
import { describe, expect, it } from "vitest";

import { CODE_THEME } from "./codeTheme";

describe("CODE_THEME", () => {
  it("names every colour as a theme variable", () => {
    for (const [token, style] of Object.entries(CODE_THEME)) {
      for (const key of ["color", "background", "backgroundColor"] as const) {
        const value = style[key];
        if (value === undefined || value === "transparent") continue;
        expect(String(value), `${token}.${key}`).toMatch(/^var\(--[a-z0-9-]+\)$/);
      }
    }
  });

  it("colours the kinds a reader needs to tell apart", () => {
    for (const token of ["comment", "keyword", "string", "number", "function"]) {
      expect(CODE_THEME[token]?.color, token).toBeDefined();
    }
  });

  it("is what the chat's code block uses, and no fixed palette is left", () => {
    const src = readFileSync(
      fileURLToPath(new URL("../components/MarkdownMessage.tsx", import.meta.url)),
      "utf8",
    );
    expect(src).toContain("style={CODE_THEME}");
    expect(src).not.toMatch(/react-syntax-highlighter\/dist\/[a-z]+\/styles/);
  });
});
