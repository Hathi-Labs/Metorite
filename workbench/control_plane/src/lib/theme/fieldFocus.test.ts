/**
 * A text field's focus ring sits ON its border, never outside it.
 *
 * Owner, 2026-10-12: the rail's rename field and its Save button looked
 * merged. A field always matches `:focus-visible` while focused, so it wore
 * its `focus:border-primary` border AND the shared 2px ring 2px outside it.
 * The ring reached 4px past the box, into the button beside it.
 */
import { readFileSync } from "node:fs";
import { fileURLToPath } from "node:url";

import { describe, expect, it } from "vitest";

const css = readFileSync(
  fileURLToPath(new URL("../../app/globals.css", import.meta.url)),
  "utf-8",
);

describe("the field focus ring", () => {
  it("insets the ring on every text field the primitives draw", () => {
    expect(css).toMatch(
      /\.cc-control:is\(input, textarea, select\):focus-visible\s*\{\s*outline-offset:\s*-1px;/,
    );
  });

  it("keeps the outer ring for everything else", () => {
    expect(css).toMatch(/\.cc-control:focus-visible,\s*\.cc-link:focus-visible\s*\{[^}]*outline-offset:\s*2px;/);
  });
});
