/**
 * The `@` mention menu (WS-61): its rows, the places it opens and the
 * dates it parses. The browser half is `e2e/knowledge-shortcuts.spec.ts`.
 */
import { getSchema } from "@tiptap/core";
import { EditorState } from "@tiptap/pm/state";
import { findSuggestionMatch } from "@tiptap/suggestion";
import { describe, expect, it } from "vitest";

import { MENTION_PREFIXES, buildMentionItems, dateItems, parseTypedDate, shiftDay } from "./mention";
import type { PageChoice } from "./pageLinks";
import { knowledgeExtensions } from "./schema";

const page = (id: string, title: string, icon: string | null = null, updatedAt: string | null = null): PageChoice => ({
  id,
  title,
  spaceName: "Company",
  updatedAt,
  icon,
});
const NOW = new Date(2026, 9, 22, 10, 0, 0);

/** Does `@` open a menu with the caret at the end of this text? */
function opens(text: string): { query: string } | null {
  const schema = getSchema(knowledgeExtensions());
  const doc = schema.node("doc", null, [schema.node("paragraph", null, text ? [schema.text(text)] : [])]);
  const state = EditorState.create({ schema, doc });
  const $position = state.doc.resolve(1 + text.length);
  const m = findSuggestionMatch({
    char: "@",
    allowSpaces: true,
    allowToIncludeChar: false,
    allowedPrefixes: MENTION_PREFIXES,
    startOfLine: false,
    $position,
  });
  return m && !/^\s/.test(m.query) ? { query: m.query } : null;
}

describe("where @ opens the menu", () => {
  it("opens at the start of a line and after a space", () => {
    expect(opens("@")).toEqual({ query: "" });
    expect(opens("see @ref")).toEqual({ query: "ref" });
    expect(opens("see @Q3 plan")).toEqual({ query: "Q3 plan" });
  });

  it("opens nothing inside an email address", () => {
    expect(opens("mail a@b.com")).toBeNull();
    expect(opens("a@")).toBeNull();
  });

  it("closes when @ is followed by a space", () => {
    expect(opens("see @ ")).toBeNull();
  });
});

describe("the date rows", () => {
  it("offers Today, Tomorrow and Yesterday for no words", () => {
    const rows = dateItems("", NOW);
    expect(rows.map((r) => [r.label, r.iso])).toEqual([
      ["Today", "2026-10-22"],
      ["Tomorrow", "2026-10-23"],
      ["Yesterday", "2026-10-21"],
    ]);
  });

  it("narrows by the typed start of a name", () => {
    expect(dateItems("tom", NOW).map((r) => r.label)).toEqual(["Tomorrow"]);
    expect(dateItems("TODAY", NOW).map((r) => r.label)).toEqual(["Today"]);
    expect(dateItems("zzz", NOW)).toEqual([]);
  });

  it("parses a typed YYYY-MM-DD and ignores a day that does not exist", () => {
    expect(parseTypedDate("2026-10-19")).toBe("2026-10-19");
    expect(parseTypedDate(" 2026-10-19 ")).toBe("2026-10-19");
    expect(parseTypedDate("2026-02-30")).toBeNull();
    expect(parseTypedDate("2026-13-01")).toBeNull();
    expect(parseTypedDate("19-10-2026")).toBeNull();
    expect(dateItems("2026-10-19", NOW).map((r) => r.iso)).toEqual(["2026-10-19"]);
    expect(dateItems("2026-02-30", NOW)).toEqual([]);
  });

  it("moves a day across a month end", () => {
    expect(shiftDay("2026-10-31", 1)).toBe("2026-11-01");
    expect(shiftDay("2026-03-01", -1)).toBe("2026-02-28");
  });
});

describe("the rows of the menu", () => {
  const pages = Array.from({ length: 10 }, (_, i) => page(`p${i}`, `Refund note ${i}`, i === 0 ? "📄" : null));

  it("lists at most 8 pages, then the dates", () => {
    const rows = buildMentionItems(pages, "", { now: NOW });
    expect(rows.filter((r) => r.kind === "page")).toHaveLength(8);
    expect(rows.slice(8).map((r) => r.section)).toEqual(["Date", "Date", "Date"]);
  });

  it("matches pages by title and leaves out the open page", () => {
    const rows = buildMentionItems(pages, "note 3", { exclude: "p3", now: NOW });
    expect(rows).toEqual([]);
    const one = buildMentionItems(pages, "note 3", { now: NOW });
    expect(one.map((r) => r.id)).toEqual(["p3"]);
  });

  it("carries the page emoji and the space name", () => {
    const [first] = buildMentionItems(pages, "note 0", { now: NOW });
    expect(first).toMatchObject({ kind: "page", glyph: "📄", hint: "Company", section: "Pages" });
  });

  it("puts a typed date after the pages", () => {
    const rows = buildMentionItems([page("a", "2026 plan")], "2026-10-19", { now: NOW });
    expect(rows.map((r) => r.kind)).toEqual(["date"]);
  });
});
