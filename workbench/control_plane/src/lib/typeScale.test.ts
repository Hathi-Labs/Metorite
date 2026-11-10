/**
 * The generative-UI templates draw text on the type scale, in `rem`, so it
 * follows the member's density (follow-up of #716 and #735, spec
 * `projects_ai_chat.md` §24.8). They drew `fontSize: 11`, `12`, `13` and
 * `14`, and a `px` value does not read `--ui-scale`.
 *
 * Mutations this file catches (R7), each run red before the change:
 *
 * - a template gets `fontSize: 12` back -> "no template file uses an inline
 *   px font size";
 * - a step of `TYPE` is written in px -> "every step is in rem".
 */
import { readdirSync, readFileSync } from "node:fs";
import { join } from "node:path";
import { fileURLToPath } from "node:url";
import { describe, expect, it } from "vitest";

import { TYPE } from "@/lib/typeScale";

const COMPONENTS = fileURLToPath(new URL("../components", import.meta.url));

/** The template files: the tiers of the generative UI, by name. */
const TEMPLATE_FILE = /^(?:genUI|GenUI|GenUi|GenerativeUI)\w*\.tsx$/;

/** `fontSize: 12`, `fontSize: "12px"`, `font-size: 12px`. An SVG `fontSize="16"` inside a viewBox is art, and it scales with the box. */
const PX_FONT = /fontSize:\s*(?:\d|["'`]\s*\d+(?:\.\d+)?px)|font-size:\s*\d+(?:\.\d+)?px/;

describe("the type scale", () => {
  it("every step is in rem", () => {
    for (const [step, value] of Object.entries(TYPE)) expect(value, step).toMatch(/^\d*\.?\d+rem$/);
  });

  it("no template file uses an inline px font size", () => {
    const files = readdirSync(COMPONENTS).filter((f) => TEMPLATE_FILE.test(f));
    expect(files).toContain("genUITemplates.tsx");
    const hits: string[] = [];
    for (const f of files) {
      readFileSync(join(COMPONENTS, f), "utf8")
        .split(/\r?\n/)
        .forEach((line, i) => {
          if (PX_FONT.test(line)) hits.push(`${f}:${i + 1}: ${line.trim()}`);
        });
    }
    expect(hits).toEqual([]);
  });

  it("the pattern catches each px form", () => {
    for (const bad of ["fontSize: 12,", 'fontSize: "12px"', "font-size: 11px"]) expect(PX_FONT.test(bad), bad).toBe(true);
    for (const good of ["fontSize: TYPE.xs,", 'fontSize="16"', 'fontSize: "0.75rem"']) expect(PX_FONT.test(good), good).toBe(false);
  });
});
