/**
 * D100: ONE bar at the foot of the page asks, searches and opens. It replaced
 * the command bar's field at the top of the shell (owner, 2026-10-13: "there
 * is an overlap between the top search bar and the bottom agent bar … just
 * have one").
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

describe("the highlight: the field is -1, and the results sit above it", () => {
  it("↑ from the field lands on the bottom row, the one nearest the field", () => {
    expect(moveHighlight(-1, "ArrowUp", 4)).toBe(3);
  });

  it("↑ climbs, and stops at the top row", () => {
    expect(moveHighlight(3, "ArrowUp", 4)).toBe(2);
    expect(moveHighlight(0, "ArrowUp", 4)).toBe(0);
  });

  it("↓ walks down, and past the bottom row returns to the field", () => {
    expect(moveHighlight(1, "ArrowDown", 4)).toBe(2);
    expect(moveHighlight(3, "ArrowDown", 4)).toBe(-1);
    expect(moveHighlight(-1, "ArrowDown", 4)).toBe(-1);
  });

  it("with no results there is only the field", () => {
    expect(moveHighlight(-1, "ArrowUp", 0)).toBe(-1);
  });
});

describe("Enter asks, unless the member chose a result", () => {
  const bar = code(read("lib/shell/ChatBar.tsx"));

  it("Enter on a highlighted result runs it, and only then", () => {
    expect(bar).toMatch(/e\.key === "Enter" && held >= 0\)\s*\{\s*e\.preventDefault\(\);\s*rows\[held\]\.run\(\);/);
  });

  it("any other Enter submits the form, which asks the assistant", () => {
    expect(bar).toMatch(/onSubmit=\{\(e\) => \{\s*e\.preventDefault\(\);\s*ask\(\);/);
    expect(bar).toContain("openAssistant({ text: words });");
  });

  it("the bar's results carry no Ask row of their own: its Enter IS the ask", () => {
    expect(bar).toContain("askRow: false,");
    expect(bar).toContain("data-chat-bar-ask");
  });

  it("the placeholder says the field does all three", () => {
    expect(BAR_PLACEHOLDER).toMatch(/^Ask Metorite, or search and open anything/);
  });

  it("an empty field opens no results on a plain click: a chat box first", () => {
    expect(bar).toContain("setShowing(e.target.value.trim().length > 0);");
    expect(bar).not.toMatch(/onFocus=\{/);
  });
});

describe("one engine for both surfaces", () => {
  it("the bar and the modal read the same result engine", () => {
    expect(read("lib/shell/ChatBar.tsx")).toMatch(/import \{ ResultList, resultOptionId, useBarResults \} from "\.\/CommandBar";/);
    expect(code(read("lib/shell/CommandBar.tsx"))).toContain("useBarResults({ active: open,");
  });
});

describe("the shell routes every search to the one bar", () => {
  const shell = code(read("lib/shell/ShellBar.tsx"));

  it("with the chat bar on, the desktop row draws no field", () => {
    expect(shell).toContain("const unified = chatOn && bar;");
    expect(shell).toContain("search={!unified}");
  });

  it("⌘K, `/` and a search link reach the bar, with the results open", () => {
    expect(shell).toMatch(/FOCUS_CHAT_BAR, \{ detail: \{ query, results: true \} \}/);
    expect(shell).toMatch(/if \(unified && !assistantState\(\)\.open\) \{\s*search\(""\);/);
    expect(shell).toMatch(/else search\(""\);/);
    expect(shell).toContain("const onOpen = (e: Event) => search(");
  });

  it("while the assistant is open the bar is hidden, so the modal answers", () => {
    expect(shell).toMatch(/if \(unified && !assistantState\(\)\.open\) \{\s*window\.dispatchEvent/);
    expect(shell).toContain("openWith(query);");
  });
});
