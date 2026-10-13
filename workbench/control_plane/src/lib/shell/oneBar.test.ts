/**
 * D102: ONE field asks, searches and opens. It sat at the foot of the page
 * from 2026-10-13, and moved into the middle of the shell bar on 2026-10-14
 * (owner: "remove the bar at the bottom and have it at the title bar … The
 * functionality should be as expected by the user").
 *
 * Each case names the rule it holds.
 */
import { readFileSync } from "node:fs";
import { join } from "node:path";
import { fileURLToPath } from "node:url";

import { describe, expect, it } from "vitest";

import { BAR_PLACEHOLDER, moveHighlight } from "./ChatBar";

const SRC = fileURLToPath(new URL("../..", import.meta.url));
const read = (rel: string) => readFileSync(join(SRC, rel), "utf8");
const code = (text: string) => text.replace(/\/\*[\s\S]*?\*\//g, "").replace(/^\s*\/\/.*$/gm, "");

describe("the highlight: the field is -1, and the results drop below it", () => {
  it("↓ from the field lands on the top row, the one nearest the field", () => {
    expect(moveHighlight(-1, "ArrowDown", 4)).toBe(0);
  });

  it("↓ walks down, and stops at the bottom row", () => {
    expect(moveHighlight(1, "ArrowDown", 4)).toBe(2);
    expect(moveHighlight(3, "ArrowDown", 4)).toBe(3);
  });

  it("↑ climbs, and past the top row returns to the field", () => {
    expect(moveHighlight(2, "ArrowUp", 4)).toBe(1);
    expect(moveHighlight(0, "ArrowUp", 4)).toBe(-1);
    expect(moveHighlight(-1, "ArrowUp", 4)).toBe(-1);
  });

  it("with no results there is only the field", () => {
    expect(moveHighlight(-1, "ArrowDown", 0)).toBe(-1);
  });
});

describe("Enter asks, unless the member chose a result", () => {
  const field = code(read("lib/shell/ChatBar.tsx"));

  it("Enter on a highlighted result runs it, and only then", () => {
    expect(field).toMatch(/e\.key === "Enter" && held >= 0\)\s*\{\s*e\.preventDefault\(\);\s*rows\[held\]\.run\(\);/);
  });

  it("any other Enter submits the form, which asks the assistant", () => {
    expect(field).toMatch(/onSubmit=\{\(e\) => \{\s*e\.preventDefault\(\);\s*ask\(\);/);
    expect(field).toContain("openAssistant({ text: words });");
  });

  it("Enter on an empty field opens the assistant", () => {
    expect(field).toMatch(/if \(!words\) \{\s*openAssistant\(\);\s*return;/);
  });

  it("the results carry no Ask row of the engine: the field's own ask row leads them", () => {
    expect(field).toContain("askRow: false,");
    expect(field).toContain("data-ask-field-ask");
  });

  it("the results drop below the field", () => {
    expect(field).toContain("absolute left-0 right-0 top-full z-50 mt-1");
  });

  it("the placeholder says the field does all three", () => {
    expect(BAR_PLACEHOLDER).toMatch(/^Ask Metorite, or search and open anything/);
  });

  it("an empty field opens no results on a plain click", () => {
    expect(field).toContain("setShowing(e.target.value.trim().length > 0);");
    expect(field).not.toMatch(/onFocus=\{/);
  });
});

describe("one engine for both surfaces", () => {
  it("the field and the modal read the same result engine", () => {
    expect(read("lib/shell/ChatBar.tsx")).toMatch(/import \{ ResultList, resultOptionId, useBarResults \} from "\.\/CommandBar";/);
    expect(code(read("lib/shell/CommandBar.tsx"))).toContain("useBarResults({ active: open,");
  });
});

describe("the shell routes every search to the one field", () => {
  const shell = code(read("lib/shell/ShellBar.tsx"));

  it("with the chat on, the desktop row draws the real field in the middle", () => {
    expect(shell).toContain("const unified = chatOn && bar;");
    expect(shell).toContain("field={unified ? <AskField /> : undefined}");
  });

  it("⌘K, `/` and a search link reach the field, with the results open", () => {
    expect(shell).toMatch(/FOCUS_ASK_FIELD, \{ detail: \{ query, results: true \} \}/);
    expect(shell).toMatch(/if \(unified\) \{\s*search\(""\);/);
    expect(shell).toMatch(/else search\(""\);/);
    expect(shell).toContain("const onOpen = (e: Event) => search(");
  });

  it("the field shows over every form of the assistant, so the desktop never needs the modal", () => {
    // The row sits above the assistant's work area, never inside it.
    expect(shell).toMatch(/if \(unified\) \{\s*window\.dispatchEvent/);
    expect(shell).not.toMatch(/unified && !assistantState\(\)\.open/);
  });

  it("⌘J opens the assistant when it is closed, and never only moves the cursor", () => {
    expect(shell).toMatch(/if \(!assistantState\(\)\.open\) \{\s*openAssistant\(\);\s*return;/);
  });
});
