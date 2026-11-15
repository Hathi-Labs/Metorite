/**
 * WFC-4a-1 fence: the palette offers the human step, and no step that
 * cannot run or has no form (`pm_task`, `pm_lifecycle`, `studio_write`).
 *
 * The gateway leaves those three out of the search
 * (`catalog.UNOFFERED_NODE_TYPES`, fenced by
 * `tests/unit/test_workflows_node_offer.py`). This file holds the palette's
 * own list and its drops to the same offer, so a stale search answer still
 * drops nothing.
 *
 * Mutation 1: add a `pm_task` item to the Logic section. "no item" turns red.
 * Mutation 2: map `pm_task` in `dropForResult`. "drops nothing" turns red.
 * Mutation 3 (WFC-4a-2b-1): draw "Change a record" outside `studioWriteOffered`.
 * "is not in the palette of a member who is not the owner" turns red.
 */
import { readFileSync } from "node:fs";
import { createElement } from "react";
import { renderToStaticMarkup } from "react-dom/server";
import { describe, expect, it } from "vitest";

import NodePalette, { dropForResult, humanStepDrop, studioWriteChangeDrop, studioWriteDrop } from "./NodePalette";
import type { Catalog, SearchResult } from "../lib/types";

const catalog: Catalog = {
  node_types: [],
  condition_ops: ["equals"],
  agents: [],
  integrations: [],
  tools: [],
  modules: [],
};

const UNOFFERED = ["pm_task", "pm_lifecycle", "studio_write"] as const;

const nodeResult = (key: string, label: string): SearchResult => ({
  kind: "node",
  key,
  label,
  description: "",
  category: "logic",
  score: 4,
});

const draw = (studioWriteOffered?: boolean) =>
  renderToStaticMarkup(createElement(NodePalette, { catalog, onAdd: () => {}, studioWriteOffered }));

describe("NodePalette", () => {
  it("offers the human step in the Logic section", () => {
    const html = draw();
    expect(html).toContain("Human step");
    expect(html).toContain("give a member a task, wait until it is done");
  });

  it("has no item for a step that cannot run or has no form", () => {
    const html = draw();
    for (const words of ["Update task", "Lifecycle sweep", "Task clean-up", "Write a record"]) {
      expect(html, words).not.toContain(words);
    }
  });

  it("drops a human step with the three keys, empty", () => {
    expect(humanStepDrop()).toEqual({
      nodeType: "human_step",
      label: "Human step",
      config: { assignee: "", title: "", description: "" },
    });
  });

  it("drops a human step that the search found", () => {
    expect(dropForResult(nodeResult("human_step", "Human step"), catalog)).toEqual(humanStepDrop());
  });

  it.each(UNOFFERED)("drops nothing for %s from a search answer", (type) => {
    expect(dropForResult(nodeResult(type, type), catalog)).toBeNull();
  });
});

describe("the pairs that add or change a record (WFC-4a-2a, WFC-4a-2b-1)", () => {
  it("is not in the palette of a member who is not the owner", () => {
    for (const html of [draw(), draw(false)]) {
      expect(html).not.toContain("Add a record");
      expect(html).not.toContain("Change a record");
    }
  });

  it("is in the palette of the owner", () => {
    const html = draw(true);
    expect(html).toContain("Add a record");
    expect(html).toContain("approval, then a new record in your app");
    expect(html).toContain("Change a record");
    expect(html).toContain("approval, then a change to a record in your app");
  });

  it("drops the change as a Human approval, then a write with no action", () => {
    const drop = studioWriteChangeDrop();
    expect(drop.nodeType).toBe("approval");
    expect(drop.config).toEqual({ message: "Change this record in the app?" });
    expect(drop.then).toHaveLength(1);
    expect(drop.then?.[0]).toEqual({
      nodeType: "studio_write",
      label: "Change a record",
      config: { package_id: "", action: "", data: {} },
    });
  });

  it("drops a Human approval, then the write, as one chain", () => {
    const drop = studioWriteDrop();
    expect(drop.nodeType).toBe("approval");
    expect(drop.then).toHaveLength(1);
    expect(drop.then?.[0]).toMatchObject({
      nodeType: "studio_write",
      config: { package_id: "", action: "", data: {} },
    });
  });
});

describe("the source", () => {
  const src = readFileSync(new URL("./NodePalette.tsx", import.meta.url), "utf8");
  it.each(["pm_task", "pm_lifecycle"] as const)("names no %s drop", (type) => {
    expect(src).not.toContain(`nodeType: "${type}"`);
    expect(src).not.toContain(`"logic:${type}"`);
  });

  it("shows the pair only when the editor says the member is the owner", () => {
    expect(src.match(/studioWriteOffered &&/g) ?? []).toHaveLength(2);
    expect(src).not.toContain('nodeType: "studio_write"');
    expect(src).not.toContain('r.key === "studio_write"');
  });
});
