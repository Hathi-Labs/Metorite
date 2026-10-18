/**
 * WS-61 KB-12: the two Knowledge jobs of the command bar (`knowledge_base.md`
 * §11). Each case is written so the obvious wrong build fails it.
 *
 *   • The jobs belong to the Knowledge pane, which is live. A job of a
 *     preview pane would reach the coordinator for every member.
 *   • "Write a page" names its own `?do=`, and the layout listens for it.
 *     Without the listener the link opens the Spaces view and nothing else.
 *   • The form opens through the leave guard, so a dirty page asks first.
 */
import { readFileSync } from "node:fs";
import path from "node:path";

import { describe, expect, it } from "vitest";

import { PANES } from "@/lib/nav";
import { JOBS, buildItems, rank } from "@/lib/shell/registry";

const LAYOUT = readFileSync(path.join(__dirname, "..", "layout.tsx"), "utf-8");
const KNOWLEDGE = PANES.find((p) => p.href === "/knowledge")!;

describe("the Knowledge jobs (KB-12)", () => {
  it("are two, of a live pane, each opening a Knowledge route", () => {
    expect(KNOWLEDGE.launch).toBe("live");
    const mine = JOBS.filter((j) => j.app === "/knowledge").map((j) => [j.id, j.href]);
    expect(mine).toEqual([
      ["find-knowledge", "/knowledge/search"],
      ["write-page", "/knowledge?do=write-page"],
    ]);
  });

  it("the layout listens for write-page, and opens the form through the leave guard", () => {
    expect(LAYOUT).toMatch(
      /<ShellJob\s+id="write-page"\s+onOpen=\{\(\) => requestLeave\(\(\) => openNewPage\(/,
    );
  });

  it("the bar finds each job by the words a member types", () => {
    const items = buildItems([KNOWLEDGE]);
    const top = (query: string) => rank({ items, query, context: null, recent: [] })[0]?.key;
    expect(top("write a page")).toBe("do:write-page");
    expect(top("new page")).toBe("do:write-page");
    expect(top("find in knowledge")).toBe("do:find-knowledge");
    expect(top("handbook")).toBe("do:find-knowledge");
    expect(top("refund policy")).toBe("do:find-knowledge");
  });
});
