/**
 * An agent's live card edge is the chat box's spectrum (owner, 2026-11-04:
 * "use the same colored spinner that we use for the text box of the chat").
 *
 * The chat box's live edge is `conic-gradient(var(--glow-1) … var(--glow-5))`,
 * turned once each 5 s (`glass-glow-turn`). The card turns a layer of the same
 * spectrum at the same speed, with transform only (DESIGN_SYSTEM.md §4), and
 * under reduced motion it keeps the still spectrum. The quiet edges (steady,
 * breathing) keep the member's accent.
 */
import { readFileSync } from "node:fs";
import { fileURLToPath } from "node:url";
import { describe, expect, it } from "vitest";

const css = readFileSync(fileURLToPath(new URL("../../globals.css", import.meta.url)), "utf-8");
const SPECTRUM =
  /conic-gradient\(var\(--glow-1\), var\(--glow-2\), var\(--glow-3\), var\(--glow-4\), var\(--glow-5\), var\(--glow-1\)\)/;

/** The body of the first rule whose selector is exactly `selector`, after `from`. */
function rule(selector: string, from = 0): string {
  const at = css.indexOf(`${selector} {`, from);
  expect(at, `${selector} is in globals.css`).toBeGreaterThanOrEqual(0);
  return css.slice(at, css.indexOf("}", at) + 1);
}

describe("the live card edge is the chat box's spectrum", () => {
  it("draws the spectrum as the still edge, also under reduced motion", () => {
    expect(rule('[data-agent-edge="live"]')).toMatch(SPECTRUM);
  });

  it("turns the spectrum at the chat box's speed, only when motion is allowed", () => {
    const gate = css.indexOf("@media (prefers-reduced-motion: no-preference) {", css.indexOf("@keyframes agent-edge-travel"));
    expect(gate).toBeGreaterThan(0);
    const layer = rule('  [data-agent-edge="live"]::before', gate);
    expect(layer).toMatch(SPECTRUM);
    expect(layer).toMatch(/animation: agent-edge-travel 5s linear infinite/);
    // The chat box turns once each 5 s too.
    expect(css).toMatch(/animation: glass-glow-turn 5s linear infinite/);
  });

  it("keeps no accent tail on the live edge, and the quiet edges keep the accent", () => {
    expect(css).not.toMatch(/conic-gradient\(from 0deg, transparent 0 70%, var\(--primary\) 100%\)/);
    expect(rule('[data-agent-edge="steady"]')).toMatch(/var\(--primary\)/);
    expect(rule('[data-agent-edge="breathing"]')).toMatch(/var\(--primary\)/);
  });
});
