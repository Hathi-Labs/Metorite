/**
 * The page mention chip (WS-61): only a link to a page of this app by id
 * draws as one. The browser half is `e2e/knowledge-shortcuts.spec.ts`.
 */
import { getSchema } from "@tiptap/core";
import { EditorState } from "@tiptap/pm/state";
import { describe, expect, it } from "vitest";

import { parseBody, writeBody } from "./markdown";
import { chipRuns } from "./pageChips";
import { chipPageId } from "./pageLinks";
import { knowledgeExtensions } from "./schema";

const ID = "3f2b8c1e-5a4d-4e6f-9b7a-1c2d3e4f5a6b";
const schema = getSchema(knowledgeExtensions());
const docOf = (md: string) => EditorState.create({ schema, doc: schema.nodeFromJSON(parseBody(md)) }).doc;

describe("chipPageId", () => {
  it("takes a path to a page by uuid, with or without an anchor", () => {
    expect(chipPageId(`/knowledge/pages/${ID}`)).toBe(ID);
    expect(chipPageId(`/knowledge/pages/${ID}#intro`)).toBe(ID);
  });

  it("refuses an external link and a path that is no page by uuid", () => {
    expect(chipPageId("https://example.com/knowledge/pages/" + ID)).toBeNull();
    expect(chipPageId("/knowledge/pages/kp-a")).toBeNull();
    expect(chipPageId("/knowledge/search?q=x")).toBeNull();
    expect(chipPageId("https://example.com")).toBeNull();
    expect(chipPageId(null)).toBeNull();
  });
});

describe("chipRuns", () => {
  it("finds an internal link and skips an external one", () => {
    const md = `See [Refunds](/knowledge/pages/${ID}) and [the web](https://example.com).`;
    const runs = chipRuns(docOf(md));
    expect(runs).toHaveLength(1);
    expect(runs[0].pageId).toBe(ID);
  });

  it("gives one glyph to a link that holds a bold word", () => {
    const md = `[Big **refund** policy](/knowledge/pages/${ID})`;
    expect(chipRuns(docOf(md))).toHaveLength(1);
  });

  it("keeps the stored form a Markdown link", () => {
    const md = `See [Refunds](/knowledge/pages/${ID}) here.`;
    expect(writeBody(parseBody(md))).toBe(md);
  });
});
