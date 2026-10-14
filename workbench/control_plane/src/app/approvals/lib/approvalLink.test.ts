/**
 * The link to one proposal in the approval queue (WS-58 MD-3a,
 * `my_day_feed.md` §5.8 item 9).
 *
 * Mutations caught: the reader takes an id that is not a UUID; the page
 * ignores the link; a row that left the queue says nothing; the line shows
 * before the queue has loaded, or after a failed load; a failed reload empties
 * the queue; the page marks the row with a look of its own.
 */
import { readFileSync } from "node:fs";
import { describe, expect, it } from "vitest";

import { isInAppPath } from "@/lib/inAppLink";
import {
  ACTION_PARAM,
  actionIdFromSearch,
  afterLoad,
  approvalRowDomId,
  markedRow,
  type QueueState,
} from "./approvalLink";

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
    expect(markedRow(rows, ID, "ok")).toEqual({ marked: ID.toUpperCase(), gone: false });
  });

  it("says the proposal is gone when the loaded queue has no row for it", () => {
    expect(markedRow([{ id: OTHER }], ID, "ok")).toEqual({ marked: null, gone: true });
    expect(markedRow([], ID, "ok")).toEqual({ marked: null, gone: true });
  });

  it("says nothing before the first load", () => {
    expect(markedRow([], ID, "pending")).toEqual({ marked: null, gone: false });
  });

  it("marks nothing and says nothing with no link", () => {
    expect(markedRow(rows, null, "ok")).toEqual({ marked: null, gone: false });
  });

  it("says nothing after a failed load, because the page does not know", () => {
    expect(markedRow([], ID, "failed")).toEqual({ marked: null, gone: false });
    expect(markedRow([{ id: OTHER }], ID, "failed")).toEqual({ marked: null, gone: false });
  });

  it("gives each row a DOM id to scroll to", () => {
    expect(approvalRowDomId(ID)).toBe(`approval-${ID}`);
  });
});

describe("a load of the queue (MD-3a review)", () => {
  const start: QueueState<{ id: string }> = { rows: [], load: "pending" };

  it("a failed first load shows no queue and no gone line", () => {
    const after = afterLoad(start, { ok: false });
    expect(after).toEqual({ rows: [], load: "failed" });
    expect(markedRow(after.rows, ID, after.load).gone).toBe(false);
  });

  it("a failed reload after a good one keeps the old rows and shows no gone line", () => {
    const good = afterLoad(start, { ok: true, rows: [{ id: OTHER }] });
    // The link's row is not in the queue, so after a GOOD load it is gone.
    expect(markedRow(good.rows, ID, good.load).gone).toBe(true);
    const failed = afterLoad(good, { ok: false });
    expect(failed).toEqual({ rows: [{ id: OTHER }], load: "failed" });
    expect(markedRow(failed.rows, ID, failed.load).gone).toBe(false);
    // And the marked row stays marked through a failed reload.
    const marked = afterLoad(start, { ok: true, rows: [{ id: ID }] });
    expect(markedRow(afterLoad(marked, { ok: false }).rows, ID, "failed").marked).toBe(ID);
  });

  it("a good load replaces the rows", () => {
    const good = afterLoad({ rows: [{ id: OTHER }], load: "failed" }, { ok: true, rows: [] });
    expect(good).toEqual({ rows: [], load: "ok" });
  });
});

describe("the page reads the link", () => {
  const page = readFileSync(new URL("../page.tsx", import.meta.url), "utf-8");

  it("reads the id through the parser, inside a Suspense boundary", () => {
    expect(page).toMatch(/actionIdFromSearch\(useSearchParams\(\)\)/);
    expect(page).toMatch(/<Suspense fallback=\{null\}>\s*<ActionParam onAction=\{setActionId\} \/>/);
    expect(page).toMatch(/markedRow\(rows, actionId, queue\.load\)/);
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
    // One live region, always mounted: the line goes INSIDE it.
    const region = page.indexOf('<div role="status">');
    expect(region).toBeGreaterThan(-1);
    expect(region).toBeLessThan(line);
    expect(page.slice(region, line)).not.toMatch(/\{gone && \(\s*<div role="status"/);
  });

  it("a load runs through afterLoad, and a failure never empties the queue", () => {
    expect(page).toMatch(/setQueue\(\(q\) => afterLoad\(q, result\)\)/);
    expect(page).not.toMatch(/setQueue\(\{\s*rows: \[\]/);
    // The empty queue shows only after a GOOD load.
    expect(page).toMatch(/queue\.load === "ok" && rows\.length === 0/);
  });
});
