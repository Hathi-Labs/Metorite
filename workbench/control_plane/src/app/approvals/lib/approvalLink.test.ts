/**
 * The link to one proposal in the approval queue (WS-58 MD-3a,
 * `my_day_feed.md` §5.8 item 9).
 *
 * Mutations caught: the reader takes an id that is not a UUID; the page
 * ignores the link; a row that left the queue says nothing; the line shows
 * before the queue has loaded; the page marks the row with a look of its own.
 */
import { readFileSync } from "node:fs";
import { describe, expect, it } from "vitest";

import { isInAppPath } from "@/lib/inAppLink";
import { ACTION_PARAM, actionIdFromSearch, approvalRowDomId, markedRow } from "./approvalLink";

const ID = "0f8fad5b-d9cb-469f-a165-70867728950e";
const OTHER = "7c9e6679-7425-40de-944b-e07fc1f90ae7";
const q = (s: string) => new URLSearchParams(s);

describe("actionIdFromSearch", () => {
  it("reads a UUID, lower-case and trimmed", () => {
    expect(actionIdFromSearch(q(`${ACTION_PARAM}=${ID}`))).toBe(ID);
    expect(actionIdFromSearch(q(`action=${ID.toUpperCase()}`))).toBe(ID);
    expect(actionIdFromSearch(q(`action=%20${ID}%20`))).toBe(ID);
  });

  it("ignores anything that is not a UUID, so the page opens the queue", () => {
    for (const bad of ["", "pa-1", "../admin", `${ID}x`, "javascript:alert(1)", `${ID}&x=1`]) {
      expect(actionIdFromSearch(q(`action=${encodeURIComponent(bad)}`)), bad).toBeNull();
    }
    expect(actionIdFromSearch(q(""))).toBeNull();
    expect(actionIdFromSearch(q(`task=${ID}`))).toBeNull();
    expect(actionIdFromSearch(null)).toBeNull();
  });

  it("is an in-app path, so the feed opens it in this tab", () => {
    expect(isInAppPath(`/approvals?action=${ID}`)).toBe(true);
  });
});

describe("markedRow", () => {
  const rows = [{ id: OTHER }, { id: ID.toUpperCase() }];

  it("marks the row the link names, as the queue spells it", () => {
    expect(markedRow(rows, ID, true)).toEqual({ marked: ID.toUpperCase(), gone: false });
  });

  it("says the proposal is gone when the loaded queue has no row for it", () => {
    expect(markedRow([{ id: OTHER }], ID, true)).toEqual({ marked: null, gone: true });
    expect(markedRow([], ID, true)).toEqual({ marked: null, gone: true });
  });

  it("says nothing before the first load", () => {
    expect(markedRow([], ID, false)).toEqual({ marked: null, gone: false });
  });

  it("marks nothing and says nothing with no link", () => {
    expect(markedRow(rows, null, true)).toEqual({ marked: null, gone: false });
  });

  it("gives each row a DOM id to scroll to", () => {
    expect(approvalRowDomId(ID)).toBe(`approval-${ID}`);
  });
});

describe("the page reads the link", () => {
  const page = readFileSync(new URL("../page.tsx", import.meta.url), "utf-8");

  it("reads the id through the parser, inside a Suspense boundary", () => {
    expect(page).toMatch(/actionIdFromSearch\(useSearchParams\(\)\)/);
    expect(page).toMatch(/<Suspense fallback=\{null\}>\s*<ActionParam onAction=\{setActionId\} \/>/);
    expect(page).toMatch(/markedRow\(rows, actionId, fetched\)/);
  });

  it("scrolls to the marked row and marks it with the house selected look", () => {
    expect(page).toMatch(/getElementById\(approvalRowDomId\(marked\)\)/);
    expect(page).toMatch(/id=\{approvalRowDomId\(r\.id\)\}/);
    expect(page).toMatch(/aria-current=\{isMarked \? "true" : undefined\}/);
    // The cursor look of `InboxCard.tsx`, not a look of this page's own.
    expect(page).toContain("border-primary ring-1 ring-primary/40");
  });

  it("says so above the queue when the proposal is no longer waiting", () => {
    const line = page.indexOf("That request is no longer waiting for you.");
    expect(line).toBeGreaterThan(-1);
    expect(page.slice(0, line)).toMatch(/\{gone && \(/);
    expect(line).toBeLessThan(page.indexOf("rows.map((r)"));
  });
});
