/** WS-64 #11 — a move reads My work again and re-points an open panel. */
import fs from "node:fs";
import path from "node:path";

import { describe, expect, it } from "vitest";

import { moveFollowUp } from "./moveRefresh";

describe("moveFollowUp", () => {
  it("reads My work again after a move there", () => {
    expect(moveFollowUp({ myWork: true, openTaskId: null, movedIds: ["a"] }).reloadMyWork).toBe(true);
    expect(moveFollowUp({ myWork: false, openTaskId: null, movedIds: ["a"] }).reloadMyWork).toBe(false);
  });

  it("re-opens the panel task when it moved, and only then", () => {
    expect(moveFollowUp({ myWork: true, openTaskId: "a", movedIds: ["a", "b"] }).reopenTaskId).toBe("a");
    expect(moveFollowUp({ myWork: true, openTaskId: "c", movedIds: ["a", "b"] }).reopenTaskId).toBeNull();
    expect(moveFollowUp({ myWork: true, openTaskId: null, movedIds: ["a"] }).reopenTaskId).toBeNull();
  });

  it("the page's move calls it, and reads the store and the panel", () => {
    const page = fs.readFileSync(path.join(__dirname, "..", "page.tsx"), "utf-8");
    const move = page.slice(page.indexOf("async function moveTasksTo("));
    const body = move.slice(0, move.indexOf("async function", 20));
    expect(body).toContain("moveFollowUp(");
    expect(body).toContain("reloadItems()");
    expect(body).toContain("openTaskById(follow.reopenTaskId");
  });
});
