/**
 * The pure rules of KB-2i (WS-61, `knowledge_base.md` §21.7): footnotes,
 * the GFM table rules, the move of a row or a column, find and replace,
 * the title flow and the `:` emoji search. The browser half is
 * `e2e/knowledge-editor-tools.spec.ts`.
 */
import { getSchema, type JSONContent } from "@tiptap/core";
import { EditorState, TextSelection } from "@tiptap/pm/state";
import { describe, expect, it } from "vitest";

import { searchEmoji } from "../emoji";
import { footnoteBody, footnoteNumbers, nextFootnoteLabel, renderFootnote } from "./footnotes";
import { normaliseBody, parseBody, writeBody } from "./markdown";
import { knowledgeExtensions } from "./schema";
import { getRegex, processSearches } from "./searchAndReplace";
import { moveColumn, moveRow } from "./tableMove";
import { clearCells, columnAlign, dropEmptyHeaderRow, setColumnAlign } from "./tables";
import { isAtBodyStart, isEmptyBody, splitTitle } from "./titleFlow";

const schema = getSchema(knowledgeExtensions());
const stateOf = (md: string) => EditorState.create({ schema, doc: schema.nodeFromJSON(parseBody(md)) });
const mdOf = (state: EditorState) => writeBody(state.doc.toJSON() as JSONContent);

/** The state, the table's place, and a caret in its first cell. */
function tableState(md: string) {
  const state = stateOf(md);
  let tablePos = -1;
  state.doc.descendants((node, pos) => {
    if (tablePos < 0 && node.type.name === "table") tablePos = pos;
  });
  return { state: state.apply(state.tr.setSelection(TextSelection.near(state.doc.resolve(tablePos + 3)))), tablePos };
}

const TABLE = "| a | b | c |\n| --- | :---: | --- |\n| 1 | 2 | 3 |\n| 4 | 5 | 6 |";

describe("footnotes", () => {
  it("reads the text of a note and writes it back with its indent", () => {
    expect(footnoteBody("[^n]: one\n    two\n\n    three")).toBe("one\ntwo\n\nthree");
    expect(renderFootnote("n", "one\ntwo\n\nthree")).toBe("[^n]: one\n    two\n\n    three");
    expect(renderFootnote("1", "")).toBe("[^1]:");
  });

  it("gives a new mark the next free number, past any named label", () => {
    expect(nextFootnoteLabel([])).toBe("1");
    expect(nextFootnoteLabel(["1", "note", "3"])).toBe("4");
  });

  it("numbers each label by its first mark", () => {
    const doc = stateOf("B[^b] and A[^a] and B[^b].\n\n[^a]: x\n\n[^b]: y").doc;
    expect([...footnoteNumbers(doc)]).toEqual([["b", 1], ["a", 2]]);
  });

  it("parses a mark and a note into nodes, and keeps a stray [^ as text", () => {
    const doc = parseBody("A[^1] and [^ not one].\n\n[^1]: Note.");
    const types = JSON.stringify(doc);
    expect(types).toContain('"type":"footnoteReference","attrs":{"label":"1"}');
    expect(types).toContain('"type":"footnoteDefinition"');
    // A `[^` that is no mark stays text: the stock rule escapes it once.
    const saved = normaliseBody("A [^ not one].");
    expect(JSON.stringify(parseBody(saved))).not.toContain("footnote");
    expect(normaliseBody(saved)).toBe(saved);
  });
});

describe("the GFM table rules", () => {
  it("reads a header row with no text as a table with the header off", () => {
    const doc = dropEmptyHeaderRow(parseBody("|  |  |\n| --- | --- |\n| 1 | 2 |"));
    const table = doc.content?.[0];
    expect(table?.content?.length).toBe(1);
    expect(table?.content?.[0].content?.[0].type).toBe("tableCell");
  });

  it("sets and clears the alignment of a column", () => {
    const { state, tablePos } = tableState(TABLE);
    const table = state.doc.nodeAt(tablePos)!;
    expect(columnAlign(table, 1)).toBe("center");
    expect(columnAlign(table, 0)).toBeNull();
    const tr = state.tr;
    expect(setColumnAlign(tr, tablePos, 0, "right")).toBe(true);
    setColumnAlign(tr, tablePos, 1, null);
    expect(mdOf(state.apply(tr))).toBe("| a | b | c |\n| ---: | --- | --- |\n| 1 | 2 | 3 |\n| 4 | 5 | 6 |");
  });

  it("clears the cells of a row and of a column", () => {
    const { state, tablePos } = tableState(TABLE);
    const tr = state.tr;
    clearCells(tr, tablePos, { kind: "row", index: 1 });
    clearCells(tr, tablePos, { kind: "col", index: 2 });
    expect(mdOf(state.apply(tr))).toBe("| a | b |  |\n| --- | :---: | --- |\n|  |  |  |\n| 4 | 5 |  |");
  });
});

describe("the move of a row or a column", () => {
  it("moves a column with its alignment", () => {
    const { state, tablePos } = tableState(TABLE);
    const tr = state.tr;
    expect(moveColumn({ tr, originIndex: 1, targetIndex: 0, select: true, pos: tablePos + 1 })).toBe(true);
    expect(mdOf(state.apply(tr))).toBe("| b | a | c |\n| :---: | --- | --- |\n| 2 | 1 | 3 |\n| 5 | 4 | 6 |");
  });

  it("moves a body row, and a row moved to the top becomes the header", () => {
    const { state, tablePos } = tableState(TABLE);
    let tr = state.tr;
    moveRow({ tr, originIndex: 2, targetIndex: 1, select: false, pos: tablePos + 1 });
    expect(mdOf(state.apply(tr))).toBe("| a | b | c |\n| --- | :---: | --- |\n| 4 | 5 | 6 |\n| 1 | 2 | 3 |");
    tr = state.tr;
    moveRow({ tr, originIndex: 2, targetIndex: 0, select: false, pos: tablePos + 1 });
    expect(mdOf(state.apply(tr))).toBe("| 4 | 5 | 6 |\n| --- | :---: | --- |\n| a | b | c |\n| 1 | 2 | 3 |");
  });

  it("refuses a move onto itself", () => {
    const { state, tablePos } = tableState(TABLE);
    expect(moveColumn({ tr: state.tr, originIndex: 1, targetIndex: 1, select: true, pos: tablePos + 1 })).toBe(false);
  });
});

describe("find and replace", () => {
  it("finds the words as text, never as a pattern, with and without case", () => {
    const doc = stateOf("Apple pie. An apple tart (a.b). APPLE.").doc;
    expect(processSearches(doc, getRegex(["apple"], true, false, false), "m", 0).results).toHaveLength(3);
    expect(processSearches(doc, getRegex(["apple"], true, true, false), "m", 0).results).toHaveLength(1);
    expect(processSearches(doc, getRegex(["a.b"], true, false, false), "m", 0).results).toHaveLength(1);
  });

  it("marks the match in focus with the current class", () => {
    const doc = stateOf("one one one").doc;
    const { decorationsToReturn } = processSearches(doc, getRegex(["one"], true, false, false), "kb-find-match", 1);
    const classes = decorationsToReturn.find().map((d) => (d as unknown as { type: { attrs: { class: string } } }).type.attrs.class);
    expect(classes).toEqual(["kb-find-match", "kb-find-match kb-find-match-current", "kb-find-match"]);
  });
});

describe("the title flow", () => {
  it("moves the text after the caret, and drops the space at the cut", () => {
    expect(splitTitle("Refund policy", 6)).toEqual({ title: "Refund", moved: "policy" });
    expect(splitTitle("Refund policy", 13)).toEqual({ title: "Refund policy", moved: "" });
    expect(splitTitle("Refund policy", 0, 7)).toEqual({ title: "", moved: "policy" });
  });

  it("knows the start of the body and an empty body", () => {
    const empty = stateOf("");
    expect(isEmptyBody(empty)).toBe(true);
    expect(isAtBodyStart(empty)).toBe(true);
    const full = stateOf("Text.");
    expect(isEmptyBody(full)).toBe(false);
    expect(isAtBodyStart(full.apply(full.tr.setSelection(TextSelection.create(full.doc, 3))))).toBe(false);
  });
});

describe("the : emoji search", () => {
  it("takes the start of a name first, and reads _ as a space", () => {
    expect(searchEmoji("rock")[0]).toEqual({ char: "🚀", name: "rocket" });
    expect(searchEmoji("thumbs_up")[0].char).toBe("👍");
    expect(searchEmoji("zzzz")).toEqual([]);
    expect(searchEmoji("", 3)).toHaveLength(3);
  });
});
