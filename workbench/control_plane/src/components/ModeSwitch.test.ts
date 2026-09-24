/**
 * The view switcher: one control, in Projects and in My Tasks.
 *
 * My Tasks drew its List / Board switch by hand, with a solid `bg-primary`
 * active fill. Projects drew ghost `Button`s with `selected`. Both render
 * `components/ModeSwitch.tsx` now.
 */

import { readFileSync } from "node:fs";
import { join } from "node:path";
import { fileURLToPath } from "node:url";

import { createElement } from "react";
import { renderToStaticMarkup } from "react-dom/server";
import { describe, expect, it } from "vitest";

import { ModeSwitch } from "./ModeSwitch";

const SRC = fileURLToPath(new URL("..", import.meta.url));
const read = (rel: string) => readFileSync(join(SRC, rel), "utf8");
const code = (text: string) =>
  text.replace(/\/\*[\s\S]*?\*\//g, "").replace(/(?<![:"'/])\/\/[^\n]*/g, "");

/** Each rendered button's opening tag and its text, in order. */
function buttons(html: string): { tag: string; text: string }[] {
  return [...html.matchAll(/(<button\b[^>]*>)([\s\S]*?)<\/button>/g)].map((m) => ({
    tag: m[1],
    text: m[2].replace(/<[^>]*>/g, ""),
  }));
}

describe("the view switcher", () => {
  const html = renderToStaticMarkup(
    createElement(ModeSwitch<"list" | "board">, {
      modes: [
        { id: "list", icon: "List" },
        { id: "board", icon: "Kanban" },
      ],
      mode: "board",
      onPick: () => {},
    }),
  );

  it("draws one Button per mode, and the ON one wears the house pair", () => {
    const [list, board] = buttons(html);
    expect(list.text).toBe("List");
    expect(board.text).toBe("Board");
    for (const b of [list, board]) expect(b.tag).toContain("cc-control");
    expect(board.tag).toContain('aria-pressed="true"');
    expect(board.tag).toContain("bg-primary/10 text-primary");
    expect(list.tag).toContain('aria-pressed="false"');
    // The solid fill My Tasks used to draw.
    expect(html).not.toMatch(/\bbg-primary text-primary-foreground\b/);
  });

  it("is a named group", () => {
    expect(html).toMatch(/<div[^>]*role="group"[^>]*aria-label="View mode"/);
  });
});

describe("both apps render it", () => {
  it("Projects declares no switcher of its own", () => {
    const src = code(read("app/projects/page.tsx"));
    expect(src).not.toMatch(/function ModeSwitch\b/);
    expect(src.match(/<ModeSwitch\b/g) ?? []).toHaveLength(2);
  });

  it("My Tasks' List / Board is the shared switcher, not a hand-rolled pair", () => {
    const src = code(read("app/tasks/components/ItemList.tsx"));
    expect(src).toMatch(/import \{ type ModeOption, ModeSwitch \} from "@\/components\/ModeSwitch";/);
    expect(src).toMatch(/<ModeSwitch\b/);
    expect(src).not.toMatch(/onClick=\{\(\) => setModePersist\(/);
  });

  it("both view headings use Projects' scale", () => {
    // The project name in Projects, the view name in My Tasks.
    expect(read("app/projects/page.tsx")).toMatch(
      /<h2 className="min-w-0 truncate text-sm font-medium text-foreground">\s*\{title\}/,
    );
    expect(read("app/tasks/components/ItemList.tsx")).toMatch(
      /<h2 className="whitespace-nowrap text-sm font-medium text-foreground">/,
    );
  });
});
