import { describe, expect, it } from "vitest";

import { escapeCell, escapeInline, escapeLine, toTable } from "./mdText";

describe("markdown text escapes", () => {
  it("escapes inline marks", () => {
    expect(escapeInline("a*b_c`d[e]f<g>h\\i")).toBe("a\\*b\\_c\\`d\\[e\\]f\\<g\\>h\\\\i");
  });
  it("escapes a line start that would be a block", () => {
    expect(escapeLine("# Title")).toBe("\\# Title");
    expect(escapeLine("- item")).toBe("\\- item");
    expect(escapeLine("1. item")).toBe("1\\. item");
    expect(escapeLine("---")).toBe("\\---");
    expect(escapeLine("a - b")).toBe("a - b");
  });
  it("escapes a cell on one line", () => {
    expect(escapeCell("a|b\nc")).toBe("a\\|b c");
  });
  it("pads short rows and starts with the header", () => {
    expect(toTable([["a", "b"], ["1"]])).toBe("| a | b |\n| --- | --- |\n| 1 |  |");
    expect(toTable([])).toBe("");
  });
});
